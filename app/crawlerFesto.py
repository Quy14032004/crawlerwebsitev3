"""
Festo Product Crawler - Cào dữ liệu sản phẩm từ website festo.com/vn/vi
Phiên bản: 1.0
Tác giả: Antigravity
"""

import os
import shutil
import time
import zipfile
from datetime import datetime
from app.crawler import get_html_content
from app import socketio

def get_html_content_festo(url, session=None):
    """Request nhanh bằng requests/cloudscraper, trả về None ngay lập tức khi lỗi (tránh bị sleep 30s-90s)"""
    try:
        import cloudscraper
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
            'Referer': 'https://www.festo.com/'
        }
        if session:
            resp = session.get(url, headers=headers, timeout=15)
        else:
            scraper = cloudscraper.create_scraper()
            resp = scraper.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

thread_local = threading.local()
thread_drivers = []
drivers_lock = threading.Lock()

def init_selenium_driver():
    """Khởi tạo Selenium Chrome Driver với cấu hình tối ưu"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1920,1080')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    )
    
    driver = None
    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        try:
            driver = webdriver.Chrome(service=Service(), options=opts)
        except Exception as e2:
            print(f"❌ Khởi chạy Chrome Driver thất bại: {e2}")
            raise e2
    return driver

def get_thread_driver():
    """Lấy hoặc tạo driver của luồng hiện tại"""
    if not hasattr(thread_local, "driver") or thread_local.driver is None:
        driver = init_selenium_driver()
        thread_local.driver = driver
        with drivers_lock:
            thread_drivers.append(driver)
    return thread_local.driver

def close_thread_drivers():
    """Đóng tất cả driver đã mở bởi các luồng"""
    global thread_drivers
    with drivers_lock:
        for driver in thread_drivers:
            try:
                driver.quit()
            except Exception:
                pass
        thread_drivers.clear()
import requests
import traceback
from queue import Queue
import logging

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def safe_socketio_emit(event: str, data: dict):
    """Emit Socket.IO một cách an toàn."""
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"Lỗi khi emit Socket.IO ({event}): {e}")

def get_category_festo_name(url):
    """Lấy tên danh mục từ URL festo.com"""
    try:
        parsed = urlparse(url)
        path = parsed.path.strip('/').split('/')
        if path:
            # Lấy phần tử cuối cùng làm tên danh mục
            name = path[-1]
            # Loại bỏ prefix hoặc id nếu có
            name = re.sub(r'^id_', '', name)
            return name
    except Exception:
        pass
    return 'danh_muc_festo'

def is_festo_category_url(url):
    """Kiểm tra xem URL có phải là danh mục của festo.com không"""
    if not url or 'festo.com' not in url:
        return False
    return '/p/' in url

def is_festo_product_url(url):
    """Kiểm tra xem URL có phải là sản phẩm của festo.com không"""
    if not url or 'festo.com' not in url:
        return False
    return '/a/' in url

def extract_festo_products_from_category(category_url, session=None):
    """Trích xuất danh sách thông tin sản phẩm trực tiếp từ trang danh mục Festo (hỗ trợ phân trang)"""
    products = []
    print(f"🔍 Đang quét danh mục: {category_url}")
    
    current_page = 1
    max_page = 1
    
    while current_page <= max_page:
        page_url = category_url
        if current_page > 1:
            sep = '&' if '?' in category_url else '?'
            page_url = f"{category_url}{sep}page={current_page - 1}"
            
        print(f"⏳ [DEBUG] Đang tải trang {current_page}/{max_page}: {page_url}")
        html = get_html_content_festo(page_url, session=session)
        if not html:
            try:
                print("⚠️ [DEBUG] HTML trống hoặc bị lỗi, thử sử dụng Selenium...")
                html = get_html_with_selenium(page_url)
            except Exception as e:
                print(f"❌ [DEBUG] Lỗi Selenium: {e}")
                break

        if not html:
            break

        soup = BeautifulSoup(html, 'html.parser')
        
        # Ở trang đầu tiên, tìm tổng số trang
        if current_page == 1:
            max_page_elem = soup.select_one('.fwe-page-max')
            if max_page_elem:
                try:
                    max_page = int(max_page_elem.get_text(strip=True))
                    print(f"📄 Tìm thấy tổng cộng {max_page} trang.")
                except:
                    pass
        
        # Tìm tất cả phần tử sản phẩm
        articles = soup.select('.single-article--cNEBP, [data-testid="suggested-article"]')
        print(f"📊 Tìm thấy {len(articles)} thẻ sản phẩm trong trang {current_page}")
        
        if not articles:
            break
        
        for article in articles:
            try:
                # 1. URL chi tiết và Tên sản phẩm
                link_elem = article.select_one('.triad-article-link-wrapper--QmUzI a, a[href*="/a/"]')
                if not link_elem:
                    continue
                    
                href = link_elem.get('href', '')
                product_url = urljoin(category_url, href)
                
                # Tên sản phẩm
                name = ""
                name_elem = article.select_one('.triad-title--dMKPJ, [class*="triad-title"]')
                if name_elem:
                    name = name_elem.get_text(strip=True)
                else:
                    name = link_elem.get_text(strip=True)
                    
                # 2. Mã đặt hàng (Order Code)
                order_code = ""
                order_code_elem = article.select_one('.triad-order-code--wX7MQ, [class*="triad-order-code"]')
                if order_code_elem:
                    order_code = order_code_elem.get_text(strip=True)
                    
                # 3. Part number (Mã mặt hàng)
                part_number = ""
                part_number_elem = article.select_one('.triad-part-number--zBvK9, [class*="triad-part-number"]')
                if part_number_elem:
                    part_number = part_number_elem.get_text(strip=True)
                    
                # 4. Hình ảnh sản phẩm
                image_url = ""
                for img in article.select('img'):
                    src = img.get('src') or img.get('data-src') or img.get('srcset')
                    if src and not src.endswith('.svg'):
                        if ',' in src:
                            src = src.split(',')[0].strip().split(' ')[0]
                        image_url = src
                        break
                if not image_url:
                    img_elem = article.select_one('img')
                    if img_elem:
                        image_url = img_elem.get('src') or img_elem.get('data-src')
                
                if image_url and image_url.startswith('/'):
                    image_url = urljoin(category_url, image_url)
                
                # 5. Thông số kỹ thuật hiển thị
                specifications = {}
                info_items = article.select('.article-information-item--LQEs9, [class*="article-information-item"]')
                for item in info_items:
                    label_elem = item.select_one('.information-label--punt3, [class*="information-label"]')
                    val_elem = item.select_one('.information-text--X2tfg, [class*="information-text"]')
                    if label_elem and val_elem:
                        specifications[label_elem.get_text(strip=True)] = val_elem.get_text(strip=True)
                
                # Thêm thông tin bản quyền
                specifications['Copyright'] = 'haiphongtech.vn'
                
                # 6. Giá sản phẩm
                price = "Login to see price"
                price_elem = article.select_one('[data-testid="article-login-info"], .section-pricing-container--usQtq')
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    if price_text:
                        price = price_text
                
                product_info = {
                    'url': product_url,
                    'name': name,
                    'code': order_code if order_code else part_number,
                    'part_number': part_number,
                    'price': price,
                    'image_url': image_url,
                    'specifications': specifications
                }
                
                products.append(product_info)
                print(f"   ✅ Đã trích xuất: {name} - Code: {order_code} - Part: {part_number}")
                
            except Exception as e:
                print(f"❌ Lỗi khi trích xuất sản phẩm trong list: {e}")
                
        current_page += 1
        
    return products

def get_html_with_selenium(url, driver=None):
    """Khởi chạy Selenium Driver để tải trang SPA của Festo và trả về HTML"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    should_quit = False
    if driver is None:
        driver = init_selenium_driver()
        should_quit = True
        
    try:
        print(f"⏳ [DEBUG] Đang truy cập URL bằng Selenium: {url}")
        driver.get(url)
        wait = WebDriverWait(driver, 15)
        
        # 1. Chấp nhận cookie nếu xuất hiện banner
        try:
            print("⏳ [DEBUG] Đang kiểm tra và chấp nhận cookie...")
            cookie_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[self::button or self::span or self::div][contains(text(), 'Accept all cookies') or contains(text(), 'Đồng ý tất cả') or contains(text(), 'Accept cookies')]")))
            driver.execute_script("arguments[0].click();", cookie_btn)
            print("✅ [DEBUG] Đã chấp nhận cookie.")
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ [DEBUG] Không tìm thấy banner cookie hoặc lỗi khi click: {e}")
            
        # 2. Click vào tab "Dữ liệu kỹ thuật" và chờ bảng thông số (chỉ với trang sản phẩm '/a/')
        is_product_page = '/a/' in url
        if is_product_page:
            try:
                print("⏳ [DEBUG] Đang tìm tab Dữ liệu kỹ thuật...")
                tab_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'tab-navigation-item-button')][contains(., 'Dữ liệu kỹ thuật') or contains(., 'Technical Data') or contains(., 'Technical data')]")))
                print(f"✅ [DEBUG] Tìm thấy nút tab: {tab_btn.text}. Tiến hành click...")
                driver.execute_script("arguments[0].click();", tab_btn)
                time.sleep(2)
            except Exception as e:
                print(f"⚠️ [DEBUG] Không thể click tab bằng nút lớp tab-navigation-item-button, thử cách click dự phòng JS: {e}")
                try:
                    # Cách dự phòng click qua tìm kiếm các thẻ chứa nội dung
                    driver.execute_script("""
                        var elements = document.querySelectorAll('button[class*="tab-navigation-item-button"], button');
                        for(var i = 0; i < elements.length; i++) {
                            var text = elements[i].innerText || elements[i].textContent;
                            if(text && (text.trim() === 'Dữ liệu kỹ thuật' || text.trim() === 'Technical Data' || text.trim() === 'Technical data')) {
                                elements[i].click();
                                return true;
                            }
                        }
                        return false;
                    """)
                    time.sleep(2)
                except Exception as js_err:
                    print(f"⚠️ [DEBUG] Click dự phòng JS cũng thất bại: {js_err}")

            # 3. Chờ bảng thông số kỹ thuật (technical-data-table) hiển thị
            try:
                print("⏳ [DEBUG] Chờ bảng thông số hiển thị...")
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='technical-data-table'], ul[class*='technical-data-table']")))
                print("✅ [DEBUG] Đã tìm thấy bảng thông số kỹ thuật.")
            except Exception as e:
                print(f"⚠️ [DEBUG] Chờ bảng thông số thất bại: {e}")
        else:
            print("ℹ️ [DEBUG] Đây là trang danh mục, bỏ qua bước click tab thông số.")
            
        # Scroll xuống cuối trang để load ảnh lazy-load và đảm bảo React dựng đủ bảng
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        html = driver.page_source
        return html
    finally:
        if should_quit and driver:
            driver.quit()

