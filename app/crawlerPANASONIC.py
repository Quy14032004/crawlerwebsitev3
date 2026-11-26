"""
Panasonic Industry Product Crawler - Cào dữ liệu sản phẩm từ website industry.panasonic.com
Phiên bản: 1.0
Tác giả: Auto-generated based on CDHA crawler patterns
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

# Thử import Selenium, nếu không có thì dùng fallback
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logger.warning("⚠️  Selenium không được cài đặt. Cần cài: pip install selenium")

# Thử import requests_html (nhẹ hơn Selenium)
try:
    # pyrefly: ignore [missing-import]
    from requests_html import HTMLSession
    REQUESTS_HTML_AVAILABLE = True
except ImportError:
    REQUESTS_HTML_AVAILABLE = False
    logger.warning("⚠️  requests-html không được cài đặt. Có thể cài: pip install requests-html")

# Thông tin mẫu để điền form catalog Panasonic
CATALOG_FORM_DETAILS = {
    'email': 'huonglinhxinh24@gmail.com',
    'purpose': 'For information gathering purposes',
    'firstname': 'Huong',
    'lastname': 'Linh',
    'company': 'ACB company',
    'department': 'Number5. stress 4',
    'country': 'Viet Nam',
    'state': 'HO CHI MINH',
    'phone': '0364997107'
}


def emit_progress_update(payload):
    """
    Gửi tiến trình qua SocketIO nếu khả dụng, nếu không thì in ra console.
    Giúp crawler không bị lỗi khi SocketIO không khởi tạo (chạy Flask thuần).
    """
    try:
        # socketio có thể là None nếu không khởi tạo được
        if 'socketio' in globals() and socketio is not None:
            socketio.emit('progress_update', payload)
        else:
            # Fallback: log ra console cho dễ debug
            percent = payload.get('percent')
            message = payload.get('message')
            detail = payload.get('detail', '')
            print(f"[progress_update] {percent}% - {message} | {detail}")
    except Exception as e:
        # Không để việc emit làm hỏng quá trình cào dữ liệu
        print(f"[progress_update] SocketIO error: {str(e)}")

def get_html_content_with_selenium(url, wait_time=15):
    """
    Lấy HTML từ URL bằng Selenium hoặc requests-html để render JavaScript
    Returns HTML string hoặc None nếu lỗi
    """
    # Thử dùng requests-html trước (nhẹ hơn)
    if REQUESTS_HTML_AVAILABLE:
        try:
            print(f"   🌐 Đang tải trang bằng requests-html (render JavaScript)...")
            session = HTMLSession()
            response = session.get(url, timeout=wait_time)
            # Render JavaScript
            response.html.render(timeout=wait_time, wait=2)
            html = response.html.html
            print(f"   ✅ Đã render xong bằng requests-html")
            return html
        except Exception as e:
            print(f"   ⚠️  Lỗi requests-html: {str(e)[:100]}")
    
    # Nếu không có requests-html hoặc lỗi, thử Selenium
    if SELENIUM_AVAILABLE:
        try:
            # Cấu hình Chrome headless
            chrome_options = Options()
            chrome_options.add_argument('--headless=new')  # Dùng headless mới
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            
            driver = None
            try:
                # Thử khởi tạo Chrome driver
                driver = webdriver.Chrome(options=chrome_options)
                driver.set_page_load_timeout(wait_time)
                
                print(f"   🌐 Đang tải trang bằng Selenium: {url[:60]}...")
                driver.get(url)
                
                # Đợi cho đến khi có phần tử search result xuất hiện
                try:
                    # Đợi tối đa wait_time giây cho phần tử xuất hiện
                    WebDriverWait(driver, wait_time).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '[data-ms-item-id], tbody, .mf_mps_search_result_table__table, td.mf_mps_cell'))
                    )
                    print(f"   ✅ Trang đã render xong")
                except TimeoutException:
                    print(f"   ⚠️  Timeout đợi phần tử render, nhưng vẫn lấy HTML")
                
                # Đợi thêm một chút để đảm bảo JavaScript chạy xong
                time.sleep(3)
                
                # Lấy HTML sau khi render
                html = driver.page_source
                return html
                
            finally:
                if driver:
                    driver.quit()
                    
        except WebDriverException as e:
            print(f"   ⚠️  Lỗi Selenium: {str(e)[:100]}")
        except Exception as e:
            print(f"   ⚠️  Lỗi khi dùng Selenium: {str(e)[:100]}")
    
    # Fallback: dùng get_html_content thông thường
    print("   ⚠️  Không có Selenium/requests-html, dùng get_html_content thông thường")
    return get_html_content(url)

def normalize_panasonic_url(url):
    """Chuẩn hóa URL Panasonic (thêm https:// nếu là //industry.panasonic.com)"""
    if not url:
        return url
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        # Cần base URL để join, nhưng hàm này chỉ normalize
        return url
    return url

def is_panasonic_category_url(url):
    """Kiểm tra xem URL có phải là danh mục của Panasonic không"""
    if not url or 'industry.panasonic.com' not in url:
        return False
    
    # Pattern cho danh mục: có /products/ trong URL
    if '/products/' in url.lower():
        # Loại trừ URL sản phẩm cụ thể (có /number/ hoặc là file)
        if '/number/' not in url.lower():
            return True
    
    return False

def is_panasonic_series_url(url):
    """Kiểm tra xem URL có phải là series sản phẩm không"""
    if not url or 'industry.panasonic.com' not in url:
        return False
    
    # Series URL thường có pattern: /products/.../.../series-name
    # Ví dụ: /products/motor/fa-motor/ac-servo/a7
    if '/products/' in url.lower() and '/number/' not in url.lower():
        # Có thể là series nếu không phải danh mục gốc
        return True
    
    return False

def is_panasonic_product_url(url):
    """Kiểm tra xem URL có phải là sản phẩm của Panasonic không"""
    if not url or 'industry.panasonic.com' not in url:
        return False
    
    # Pattern cho sản phẩm: có /number/ trong URL
    if '/number/' in url.lower():
        return True
    
    return False

def get_category_name(url):
    """Lấy tên danh mục từ URL Panasonic"""
    try:
        parsed = urlparse(url)
        path = parsed.path.strip('/').split('/')
        
        # Tìm phần products và lấy phần sau đó
        if 'products' in path:
            idx = path.index('products')
            if idx + 1 < len(path):
                # Lấy phần cuối cùng làm tên danh mục
                name = path[-1] if path[-1] else path[-2] if len(path) > 1 else 'danh_muc'
                return re.sub(r'[\\/:*?"<>|]', '_', name)
        
        return 'danh_muc'
    except Exception as e:
        logger.error(f"Lỗi khi lấy tên danh mục: {str(e)}")
        return 'danh_muc'

def extract_panasonic_series_urls(category_url):
    """Trích xuất danh sách URL series sản phẩm từ trang danh mục Panasonic"""
    series_urls = []
    
    try:
        print(f"🔍 Đang quét danh mục: {category_url}")
        html = get_html_content(category_url)
        if not html:
            print(f"❌ Không thể tải trang danh mục")
            return series_urls
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm các link series sản phẩm - thử nhiều selector
        selectors = [
            'a[href*="/products/"][href*="/motor/"]',  # Cho motor products
            'a[href*="/products/"]',  # Tất cả products links
            '.product-series a',
            '.series-link',
            'li.product-series a',
            '.product-list a',
            'div.product-item a',
            'ul.product-list li a',
            '.product-grid a'
        ]
        
        found_links = []
        for selector in selectors:
            links = soup.select(selector)
            if links:
                print(f"   ✅ Tìm thấy {len(links)} links với selector: {selector}")
                found_links.extend(links)
                # Không break để thu thập tất cả
        
        # Nếu không tìm thấy với selector cụ thể, thử tìm tất cả link có /products/
        if not found_links:
            all_links = soup.select('a[href*="/products/"]')
            found_links = all_links
            print(f"   ✅ Tìm thấy {len(found_links)} links tổng cộng")
        
        # Lọc và chuẩn hóa URL
        seen_urls = set()
        base_path = urlparse(category_url).path.rstrip('/')
        
        for link in found_links:
            href = link.get('href', '')
            if not href:
                continue
            
            # Chuẩn hóa URL
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/'):
                href = urljoin(category_url, href)
            
            # Kiểm tra xem có phải series URL không
            # Series URL thường có pattern: /products/.../.../series-name (không có /number/)
            if 'industry.panasonic.com' in href and '/products/' in href.lower():
                # Loại bỏ URL có /number/ (sản phẩm cụ thể)
                if '/number/' not in href.lower():
                    # Loại bỏ URL danh mục gốc (chỉ lấy series con)
                    parsed = urlparse(href)
                    href_path = parsed.path.rstrip('/')
                    
                    # Series phải là con của danh mục (có nhiều phần hơn)
                    if href_path != base_path and len(href_path.split('/')) > len(base_path.split('/')):
                        normalized = href.rstrip('/')
                        if normalized not in seen_urls:
                            seen_urls.add(normalized)
                            series_urls.append(normalized)
                            print(f"   ✅ Series: {normalized}")
        
        print(f"📊 Tìm thấy tổng cộng {len(series_urls)} series")
        
    except Exception as e:
        print(f"❌ Lỗi khi trích xuất series: {str(e)}")
        traceback.print_exc()
    
    return series_urls

def extract_panasonic_catalog_links(category_url):
    """Trích xuất danh sách link catalog từ trang danh mục Panasonic"""
    catalog_links = []
    try:
        print(f"🔍 Đang quét link catalog từ danh mục: {category_url}")
        html = get_html_content_with_selenium(category_url)
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm các link catalog: <a class="bl_productCard_footer_link" href="/global/en/downloads?tab=catalog&series_cd=3924">
        cards = soup.select('a.bl_productCard_footer_link[href*="tab=catalog"]')
        
        for card in cards:
            href = card.get('href', '')
            if href:
                full_url = urljoin(category_url, href)
                if full_url not in catalog_links:
                    catalog_links.append(full_url)
                    print(f"   ✅ Tìm thấy link catalog page: {full_url}")
        
        # Thử thêm selector khác nếu không thấy
        if not catalog_links:
            links = soup.select('a[href*="tab=catalog"]')
            for link in links:
                href = link.get('href', '')
                full_url = urljoin(category_url, href)
                if full_url not in catalog_links:
                    catalog_links.append(full_url)
                    print(f"   ✅ Tìm thấy link catalog page (fallback): {full_url}")
                    
        print(f"📊 Tìm thấy tổng cộng {len(catalog_links)} trang catalog")
    except Exception as e:
        print(f"❌ Lỗi khi trích xuất link catalog: {str(e)}")
    return catalog_links

def _wait_for_redirect(driver, timeout=40):
    """Đợi trang chuyển hướng khỏi trang loading"""
    start = time.time()
    while time.time() - start < timeout:
        url = driver.current_url
        if 'loading' not in url and len(url) > 30:
            return url
        time.sleep(1)
    return driver.current_url


def _get_pdf_link_from_confirmation_page(driver):
    """Lấy link PDF từ trang xác nhận 'Downloading now. Click here'"""
    try:
        # Tìm link có chứa /e_download/ hoặc .pdf
        all_a = driver.find_elements(By.TAG_NAME, 'a')
        for a in all_a:
            try:
                href = a.get_attribute('href') or ''
                text = a.text.strip().lower()
                if '/e_download/' in href or '.pdf' in href.lower():
                    return href
                if 'click here' in text and len(href) > 10:
                    return href
            except:
                continue
    except:
        pass
    return None


def _download_pdf_via_requests(pdf_url, output_folder, filename=None):
    """Tải file PDF trực tiếp qua requests"""
    try:
        if not pdf_url.startswith('http'):
            pdf_url = 'https://industry.panasonic.com' + pdf_url
        
        if not filename:
            # Lấy tên file từ URL (trước dấu ?)
            path_part = pdf_url.split('?')[0]
            filename = path_part.split('/')[-1]
            if not filename.lower().endswith('.pdf'):
                filename += '.pdf'
        
        file_path = os.path.join(output_folder, filename)
        if os.path.exists(file_path):
            print(f"   ⏭ File đã tồn tại: {filename}")
            return file_path
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            'Referer': 'https://industry.panasonic.com/'
        }
        resp = requests.get(pdf_url, headers=headers, timeout=60, stream=True)
        if resp.status_code == 200 and 'pdf' in resp.headers.get('content-type', '').lower():
            with open(file_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            size_kb = os.path.getsize(file_path) / 1024
            print(f"   ✅ Đã tải: {filename} ({size_kb:.1f} KB)")
            return file_path
        else:
            print(f"   ❌ Tải thất bại ({resp.status_code}): {pdf_url[:80]}")
    except Exception as e:
        print(f"   ❌ Lỗi tải PDF: {str(e)[:80]}")
    return None


def download_panasonic_catalog(download_page_url, output_folder):
    """
    Truy cập trang download Panasonic, lặp qua từng catalog:
    1. Nhấn nút dlc_product_button → mở form HubSpot
    2. Điền form và Submit
    3. Nhấn nút dl_auth_button → mở tab mới
    4. Đợi redirect từ /loading → /download_hubspot/index.jsp
    5. Lấy link PDF từ trang xác nhận và tải về
    """
    if not SELENIUM_AVAILABLE:
        print("❌ Selenium không khả dụng, không thể xử lý form download")
        return None

    os.makedirs(output_folder, exist_ok=True)
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-popup-blocking')
    chrome_options.add_argument('--disable-notifications')
    # Cho phép mở tab mới từ JavaScript
    chrome_options.add_experimental_option('prefs', {
        'profile.default_content_setting_values.popups': 1,  # 1 = cho phép popup
    })

    driver = None
    downloaded_files = []
    form_submitted = False  # Track nếu đã submit form 1 lần thì không cần submit lại

    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(40)

        print(f"🌐 Đang truy cập: {download_page_url}")
        driver.get(download_page_url)
        time.sleep(5)

        # Lấy tất cả nút dlc_product_button (mỗi catalog 1 nút)
        product_btns = driver.find_elements(By.CSS_SELECTOR, 'button.dlc_product_button')
        if not product_btns:
            product_btns = driver.find_elements(By.CSS_SELECTOR, 'button.rr_buttons_color_download')
        
        if not product_btns:
            print("⚠️ Không tìm thấy nút download nào trên trang")
            return None
        
        print(f"📦 Tìm thấy {len(product_btns)} catalog cần tải")
        main_window = driver.current_window_handle

        for idx in range(len(product_btns)):
            try:
                # Re-fetch nút vì DOM có thể thay đổi
                product_btns = driver.find_elements(By.CSS_SELECTOR, 'button.dlc_product_button')
                if not product_btns:
                    product_btns = driver.find_elements(By.CSS_SELECTOR, 'button.rr_buttons_color_download')
                
                if idx >= len(product_btns):
                    break

                btn = product_btns[idx]
                print(f"\n📄 Đang xử lý catalog {idx+1}/{len(product_btns)}...")

                # Bước 1: Nhấn nút mở form
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)

                # Bước 2: Điền và Submit form (chỉ lần đầu hoặc khi form xuất hiện lại)
                if not form_submitted:
                    try:
                        wait = WebDriverWait(driver, 15)
                        form = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'form[id^="hsForm_"]')))
                        if form:
                            print("   📝 Đang điền form HubSpot...")
                            
                            def fill(css, val, is_select=False):
                                try:
                                    el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, css)))
                                    if is_select:
                                        from selenium.webdriver.support.ui import Select
                                        s = Select(el)
                                        try: s.select_by_visible_text(val)
                                        except:
                                            try: s.select_by_value(val)
                                            except: s.select_by_index(1)
                                    else:
                                        el.clear(); el.send_keys(val)
                                except: pass

                            fill('input[name="email"], input[id^="email-"]', CATALOG_FORM_DETAILS['email'])
                            fill('select[name="purpose_of_download_gmsd"], select[id^="purpose_of_download_gmsd-"]', CATALOG_FORM_DETAILS['purpose'], True)
                            # Thử điền thêm firstname nếu có
                            fill('input[name="firstname"], input[id^="firstname-"]', CATALOG_FORM_DETAILS['firstname'])
                            fill('input[name="lastname"], input[id^="lastname-"]', CATALOG_FORM_DETAILS['lastname'])
                            fill('input[name="company"], input[id^="company-"]', CATALOG_FORM_DETAILS['company'])

                            # Submit
                            submit_btn = None
                            for sel in ['input.hs-button.primary', 'input[type="submit"]', 'button[type="submit"]']:
                                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                                for b in btns:
                                    if b.is_displayed():
                                        submit_btn = b; break
                                if submit_btn: break
                            
                            if submit_btn:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit_btn)
                                time.sleep(1)
                                driver.execute_script("arguments[0].click();", submit_btn)
                                print("   ✅ Đã submit form HubSpot")
                                time.sleep(5)
                                form_submitted = True
                    except Exception as e:
                        print(f"   ℹ️ Form không hiện hoặc lỗi điền form: {str(e)[:60]}")

                # Bước 3: Nhấn nút dl_auth_button để bắt đầu tải
                try:
                    wait_dl = WebDriverWait(driver, 15)
                    dl_btn = wait_dl.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'button.dl_auth_button, .dl_auth_button_area button')))
                    
                    print("   🚀 Đang nhấn nút Download (dl_auth_button)...")
                    original_handles = set(driver.window_handles)
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dl_btn)
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", dl_btn)
                    
                    # Bước 4: Đợi tab mới mở ra (tối đa 60 giây)
                    new_handle = None
                    for _ in range(60):
                        current_handles = set(driver.window_handles)
                        new_handles = current_handles - original_handles
                        if new_handles:
                            new_handle = new_handles.pop()
                            break
                        time.sleep(1)
                    
                    if new_handle:
                        driver.switch_to.window(new_handle)
                        print(f"   ↗️ Tab mới: {driver.current_url}")
                        
                        # Đợi redirect khỏi /loading
                        final_url = _wait_for_redirect(driver, timeout=60)
                        print(f"   ↗️ Trang xác nhận: {final_url}")
                        time.sleep(3)
                        
                        # Bước 5: Lấy link PDF từ trang xác nhận
                        pdf_link = _get_pdf_link_from_confirmation_page(driver)
                        if pdf_link:
                            print(f"   🔗 Link PDF: {pdf_link[:80]}")
                            saved = _download_pdf_via_requests(pdf_link, output_folder)
                            if saved:
                                downloaded_files.append(saved)
                        else:
                            print(f"   ⚠️ Không tìm thấy link PDF trên trang xác nhận (URL: {driver.current_url})")
                        
                        # Đóng tab xác nhận và quay lại tab chính
                        driver.close()
                        driver.switch_to.window(main_window)
                        time.sleep(2)
                    else:
                        # Fallback: tab không mở được - thử lấy link download từ JS của nút và điều hướng trực tiếp
                        print("   ⚠️ Không thấy tab mới, thử fallback điều hướng trực tiếp...")
                        try:
                            # Thử lấy URL download từ onclick attribute
                            onclick = dl_btn.get_attribute('onclick') or ''
                            href = dl_btn.get_attribute('data-href') or dl_btn.get_attribute('data-url') or ''
                            
                            # Thử extract URL từ JS
                            import re as _re
                            url_match = _re.search(r"(https?://[^\s'\"]+|/ac/e_download/[^\s'\"]+)", onclick + href)
                            if url_match:
                                direct_url = url_match.group(1)
                                print(f"   🔗 Tìm thấy URL trực tiếp: {direct_url[:80]}")
                                saved = _download_pdf_via_requests(direct_url, output_folder)
                                if saved:
                                    downloaded_files.append(saved)
                            else:
                                # Cuối cùng: navigate tab hiện tại sang trang xác nhận
                                print("   🔄 Thử navigate trực tiếp sang trang loading...")
                                driver.switch_to.window(main_window)
                        except Exception as fb_e:
                            print(f"   ❌ Fallback thất bại: {str(fb_e)[:80]}")
                        if main_window in driver.window_handles:
                            driver.switch_to.window(main_window)
                        
                except Exception as e:
                    print(f"   ❌ Lỗi bước Download: {str(e)[:100]}")
                    # Đảm bảo quay lại main window
                    try:
                        if main_window in driver.window_handles:
                            driver.switch_to.window(main_window)
                    except: pass

            except Exception as e:
                print(f"❌ Lỗi xử lý catalog {idx+1}: {str(e)[:100]}")
                try:
                    if main_window in driver.window_handles:
                        driver.switch_to.window(main_window)
                except: pass

        if downloaded_files:
            print(f"\n✅ Tổng cộng đã tải {len(downloaded_files)} file catalog từ trang này")
        else:
            print("⚠️ Không tải được file catalog nào từ trang này")
        
        return downloaded_files if downloaded_files else None

    except Exception as e:
        print(f"❌ Lỗi khi tải catalog: {str(e)}")
        traceback.print_exc()
    finally:
        if driver:
            try: driver.quit()
            except: pass
    return None


