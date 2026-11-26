"""
Thế Giới Cáp Servo Crawler (Bản nâng cấp - Hỗ trợ Tự Động Tiếp Tục Cào / Checkpoint / Chống Chặn IP)
- Tự động tiếp tục (Resume) không cào lại từ đầu nếu bị ngắt.
- Làm sạch URL triệt để (xử lý khoảng trắng thừa, %20).
- Checkpoint định kỳ: Lưu tiến trình cào vào JSON sau mỗi sản phẩm thành công.
- Cơ chế Exponential Backoff & xoay vòng User-Agent trình duyệt thật.
- Chuẩn hóa thông số kỹ thuật HTML Table có Copyright Haiphongtech.vn.
- Ảnh WebP 800x800 chuẩn, xuất file Excel có cột Tên ảnh riêng.
"""

import os
import shutil
import time
import json
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin, unquote
from bs4 import BeautifulSoup
import requests
import traceback
import logging
from PIL import Image
import io

from app import socketio

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Headers giả lập trình duyệt thật đầy đủ Sec-Ch-Ua để tránh bị firewall WAF/Apache chặn
REAL_BROWSER_HEADERS = [
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Ch-Ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
    },
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
    }
]

def clean_url(url):
    """Làm sạch URL, loại bỏ khoảng trắng thừa đầu/cuối và %20."""
    if not url:
        return ""
    u = url.strip()
    # Unquote and strip
    u = re.sub(r'\s+', '', u)
    u = u.replace('%20', '').strip()
    return u

def safe_socketio_emit(event: str, data: dict):
    """Emit Socket.IO một cách an toàn."""
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"Lỗi khi emit Socket.IO ({event}): {e}")

def get_page_html(url, session=None, max_retries=4):
    """Tải mã HTML của URL với cơ chế thử lại, xoay User-Agent và exponential backoff."""
    url = clean_url(url)
    if not url:
        return None
        
    for retry in range(max_retries):
        headers = REAL_BROWSER_HEADERS[retry % len(REAL_BROWSER_HEADERS)].copy()
        try:
            req = session if session else requests
            r = req.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                return r.text
            elif r.status_code in (403, 429):
                # Rate limited hoặc WAF tạm thời
                time.sleep(2 * (retry + 1))
        except Exception as e:
            if retry == max_retries - 1:
                logger.error(f"Lỗi tải HTML {url}: {e}")
            time.sleep(2 * (retry + 1))
    return None

def is_thegioicapservo_category_url(url):
    """Kiểm tra xem URL có phải là danh mục của thegioicapservo.com không."""
    url = clean_url(url)
    if not url or 'thegioicapservo.com' not in url:
        return False
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    if not path:
        return False
    segments = [s for s in path.split('/') if s]
    if len(segments) == 1:
        return True
    return False

def is_thegioicapservo_product_url(url):
    """Kiểm tra xem URL có phải là sản phẩm của thegioicapservo.com không."""
    url = clean_url(url)
    if not url or 'thegioicapservo.com' not in url:
        return False
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    segments = [s for s in path.split('/') if s]
    if len(segments) >= 2:
        return True
    return False

def get_category_name(url, session=None):
    """Lấy tên danh mục chuẩn hóa để đặt tên thư mục."""
    url = clean_url(url)
    html = get_page_html(url, session=session)
    name = None
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.select_one('h1, .breadcrumb li.active, .breadcrumb-item.active, .breadcrumb li:last-child')
        if title and title.get_text(strip=True):
            name = title.get_text(strip=True)
    
    if not name:
        parsed = urlparse(url)
        path = parsed.path.strip('/').split('/')
        name = path[0] if path and path[0] else 'danh_muc'

    name = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name

def detect_pagination(category_url, session=None):
    """Phát hiện tổng số trang của danh mục trên thegioicapservo.com."""
    try:
        html = get_page_html(category_url, session=session)
        if not html:
            return 1
        
        soup = BeautifulSoup(html, 'html.parser')
        pagination = soup.select_one('.pagination, .paging, ul.pagination, .product-filter__pagination')
        if not pagination:
            return 1
        
        max_page = 1
        for a in pagination.select('a[href]'):
            href = a['href']
            m = re.search(r'[?&]page=(\d+)', href)
            if m:
                page_num = int(m.group(1))
                if page_num > max_page:
                    max_page = page_num
            txt = a.get_text(strip=True)
            if txt.isdigit():
                max_page = max(max_page, int(txt))
                
        logger.info(f"Phát hiện tổng cộng {max_page} trang cho danh mục: {category_url}")
        return max_page
    except Exception as e:
        logger.warning(f"Lỗi khi phát hiện số trang ({category_url}): {e}")
        return 1