def extract_festo_single_product_info(product_url, session=None, driver=None):
    """Trích xuất thông tin chi tiết từ trang sản phẩm Festo"""
    try:
        # Lấy HTML bằng requests
        html = get_html_content_festo(product_url, session=session)
        
        # Bắt buộc dùng Selenium nếu trang dùng React và chưa render bảng thông số (technical-data-table)
        if not html or 'technical-data-table' not in html:
            if driver is None:
                driver = get_thread_driver()
            html = get_html_with_selenium(product_url, driver=driver)
            
        if not html:
            return None
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Lấy tên sản phẩm
        name = ""
        name_elem = soup.select_one('h1, .product-detail-title, .title')
        if name_elem:
            name = name_elem.get_text(strip=True)
            
        # Lấy part number / order code từ URL hoặc từ trang
        part_number = ""
        parsed = urlparse(product_url)
        path_parts = parsed.path.strip('/').split('/')
        if path_parts:
            part_number = path_parts[-1] # vd: 537707
            
        # Các thông số specs
        specifications = {}
        
        # Cách 1: Khớp bộ chọn class động Modern.js dùng hậu tố băm ngẫu nhiên
        tech_data_containers = soup.select('[class*="technical-data-table"]')
        for tech_data_container in tech_data_containers:
            rows = tech_data_container.select('[class*="technical-data-table-row"], [class*="technical-data-row"], li')
            for row in rows:
                lbl_elem = row.select_one('[class*="technical-data-property"], [class*="technical-data-label"]')
                val_elem = row.select_one('[class*="technical-data-value"]')
                if lbl_elem and val_elem:
                    lbl = lbl_elem.get_text(separator=" ", strip=True)
                    val = val_elem.get_text(separator=" ", strip=True)
                    if lbl and val:
                        specifications[lbl] = val
                        
        # Cách 2: Tìm kiếm hàng tổng quát dự phòng
        if not specifications:
            for row in soup.select('[class*="technical-data-table-row"], [class*="technical-data-row"], li'):
                lbl_elem = row.select_one('[class*="property"], [class*="label"]')
                val_elem = row.select_one('[class*="value"]')
                if lbl_elem and val_elem:
                    lbl = lbl_elem.get_text(separator=" ", strip=True)
                    val = val_elem.get_text(separator=" ", strip=True)
                    if lbl and val:
                        specifications[lbl] = val
                        
        # Cách 3: Bảng HTML <table> truyền thống nếu có
        spec_table = soup.select_one('table, .specifications-table, .technical-data')
        if spec_table and not specifications:
            for row in spec_table.select('tr'):
                cols = row.select('td, th')
                if len(cols) >= 2:
                    lbl = cols[0].get_text(separator=" ", strip=True)
                    val = cols[1].get_text(separator=" ", strip=True)
                    if lbl and val:
                        specifications[lbl] = val
                        
        specifications['Copyright'] = 'haiphongtech.vn'
        
        # Hình ảnh
        image_url = ""
        # 1. Tìm trong gallery wrapper trước
        gallery_imgs = soup.select('[class*="gallery-image-wrapper"] img, [class*="gallery-image-preview-container"] img, img[class*="image--"]')
        
        candidates = []
        for img in gallery_imgs:
            src = img.get('src') or img.get('data-src') or img.get('srcset')
            if src and ('/media/catalog/' in src or '/media/' in src) and not src.endswith('.svg'):
                if ',' in src:
                    src = src.split(',')[0].strip().split(' ')[0]
                
                # Check if inside cloned slide
                is_cloned = False
                parent = img.parent
                while parent and parent.name != 'body':
                    classes = parent.get('class', [])
                    if any('slick-cloned' in c for c in classes):
                        is_cloned = True
                        break
                    parent = parent.parent
                
                # Check if active/current
                is_active = False
                parent = img.parent
                while parent and parent.name != 'body':
                    classes = parent.get('class', [])
                    if any('slick-active' in c or 'slick-current' in c for c in classes):
                        is_active = True
                        break
                    parent = parent.parent
                
                candidates.append({
                    'url': src,
                    'is_cloned': is_cloned,
                    'is_active': is_active
                })
        
        # Lọc danh sách ứng viên
        best_candidates = [c for c in candidates if c['is_active']]
        if not best_candidates:
            best_candidates = [c for c in candidates if not c['is_cloned']]
        if not best_candidates:
            best_candidates = candidates
            
        if best_candidates:
            image_url = best_candidates[0]['url']
                
        # 2. Dự phòng nếu không tìm thấy
        if not image_url:
            for img in soup.select('img[src*="catalog"], img[src*="media/catalog"]'):
                src = img.get('src') or img.get('data-src') or img.get('srcset')
                if src and not src.endswith('.svg'):
                    if ',' in src:
                        src = src.split(',')[0].strip().split(' ')[0]
                    image_url = src
                    break
                    
        # 3. Dự phòng cuối cùng (chấp nhận cả SVG hoặc bất kỳ ảnh catalog nào nếu không có lựa chọn khác)
        if not image_url:
            img_elem = soup.select_one('.product-image img, img[src*="catalog"]')
            if img_elem:
                image_url = img_elem.get('src') or img_elem.get('data-src')
                
        if image_url and image_url.startswith('/'):
            image_url = urljoin(product_url, image_url)
                
        return {
            'url': product_url,
            'name': name if name else f"Festo Product {part_number}",
            'code': part_number,
            'part_number': part_number,
            'price': 'Login to see price',
            'image_url': image_url,
            'specifications': specifications
        }
    except Exception as e:
        print(f"❌ Lỗi khi cào sản phẩm đơn lẻ {product_url}: {e}")
        return None

