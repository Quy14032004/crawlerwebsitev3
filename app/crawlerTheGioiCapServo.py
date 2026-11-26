"""
Thế Giới Cáp Servo Crawler - Cào dữ liệu sản phẩm từ website thegioicapservo.com
Tương tự logic cào dữ liệu Cơ Điện Hải Âu & cấu trúc tổ chức dữ liệu / xuất báo cáo của BAA.vn
"""

import os
import shutil
import time
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
import traceback
from queue import Queue
import logging
from PIL import Image
import io

from app import socketio

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Headers tối ưu để tránh bị chặn 403 từ Apache/WAF của thegioicapservo.com
HEADERS = {
    'User-Agent': 'curl/8.4.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'vi,en;q=0.9',
}

FALLBACK_USER_AGENTS = [
    'curl/8.4.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0'
]

def safe_socketio_emit(event: str, data: dict):
    """Emit Socket.IO một cách an toàn."""
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"Lỗi khi emit Socket.IO ({event}): {e}")

def get_page_html(url, session=None, max_retries=3):
    """Tải mã HTML của URL với cơ chế thử lại và xoay User-Agent."""
    for retry in range(max_retries):
        ua = FALLBACK_USER_AGENTS[retry % len(FALLBACK_USER_AGENTS)]
        headers = HEADERS.copy()
        headers['User-Agent'] = ua
        try:
            req = session if session else requests
            r = req.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                return r.text
            elif r.status_code == 403:
                time.sleep(1 + retry)
        except Exception as e:
            if retry == max_retries - 1:
                logger.error(f"Lỗi tải HTML {url}: {e}")
            time.sleep(1 + retry)
    return None

def is_thegioicapservo_category_url(url):
    """Kiểm tra xem URL có phải là danh mục của thegioicapservo.com không."""
    if not url or 'thegioicapservo.com' not in url:
        return False
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    if not path:
        return False
    # Nếu path có chứa / (ví dụ /cap-dong-luc-mitsubishi/MR-PWS1CBL1M-A1-L) -> sản phẩm
    # Danh mục thường là cấp 1, ví dụ: /cap-dong-luc-mitsubishi hoặc có query ?page=
    segments = [s for s in path.split('/') if s]
    if len(segments) == 1:
        return True
    return False

def is_thegioicapservo_product_url(url):
    """Kiểm tra xem URL có phải là sản phẩm của thegioicapservo.com không."""
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

    # Chuẩn hóa tên thư mục hợp lệ
    name = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name

def detect_pagination(category_url, session=None):
    """
    Phát hiện tổng số trang của danh mục trên thegioicapservo.com
    (OpenCart phân trang dạng ?page=X)
    """
    try:
        html = get_page_html(category_url, session=session)
        if not html:
            return 1
        
        soup = BeautifulSoup(html, 'html.parser')
        pagination = soup.select_one('.pagination, .paging, ul.pagination, .product-filter__pagination')
        if not pagination:
            return 1
        
        max_page = 1
        # Tìm tất cả thẻ a trong pagination
        for a in pagination.select('a[href]'):
            href = a['href']
            # Tìm ?page=(\d+)
            m = re.search(r'[?&]page=(\d+)', href)
            if m:
                page_num = int(m.group(1))
                if page_num > max_page:
                    max_page = page_num
            # Kiểm tra text là số
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
        # Thay thế hoặc bổ sung page
        if 'page=' in query:
            new_query = re.sub(r'page=\d+', f'page={page_number}', query)
        else:
            new_query = f"{query}&page={page_number}"
    else:
        new_query = f"page={page_number}"
    
    new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
    return new_url