def make_pagination_url(base_url, page_number):
    """Tạo URL cho trang thứ page_number."""
    if page_number <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = parsed.query
    if query:
        if 'page=' in query:
            new_query = re.sub(r'page=\d+', f'page={page_number}', query)
        else:
            new_query = f"{query}&page={page_number}"
    else:
        new_query = f"page={page_number}"
    
    new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
    return new_url

def extract_thegioicapservo_product_urls(category_url, session=None):
    """Quét toàn bộ link sản phẩm trong danh mục, loại bỏ URL rác và khoảng trắng."""
    product_urls = []
    max_pages = detect_pagination(category_url, session=session)
    logger.info(f"Bắt đầu quét {max_pages} trang của danh mục: {category_url}")
    
    for page in range(1, max_pages + 1):
        page_url = make_pagination_url(category_url, page)
        html = get_page_html(page_url, session=session)
        if not html:
            continue
            
        soup = BeautifulSoup(html, 'html.parser')
        product_items = soup.select('.product-listing__collection .product')
        if not product_items:
            product_items = soup.select('.product_listing_main .product, .product')
            
        for item in product_items:
            a = item.select_one('.product_img a.img_change, .product_name a, a.btn-info, a[href]')
            if a and a.get('href'):
                raw_href = clean_url(a['href'])
                full_url = clean_url(urljoin(category_url, raw_href))
                if full_url and full_url not in product_urls:
                    product_urls.append(full_url)
                    
        time.sleep(0.2)
        
    logger.info(f"Tổng số URL sản phẩm thu được từ {category_url}: {len(product_urls)}")
    return product_urls