def download_festo_image(product_info, output_folder, driver=None):
    """Tải ảnh sản phẩm Festo và lưu thành dạng WebP"""
    image_url = product_info.get('image_url')
    if not image_url:
        return None
        
    os.makedirs(output_folder, exist_ok=True)
    
    from app.webp_converter import WebPConverter
    
    # Tạo tên file an toàn dựa trên mã đặt hàng (sử dụng regex phủ định để giữ lại chữ, số và dấu gạch ngang)
    code = product_info.get('code', 'product').strip()
    clean_code = re.sub(r'[^\w\-]', '_', code)
    if not clean_code.strip():
        clean_code = product_info.get('part_number', 'product')
        
    filename = f"{clean_code}.webp"
    filepath = os.path.join(output_folder, filename)
    
    if os.path.exists(filepath):
        return filepath
        
    try:
        # Tải ảnh qua Selenium để tránh WAF 403
        if driver is None:
            driver = get_thread_driver()
            
        if driver.current_url == "about:blank" or not driver.current_url:
            driver.get("https://www.festo.com")
            
        # Tạo link độ phân giải cao nếu có dạng kích thước (vd: _488x366 -> _1056x1024)
        high_res_url = re.sub(r'_\d+x\d+', '_1056x1024', image_url)
        
        # Script lấy base64 của ảnh trực tiếp từ trình duyệt (có cơ chế fallback nếu ảnh độ phân giải cao lỗi)
        script = """
        var callback = arguments[arguments.length - 1];
        var targetUrl = arguments[0];
        var fallbackUrl = arguments[1];
        
        function tryFetch(url, isFallback) {
            fetch(url)
              .then(response => {
                if (!response.ok) {
                    throw new Error("HTTP error " + response.status);
                }
                return response.arrayBuffer();
              })
              .then(buffer => {
                var binary = '';
                var bytes = new Uint8Array(buffer);
                for (var i = 0; i < bytes.byteLength; i++) {
                    binary += String.fromCharCode(bytes[i]);
                }
                callback(window.btoa(binary));
              })
              .catch(err => {
                if (!isFallback && fallbackUrl && fallbackUrl !== url) {
                    tryFetch(fallbackUrl, true);
                } else {
                    callback('error: ' + err);
                }
              });
        }
        tryFetch(targetUrl, false);
        """
        driver.set_script_timeout(30)
        base64_data = driver.execute_async_script(script, high_res_url, image_url)
        
        if base64_data and not base64_data.startswith('error'):
            import base64
            img_data = base64.b64decode(base64_data)
            webp_path = WebPConverter.convert_bytes_to_webp(img_data, filepath)
            if webp_path and os.path.exists(webp_path):
                return webp_path
        else:
            print(f"⚠️ Selenium fetch error: {base64_data}")
            
    except Exception as e:
        print(f"⚠️ Không thể tải ảnh từ {image_url} bằng Selenium: {e}")
        
    return None