def find_driver_search_links(series_url):
    """
    Tìm cả Driver Search và Motor Search by Specifications trong trang series.
    Trả về danh sách link (ưu tiên Driver trước, Motor sau), đã loại trừ trùng.
    """
    try:
        print(f"🔍 Đang tìm Driver/Motor Search link trong: {series_url}")
        html = get_html_content(series_url)
        if not html:
            print(f"❌ Không thể tải trang series")
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        
        found_links = []
        
        def normalize_href(href):
            if href.startswith('//'):
                return 'https:' + href
            if href.startswith('/'):
                return urljoin(series_url, href)
            return href
        
        # Ưu tiên tìm trong li.el_product_btn
        product_buttons = soup.select('li.el_product_btn a')
        for link in product_buttons:
            link_text = (link.get_text(strip=True) or "").lower()
            href = link.get('href', '') or ''
            if not href:
                continue
            if ('driver search' in link_text or 'motor search' in link_text) and 'specification' in link_text:
                found_links.append(normalize_href(href))
        
        # Fallback: quét tất cả link
        if not found_links:
            for link in soup.select('a'):
                link_text = (link.get_text(strip=True) or "").lower()
                href = link.get('href', '') or ''
                if not href:
                    continue
                if ('driver search' in link_text or 'motor search' in link_text) and 'specification' in link_text:
                    found_links.append(normalize_href(href))
        
        if not found_links:
            print(f"   ⚠️  Không tìm thấy Driver/Motor Search link trong series: {series_url}")
            return []
        
        # Loại bỏ trùng lặp, ưu tiên driver trước motor
        dedup = []
        seen = set()
        # Sắp xếp để driver lên trước motor
        def weight(u):
            low = u.lower()
            if 'driver' in low:
                return 0
            if 'motor' in low:
                return 1
            return 2
        for href in sorted(found_links, key=weight):
            norm = href.rstrip('/')
            if norm not in seen:
                seen.add(norm)
                dedup.append(norm)
                print(f"   ✅ Search link: {norm}")
        return dedup
        
    except Exception as e:
        print(f"❌ Lỗi khi tìm Driver/Motor Search link: {str(e)}")
        traceback.print_exc()
        return []

