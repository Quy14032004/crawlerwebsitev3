"""
LS Automation Product Crawler - Cào dữ liệu sản phẩm từ website ls-automation.com
Phiên bản: 1.0
Tác giả: Auto-generated based on CDHA and BAA crawler patterns
Ngày tạo: 2024
"""

import os
import shutil
import time
import zipfile
from datetime import datetime
from app.crawler import get_html_content
from app import socketio
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
import traceback
from queue import Queue
import logging

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def standardize_filename(value: str) -> str:
    """
    Chuẩn hóa chuỗi để dùng làm tên file.
    - Loại bỏ các ghi chú như (with special coating), addon kit...
    - Thay ký tự đặc biệt bằng '-'
    - Giữ lại a-z, A-Z, 0-9, dấu '-' và '_'
    """
    if not value:
        return ""

    clean = value.strip()
    clean = re.sub(r'\(with special coating\)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\[with special coating\]', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'add[\s\-]*on[\s\-]*kit', '', clean, flags=re.IGNORECASE)
    clean = clean.strip()

    clean = re.sub(r'[\\/:*?"<>|,=\s]+', '-', clean)
    clean = re.sub(r'-+', '-', clean)
    clean = re.sub(r'[^a-zA-Z0-9\-_]', '', clean)
    clean = clean.strip('-')
    return clean


def extract_best_image_url(img_element, base_url):
    """Lấy URL ảnh có độ phân giải cao nhất từ thẻ img (ưu tiên srcset/data-srcset)."""
    if not img_element:
        return None

    candidates = []

    srcset = img_element.get('srcset') or img_element.get('data-srcset')
    if srcset:
        for part in srcset.split(','):
            part = part.strip()
            if ' ' in part:
                url_part, size_part = part.rsplit(' ', 1)
                width = 0
                try:
                    if size_part.endswith('w'):
                        width = int(size_part[:-1])
                except Exception:
                    width = 0
                candidates.append((url_part.strip(), width))

    src = img_element.get('src') or img_element.get('data-src') or img_element.get('data-large_image')
    if src:
        candidates.append((src.strip(), 10**6))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    best_url = candidates[0][0]

    if best_url.startswith('/'):
        best_url = urljoin(base_url, best_url)

    if '?' in best_url:
        best_url = best_url.split('?')[0]

    return best_url

def get_lsa_category_name(url):
    """Lấy tên danh mục từ URL ls-automation.com"""
    html = get_html_content(url)
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        # Ưu tiên lấy từ breadcrumb
        breadcrumb = soup.select_one('.breadcrumb li.active, .breadcrumb-item.active, .breadcrumb li:last-child, .breadcrumb-item:last-child')
        if breadcrumb and breadcrumb.text.strip():
            return re.sub(r'[\\/:*?"<>|]', '_', breadcrumb.text.strip())
        # Hoặc lấy từ thẻ h1
        h1 = soup.select_one('h1, .category-title, .page-title')
        if h1 and h1.text.strip():
            return re.sub(r'[\\/:*?"<>|]', '_', h1.text.strip())
    # Nếu không lấy được thì fallback sang tên từ URL
    parsed = urlparse(url)
    path = parsed.path.strip('/').split('/')
    if path:
        last = path[-1]
        name = re.sub(r'_\d+$', '', last)
        return name
    return 'danh_muc'