def extract_thegioicapservo_product_urls(category_url, session=None):
    """
    Quét toàn bộ link sản phẩm trong danh mục qua container:
    <div class="product-listing__collection"> <div class="product_listing_main">
    """
    product_urls = []
    max_pages = detect_pagination(category_url, session=session)
    logger.info(f"Bắt đầu quét {max_pages} trang của danh mục: {category_url}")
    
    for page in range(1, max_pages + 1):
        page_url = make_pagination_url(category_url, page)
        html = get_page_html(page_url, session=session)
        if not html:
            continue
            
        soup = BeautifulSoup(html, 'html.parser')
        # Tìm sản phẩm trong container theo yêu cầu
        product_items = soup.select('.product-listing__collection .product')
        if not product_items:
            # Fallback nếu không có class product-listing__collection
            product_items = soup.select('.product_listing_main .product, .product')
            
        for item in product_items:
            # Ưu tiên link từ a.img_change, .product_name a, a.btn-info
            a = item.select_one('.product_img a.img_change, .product_name a, a.btn-info, a[href]')
            if a and a.get('href'):
                full_url = urljoin(category_url, a['href'])
                if full_url not in product_urls:
                    product_urls.append(full_url)
                    
        time.sleep(0.3)
        
    logger.info(f"Tổng số URL sản phẩm thu được từ {category_url}: {len(product_urls)}")
    return product_urls