def detect_pagination_panasonic(driver_search_url):
    """Phát hiện số trang tối đa trong trang Driver Search"""
    try:
        # Thử dùng Selenium để render JavaScript
        html = get_html_content_with_selenium(driver_search_url)
        if not html:
            return 1
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm phân trang: <ul class="mf_mps_pager_list">
        pagination = soup.select_one('ul.mf_mps_pager_list')
        if not pagination:
            print("   ⚠️  Không tìm thấy phân trang, kiểm tra trang có sản phẩm không...")
            # Kiểm tra xem có sản phẩm nào không
            tbody = soup.select_one('tbody')
            if tbody and tbody.select('a[href*="/number/"]'):
                return 1  # Có sản phẩm nhưng không có phân trang
            return 1
        
        # Tìm số trang lớn nhất từ các button
        max_page = 1
        
        # Tìm tất cả li có class mf_mps_pager_number
        page_items = pagination.select('li.mf_mps_pager_number')
        
        for item in page_items:
            # Lấy số trang từ data-ms-page-num
            page_num = item.get('data-ms-page-num')
            if page_num:
                try:
                    page_num = int(page_num)
                    max_page = max(max_page, page_num)
                except:
                    pass
            
            # Thử lấy từ button bên trong
            button = item.select_one('button')
            if button:
                text = button.get_text(strip=True)
                if text and text.isdigit():
                    try:
                        page_num = int(text)
                        max_page = max(max_page, page_num)
                    except:
                        pass
        
        # Nếu không tìm thấy, thử tìm trong toàn bộ pagination
        if max_page == 1:
            all_text = pagination.get_text()
            # Tìm số lớn nhất trong text
            numbers = re.findall(r'\d+', all_text)
            if numbers:
                try:
                    max_page = max([int(n) for n in numbers])
                except:
                    pass
        
        print(f"   📄 Phát hiện {max_page} trang")
        return max(max_page, 1)
        
    except Exception as e:
        print(f"❌ Lỗi khi phát hiện phân trang: {str(e)}")
        traceback.print_exc()
        return 1