def extract_thegioicapservo_product_info(product_url, session=None):
    """Trích xuất chi tiết sản phẩm."""
    product_url = clean_url(product_url)
    try:
        html = get_page_html(product_url, session=session)
        if not html:
            return None
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Tên sản phẩm
        product_name = ""
        name_el = soup.select_one('.product_name[itemprop="name"], h1.product_name, .product_info .product_name, .product_name')
        if name_el:
            product_name = name_el.get_text(strip=True)
            
        # 2. Mã sản phẩm
        product_code = ""
        vendor_el = soup.select_one('.product_vendor')
        if vendor_el:
            a_tag = vendor_el.select_one('a')
            if a_tag and a_tag.get_text(strip=True):
                product_code = a_tag.get_text(strip=True)
            else:
                raw_text = vendor_el.get_text(strip=True)
                product_code = re.sub(r'^D[òo]ng\s*s[ảa]n\s*ph[ẩa]m\s*[:\s]+', '', raw_text, flags=re.IGNORECASE).strip()
                
        if not product_code:
            parsed = urlparse(product_url)
            last_segment = parsed.path.strip('/').split('/')[-1]
            product_code = last_segment
            
        # 3. Giá sản phẩm
        product_price = "Liên hệ"
        price_el = soup.select_one('.product_price .money, .product_price, .price-new, .product-price')
        if price_el:
            product_price = price_el.get_text(strip=True)
            
        # 4. Tìm link ảnh chất lượng cao
        raw_images = []
        for img in soup.select('#image, .product_image_main img, .product_img img, .thumbnails img, #default_gallery img, .image-additional img, .product-image img'):
            src = img.get('data-zoom-image') or img.get('data-large_image') or img.get('src')
            if src:
                src = urljoin(product_url, src.strip())
                if src not in raw_images:
                    raw_images.append(src)
                    
        for a in soup.select('.thumbnails a, .image-additional a, a.thumbnail'):
            href = a.get('href')
            if href and ('/catalog/' in href or '/image/' in href):
                href = urljoin(product_url, href.strip())
                if href not in raw_images:
                    raw_images.append(href)
                    
        for el in soup.find_all(style=re.compile(r'background-image')):
            m = re.search(r'url\(["\']?([^"\')]+)["\']?\)', el['style'])
            if m:
                bg = urljoin(product_url, m.group(1).strip())
                if bg not in raw_images:
                    raw_images.append(bg)
                    
        valid_images = []
        for img_url in raw_images:
            lower = img_url.lower()
            if any(lower.endswith(ext) or ext in lower for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                if all(bad not in lower for bad in ['logo', 'icon', 'banner', 'placeholder', 'zalo', 'cart']):
                    valid_images.append(img_url)
                    
        # 5. Xử lý thông số kỹ thuật sang HTML table chuẩn có Copyright Haiphongtech.vn
        desc_el = soup.select_one('#tab-description, .tab-pane#tab-description')
        specifications_html = parse_thegioicapservo_specs_to_html(desc_el, product_code, product_name)
        
        return {
            'url': product_url,
            'name': product_name,
            'code': product_code,
            'price': product_price,
            'images': valid_images,
            'specifications': specifications_html
        }
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất sản phẩm {product_url}: {e}")
        return None

def parse_thegioicapservo_specs_to_html(desc_el, product_code, product_name):
    """Chuyển đổi nội dung #tab-description thành table HTML chuẩn có Copyright."""
    rows = []
    rows.append(('Mã sản phẩm', product_code or 'N/A'))
    rows.append(('Tên sản phẩm', product_name or 'N/A'))
    
    if desc_el:
        tables = desc_el.select('table')
        if tables:
            for table in tables:
                for tr in table.select('tr'):
                    tds = tr.select('th, td')
                    if len(tds) >= 2:
                        k = tds[0].get_text(strip=True)
                        v = tds[1].get_text(strip=True)
                        if k and v and k != 'Thông số' and (k, v) not in rows:
                            rows.append((k, v))
        else:
            paragraphs = desc_el.find_all(['p', 'li', 'div', 'h3', 'h4'])
            prev_strong_header = None
            
            for p in paragraphs:
                p_text = p.get_text(strip=True)
                if not p_text:
                    continue
                    
                strong = p.select_one('strong, b')
                strong_text = strong.get_text(strip=True) if strong else ""
                
                if strong_text and strong_text in p_text and len(p_text) > len(strong_text):
                    remainder = p_text[len(strong_text):].strip()
                    if remainder.startswith(':'):
                        remainder = remainder[1:].strip()
                    clean_k = strong_text.rstrip(':').strip()
                    if clean_k and remainder:
                        if (clean_k, remainder) not in rows:
                            rows.append((clean_k, remainder))
                        prev_strong_header = None
                        continue
                        
                if ':' in p_text and not p_text.startswith('http'):
                    parts = p_text.split(':', 1)
                    k = parts[0].strip()
                    v = parts[1].strip()
                    if k and v and len(k) < 60:
                        if (k, v) not in rows:
                            rows.append((k, v))
                        prev_strong_header = None
                        continue
                        
                if strong and len(strong_text) >= len(p_text) * 0.8 and len(strong_text) < 80:
                    prev_strong_header = strong_text
                    continue
                    
                if prev_strong_header and p_text and len(p_text) > 3:
                    if (prev_strong_header, p_text) not in rows:
                        rows.append((prev_strong_header, p_text))
                    prev_strong_header = None
                    continue
    
    html_table = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html_table += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
    html_table += '<tbody>'
    for key, val in rows:
        html_table += f'<tr><td style="font-weight: bold;">{key}</td><td>{val}</td></tr>'
    html_table += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
    html_table += '</tbody></table>'
    return html_table

def download_and_process_image(image_url, product_code, img_index, output_folder):
    """Tải ảnh, ép kích thước về 800x800 px và lưu thành định dạng .webp."""
    clean_code = re.sub(r'[\\/:*?"<>|]', '_', product_code).strip()
    if not clean_code:
        clean_code = "product"
        
    filename = f"{clean_code}.webp" if img_index == 0 else f"{clean_code}_{img_index + 1}.webp"
    filepath = os.path.abspath(os.path.join(output_folder, filename))
    
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return filename, filepath
        
    for retry in range(3):
        try:
            r = requests.get(image_url, headers=REAL_BROWSER_HEADERS[0], timeout=25)
            if r.status_code == 200 and r.content:
                image = Image.open(io.BytesIO(r.content))
                if image.mode in ('RGBA', 'LA'):
                    bg = Image.new('RGB', (800, 800), (255, 255, 255))
                    image_resized = image.resize((800, 800), Image.Resampling.LANCZOS)
                    if image_resized.mode == 'RGBA':
                        bg.paste(image_resized, (0, 0), image_resized)
                        final_image = bg
                    else:
                        final_image = image_resized.convert('RGB')
                else:
                    final_image = image.convert('RGB').resize((800, 800), Image.Resampling.LANCZOS)
                    
                final_image.save(filepath, 'WEBP', quality=92, method=6)
                return filename, filepath
        except Exception:
            time.sleep(1)
            
    return None, None

def create_excel_report(products, output_file, sheet_name="TheGioiCapServo"):
    """Tạo file Excel báo cáo với cột Tên ảnh riêng biệt."""
    try:
        excel_data = []
        for i, prod in enumerate(products):
            image_names = prod.get('image_filenames', [])
            image_names_str = '\n'.join(image_names) if isinstance(image_names, list) else str(image_names)
            image_paths = prod.get('image_paths', [])
            image_paths_str = '\n'.join(image_paths) if isinstance(image_paths, list) else str(image_paths)
            raw_images = prod.get('images', [])
            raw_images_str = '\n'.join(raw_images) if isinstance(raw_images, list) else str(raw_images)
            
            excel_data.append({
                'STT': i + 1,
                'URL': prod.get('url', ''),
                'Mã sản phẩm': prod.get('code', ''),
                'Tên sản phẩm': prod.get('name', ''),
                'Giá sản phẩm': prod.get('price', 'Liên hệ'),
                'Tên ảnh': image_names_str,
                'Số lượng ảnh': len(image_names),
                'Đường dẫn link ảnh': image_paths_str,
                'Thông số kỹ thuật': prod.get('specifications', ''),
                'Danh sách ảnh': raw_images_str
            })
            
        df = pd.DataFrame(excel_data)
        output_abs = os.path.abspath(output_file)
        os.makedirs(os.path.dirname(output_abs), exist_ok=True)
        
        with pd.ExcelWriter(output_abs, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)
            worksheet = writer.sheets[sheet_name]
            for col in worksheet.columns:
                col_name = str(col[0].value) if col[0].value is not None else ''
                col_letter = col[0].column_letter
                if col_name in ['URL', 'Đường dẫn link ảnh', 'Danh sách ảnh']:
                    worksheet.column_dimensions[col_letter].width = 45
                elif col_name == 'Thông số kỹ thuật':
                    worksheet.column_dimensions[col_letter].width = 60
                elif col_name in ['Tên sản phẩm', 'Mã sản phẩm', 'Tên ảnh']:
                    worksheet.column_dimensions[col_letter].width = 32
                else:
                    worksheet.column_dimensions[col_letter].width = 16
        return True
    except Exception as e:
        logger.error(f"Lỗi tạo Excel {output_file}: {e}")
        return False

class TheGioiCapServoCrawler:
    """
    Crawler thông minh:
    - Có khả năng RESUME / CHECKPOINT: Tiếp tục cào không dừng, không cào lại từ đầu.
    - Checkpoint lưu định kỳ vào products_checkpoint.json.
    - Tự động bỏ qua danh mục đã hoàn thành.
    """
    def __init__(self, output_root="output_thegioicapservo", max_workers=5, max_retries=3, resume_session_dir=None):
        self.output_root = os.path.abspath(output_root)
        self.max_workers = min(max(1, int(max_workers)), 8)
        self.max_retries = max_retries
        self.resume_session_dir = resume_session_dir
        self.session = requests.Session()
        os.makedirs(self.output_root, exist_ok=True)

    def _find_latest_session_dir(self):
        """Tìm thư mục phiên cào gần nhất nếu có để tiếp tục."""
        if self.resume_session_dir and os.path.exists(self.resume_session_dir):
            return self.resume_session_dir
            
        sessions = [
            os.path.join(self.output_root, d) for d in os.listdir(self.output_root)
            if d.startswith("TheGioiCapServo_ngay") and os.path.isdir(os.path.join(self.output_root, d))
        ]
        if sessions:
            sessions.sort(key=lambda s: os.path.getmtime(s), reverse=True)
            return sessions[0]
        return None

    def crawl_products(self, input_urls, resume=True):
        start_time = time.time()
        
        # 1. Xác định thư mục lưu kết quả (tiếp tục phiên cũ nếu có hoặc tạo mới)
        result_dir = None
        if resume:
            result_dir = self._find_latest_session_dir()
            
        if not result_dir:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = os.path.join(self.output_root, f"TheGioiCapServo_ngay{timestamp}")
            os.makedirs(result_dir, exist_ok=True)
            
        report_dir = os.path.join(result_dir, "Bao_cao")
        os.makedirs(report_dir, exist_ok=True)
        
        stats = {
            "categories": 0,
            "single_products": 0,
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "failed": 0
        }
        
        safe_socketio_emit('progress_update', {
            'percent': 5,
            'message': f'Bắt đầu phân tích {len(input_urls)} URL (Hỗ trợ tự động Resume)...',
            'detail': 'Đang phân loại danh mục và kiểm tra checkpoint...'
        })
        
        # 2. Phân loại URL
        category_map = {}
        single_products = []
        
        for url in input_urls:
            url = clean_url(url)
            if not url:
                continue
            if is_thegioicapservo_category_url(url):
                cat_name = get_category_name(url, session=self.session)
                if cat_name not in category_map:
                    category_map[cat_name] = []
                    stats["categories"] += 1
                category_map[cat_name].append(url)
            else:
                single_products.append(url)
                stats["single_products"] += 1
                
        all_products = []
        total_steps = len(category_map) + (1 if single_products else 0)
        current_step = 0
        
        def process_product_worker(p_url, img_folder):
            """Xử lý cào 1 sản phẩm kèm độ trễ lịch sự."""
            p_url = clean_url(p_url)
            info = extract_thegioicapservo_product_info(p_url, session=self.session)
            if not info:
                return None
            
            downloaded_filenames = []
            downloaded_paths = []
            for idx, img_url in enumerate(info.get('images', [])):
                fname, fpath = download_and_process_image(img_url, info['code'], idx, img_folder)
                if fname and fpath:
                    downloaded_filenames.append(fname)
                    downloaded_paths.append(fpath)
                    
            info['image_filenames'] = downloaded_filenames
            info['image_paths'] = downloaded_paths
            time.sleep(0.15)  # Tránh gửi request quá dồn dập
            return info
            
        # 3. Xử lý từng danh mục với Checkpoint & Resume
        for cat_name, cat_urls in category_map.items():
            current_step += 1
            cat_folder = os.path.join(result_dir, cat_name)
            img_folder = os.path.join(cat_folder, "Anh")
            os.makedirs(img_folder, exist_ok=True)
            
            checkpoint_file = os.path.join(cat_folder, "products_checkpoint.json")
            cat_excel = os.path.join(cat_folder, f"{cat_name}.xlsx")
            urls_file = os.path.join(cat_folder, f"{cat_name}_urls.txt")
            
            # Đọc checkpoint cũ nếu có
            cat_products = []
            crawled_urls = set()
            
            if os.path.exists(checkpoint_file):
                try:
                    with open(checkpoint_file, 'r', encoding='utf-8') as f:
                        cat_products = json.load(f)
                        crawled_urls = {clean_url(p['url']) for p in cat_products if p.get('url')}
                    logger.info(f"[{cat_name}] Đã nạp {len(cat_products)} sản phẩm từ checkpoint!")
                except Exception as e:
                    logger.warning(f"Lỗi đọc checkpoint: {e}")
                    
            if not cat_products and os.path.exists(cat_excel):
                try:
                    df = pd.read_excel(cat_excel)
                    for _, row in df.iterrows():
                        u = clean_url(str(row.get('URL', '')))
                        if u:
                            crawled_urls.add(u)
                            cat_products.append({
                                'url': u,
                                'name': str(row.get('Tên sản phẩm', '')),
                                'code': str(row.get('Mã sản phẩm', '')),
                                'price': str(row.get('Giá sản phẩm', 'Liên hệ')),
                                'image_filenames': str(row.get('Tên ảnh', '')).splitlines() if pd.notna(row.get('Tên ảnh')) else [],
                                'image_paths': str(row.get('Đường dẫn link ảnh', '')).splitlines() if pd.notna(row.get('Đường dẫn link ảnh')) else [],
                                'specifications': str(row.get('Thông số kỹ thuật', '')),
                                'images': str(row.get('Danh sách ảnh', '')).splitlines() if pd.notna(row.get('Danh sách ảnh')) else []
                            })
                    logger.info(f"[{cat_name}] Đã nạp {len(cat_products)} sản phẩm từ Excel hiện có!")
                except Exception as e:
                    logger.warning(f"Lỗi đọc Excel: {e}")

            # Thu thập URL danh mục (nếu đã có file urls.txt thì đọc lại, không quét lại trang web để tiết kiệm thời gian)
            cat_product_urls = []
            if os.path.exists(urls_file):
                with open(urls_file, 'r', encoding='utf-8') as f:
                    cat_product_urls = [clean_url(line) for line in f if clean_url(line)]
            else:
                for c_url in cat_urls:
                    p_urls = extract_thegioicapservo_product_urls(c_url, session=self.session)
                    cat_product_urls.extend(p_urls)
                cat_product_urls = list(dict.fromkeys(cat_product_urls))
                with open(urls_file, 'w', encoding='utf-8') as f:
                    for u in cat_product_urls:
                        f.write(f"{u}\n")

            stats["products_found"] += len(cat_product_urls)
            
            # Lọc danh sách URL CÒN LẠI cần cào
            remaining_urls = [u for u in cat_product_urls if u not in crawled_urls]
            
            if not remaining_urls and cat_products:
                logger.info(f"[{cat_name}] ĐÃ HOÀN THÀNH ĐẦY ĐỦ ({len(cat_products)}/{len(cat_product_urls)} sản phẩm). Bỏ qua cào lại!")
                all_products.extend(cat_products)
                continue
                
            logger.info(f"[{cat_name}] Cần cào tiếp: {len(remaining_urls)} / {len(cat_product_urls)} sản phẩm (Đã có sẵn {len(cat_products)})")
            
            safe_socketio_emit('progress_update', {
                'percent': int(10 + (current_step - 1) / max(1, total_steps) * 80),
                'message': f'[{current_step}/{total_steps}] Tiếp tục cào {cat_name}: {len(remaining_urls)} link còn lại...',
                'detail': f'Đã có sẵn: {len(cat_products)} sản phẩm'
            })
            
            # Cào các URL còn lại đa luồng
            if remaining_urls:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {executor.submit(process_product_worker, u, img_folder): u for u in remaining_urls}
                    done_count = 0
                    for future in as_completed(futures):
                        done_count += 1
                        res = future.result()
                        if res:
                            cat_products.append(res)
                            crawled_urls.add(clean_url(res['url']))
                            stats["products_processed"] += 1
                            stats["images_downloaded"] += len(res.get('image_filenames', []))
                        else:
                            stats["failed"] += 1
                            
                        # Ghi checkpoint định kỳ mỗi 5 sản phẩm
                        if done_count % 5 == 0 or done_count == len(remaining_urls):
                            try:
                                with open(checkpoint_file, 'w', encoding='utf-8') as f:
                                    json.dump(cat_products, f, ensure_ascii=False, indent=2)
                            except Exception:
                                pass
                                
                        if done_count % 5 == 0 or done_count == len(remaining_urls):
                            cat_percent = int(10 + (current_step - 1 + (len(cat_products)) / max(1, len(cat_product_urls))) / max(1, total_steps) * 80)
                            safe_socketio_emit('progress_update', {
                                'percent': min(90, cat_percent),
                                'message': f'[{cat_name}] Tiến độ: {len(cat_products)}/{len(cat_product_urls)} sản phẩm',
                                'detail': f'Thành công: {len(cat_products)}, Lỗi: {stats["failed"]}'
                            })
                            
            # Lưu Excel hoàn chỉnh cho danh mục
            if cat_products:
                create_excel_report(cat_products, cat_excel, sheet_name=cat_name[:30])
                all_products.extend(cat_products)

        # 4. Xuất file Excel tổng hợp
        if all_products:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            summary_excel = os.path.join(report_dir, f"TheGioiCapServo_Tong_hop_{timestamp}.xlsx")
            create_excel_report(all_products, summary_excel, sheet_name="Tong_Hop")
            
        # 5. Nén kết quả
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_file = os.path.join(result_dir, f"TheGioiCapServo_ket_qua_{timestamp}.zip")
        with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(result_dir):
                for f in files:
                    if not f.endswith('.zip') and not f.endswith('.json'):
                        f_path = os.path.join(root, f)
                        arcname = os.path.relpath(f_path, result_dir)
                        zipf.write(f_path, arcname)
                        
        duration = time.time() - start_time
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': f'Hoàn thành! Đã cào {len(all_products)} sản phẩm TheGioiCapServo.',
            'detail': f'Thời gian: {duration:.1f}s'
        })
        return all_products, result_dir