def detect_lsa_pagination(url):
    """Phát hiện số trang tối đa cho danh mục ls-automation.com"""
    try:
        html = get_html_content(url)
        if not html:
            return 1
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm thành phần phân trang với selector cụ thể cho ls-automation.com
        pagination = soup.select_one('ul.page-numbers')
        
        if not pagination:
            print("⚠️  Không tìm thấy thành phần phân trang")
            return 1
        
        # Tìm số trang lớn nhất
        max_page = 1
        
        # Tìm tất cả các link phân trang
        page_links = pagination.select('a.page-numbers')
        
        for link in page_links:
            href = link.get('href', '')
            if href:
                # Tìm số trang trong URL như /page/2/
                match = re.search(r'/page/(\d+)/', href)
                if match:
                    try:
                        page_num = int(match.group(1))
                        max_page = max(max_page, page_num)
                    except ValueError:
                        pass
        
        # Kiểm tra số trang hiện tại
        current_page = pagination.select_one('span.page-numbers.current')
        if current_page:
            try:
                current_num = int(current_page.text.strip())
                max_page = max(max_page, current_num)
            except ValueError:
                pass
        
        result = max(max_page, 1)
        print(f"📄 Tổng số trang phát hiện: {result}")
        return result
        
    except Exception as e:
        print(f"❌ Lỗi khi phát hiện số trang: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

def make_lsa_pagination_url(base_url, page_number):
    """Tạo URL phân trang cho ls-automation.com"""
    if page_number <= 1:
        return base_url
        
    try:
        # Phân tích URL gốc
        parsed_url = urlparse(base_url)
        path = parsed_url.path
        
        # Kiểm tra xem URL đã có /page/ chưa
        if '/page/' in path:
            # Thay thế số trang hiện tại
            new_path = re.sub(r'/page/\d+/', f'/page/{page_number}/', path)
        else:
            # Thêm /page/X/ vào cuối path
            if path.endswith('/'):
                new_path = f"{path}page/{page_number}/"
            else:
                new_path = f"{path}/page/{page_number}/"
        
        # Tạo URL mới
        new_url = f"{parsed_url.scheme}://{parsed_url.netloc}{new_path}"
        
        return new_url
        
    except Exception as e:
        print(f"Lỗi khi tạo URL phân trang: {str(e)}")
        # Fallback: thử thêm /page/X/ vào cuối URL
        try:
            if base_url.endswith('/'):
                return f"{base_url}page/{page_number}/"
            else:
                return f"{base_url}/page/{page_number}/"
        except:
            return base_url

def is_lsa_category_url(url):
    """Kiểm tra xem URL có phải là danh mục của ls-automation.com không"""
    if not url or 'ls-automation.com' not in url:
        return False
    
    # Các pattern cho danh mục
    category_patterns = [
        r'/danh-muc-san-pham/',
        r'/category/',
    ]
    
    for pattern in category_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    
    return False

def is_lsa_product_url(url):
    """Kiểm tra xem URL có phải là sản phẩm của ls-automation.com không"""
    if not url or 'ls-automation.com' not in url:
        return False
    
    # Pattern cho sản phẩm
    product_patterns = [
        r'/san-pham/',
    ]
    
    for pattern in product_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    
    return False

def extract_lsa_product_urls(category_url):
    """Trích xuất danh sách URL sản phẩm từ danh mục ls-automation.com"""
    product_urls = []
    
    try:
        # Phát hiện số trang
        max_pages = detect_lsa_pagination(category_url)
        print(f"📁 Danh mục: {max_pages} trang")
        
        for page in range(1, max_pages + 1):
            page_url = make_lsa_pagination_url(category_url, page)
            print(f"🔍 Đang xử lý trang {page}/{max_pages}: {page_url}")
            
            html = get_html_content(page_url)
            if not html:
                print(f"❌ Không thể tải trang {page}")
                continue
                
            soup = BeautifulSoup(html, 'html.parser')
            
            # Tìm container chứa sản phẩm: <div id="brxe-963020" class="brxe-container brx-grid">
            product_container = soup.select_one('div.brxe-container.brx-grid#brxe-963020')
            if not product_container:
                # Fallback: tìm bất kỳ container brxe-container brx-grid nào
                product_container = soup.select_one('div.brxe-container.brx-grid')
            
            if not product_container:
                print(f"   ⚠️  Không tìm thấy container sản phẩm ở trang {page}")
                break
            
            # Tìm tất cả các link sản phẩm trong container
            # Các link có thể nằm trong:
            # - <a class="brxe-a07979 brxe-button bricks-button" href="...">
            # - <a class="brxe-2add96 brxe-image tag" href="...">
            # - <h3 class="brxe-1d2799 brxe-heading"><a href="...">
            
            found_links = []
            
            # Selector 1: Link từ button "Xem Thêm"
            button_links = product_container.select('a.brxe-a07979.brxe-button.bricks-button')
            for link in button_links:
                href = link.get('href')
                if href:
                    found_links.append(href)
            
            # Selector 2: Link từ image tag
            image_links = product_container.select('a.brxe-2add96.brxe-image.tag')
            for link in image_links:
                href = link.get('href')
                if href:
                    found_links.append(href)
            
            # Selector 3: Link từ heading
            heading_links = product_container.select('h3.brxe-1d2799.brxe-heading a')
            for link in heading_links:
                href = link.get('href')
                if href:
                    found_links.append(href)
            
            # Loại bỏ trùng lặp
            unique_links = []
            seen_hrefs = set()
            for link in found_links:
                if link and link not in seen_hrefs and '/san-pham/' in link:
                    seen_hrefs.add(link)
                    # Đảm bảo URL đầy đủ
                    if link.startswith('/'):
                        link = urljoin(page_url, link)
                    unique_links.append(link)
            
            print(f"   📊 Tìm thấy {len(unique_links)} links sản phẩm duy nhất")
            
            product_urls.extend(unique_links)
            
            # Nếu không tìm thấy sản phẩm nào, dừng lại
            if not unique_links:
                print(f"   ⚠️  Không tìm thấy sản phẩm nào ở trang {page}")
                break
                
    except Exception as e:
        print(f"❌ Lỗi danh mục: {str(e)}")
        import traceback
        traceback.print_exc()
    
    # Loại bỏ trùng lặp
    product_urls = list(set(product_urls))
    print(f"✅ Tìm thấy tổng cộng {len(product_urls)} sản phẩm")
    
    return product_urls

def extract_lsa_product_info(product_url):
    """Trích xuất thông tin sản phẩm từ ls-automation.com"""
    try:
        html = get_html_content(product_url)
        if not html:
            return None
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Tên sản phẩm: <h3 id="brxe-risnbl" class="brxe-heading">
        product_name = ""
        name_element = soup.select_one('h3.brxe-heading#brxe-risnbl')
        if not name_element:
            # Fallback: tìm bất kỳ h3.brxe-heading nào
            name_element = soup.select_one('h3.brxe-heading')
        if name_element:
            product_name = name_element.get_text(strip=True)
        
        # 2. Mã sản phẩm: từ <div class="motasanphamngan"> trong phần <p>Model (P/N): ...</p>
        product_code = ""
        code_container = soup.select_one('div.motasanphamngan')
        if code_container:
            # Tìm tất cả các thẻ p
            p_tags = code_container.select('p')
            for p in p_tags:
                text = p.get_text(strip=True)
                # Tìm pattern "Model (P/N): CODE"
                match = re.search(r'Model\s*\(P/N\):\s*(.+)', text, re.IGNORECASE)
                if match:
                    product_code = match.group(1).strip()
                    break
        
        # 3. Ảnh sản phẩm: ưu tiên đúng thẻ img chứa ảnh chính
        product_images = []
        image_selectors = [
            'div#brxe-rkwdel img#brxe-nhwyhk',
            'img#brxe-nhwyhk',
            'div#brxe-rkwdel img',
            'div.brxe-2add96 img',
            'a.brxe-2add96 img',
            'img.brxe-image.css-filter.size-full',
            'div.brxe-image img',
            'img[src*="wp-content/uploads"]'
        ]

        for selector in image_selectors:
            img_element = soup.select_one(selector)
            best_url = extract_best_image_url(img_element, product_url)
            if best_url:
                product_images = [best_url]
                break

        if not product_images:
            fallback_img = soup.find('img', attrs={'src': re.compile(r'wp-content/uploads', re.IGNORECASE)})
            best_url = extract_best_image_url(fallback_img, product_url)
            if best_url:
                product_images = [best_url]
        
        # 4. Thông số kỹ thuật: từ <div class="motasanpham"> trong bảng
        specifications = extract_lsa_specifications(soup)
        
        return {
            'url': product_url,
            'name': product_name,
            'code': product_code,
            'images': product_images,
            'specifications': specifications
        }
        
    except Exception as e:
        print(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def extract_lsa_specifications(soup):
    """Trích xuất thông số kỹ thuật từ bảng trong <div class="motasanpham">"""
    specifications = {}
    
    try:
        # Tìm container thông số kỹ thuật
        spec_container = soup.select_one('div.motasanpham')
        if not spec_container:
            return specifications
        
        # Tìm bảng thông số kỹ thuật
        spec_table = spec_container.select_one('table')
        if not spec_table:
            return specifications
        
        # Trích xuất từng hàng trong bảng
        rows = spec_table.select('tbody tr')
        if not rows:
            # Fallback: tìm tất cả tr trong bảng
            rows = spec_table.select('tr')
        
        for row in rows:
            # Lấy tất cả các td trong hàng
            tds = row.select('td')
            
            # Cấu trúc: <tr><td>Label</td><td>Value</td></tr>
            if len(tds) >= 2:
                label = tds[0].get_text(strip=True)
                value = tds[1].get_text(strip=True)
                if label and value:
                    specifications[label] = value
        
        # Thêm Copyright như yêu cầu
        specifications['Copyright'] = 'Haiphongtech.vn'
        
    except Exception as e:
        print(f"Lỗi khi trích xuất thông số kỹ thuật: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return specifications

def create_lsa_specifications_html_table(specifications, product_code="", product_name=""):
    """Tạo HTML table cho thông số kỹ thuật theo định dạng yêu cầu"""
    html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
    html += '<tbody>'
    
    # Thêm mã sản phẩm nếu có
    if product_code:
        html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product_code}</td></tr>'
    
    # Thêm tên sản phẩm nếu có
    if product_name:
        html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product_name}</td></tr>'
    
    # Thêm các thông số kỹ thuật
    for key, value in specifications.items():
        if key != 'Copyright':  # Bỏ qua Copyright ở đây, sẽ thêm ở cuối
            html += f'<tr><td style="font-weight: bold;">{key}</td><td>{value}</td></tr>'
    
    # Bắt buộc thêm Copyright ở cuối
    html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
    
    html += '</tbody></table>'
    return html

def download_lsa_product_images(product_info, output_folder):
    """Tải ảnh sản phẩm từ ls-automation.com và chuyển đổi sang WebP"""
    downloaded_images = []
    
    if not product_info or not product_info.get('images'):
        print(f"⚠️  Không có ảnh để tải cho sản phẩm: {product_info.get('name', 'Unknown')}")
        return downloaded_images
    
    os.makedirs(output_folder, exist_ok=True)
    
    # Import WebP converter
    from app.webp_converter import WebPConverter
    webp_converter = WebPConverter()
    
    print(f"🖼️  Bắt đầu tải {len(product_info['images'])} ảnh cho sản phẩm: {product_info.get('name', 'Unknown')}")
    
    for i, image_url in enumerate(product_info['images']):
        try:
            # Tạo tên file WebP dựa trên mã sản phẩm
            product_code = product_info.get('code', '').strip()
            filename_base = standardize_filename(product_code)

            if not filename_base:
                product_name = product_info.get('name', 'product')
                filename_base = standardize_filename(product_name) or "san_pham"

            suffix = "" if i == 0 else f"_{i+1}"
            filename = f"{filename_base}{suffix}.webp"
            
            filepath = os.path.join(output_folder, filename)
            
            # Kiểm tra xem file đã tồn tại chưa
            if os.path.exists(filepath):
                print(f"⏭️  Ảnh đã tồn tại: {filename}")
                downloaded_images.append(filepath)
                continue
            
            # Tải ảnh với retry logic
            max_retries = 3
            for retry in range(max_retries):
                try:
                    print(f"📥 Đang tải ảnh {i+1}/{len(product_info['images'])}: {image_url[:50]}...")
                    
                    # Thêm headers để tránh bị chặn
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                        'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
                        'Referer': product_info.get('url', ''),
                    }
                    
                    response = requests.get(image_url, timeout=30, headers=headers, stream=True)
                    response.raise_for_status()
                    
                    # Kiểm tra content type
                    content_type = response.headers.get('content-type', '').lower()
                    if not any(img_type in content_type for img_type in ['image/', 'jpeg', 'jpg', 'png', 'webp', 'gif']):
                        print(f"⚠️  Không phải ảnh: {content_type}")
                        break
                    
                    # Chuyển đổi bytes ảnh sang WebP trực tiếp (không cần lưu tạm)
                    webp_path = WebPConverter.convert_bytes_to_webp(response.content, filepath)
                    if webp_path and os.path.exists(webp_path):
                        downloaded_images.append(webp_path)
                        print(f"✅ Tải thành công: {filename}")
                        break
                    else:
                        print(f"❌ Chuyển đổi WebP thất bại: {filename}")
                        if retry < max_retries - 1:
                            print(f"🔄 Thử lại lần {retry + 2}...")
                            time.sleep(1)
                        
                except requests.exceptions.RequestException as e:
                    print(f"❌ Lỗi tải ảnh lần {retry + 1}: {str(e)[:50]}...")
                    if retry < max_retries - 1:
                        print(f"🔄 Thử lại lần {retry + 2}...")
                        time.sleep(2)
                    else:
                        print(f"❌ Thất bại hoàn toàn: {filename}")
                except Exception as e:
                    print(f"❌ Lỗi không xác định: {str(e)[:50]}...")
                    break
            
        except Exception as e:
            print(f"❌ Lỗi xử lý ảnh: {str(e)[:50]}...")
    
    print(f"📊 Kết quả tải ảnh: {len(downloaded_images)}/{len(product_info['images'])} thành công")
    return downloaded_images