def make_pagination_url_panasonic(base_url, page_number):
    """Tạo URL phân trang cho Panasonic Driver Search"""
    if page_number <= 1:
        return base_url
    
    try:
        # Thử thêm tham số page vào URL
        if '?' in base_url:
            return f"{base_url}&page={page_number}"
        else:
            return f"{base_url}?page={page_number}"
    except Exception as e:
        print(f"Lỗi khi tạo URL phân trang: {str(e)}")
        return base_url

def extract_panasonic_product_urls(search_url):
    """Trích xuất danh sách URL sản phẩm từ trang search (spec-search-motor hoặc spec-search-driver)"""
    product_urls = []
    
    try:
        # Chuẩn hóa URL
        if search_url.startswith('//'):
            search_url = 'https:' + search_url

        # Phát hiện số trang
        max_pages = detect_pagination_panasonic(search_url)
        print(f"📁 Search page: {max_pages} trang")
        
        for page in range(1, max_pages + 1):
            page_url = make_pagination_url_panasonic(search_url, page)
            print(f"🔍 Đang xử lý trang {page}/{max_pages}: {page_url}")
            
            # Thử dùng Selenium để render JavaScript trước
            html = get_html_content_with_selenium(page_url)
            if not html:
                print(f"❌ Không thể tải trang {page}")
                continue
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Vì trang render động bằng Vue, tìm link từ các phần tử có data-ms-item-id
            links = []
            
            # Cách 1: Tìm các phần tử có data-ms-item-id và lấy link từ đó
            items_with_id = soup.select('[data-ms-item-id]')
            if items_with_id:
                print(f"   ✅ Tìm thấy {len(items_with_id)} phần tử có data-ms-item-id")
                for item in items_with_id:
                    # Tìm link /number/ trong phần tử này hoặc trong parent
                    item_links = item.select('a[href*="/number/"]')
                    if not item_links:
                        # Thử tìm trong parent td hoặc div
                        parent = item.find_parent(['td', 'div', 'tr'])
                        if parent:
                            item_links = parent.select('a[href*="/number/"]')
                    if item_links:
                        links.extend(item_links)
                
                if links:
                    print(f"   ✅ Tìm thấy {len(links)} links từ các phần tử data-ms-item-id")
            
            # Cách 2: Tìm trong các td có class mf_mps_cell
            if not links:
                cells = soup.select('td.mf_mps_cell, td[class*="mf_mps_cell"]')
                if cells:
                    print(f"   ✅ Tìm thấy {len(cells)} cell(s) có class mf_mps_cell")
                    for cell in cells:
                        cell_links = cell.select('a[href*="/number/"]')
                        if cell_links:
                            links.extend(cell_links)
                    if links:
                        print(f"   ✅ Tìm thấy {len(links)} links từ các cell mf_mps_cell")
            
            # Cách 3: Tìm trong các tr có chứa td với class mf_mps_search_result_table
            if not links:
                rows = soup.select('tr')
                for row in rows:
                    # Kiểm tra xem row có chứa td với class mf_mps_search_result_table không
                    tds = row.select('td[class*="mf_mps_search_result_table"]')
                    if tds:
                        row_links = row.select('a[href*="/number/"]')
                        if row_links:
                            links.extend(row_links)
                if links:
                    print(f"   ✅ Tìm thấy {len(links)} links từ các row")
            
            # Cách 4: Tìm tbody trực tiếp (nếu có)
            if not links:
                tbody = soup.select_one('tbody')
                if tbody:
                    links = tbody.select('a[href*="/number/"]')
                    if links:
                        print(f"   ✅ Tìm thấy {len(links)} links trong tbody")
            
            # Cách 5: Tìm trong div có class mf_mps_search_result_table
            if not links:
                divs = soup.select('div[class*="mf_mps_search_result_table"]')
                for div in divs:
                    div_links = div.select('a[href*="/number/"]')
                    if div_links:
                        links.extend(div_links)
                if links:
                    print(f"   ✅ Tìm thấy {len(links)} links trong div mf_mps_search_result_table")
            
            # Cách 6: Tìm trong toàn bộ trang (fallback cuối cùng)
            if not links:
                all_number_links = soup.select('a[href*="/number/"]')
                if all_number_links:
                    # Lọc chỉ lấy link từ các phần tử có liên quan đến search result
                    for link in all_number_links:
                        # Kiểm tra xem link có nằm trong phần tử có class mf_mps không
                        parent = link.find_parent(['div', 'td', 'tr', 'table'])
                        if parent and ('mf_mps' in ' '.join(parent.get('class', [])) or 
                                      parent.select_one('[data-ms-item-id]')):
                            links.append(link)
                    if links:
                        print(f"   ✅ Tìm thấy {len(links)} links trong toàn bộ trang (đã lọc)")
            
            # Cách 7: Nếu vẫn không tìm thấy, dùng regex để tìm link trong HTML raw
            # (Vì Vue comment có thể làm BeautifulSoup không parse được)
            if not links:
                print(f"   ⚠️  Không tìm thấy link bằng BeautifulSoup, thử dùng regex trong HTML raw...")
                
                # Tìm tất cả link /number/ trong HTML raw (kể cả trong Vue comment)
                # Pattern 1: href="..." hoặc href='...' với /number/ bên trong
                pattern1 = r'href=["\']([^"\']*?/number/[^"\']*?)["\']'
                # Pattern 2: Tìm trong các phần tử có data-ms-item-id và href gần đó
                pattern2 = r'data-ms-item-id=["\']([^"\']+)["\'][^>]*>.*?href=["\']([^"\']*?/number/[^"\']*?)["\']'
                # Pattern 3: Tìm trong tbody (kể cả có Vue comment)
                pattern3 = r'<tbody[^>]*>(.*?)</tbody>'
                
                all_matches = []
                # Thử pattern 1 trước
                matches1 = re.findall(pattern1, html, re.IGNORECASE | re.DOTALL)
                all_matches.extend(matches1)
                
                # Thử pattern 2 (tìm trong phần tử có data-ms-item-id)
                matches2 = re.findall(pattern2, html, re.IGNORECASE | re.DOTALL)
                for match in matches2:
                    if len(match) == 2:
                        all_matches.append(match[1])  # Lấy href
                
                # Thử pattern 3 (tìm trong tbody)
                tbody_matches = re.findall(pattern3, html, re.IGNORECASE | re.DOTALL)
                for tbody_content in tbody_matches:
                    tbody_links = re.findall(pattern1, tbody_content, re.IGNORECASE | re.DOTALL)
                    all_matches.extend(tbody_links)
                
                if all_matches:
                    # Lọc và chuẩn hóa link
                    panasonic_links = []
                    seen = set()
                    for href in all_matches:
                        # Chuẩn hóa href
                        if href.startswith('//'):
                            href = 'https:' + href
                        elif href.startswith('/'):
                            href = 'https://industry.panasonic.com' + href
                        
                        # Chỉ lấy link từ domain panasonic và có /number/
                        if 'panasonic.com' in href.lower() and '/number/' in href.lower():
                            normalized = href.rstrip('/')
                            if normalized not in seen:
                                seen.add(normalized)
                                panasonic_links.append(normalized)
                    
                    if panasonic_links:
                        print(f"   ✅ Tìm thấy {len(panasonic_links)} links bằng regex trong HTML raw")
                        # Tạo BeautifulSoup objects từ các link này để xử lý đồng nhất
                        for href in panasonic_links:
                            link_elem = soup.new_tag('a', href=href)
                            links.append(link_elem)
            
            if not links:
                print(f"   ⚠️  Không tìm thấy link sản phẩm nào ở trang {page}")
                # Debug thông tin
                all_tables = soup.select('table')
                all_divs = soup.select('div[class*="mf_mps"]')
                all_tbodies = soup.select('tbody')
                items_with_id = soup.select('[data-ms-item-id]')
                print(f"   🔍 Debug: {len(all_tables)} table(s), {len(all_divs)} div(s) mf_mps, {len(all_tbodies)} tbody(s), {len(items_with_id)} items với data-ms-item-id")
                
                # Thử tìm bằng regex trong HTML raw với nhiều pattern
                all_patterns = [
                    r'href=["\']([^"\']*?/number/[^"\']*?)["\']',
                    r'href=([^"\'>\s]+/number/[^"\'>\s]+)',
                    r'//industry\.panasonic\.com[^"\'>\s]+/number/[^"\'>\s]+'
                ]
                for pattern in all_patterns:
                    regex_matches = re.findall(pattern, html, re.IGNORECASE)
                    if regex_matches:
                        print(f"   🔍 Debug regex ({pattern[:30]}...): Tìm thấy {len(regex_matches)} link /number/ trong HTML raw")
                        if len(regex_matches) > 0:
                            print(f"   ⚠️  Có link trong HTML nhưng BeautifulSoup không parse được (có thể do Vue comment)")
                            # Thử dùng các link này
                            panasonic_links = [m if m.startswith('http') else ('https://industry.panasonic.com' + m if m.startswith('/') else m) 
                                             for m in regex_matches[:20] if 'number' in m.lower()]
                            if panasonic_links:
                                print(f"   ✅ Thử dùng {len(panasonic_links)} links từ regex")
                                for href in panasonic_links:
                                    if not href.startswith('http'):
                                        if href.startswith('//'):
                                            href = 'https:' + href
                                        elif href.startswith('/'):
                                            href = 'https://industry.panasonic.com' + href
                                    link_elem = soup.new_tag('a', href=href)
                                    links.append(link_elem)
                                break
                
                if not links:
                    continue
            
            # Xử lý các link đã tìm được
            seen_urls = set()
            for link in links:
                href = link.get('href', '')
                if not href:
                    continue
                
                # Chuẩn hóa URL
                if href.startswith('//'):
                    href = 'https:' + href
                elif href.startswith('/'):
                    href = urljoin(page_url, href)
                
                # Chỉ lấy link có /number/ (sản phẩm) và từ domain panasonic
                if '/number/' in href.lower() and 'industry.panasonic.com' in href.lower():
                    normalized = href.rstrip('/')
                    if normalized not in seen_urls:
                        seen_urls.add(normalized)
                        product_urls.append(normalized)
                        print(f"   ✅ Sản phẩm: {normalized}")
            
            # Nếu không tìm thấy sản phẩm nào, dừng lại
            if not links:
                print(f"   ⚠️  Không tìm thấy sản phẩm nào ở trang {page}, dừng lại")
                # Chỉ dừng nếu đã thử nhiều cách
                if page == 1:
                    # Ở trang đầu, có thể trang render động - thử đợi một chút
                    print(f"   ⚠️  Trang có thể render động bằng JavaScript, cần Selenium hoặc đợi render")
                break
        
        print(f"📊 Tìm thấy tổng cộng {len(product_urls)} sản phẩm")
        
    except Exception as e:
        print(f"❌ Lỗi khi trích xuất sản phẩm: {str(e)}")
        traceback.print_exc()
    
    # Loại bỏ trùng lặp
    product_urls = list(set(product_urls))
    return product_urls