def extract_thegioicapservo_product_info(product_url, session=None):
    """
    Trích xuất chi tiết sản phẩm:
    - Mã sản phẩm: từ .product_vendor a
    - Tên sản phẩm: từ .product_name
    - Giá sản phẩm: từ .product_price .money
    - Ảnh sản phẩm: link ảnh nét nhất 800x800
    - Thông số kỹ thuật: biến #tab-description thành table HTML chuẩn có Copyright
    """
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
            
        # 2. Mã sản phẩm: từ <div class="product_vendor">Dòng sản phẩm:: <a title="...">MÃ</a></div>
        product_code = ""
        vendor_el = soup.select_one('.product_vendor')
        if vendor_el:
            a_tag = vendor_el.select_one('a')
            if a_tag and a_tag.get_text(strip=True):
                product_code = a_tag.get_text(strip=True)
            else:
                raw_text = vendor_el.get_text(strip=True)
                # Bỏ tiền tố "Dòng sản phẩm::" hoặc "Dòng sản phẩm:"
                product_code = re.sub(r'^D[òo]ng\s*s[ảa]n\s*ph[ẩa]m\s*[:\s]+', '', raw_text, flags=re.IGNORECASE).strip()
                
        # Fallback mã sản phẩm từ URL nếu chưa có
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
        # Tìm ảnh đại diện, ảnh zoom, gallery
        raw_images = []
        for img in soup.select('#image, .product_image_main img, .product_img img, .thumbnails img, #default_gallery img, .image-additional img, .product-image img'):
            src = img.get('data-zoom-image') or img.get('data-large_image') or img.get('src')
            if src:
                src = urljoin(product_url, src)
                if src not in raw_images:
                    raw_images.append(src)
                    
        for a in soup.select('.thumbnails a, .image-additional a, a.thumbnail'):
            href = a.get('href')
            if href and ('/catalog/' in href or '/image/' in href):
                href = urljoin(product_url, href)
                if href not in raw_images:
                    raw_images.append(href)
                    
        # Check zoomWindow style background-image
        for el in soup.find_all(style=re.compile(r'background-image')):
            m = re.search(r'url\(["\']?([^"\')]+)["\']?\)', el['style'])
            if m:
                bg = urljoin(product_url, m.group(1))
                if bg not in raw_images:
                    raw_images.append(bg)
                    
        # Lọc ảnh rác và ưu tiên ảnh kích thước lớn 800x800
        valid_images = []
        for img_url in raw_images:
            lower = img_url.lower()
            if any(lower.endswith(ext) or ext in lower for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                if all(bad not in lower for bad in ['logo', 'icon', 'banner', 'placeholder', 'zalo', 'cart']):
                    valid_images.append(img_url)
                    
        # 5. Xử lý thông số kỹ thuật sang HTML table chuẩn
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
    """
    Chuyển đổi nội dung #tab-description thành table HTML chuẩn:
    <table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">
      <thead>
        <tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr>
      </thead>
      <tbody>
        <tr><td style="font-weight: bold;">Mã sản phẩm</td><td>...</td></tr>
        <tr><td style="font-weight: bold;">Tên sản phẩm</td><td>...</td></tr>
        ...
        <tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>
      </tbody>
    </table>
    """
    rows = []
    # 2 hàng đầu tiên luôn là Mã sản phẩm và Tên sản phẩm
    rows.append(('Mã sản phẩm', product_code or 'N/A'))
    rows.append(('Tên sản phẩm', product_name or 'N/A'))
    
    if desc_el:
        # Kiểm tra nếu trong tab-description có bảng table thật sự
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
            # Parse từ các thẻ p, li, div
            paragraphs = desc_el.find_all(['p', 'li', 'div', 'h3', 'h4'])
            prev_strong_header = None
            
            for p in paragraphs:
                p_text = p.get_text(strip=True)
                if not p_text:
                    continue
                    
                strong = p.select_one('strong, b')
                strong_text = strong.get_text(strip=True) if strong else ""
                
                # Trường hợp: <p><strong>Key:</strong> Value</p> hoặc <p><strong>Key</strong>: Value</p>
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
                        
                # Trường hợp có dấu hai chấm trong p_text: "Key: Value"
                if ':' in p_text and not p_text.startswith('http'):
                    parts = p_text.split(':', 1)
                    k = parts[0].strip()
                    v = parts[1].strip()
                    if k and v and len(k) < 60:
                        if (k, v) not in rows:
                            rows.append((k, v))
                        prev_strong_header = None
                        continue
                        
                # Trường hợp dòng này là strong header (ví dụ: "Chọn đúng chiều dài")
                if strong and len(strong_text) >= len(p_text) * 0.8 and len(strong_text) < 80:
                    prev_strong_header = strong_text
                    continue
                    
                # Trường hợp dòng tiếp theo là giá trị của strong header
                if prev_strong_header and p_text and len(p_text) > 3:
                    if (prev_strong_header, p_text) not in rows:
                        rows.append((prev_strong_header, p_text))
                    prev_strong_header = None
                    continue
    
    # Tạo HTML table theo mẫu của user
    html_table = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html_table += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
    html_table += '<tbody>'
    
    for key, val in rows:
        html_table += f'<tr><td style="font-weight: bold;">{key}</td><td>{val}</td></tr>'
        
    # !!! Lưu ý quan trọng từ người dùng: Bắt buộc thêm Copyright ở cuối bảng
    html_table += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
    html_table += '</tbody></table>'
    
    return html_table

def download_and_process_image(image_url, product_code, img_index, output_folder):
    """
    Tải ảnh, ép kích thước về 800x800 px và lưu thành định dạng .webp
    Đặt tên file theo mã sản phẩm: {code}.webp (hoặc {code}_{idx}.webp)
    Trả về tuple: (tên_file, đường_dẫn_tuyệt_đối)
    """
    clean_code = re.sub(r'[\\/:*?"<>|]', '_', product_code).strip()
    if not clean_code:
        clean_code = "product"
        
    if img_index == 0:
        filename = f"{clean_code}.webp"
    else:
        filename = f"{clean_code}_{img_index + 1}.webp"
        
    filepath = os.path.abspath(os.path.join(output_folder, filename))
    
    if os.path.exists(filepath):
        return filename, filepath
        
    for retry in range(3):
        try:
            r = requests.get(image_url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.content:
                # Mở ảnh bằng PIL và resize về 800x800
                image = Image.open(io.BytesIO(r.content))
                
                # Convert color mode
                if image.mode in ('RGBA', 'LA'):
                    # Tạo ảnh nền trắng vuông 800x800
                    bg = Image.new('RGB', (800, 800), (255, 255, 255))
                    image_resized = image.resize((800, 800), Image.Resampling.LANCZOS)
                    if image_resized.mode == 'RGBA':
                        bg.paste(image_resized, (0, 0), image_resized)
                        final_image = bg
                    else:
                        final_image = image_resized.convert('RGB')
                else:
                    final_image = image.convert('RGB').resize((800, 800), Image.Resampling.LANCZOS)
                    
                # Lưu sang định dạng WEBP
                final_image.save(filepath, 'WEBP', quality=92, method=6)
                return filename, filepath
        except Exception as e:
            time.sleep(1)
            
    return None, None

def create_excel_report(products, output_file, sheet_name="TheGioiCapServo"):
    """
    Tạo file Excel báo cáo:
    Bao gồm cột riêng 'Tên ảnh' sau khi đặt theo yêu cầu của user.
    """
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
                'Tên ảnh': image_names_str,             # Cột riêng biệt Tên ảnh theo yêu cầu
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
    Crawler chuyên dụng cho https://thegioicapservo.com/
    Hỗ trợ cào nhiều danh mục, lưu trữ chuẩn BAA.vn, xuất Excel có cột Tên ảnh, ảnh 800x800 WebP.
    """
    def __init__(self, output_root="output_thegioicapservo", max_workers=6, max_retries=3):
        self.output_root = os.path.abspath(output_root)
        self.max_workers = min(max(1, int(max_workers)), 12)
        self.max_retries = max_retries
        self.session = requests.Session()
        os.makedirs(self.output_root, exist_ok=True)
        
    def crawl_products(self, input_urls):
        start_time = time.time()
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
            'message': f'Bắt đầu phân tích {len(input_urls)} URL từ TheGioiCapServo...',
            'detail': 'Đang phân loại danh mục và sản phẩm...'
        })
        
        # 1. Phân loại URL
        category_map = {}
        single_products = []
        
        for url in input_urls:
            url = url.strip()
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
        
        # 2. Xử lý từng danh mục
        total_steps = len(category_map) + (1 if single_products else 0)
        current_step = 0
        
        def process_product_worker(p_url, img_folder):
            """Hàm worker xử lý cào 1 sản phẩm và tải ảnh"""
            info = extract_thegioicapservo_product_info(p_url, session=self.session)
            if not info:
                return None
            
            # Tải và xử lý ảnh sang 800x800 WebP
            downloaded_filenames = []
            downloaded_paths = []
            
            for idx, img_url in enumerate(info.get('images', [])):
                fname, fpath = download_and_process_image(img_url, info['code'], idx, img_folder)
                if fname and fpath:
                    downloaded_filenames.append(fname)
                    downloaded_paths.append(fpath)
                    
            info['image_filenames'] = downloaded_filenames
            info['image_paths'] = downloaded_paths
            return info
            
        for cat_name, cat_urls in category_map.items():
            current_step += 1
            cat_folder = os.path.join(result_dir, cat_name)
            img_folder = os.path.join(cat_folder, "Anh")
            os.makedirs(img_folder, exist_ok=True)
            
            safe_socketio_emit('progress_update', {
                'percent': int(10 + (current_step - 1) / max(1, total_steps) * 80),
                'message': f'Đang quét danh mục [{current_step}/{total_steps}]: {cat_name}',
                'detail': f'Thu thập link sản phẩm từ {len(cat_urls)} URL...'
            })
            
            # Quét tất cả link sản phẩm trong danh mục
            cat_product_urls = []
            for c_url in cat_urls:
                p_urls = extract_thegioicapservo_product_urls(c_url, session=self.session)
                cat_product_urls.extend(p_urls)
                
            cat_product_urls = list(dict.fromkeys(cat_product_urls))
            stats["products_found"] += len(cat_product_urls)
            
            # Lưu urls.txt
            urls_file = os.path.join(cat_folder, f"{cat_name}_urls.txt")
            with open(urls_file, 'w', encoding='utf-8') as f:
                for u in cat_product_urls:
                    f.write(f"{u}\n")
                    
            # Xử lý cào sản phẩm đa luồng
            cat_products = []
            if cat_product_urls:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {executor.submit(process_product_worker, u, img_folder): u for u in cat_product_urls}
                    done_count = 0
                    for future in as_completed(futures):
                        done_count += 1
                        res = future.result()
                        if res:
                            cat_products.append(res)
                            stats["products_processed"] += 1
                            stats["images_downloaded"] += len(res.get('image_filenames', []))
                        else:
                            stats["failed"] += 1
                            
                        # Cập nhật socket
                        if done_count % 3 == 0 or done_count == len(cat_product_urls):
                            cat_percent = int(10 + (current_step - 1 + done_count / len(cat_product_urls)) / max(1, total_steps) * 80)
                            safe_socketio_emit('progress_update', {
                                'percent': min(90, cat_percent),
                                'message': f'[{cat_name}] Đang cào sản phẩm: {done_count}/{len(cat_product_urls)}',
                                'detail': f'Đã xử lý: {len(cat_products)} thành công, {stats["images_downloaded"]} ảnh'
                            })
                            
            # Lưu Excel cho danh mục
            if cat_products:
                cat_excel = os.path.join(cat_folder, f"{cat_name}.xlsx")
                create_excel_report(cat_products, cat_excel, sheet_name=cat_name[:30])
                all_products.extend(cat_products)
                
        # 3. Xử lý sản phẩm đơn lẻ nếu có
        if single_products:
            current_step += 1
            single_folder = os.path.join(result_dir, "san_pham_le")
            single_img_folder = os.path.join(single_folder, "Anh")
            os.makedirs(single_img_folder, exist_ok=True)
            
            single_products = list(dict.fromkeys(single_products))
            stats["products_found"] += len(single_products)
            
            urls_file = os.path.join(single_folder, "san_pham_le_urls.txt")
            with open(urls_file, 'w', encoding='utf-8') as f:
                for u in single_products:
                    f.write(f"{u}\n")
                    
            single_prods_list = []
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(process_product_worker, u, single_img_folder): u for u in single_products}
                for future in as_completed(futures):
                    res = future.result()
                    if res:
                        single_prods_list.append(res)
                        stats["products_processed"] += 1
                        stats["images_downloaded"] += len(res.get('image_filenames', []))
                    else:
                        stats["failed"] += 1
                        
            if single_prods_list:
                single_excel = os.path.join(single_folder, "san_pham_le.xlsx")
                create_excel_report(single_prods_list, single_excel, sheet_name="SanPhamLe")
                all_products.extend(single_prods_list)
                
        # 4. Xuất file Excel tổng hợp
        if all_products:
            summary_excel = os.path.join(report_dir, f"TheGioiCapServo_Tong_hop_{timestamp}.xlsx")
            create_excel_report(all_products, summary_excel, sheet_name="Tong_Hop")
            
        # 5. Nén toàn bộ kết quả thành file ZIP
        safe_socketio_emit('progress_update', {
            'percent': 95,
            'message': 'Đang nén kết quả thành file ZIP...',
            'detail': f'Tổng cộng {len(all_products)} sản phẩm, {stats["images_downloaded"]} ảnh'
        })
        
        zip_file = os.path.join(result_dir, f"TheGioiCapServo_ket_qua_{timestamp}.zip")
        with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(result_dir):
                for f in files:
                    if not f.endswith('.zip'):
                        f_path = os.path.join(root, f)
                        arcname = os.path.relpath(f_path, result_dir)
                        zipf.write(f_path, arcname)
                        
        duration = time.time() - start_time
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': f'Hoàn thành! Đã cào {len(all_products)} sản phẩm TheGioiCapServo.',
            'detail': f'Thời gian: {duration:.1f}s • Ảnh: {stats["images_downloaded"]} WebP'
        })
        
        logger.info(f"Hoàn thành cào TheGioiCapServo: {len(all_products)} sản phẩm | {stats['images_downloaded']} ảnh | {duration:.1f}s")
        return all_products, result_dir