def create_festo_excel_report(products, output_file):
    """Tạo báo cáo Excel cho sản phẩm Festo"""
    try:
        excel_data = []
        for product in products:
            # Tạo HTML table cho specs
            spec_html = ""
            if product.get('specifications') is not None:
                spec_html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
                spec_html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
                
                if product.get('code'):
                    spec_html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product["code"]}</td></tr>'
                if product.get('name'):
                    spec_html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product["name"]}</td></tr>'
                    
                for key, val in product['specifications'].items():
                    # Bỏ qua nếu là copyright để đảm bảo nó luôn nằm cuối
                    if key.lower() not in ['copyright', 'mã sản phẩm', 'tên sản phẩm']:
                        # Bỏ qua dòng Giá nếu là "Đăng nhập để xem giá"
                        if key.lower() == 'giá' and 'đăng nhập để xem giá' in str(val).lower():
                            continue
                        # Bỏ qua dòng Price nếu là "Login to see price"
                        if key.lower() == 'price' and 'login to see price' in str(val).lower():
                            continue
                            
                        spec_html += f'<tr><td style="font-weight: bold;">{key}</td><td>{val}</td></tr>'
                        
                # Bắt buộc dòng Copyright ở cuối
                spec_html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
                spec_html += '</tbody></table>'
                
            excel_data.append({
                'URL': product['url'],
                'Tên sản phẩm': product['name'],
                'Mã sản phẩm': product['code'],
                'Mã mặt hàng (Part Number)': product.get('part_number', ''),
                'Giá sản phẩm': product.get('price', 'Login to see price'),
                'Thông số kỹ thuật': spec_html,
                'Ảnh sản phẩm': product.get('image_path_relative', '')
            })
            
        df = pd.DataFrame(excel_data)
        df.to_excel(output_file, index=False, engine='openpyxl')
        return True
    except Exception as e:
        print(f"❌ Lỗi xuất Excel: {e}")
        return False