def extract_panasonic_product_info(product_url):
    """Trích xuất thông tin sản phẩm từ trang chi tiết Panasonic"""
    try:
        html = get_html_content(product_url)
        if not html:
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Mã sản phẩm (Part Number)
        product_code = ""
        # Selector: <tr> <th class="gray"> <p>Part Number</p> </th> <td><p>MDMF102L1C5</p> </td> </tr>
        # Tìm tất cả tr có th chứa "Part Number"
        rows = soup.select('tr')
        for row in rows:
            th = row.select_one('th')
            if th:
                th_text = th.get_text(strip=True)
                if 'part number' in th_text.lower():
                    td = row.select_one('td')
                    if td:
                        # Thử lấy từ thẻ p trước
                        p = td.select_one('p')
                        if p:
                            product_code = p.get_text(strip=True)
                        else:
                            # Nếu không có p, lấy trực tiếp từ td
                            product_code = td.get_text(strip=True)
                        if product_code:
                            break
        
        # Fallback: thử tìm trong URL (mã sản phẩm thường ở cuối URL)
        if not product_code:
            parsed = urlparse(product_url)
            path_parts = parsed.path.strip('/').split('/')
            if path_parts:
                last_part = path_parts[-1]
                # Kiểm tra xem có phải mã sản phẩm không (chữ và số, thường có độ dài 6-20 ký tự)
                if re.match(r'^[A-Z0-9]{6,20}$', last_part.upper()):
                    product_code = last_part.upper()
        
        # Fallback 2: tìm trong toàn bộ trang với pattern mã sản phẩm
        if not product_code:
            all_text = soup.get_text()
            # Tìm pattern mã sản phẩm Panasonic (thường bắt đầu bằng chữ cái và có số)
            matches = re.findall(r'\b[A-Z]{2,}[A-Z0-9]{4,}\b', all_text)
            if matches:
                # Lấy mã đầu tiên có vẻ hợp lý
                for match in matches:
                    if len(match) >= 6 and len(match) <= 20:
                        product_code = match
                        break
        
        # 2. Ảnh sản phẩm
        product_images = []
        # Selector: <figure class=""> <img src="..."> </figure>
        # Thử nhiều selector
        img_selectors = [
            'figure img',
            'figure img[src]',
            '.product-image img',
            '.product-img img',
            'img[src*="panasonic"]',
            'img[src*="thumbnail"]',
            'img[src*="product"]'
        ]
        
        for selector in img_selectors:
            imgs = soup.select(selector)
            for img in imgs:
                img_url = img.get('src', '') or img.get('data-src', '')
                if img_url:
                    # Chuẩn hóa URL
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    elif img_url.startswith('/'):
                        img_url = urljoin(product_url, img_url)
                    
                    # Loại bỏ query string nếu có
                    if '?' in img_url:
                        img_url = img_url.split('?')[0]
                    
                    # Chỉ lấy ảnh hợp lệ (có extension)
                    if any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                        # Loại bỏ placeholder và logo
                        if not any(bad in img_url.lower() for bad in ['placeholder', 'logo', 'icon', 'banner']):
                            product_images.append(img_url)
                            print(f"   ✅ Ảnh: {img_url}")
                            break  # Chỉ lấy ảnh đầu tiên hợp lệ
        
        return {
            'url': product_url,
            'code': product_code,
            'images': product_images
        }
        
    except Exception as e:
        print(f"❌ Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
        traceback.print_exc()
        return None

def download_panasonic_images(product_info, output_folder):
    """Tải ảnh sản phẩm từ Panasonic và chuyển đổi sang WebP"""
    downloaded_images = []
    
    if not product_info or not product_info.get('images'):
        print(f"⚠️  Không có ảnh để tải cho sản phẩm: {product_info.get('code', 'Unknown')}")
        return downloaded_images
    
    os.makedirs(output_folder, exist_ok=True)
    
    # Import WebP converter
    from app.webp_converter import WebPConverter
    webp_converter = WebPConverter()
    
    product_code = product_info.get('code', '').strip()
    if not product_code:
        print(f"⚠️  Không có mã sản phẩm để đặt tên file")
        return downloaded_images
    
    # Làm sạch mã sản phẩm
    clean_code = re.sub(r'[^\w\-_]', '_', product_code)
    
    print(f"🖼️  Bắt đầu tải ảnh cho sản phẩm: {product_code}")
    
    for i, image_url in enumerate(product_info['images']):
        try:
            # Tên file WebP = mã sản phẩm
            filename = f"{clean_code}.webp"
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
                    
                    # Headers để tránh bị chặn
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
                    
                    # Chuyển đổi bytes ảnh sang WebP trực tiếp
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

def create_panasonic_excel_report(products, output_file):
    """Tạo báo cáo Excel cho sản phẩm Panasonic"""
    try:
        excel_data = []
        
        for product in products:
            excel_data.append({
                'URL': product['url'],
                'Mã sản phẩm': product.get('code', ''),
                'Số lượng ảnh': len(product.get('images', [])),
                'Danh sách ảnh': '\n'.join(product.get('images', []))
            })
        
        # Tạo DataFrame và lưu Excel
        df = pd.DataFrame(excel_data)
        df.to_excel(output_file, index=False, engine='openpyxl')
        
        print(f"📊 Excel: {os.path.basename(output_file)}")
        return True
        
    except Exception as e:
        print(f"❌ Excel error: {str(e)[:50]}...")
        return False

class PanasonicCrawler:
    """Crawler chuyên dụng cho website industry.panasonic.com"""
    
    def __init__(self, output_root="output_panasonic", max_workers=8, max_retries=3):
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        os.makedirs(self.output_root, exist_ok=True)

    def crawl_products(self, input_urls):
        """
        Nhận vào danh sách URL search (spec-search-motor hoặc spec-search-driver), 
        trả về tuple (list dict sản phẩm, đường dẫn folder kết quả)
        """
        # Đo thời gian thực hiện
        start_time = time.time()
        
        # Thống kê hiệu suất
        stats = {
            "urls_processed": 0,
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "failed_products": 0,
            "failed_images": 0
        }
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"PANASONIC_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        all_products = []
        
        # Thông báo bắt đầu
        emit_progress_update({
            'percent': 0,
            'message': f'Bắt đầu xử lý {len(input_urls)} URL search từ Panasonic',
            'detail': 'Đang quét sản phẩm từ các trang search...'
        })
        
        # 1. Tạo thư mục báo cáo và images
        report_dir = os.path.join(result_dir, "Bao_cao")
        os.makedirs(report_dir, exist_ok=True)
        images_folder = os.path.join(result_dir, "images")
        os.makedirs(images_folder, exist_ok=True)
        
        # 2. Quét sản phẩm từ tất cả URL search
        all_product_urls = []
        
        for i, search_url in enumerate(input_urls):
            try:
                stats["urls_processed"] += 1
                print(f"\n🔍 Đang xử lý URL search {i+1}/{len(input_urls)}: {search_url}")
                
                emit_progress_update({
                    'percent': 5 + (i * 10 // len(input_urls)),
                    'message': f'Đang quét sản phẩm từ URL {i+1}/{len(input_urls)}',
                    'detail': f'URL: {search_url[:60]}...'
                })
                
                # Quét sản phẩm từ trang search (có xử lý phân trang)
                product_urls = extract_panasonic_product_urls(search_url)
                all_product_urls.extend(product_urls)
                
                print(f"✅ Tìm thấy {len(product_urls)} sản phẩm từ URL này")
                
            except Exception as e:
                print(f"❌ Lỗi khi xử lý URL {search_url}: {str(e)}")
                traceback.print_exc()
        
        # Loại bỏ trùng lặp
        all_product_urls = list(set(all_product_urls))
        stats["products_found"] = len(all_product_urls)
        
        print(f"\n📊 Tổng cộng tìm thấy {len(all_product_urls)} sản phẩm từ {len(input_urls)} URL search")
        
        if not all_product_urls:
            print("⚠️  Không tìm thấy sản phẩm nào!")
            emit_progress_update({
                'percent': 100,
                'message': 'Hoàn thành! Không tìm thấy sản phẩm nào',
                'detail': 'Vui lòng kiểm tra lại URL search'
            })
            return all_products, result_dir
        
        # 3. Xử lý sản phẩm với đa luồng
        emit_progress_update({
            'percent': 20,
            'message': f'Bắt đầu xử lý {len(all_product_urls)} sản phẩm',
            'detail': 'Đang trích xuất thông tin và tải ảnh...'
        })
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(extract_panasonic_product_info, url): url 
                for url in all_product_urls
            }
            
            for i, future in enumerate(as_completed(future_to_url)):
                url = future_to_url[future]
                try:
                    product_info = future.result()
                    if product_info:
                        all_products.append(product_info)
                        stats["products_processed"] += 1
                        
                        # Tải ảnh sản phẩm
                        downloaded_images = download_panasonic_images(product_info, images_folder)
                        stats["images_downloaded"] += len(downloaded_images)
                        
                        # Cập nhật progress
                        progress = 20 + (i / len(all_product_urls)) * 70
                        emit_progress_update({
                            'percent': int(progress),
                            'message': f'Đang xử lý sản phẩm {i+1}/{len(all_product_urls)}',
                            'detail': f'Mã: {product_info.get("code", "N/A")} | Ảnh: {len(downloaded_images)}'
                        })
                    else:
                        stats["failed_products"] += 1
                except Exception as e:
                    print(f"Lỗi khi xử lý sản phẩm {url}: {str(e)}")
                    stats["failed_products"] += 1
        
        # 4. Tạo báo cáo Excel
        if all_products:
            emit_progress_update({
                'percent': 95,
                'message': 'Đang tạo báo cáo Excel',
                'detail': f'Đã xử lý {len(all_products)} sản phẩm'
            })
            
            # Tạo báo cáo tổng hợp
            summary_excel = os.path.join(report_dir, f"PANASONIC_Tong_hop_{timestamp}.xlsx")
            create_panasonic_excel_report(all_products, summary_excel)
            
            # Tạo file ZIP
            zip_file = os.path.join(result_dir, f"PANASONIC_ket_qua_{timestamp}.zip")
            with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        if not file.endswith('.zip'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)
        
        # 5. Hoàn thành
        end_time = time.time()
        duration = end_time - start_time
        
        emit_progress_update({
            'percent': 100,
            'message': f'Hoàn thành! Đã xử lý {len(all_products)} sản phẩm',
            'detail': f'Thời gian: {duration:.2f}s, Ảnh: {stats["images_downloaded"]}'
        })
        
        # Log thống kê cuối cùng
        print(f"\n🎯 PANASONIC HOÀN THÀNH: {stats['products_processed']} sản phẩm | {stats['images_downloaded']} ảnh | {duration:.1f}s")
        if stats['failed_products'] > 0:
            print(f"⚠️  Thất bại: {stats['failed_products']} sản phẩm")
        
        return all_products, result_dir

    def crawl_catalogs(self, input_urls):
        """
        Cào catalog từ danh sách URL danh mục Panasonic
        """
        start_time = time.time()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"PANASONIC_CATALOG_{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        catalog_folder = os.path.join(result_dir, "catalogs")
        os.makedirs(catalog_folder, exist_ok=True)
        
        all_catalog_pages = []
        
        emit_progress_update({
            'percent': 5,
            'message': 'Bắt đầu quét link catalog từ danh mục',
            'detail': f'Đang xử lý {len(input_urls)} danh mục...'
        })
        
        # 1. Quét tất cả link catalog page từ các danh mục
        for url in input_urls:
            pages = extract_panasonic_catalog_links(url)
            all_catalog_pages.extend(pages)
        
        all_catalog_pages = list(set(all_catalog_pages))
        print(f"📊 Tổng cộng có {len(all_catalog_pages)} trang catalog cần xử lý")
        
        if not all_catalog_pages:
            emit_progress_update({'percent': 100, 'message': 'Không tìm thấy catalog nào', 'detail': ''})
            return [], result_dir
            
        # 2. Tải catalog từ từng trang (Xử lý tuần tự vì liên quan đến Selenium và download file)
        downloaded_count = 0
        for i, page_url in enumerate(all_catalog_pages):
            emit_progress_update({
                'percent': 10 + int((i / len(all_catalog_pages)) * 80),
                'message': f'Đang tải catalog {i+1}/{len(all_catalog_pages)}',
                'detail': f'Trang: {page_url[:50]}...'
            })
            
            files = download_panasonic_catalog(page_url, catalog_folder)
            if files:
                downloaded_count += len(files)
        
        # 3. Hoàn thành
        duration = time.time() - start_time
        emit_progress_update({
            'percent': 100,
            'message': f'Hoàn thành! Đã tải {downloaded_count} file catalog',
            'detail': f'Thời gian: {duration:.1f}s'
        })
        
        # Tạo file ZIP kết quả
        zip_file = os.path.join(result_dir, f"PANASONIC_CATALOG_{timestamp}.zip")
        with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(result_dir):
                for file in files:
                    if not file.endswith('.zip'):
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, result_dir)
                        zipf.write(file_path, arcname)
                        
        print(f"🎯 HOÀN THÀNH: Tải {downloaded_count} file catalog trong {duration:.1f}s")
        return [], result_dir