def create_lsa_excel_report(products, output_file):
    """Tạo báo cáo Excel cho sản phẩm LSA"""
    try:
        # Chuẩn bị dữ liệu cho Excel
        excel_data = []
        
        for product in products:
            # Tạo HTML cho bảng thông số kỹ thuật
            spec_html = create_lsa_specifications_html_table(
                product['specifications'],
                product.get('code', ''),
                product.get('name', '')
            )
            
            excel_data.append({
                'STT': len(excel_data) + 1,
                'URL': product['url'],
                'Tên sản phẩm': product['name'],
                'Mã sản phẩm': product.get('code', ''),
                'Số lượng ảnh': len(product.get('images', [])),
                'Thông số kỹ thuật': spec_html,
                'Danh sách ảnh': '\n'.join(product.get('images', []))
            })
        
        # Tạo DataFrame và lưu Excel
        df = pd.DataFrame(excel_data)
        df.to_excel(output_file, index=False, engine='openpyxl')
        
        print(f"📊 Excel: {os.path.basename(output_file)}")
        return True
        
    except Exception as e:
        print(f"❌ Excel error: {str(e)[:50]}...")
        import traceback
        traceback.print_exc()
        return False

class LSACrawler:
    """Crawler chuyên dụng cho website ls-automation.com"""
    
    def __init__(self, output_root="output_lsa", max_workers=8, max_retries=3):
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        os.makedirs(self.output_root, exist_ok=True)

    def crawl_products(self, input_urls):
        """
        Nhận vào danh sách URL (danh mục hoặc sản phẩm), trả về tuple (list dict sản phẩm, đường dẫn folder kết quả)
        Cấu trúc tương tự BAA crawler
        """
        # Đo thời gian thực hiện
        start_time = time.time()
        
        # Thống kê hiệu suất
        stats = {
            "urls_processed": 0,
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "categories": 0,
            "single_products": 0,
            "failed_products": 0,
            "failed_images": 0
        }
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"LSA_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        all_products = []
        category_folders = []
        
        # Thông báo bắt đầu
        socketio.emit('progress_update', {
            'percent': 0, 
            'message': f'Bắt đầu xử lý {len(input_urls)} URL từ ls-automation.com',
            'detail': 'Đang phân tích và phân loại các URL...'
        })
        
        # 1. Phân loại URL và tạo cấu trúc thư mục
        category_map = {}  # Lưu ánh xạ tên danh mục -> danh sách URL
        single_products = []  # Lưu danh sách URL sản phẩm đơn lẻ
        
        for i, url in enumerate(input_urls):
            try:
                stats["urls_processed"] += 1
                if is_lsa_category_url(url):
                    cat_name = get_lsa_category_name(url)
                    if cat_name not in category_map:
                        category_map[cat_name] = []
                        stats["categories"] += 1
                    category_map[cat_name].append(url)
                    socketio.emit('progress_update', {
                        'percent': 2 * i // len(input_urls), 
                        'message': f'Đang phân tích URL {i+1}/{len(input_urls)}',
                        'detail': f'Đã xác định danh mục: {cat_name}'
                    })
                elif is_lsa_product_url(url):
                    single_products.append(url)
                    stats["single_products"] += 1
                    socketio.emit('progress_update', {
                        'percent': 2 * i // len(input_urls), 
                        'message': f'Đang phân tích URL {i+1}/{len(input_urls)}',
                        'detail': f'Đã xác định sản phẩm đơn lẻ'
                    })
            except Exception as e:
                print(f"Lỗi khi phân tích URL {url}: {str(e)}")
        
        # 2. Tạo các thư mục báo cáo
        report_dir = os.path.join(result_dir, "Bao_cao")
        os.makedirs(report_dir, exist_ok=True)
        
        total_categories = len(category_map)
        total_steps = total_categories + (1 if single_products else 0)
        
        # Tạo các thư mục danh mục trước
        for cat_name in category_map.keys():
            cat_folder = os.path.join(result_dir, cat_name)
            os.makedirs(cat_folder, exist_ok=True)
            os.makedirs(os.path.join(cat_folder, "Anh"), exist_ok=True)
            category_folders.append(cat_folder)
        
        # Nếu có sản phẩm đơn lẻ, tạo thư mục riêng
        if single_products:
            single_folder = os.path.join(result_dir, "san_pham_le")
            os.makedirs(single_folder, exist_ok=True)
            os.makedirs(os.path.join(single_folder, "Anh"), exist_ok=True)
            category_folders.append(single_folder)
        
        # 3. Xử lý từng danh mục với đa luồng
        for cat_idx, (cat_name, cat_urls) in enumerate(category_map.items()):
            current_step = cat_idx + 1
            step_progress_base = 5 + (current_step * 90 // max(1, total_steps))
            
            cat_folder = os.path.join(result_dir, cat_name)
            images_folder = os.path.join(cat_folder, "Anh")
            
            socketio.emit('progress_update', {
                'percent': step_progress_base, 
                'message': f'Đang xử lý danh mục [{current_step}/{total_steps}]: {cat_name}',
                'detail': f'Thu thập URL sản phẩm từ {len(cat_urls)} URL nguồn'
            })
            
            # Thu thập tất cả URL sản phẩm từ danh mục
            all_product_urls = []
            for cat_url in cat_urls:
                product_urls = extract_lsa_product_urls(cat_url)
                all_product_urls.extend(product_urls)
            
            # Loại bỏ trùng lặp
            all_product_urls = list(set(all_product_urls))
            stats["products_found"] += len(all_product_urls)
            
            socketio.emit('progress_update', {
                'percent': step_progress_base + 5, 
                'message': f'[{cat_name}] Đã thu thập {len(all_product_urls)} liên kết sản phẩm',
                'detail': f'Chuẩn bị trích xuất thông tin và tải ảnh sản phẩm'
            })
            
            # Lưu danh sách URL sản phẩm
            urls_file = os.path.join(cat_folder, f"{cat_name}_urls.txt")
            with open(urls_file, 'w', encoding='utf-8') as f:
                for url in all_product_urls:
                    f.write(f"{url}\n")
            
            if not all_product_urls:
                print(f"⚠️  Danh mục trống: {cat_name}")
                continue
            
            # Xử lý sản phẩm với đa luồng
            category_products = []
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_url = {
                    executor.submit(extract_lsa_product_info, url): url 
                    for url in all_product_urls
                }
                
                for i, future in enumerate(as_completed(future_to_url)):
                    url = future_to_url[future]
                    try:
                        product_info = future.result()
                        if product_info:
                            category_products.append(product_info)
                            stats["products_processed"] += 1
                            
                            # Tải ảnh sản phẩm
                            downloaded_images = download_lsa_product_images(product_info, images_folder)
                            stats["images_downloaded"] += len(downloaded_images)
                            
                            # Cập nhật progress
                            progress = step_progress_base + 10 + (i / len(all_product_urls)) * 50
                            socketio.emit('progress_update', {
                                'percent': int(progress),
                                'message': f'Đang xử lý danh mục: {cat_name}',
                                'detail': f'Đã xử lý {i+1}/{len(all_product_urls)} sản phẩm'
                            })
                        else:
                            stats["failed_products"] += 1
                    except Exception as e:
                        print(f"Lỗi khi xử lý sản phẩm {url}: {str(e)}")
                        stats["failed_products"] += 1
            
            # Tạo báo cáo Excel cho danh mục
            if category_products:
                excel_file = os.path.join(cat_folder, f"{cat_name}_Du_lieu.xlsx")
                create_lsa_excel_report(category_products, excel_file)
                print(f"✅ {cat_name}: {len(category_products)} sản phẩm")
                all_products.extend(category_products)
            
            socketio.emit('progress_update', {
                'percent': step_progress_base + 65, 
                'message': f'[{cat_name}] Đã hoàn thành xử lý danh mục',
                'detail': f'Đã xử lý {len(all_product_urls)} sản phẩm'
            })
        
        # Xử lý sản phẩm đơn lẻ nếu có
        if single_products:
            single_folder = os.path.join(result_dir, "san_pham_le")
            images_folder = os.path.join(single_folder, "Anh")
            
            socketio.emit('progress_update', {
                'percent': 90, 
                'message': f'Đang xử lý {len(single_products)} sản phẩm đơn lẻ',
                'detail': 'Trích xuất thông tin và tải ảnh...'
            })
            
            single_products_list = []
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_url = {
                    executor.submit(extract_lsa_product_info, url): url 
                    for url in single_products
                }
                
                for i, future in enumerate(as_completed(future_to_url)):
                    url = future_to_url[future]
                    try:
                        product_info = future.result()
                        if product_info:
                            single_products_list.append(product_info)
                            stats["products_processed"] += 1
                            
                            # Tải ảnh sản phẩm
                            downloaded_images = download_lsa_product_images(product_info, images_folder)
                            stats["images_downloaded"] += len(downloaded_images)
                    except Exception as e:
                        print(f"Lỗi khi xử lý sản phẩm {url}: {str(e)}")
                        stats["failed_products"] += 1
            
            if single_products_list:
                excel_file = os.path.join(single_folder, "san_pham_le_Du_lieu.xlsx")
                create_lsa_excel_report(single_products_list, excel_file)
                all_products.extend(single_products_list)
        
        # 5. Tạo báo cáo tổng hợp
        if all_products:
            # Tạo báo cáo tổng hợp
            summary_excel = os.path.join(result_dir, f"LSA_Tong_hop_{timestamp}.xlsx")
            create_lsa_excel_report(all_products, summary_excel)
            
            # Tạo file ZIP
            zip_file = os.path.join(result_dir, f"LSA_ket_qua_{timestamp}.zip")
            with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        if not file.endswith('.zip'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)
        
        # 6. Hoàn thành
        end_time = time.time()
        duration = end_time - start_time
        
        socketio.emit('progress_update', {
            'percent': 100,
            'message': f'Hoàn thành! Đã xử lý {len(all_products)} sản phẩm',
            'detail': f'Thời gian: {duration:.2f}s, Ảnh: {stats["images_downloaded"]}',
            'completed': True
        })
        
        # Log thống kê cuối cùng
        print(f"\n🎯 LSA HOÀN THÀNH: {stats['products_processed']} sản phẩm | {stats['images_downloaded']} ảnh | {duration:.1f}s")
        if stats['failed_products'] > 0:
            print(f"⚠️  Thất bại: {stats['failed_products']} sản phẩm")
        
        return all_products, result_dir