def process_festo_category_product(prod, images_folder, session=None):
    """Lấy thêm thông tin chi tiết và tải ảnh cho sản phẩm từ danh mục"""
    driver = None
    try:
        driver = get_thread_driver()
        detailed_info = extract_festo_single_product_info(prod['url'], session=session, driver=driver)
        if detailed_info and detailed_info.get('specifications'):
            # Gộp thông số chi tiết vào sản phẩm
            prod['specifications'].update(detailed_info['specifications'])
            if detailed_info.get('image_url'):
                prod['image_url'] = detailed_info['image_url']
    except Exception as e:
        print(f"⚠️ Không thể tải chi tiết sản phẩm {prod['url']}: {e}")
        
    img_path = download_festo_image(prod, images_folder, driver=driver)
    return img_path

class FestoCrawler:
    """Crawler chuyên dụng cào dữ liệu Festo Việt Nam"""
    
    def __init__(self, output_root="output_festo", max_workers=4):
        self.output_root = output_root
        self.max_workers = max_workers
        self.session = requests.Session()
        os.makedirs(self.output_root, exist_ok=True)
        
    def crawl_products(self, input_urls):
        try:
            start_time = time.time()
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = os.path.join(self.output_root, f"Festo_ngay{timestamp}")
            os.makedirs(result_dir, exist_ok=True)
            
            safe_socketio_emit('progress_update', {
                'percent': 5,
                'message': 'Bắt đầu cào dữ liệu Festo Việt Nam...',
                'detail': 'Đang phân loại URLs đầu vào...'
            })
            
            all_products = []
            
            # Phân loại danh mục và sản phẩm lẻ
            category_urls = []
            product_urls = []
            for url in input_urls:
                if is_festo_category_url(url):
                    category_urls.append(url)
                elif is_festo_product_url(url):
                    product_urls.append(url)
                else:
                    # Mặc định coi là danh mục nếu không rõ
                    category_urls.append(url)
                    
            # 1. Trích xuất thông tin sản phẩm từ các trang danh mục
            for cat_url in category_urls:
                cat_name = get_category_festo_name(cat_url)
                cat_folder = os.path.join(result_dir, cat_name)
                os.makedirs(cat_folder, exist_ok=True)
                images_folder = os.path.join(cat_folder, "images")
                os.makedirs(images_folder, exist_ok=True)
                
                safe_socketio_emit('progress_update', {
                    'percent': 15,
                    'message': f'Đang quét danh mục: {cat_name}',
                    'detail': 'Trích xuất sản phẩm từ list...'
                })
                
                cat_products = extract_festo_products_from_category(cat_url, session=self.session)
                
                if cat_products:
                    # Tải hình ảnh song song đa luồng
                    safe_socketio_emit('progress_update', {
                        'percent': 40,
                        'message': f'Đang tải hình ảnh sản phẩm cho danh mục: {cat_name}',
                        'detail': f'Tổng số: {len(cat_products)} sản phẩm'
                    })
                    
                    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        futures = {
                            executor.submit(process_festo_category_product, prod, images_folder, self.session): prod 
                            for prod in cat_products
                        }
                        
                        for i, future in enumerate(as_completed(futures)):
                            prod = futures[future]
                            img_path = future.result()
                            if img_path:
                                # Lưu đường dẫn tương đối để dễ hiển thị trong báo cáo
                                prod['image_path_relative'] = os.path.join("images", os.path.basename(img_path))
                            else:
                                prod['image_path_relative'] = ''
                                
                    # Tạo file Excel cho riêng danh mục
                    cat_excel = os.path.join(cat_folder, f"{cat_name}.xlsx")
                    create_festo_excel_report(cat_products, cat_excel)
                    all_products.extend(cat_products)
                    
            # 2. Trích xuất sản phẩm đơn lẻ
            if product_urls:
                single_folder = os.path.join(result_dir, "san_pham_le")
                os.makedirs(single_folder, exist_ok=True)
                images_folder = os.path.join(single_folder, "images")
                os.makedirs(images_folder, exist_ok=True)
                
                single_products = []
                safe_socketio_emit('progress_update', {
                    'percent': 70,
                    'message': 'Đang cào các sản phẩm lẻ...',
                    'detail': f'Số lượng: {len(product_urls)}'
                })
                
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {
                        executor.submit(extract_festo_single_product_info, url, session=self.session): url 
                        for url in product_urls
                    }
                    for future in as_completed(futures):
                        prod = future.result()
                        if prod:
                            # Tải ảnh
                            img_path = download_festo_image(prod, images_folder)
                            if img_path:
                                prod['image_path_relative'] = os.path.join("images", os.path.basename(img_path))
                            else:
                                prod['image_path_relative'] = ''
                            single_products.append(prod)
                            
                if single_products:
                    single_excel = os.path.join(single_folder, "san_pham_le.xlsx")
                    create_festo_excel_report(single_products, single_excel)
                    all_products.extend(single_products)
                    
            # 3. Tạo file tổng hợp Excel & nén ZIP kết quả
            if all_products:
                report_dir = os.path.join(result_dir, "Bao_cao")
                os.makedirs(report_dir, exist_ok=True)
                
                summary_excel = os.path.join(report_dir, f"Festo_Tong_hop_{timestamp}.xlsx")
                create_festo_excel_report(all_products, summary_excel)
                
                zip_file = os.path.join(result_dir, f"Festo_ket_qua_{timestamp}.zip")
                with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(result_dir):
                        for file in files:
                            if not file.endswith('.zip'):
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, result_dir)
                                zipf.write(file_path, arcname)
                                
            duration = time.time() - start_time
            safe_socketio_emit('progress_update', {
                'percent': 100,
                'message': f'Hoàn thành! Đã cào {len(all_products)} sản phẩm Festo.',
                'detail': f'Thời gian xử lý: {duration:.2f}s'
            })
            
            return all_products, result_dir
        finally:
            close_thread_drivers()