def test_single_product():
    """Test với một sản phẩm đơn lẻ để debug"""
    print("🧪 Test với một sản phẩm đơn lẻ...")
    
    test_url = "https://industry.panasonic.com/ap/en/products/motor/fa-motor/ac-servo/number/mdmf102l1c5"
    
    print(f"🔗 Test URL: {test_url}")
    
    product_info = extract_panasonic_product_info(test_url)
    
    if product_info:
        print(f"\n✅ THÔNG TIN SẢN PHẨM:")
        print(f"   • Mã: {product_info.get('code', 'N/A')}")
        print(f"   • Số ảnh: {len(product_info.get('images', []))}")
        print(f"   • URL: {product_info.get('url', 'N/A')}")
        
        if product_info.get('images'):
            print(f"\n🖼️  DANH SÁCH ẢNH:")
            for i, img_url in enumerate(product_info['images']):
                print(f"   {i+1}. {img_url}")
    else:
        print(f"\n❌ Không thể lấy thông tin sản phẩm!")

def test_category():
    """Test lấy series từ danh mục"""
    print("🧪 Test lấy series từ danh mục...")
    
    category_url = "https://industry.panasonic.com/ap/en/products/motor/fa-motor/ac-servo"
    print(f"🔗 Danh mục: {category_url}")
    
    series_urls = extract_panasonic_series_urls(category_url)
    
    print(f"\n📊 KẾT QUẢ:")
    print(f"   • Tổng series: {len(series_urls)}")
    
    if series_urls:
        print(f"\n🔗 DANH SÁCH SERIES:")
        for i, url in enumerate(series_urls[:5]):
            print(f"   {i+1}. {url}")
        if len(series_urls) > 5:
            print(f"   ... và {len(series_urls) - 5} series khác")

def test_search_page():
    """Test lấy sản phẩm từ trang search"""
    print("🧪 Test lấy sản phẩm từ trang search...")
    
    search_url = "https://industry.panasonic.com/ap/en/products/motor/fa-motor/ac-servo/spec-search-motor"
    print(f"🔗 Search URL: {search_url}")
    
    # Lấy HTML và kiểm tra - thử Selenium trước
    print("\n1️⃣ Thử với Selenium (render JavaScript)...")
    html = get_html_content_with_selenium(search_url)
    if not html:
        print("   ⚠️  Selenium không khả dụng, thử get_html_content thông thường...")
        html = get_html_content(search_url)
    
    if html:
        print(f"\n📄 HTML length: {len(html)} bytes")
        
        # Kiểm tra các pattern
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm các phần tử quan trọng
        items_with_id = soup.select('[data-ms-item-id]')
        print(f"   • Phần tử có data-ms-item-id: {len(items_with_id)}")
        
        cells = soup.select('td.mf_mps_cell, td[class*="mf_mps_cell"]')
        print(f"   • Cell có class mf_mps_cell: {len(cells)}")
        
        all_number_links = soup.select('a[href*="/number/"]')
        print(f"   • Link có /number/: {len(all_number_links)}")
        
        # Tìm bằng regex
        pattern = r'href=["\']([^"\']*?/number/[^"\']*?)["\']'
        regex_matches = re.findall(pattern, html, re.IGNORECASE)
        print(f"   • Link /number/ bằng regex: {len(regex_matches)}")
        
        if regex_matches:
            print(f"\n🔗 MỘT SỐ LINK TÌM THẤY BẰNG REGEX:")
            for i, link in enumerate(regex_matches[:10]):
                print(f"   {i+1}. {link}")
        
        # Kiểm tra xem có Vue comment không
        if '<!---->' in html:
            print(f"\n⚠️  Phát hiện Vue comment <!----> trong HTML")
            # Tìm link trong phần có Vue comment
            # Vue comment thường bao quanh các phần tử render động
        
        # Kiểm tra script tags
        scripts = soup.select('script')
        vue_scripts = [s for s in scripts if 'vue' in str(s).lower() or 'v-for' in str(s).lower()]
        print(f"   • Script có Vue: {len(vue_scripts)}")
        
        # Thử extract sản phẩm
        print(f"\n🔍 Thử extract sản phẩm...")
        product_urls = extract_panasonic_product_urls(search_url)
        print(f"\n📊 KẾT QUẢ EXTRACT:")
        print(f"   • Tổng sản phẩm: {len(product_urls)}")
        
        if product_urls:
            print(f"\n🔗 MỘT SỐ LINK SẢN PHẨM:")
            for i, url in enumerate(product_urls[:10]):
                print(f"   {i+1}. {url}")
        else:
            print(f"\n❌ Không tìm thấy sản phẩm nào!")
            print(f"\n🔍 DEBUG THÊM:")
            # In một phần HTML để xem cấu trúc
            if 'mf_mps' in html.lower():
                # Tìm vị trí của mf_mps trong HTML
                idx = html.lower().find('mf_mps')
                if idx >= 0:
                    snippet = html[max(0, idx-200):min(len(html), idx+500)]
                    print(f"   HTML snippet quanh mf_mps:")
                    print(f"   {snippet[:300]}...")
    else:
        print("❌ Không thể tải HTML")

def test_catalog_crawling():
    """Test cào catalog từ danh mục"""
    print("🧪 Test cào catalog từ danh mục...")
    
    # URL danh mục mẫu từ yêu cầu của người dùng
    category_url = "https://industry.panasonic.com/global/en/products/fasys/sensor/fiber"
    print(f"🔗 Danh mục: {category_url}")
    
    crawler = PanasonicCrawler()
    crawler.crawl_catalogs([category_url])

if __name__ == "__main__":
    print("🚀 Bắt đầu test crawler Panasonic...")
    
    # Để test catalog, bỏ comment dòng dưới
    # test_catalog_crawling()
    
    # Test 1: Lấy series từ danh mục
    test_category()
    
    print("\n" + "="*50 + "\n")
    
    # Test 2: Test với một sản phẩm đơn lẻ
    test_single_product()

