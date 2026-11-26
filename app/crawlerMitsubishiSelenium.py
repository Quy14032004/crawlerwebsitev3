"""
Mitsubishi Electric Product Crawler với Selenium - Xử lý JavaScript
Phiên bản: 4.0 - Selenium-based
"""

import os
import shutil
import time
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
import logging
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# Import existing modules
try:
    from app import socketio
except ImportError:
    socketio = None

from app.webp_converter import WebPConverter

logger = logging.getLogger(__name__)

class MitsubishiCrawlerSelenium:
    """Mitsubishi Electric Crawler sử dụng Selenium để xử lý JavaScript"""
    
    def __init__(self, max_workers=6):
        self.max_workers = max_workers
        self.base_url = "https://emea.mitsubishielectric.com"
        self.driver = None
        
    def _init_driver(self):
        """Khởi tạo Chrome driver với webdriver-manager"""
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')  # Chạy ẩn
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            # Sử dụng webdriver-manager để tự động tải Chrome driver
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Ẩn automation indicators
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            self.driver.implicitly_wait(10)
            logger.info("✅ Chrome driver initialized with webdriver-manager")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Chrome driver: {str(e)}")
            return False
    
    def _close_driver(self):
        """Đóng driver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def crawl_category(self, category_url):
        """
        Cào dữ liệu từ danh mục sử dụng Selenium
        """
        try:
            logger.info(f"Bắt đầu cào dữ liệu từ danh mục: {category_url}")
            
            # Khởi tạo driver
            if not self._init_driver():
                return {
                    'success': False,
                    'message': 'Không thể khởi tạo Chrome driver'
                }
            
            # Tạo thư mục output
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder = f"Mitsubishi_{timestamp}"
            output_dir = os.path.join('app', 'downloads', 'output_mitsubishi', output_folder)
            images_dir = os.path.join(output_dir, 'images')
            
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(images_dir, exist_ok=True)
            
            # Lấy danh sách sản phẩm
            product_urls = self._get_all_product_urls(category_url)
            
            if not product_urls:
                logger.warning("Không tìm thấy sản phẩm nào")
                return {
                    'success': False,
                    'message': 'Không tìm thấy sản phẩm nào'
                }
            
            logger.info(f"Tìm thấy {len(product_urls)} sản phẩm")
            
            # Cào chi tiết sản phẩm
            products_data = []
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_url = {
                    executor.submit(self._extract_product_details, url, images_dir): url 
                    for url in product_urls
                }
                
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        product_data = future.result()
                        if product_data:
                            products_data.append(product_data)
                            logger.info(f"✅ Đã cào: {product_data.get('code', 'Unknown')}")
                    except Exception as e:
                        logger.error(f"❌ Lỗi khi cào {url}: {str(e)}")
            
            # Tạo Excel report
            if products_data:
                excel_path = self._create_excel_report(products_data, output_dir)
                
                return {
                    'success': True,
                    'total_products': len(products_data),
                    'output_folder': output_folder,
                    'excel_file': os.path.basename(excel_path),
                    'message': f'Hoàn thành! Cào được {len(products_data)} sản phẩm'
                }
            else:
                return {
                    'success': False,
                    'message': 'Không thu thập được sản phẩm nào'
                }
                
        except Exception as e:
            logger.error(f"Lỗi trong crawl_category: {str(e)}")
            return {
                'success': False,
                'message': f'Lỗi: {str(e)}'
            }
        finally:
            self._close_driver()
    
    def _get_all_product_urls(self, category_url):
        """Lấy tất cả URL sản phẩm từ danh mục"""
        try:
            logger.info(f"Đang tải trang: {category_url}")
            self.driver.get(category_url)
            
            # Đợi trang load
            time.sleep(3)
            
            # Tìm pagination để biết số trang
            max_pages = self._detect_pagination()
            logger.info(f"Phát hiện {max_pages} trang")
            
            all_product_urls = []
            
            # Cào từng trang
            for page in range(1, max_pages + 1):
                logger.info(f"Đang xử lý trang {page}/{max_pages}")
                
                if page == 1:
                    current_url = category_url
                else:
                    current_url = f"{category_url}?page={page}"
                
                self.driver.get(current_url)
                time.sleep(2)  # Đợi trang load
                
                # Tìm sản phẩm trong trang này
                page_products = self._extract_products_from_page()
                all_product_urls.extend(page_products)
                logger.info(f"Trang {page} có {len(page_products)} sản phẩm")
            
            # Loại bỏ duplicate
            unique_urls = list(set(all_product_urls))
            logger.info(f"Tổng cộng {len(unique_urls)} sản phẩm unique")
            
            return unique_urls
            
        except Exception as e:
            logger.error(f"Lỗi khi lấy product URLs: {str(e)}")
            return []
    
    def _detect_pagination(self):
        """Phát hiện số trang từ pagination"""
        try:
            # Tìm pagination element
            pagination = self.driver.find_element(By.CSS_SELECTOR, 'ul.Pagination_list__nT87c')
            
            # Tìm tất cả page links
            page_links = pagination.find_elements(By.CSS_SELECTOR, 'a.Pagination_pageLink__eW_ES')
            
            max_page = 1
            for link in page_links:
                try:
                    aria_label = link.get_attribute('aria-label')
                    if aria_label and 'Page' in aria_label:
                        # Extract số trang từ aria-label="Page X"
                        page_num = int(re.search(r'Page (\d+)', aria_label).group(1))
                        max_page = max(max_page, page_num)
                except:
                    continue
            
            return max_page
            
        except NoSuchElementException:
            logger.info("Không tìm thấy pagination")
            return 1
        except Exception as e:
            logger.error(f"Lỗi khi detect pagination: {str(e)}")
            return 1
    
    def _extract_products_from_page(self):
        """Trích xuất sản phẩm từ trang hiện tại"""
        try:
            product_urls = []
            
            # Đợi trang load hoàn toàn
            time.sleep(3)
            
            # Tìm tất cả product links với nhiều selector
            selectors = [
                'a.ProductItem_titleLink__HjeDG',
                'div.SearchResultItem_root__Q_IQM a[href*=".html"]',
                'div.ProductItem_root__roXdu a[href*=".html"]',
                'a[href*="/fa/products/"][href$=".html"]'
            ]
            
            for selector in selectors:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    logger.info(f"Selector '{selector}' tìm thấy {len(links)} links")
                    
                    for link in links:
                        try:
                            href = link.get_attribute('href')
                            if href and href.endswith('.html') and 'mr-' in href.lower():
                                if href not in product_urls:
                                    product_urls.append(href)
                                    logger.info(f"✓ Thêm product URL: {href}")
                        except:
                            continue
                except Exception as e:
                    logger.warning(f"Lỗi với selector '{selector}': {str(e)}")
                    continue
            
            # Tìm sản phẩm trong các tab khác (COMPATIBLE PRODUCTS, SERVICE PARTS)
            try:
                tab_contents = self.driver.find_elements(By.CSS_SELECTOR, 'div.Tabs_childWrapper__vpjRP')
                logger.info(f"Tìm thấy {len(tab_contents)} tab contents")
                
                for tab_content in tab_contents:
                    # Kiểm tra nếu tab không bị ẩn
                    classes = tab_content.get_attribute('class')
                    if 'Tabs_is-hidden__ZGvUT' not in classes:
                        logger.info("Tìm thấy tab không bị ẩn")
                        # Tìm sản phẩm trong tab này
                        tab_products = tab_content.find_elements(By.CSS_SELECTOR, 'a.ProductItem_titleLink__HjeDG')
                        logger.info(f"Tab có {len(tab_products)} sản phẩm")
                        
                        for link in tab_products:
                            try:
                                href = link.get_attribute('href')
                                if href and href.endswith('.html') and 'mr-' in href.lower():
                                    if href not in product_urls:
                                        product_urls.append(href)
                                        logger.info(f"✓ Thêm product URL từ tab: {href}")
                            except:
                                continue
            except Exception as e:
                logger.warning(f"Lỗi khi tìm sản phẩm trong tabs: {str(e)}")
            
            logger.info(f"Tổng cộng tìm thấy {len(product_urls)} sản phẩm")
            return product_urls
            
        except Exception as e:
            logger.error(f"Lỗi khi extract products từ trang: {str(e)}")
            return []
    
    def _extract_product_details(self, product_url, images_dir):
        """Trích xuất chi tiết sản phẩm"""
        try:
            logger.info(f"Đang cào sản phẩm: {product_url}")
            
            # Tạo driver mới cho mỗi thread
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.implicitly_wait(10)
            
            # Ẩn automation indicators
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                driver.get(product_url)
                time.sleep(3)  # Đợi trang load hoàn toàn
                
                # Lấy mã sản phẩm
                product_code = self._extract_product_code(driver)
                if not product_code:
                    logger.warning(f"Không tìm thấy mã sản phẩm trong {product_url}")
                    return None
                
                # Lấy tên sản phẩm
                product_name = self._extract_product_name(driver)
                
                # Lấy ảnh sản phẩm
                image_path = self._extract_and_download_image(driver, product_code, images_dir)
                
                # Lấy thông số kỹ thuật
                specs_html = self._extract_specifications(driver, product_code, product_name)
                
                return {
                    'code': product_code,
                    'name': product_name,
                    'image_path': image_path,
                    'specs_html': specs_html,
                    'url': product_url,
                    'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                
            finally:
                driver.quit()
                
        except Exception as e:
            logger.error(f"Lỗi khi extract product details từ {product_url}: {str(e)}")
            return None
    
    def _extract_product_code(self, driver):
        """Lấy mã sản phẩm"""
        try:
            # Tìm h2 tag chứa mã sản phẩm
            h2_elements = driver.find_elements(By.TAG_NAME, 'h2')
            for h2 in h2_elements:
                text = h2.text.strip()
                if 'MR-' in text and len(text) > 5:
                    return text
            
            # Fallback: extract từ URL
            current_url = driver.current_url
            filename = current_url.split('/')[-1]
            if filename.endswith('.html'):
                filename = filename[:-5]
            if filename and len(filename) > 3:
                return filename.upper()
            
            # Fallback: tìm trong title
            try:
                title = driver.find_element(By.TAG_NAME, 'title')
                title_text = title.get_attribute('textContent').strip()
                if 'MR-' in title_text:
                    # Extract MR-xxx từ title
                    import re
                    match = re.search(r'MR-[A-Z0-9\-]+', title_text)
                    if match:
                        return match.group(0)
            except:
                pass
            
            return None
        except:
            return None
    
    def _extract_product_name(self, driver):
        """Lấy tên sản phẩm"""
        try:
            # Tìm description div
            desc_element = driver.find_element(By.CSS_SELECTOR, 'div.ProductOverview_description__CHeZm')
            return desc_element.text.strip()
        except:
            # Fallback: lấy từ title hoặc h1
            try:
                title = driver.find_element(By.TAG_NAME, 'title')
                return title.get_attribute('textContent').strip()
            except:
                return "Unknown Product"
    
    def _extract_and_download_image(self, driver, product_code, images_dir):
        """Lấy và download ảnh sản phẩm"""
        try:
            # Tìm ảnh sản phẩm
            img_element = driver.find_element(By.CSS_SELECTOR, 'img.Gallery_slideImg__TqcGx')
            image_url = img_element.get_attribute('src')
            
            if not image_url:
                return None
            
            # Download và convert sang WebP
            webp_path = os.path.join(images_dir, f"{product_code}.webp")
            
            # Sử dụng WebPConverter
            converter = WebPConverter()
            success = converter.convert_to_webp(image_url, webp_path, quality=90)
            
            if success:
                return f"images/{product_code}.webp"
            else:
                return None
                
        except Exception as e:
            logger.error(f"Lỗi khi download ảnh: {str(e)}")
            return None
    
    def _extract_specifications(self, driver, product_code, product_name):
        """Lấy thông số kỹ thuật"""
        try:
            # Tìm tab TECHNICAL INFORMATION
            try:
                tech_tab = driver.find_element(By.CSS_SELECTOR, 'button.Nav_link__yt4y8.Tabs_navLink__SEkZx')
                tech_tab.click()
                time.sleep(2)  # Đợi tab load
            except NoSuchElementException:
                logger.warning("Không tìm thấy tab TECHNICAL INFORMATION")
                return self._create_specifications_table_html(product_code, product_name, [])
            
            # Tìm tất cả tables
            tables = driver.find_elements(By.CSS_SELECTOR, 'table.ProductDetail_charTable__m3dh8')
            
            if not tables:
                logger.warning("Không tìm thấy bảng thông số kỹ thuật")
                return self._create_specifications_table_html(product_code, product_name, [])
            
            # Parse tables
            all_specs = []
            for table in tables:
                specs = self._parse_specs_table(table)
                all_specs.extend(specs)
            
            logger.info(f"Tìm thấy {len(all_specs)} thông số kỹ thuật")
            return self._create_specifications_table_html(product_code, product_name, all_specs)
            
        except Exception as e:
            logger.error(f"Lỗi khi extract specifications: {str(e)}")
            return self._create_specifications_table_html(product_code, product_name, [])
    
    def _parse_specs_table(self, table):
        """Parse table thông số"""
        try:
            specs = []
            rows = table.find_elements(By.TAG_NAME, 'tr')
            
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, 'td')
                if len(cells) >= 2:
                    key = cells[0].text.strip()
                    value = cells[1].text.strip()
                    if key and value:
                        specs.append({'key': key, 'value': value})
            
            return specs
        except:
            return []
    
    def _create_specifications_table_html(self, product_code, product_name, specs_data):
        """Tạo HTML table cho thông số kỹ thuật"""
        html = f"""
        <table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">
            <thead>
                <tr style="background-color: #f2f2f2;">
                    <th>Thông số</th><th>Giá trị</th>
                </tr>
            </thead>
            <tbody>
                <tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product_code}</td></tr>
                <tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product_name}</td></tr>
        """
        
        for spec in specs_data:
            html += f'<tr><td style="font-weight: bold;">{spec["key"]}</td><td>{spec["value"]}</td></tr>'
        
        html += """
                <tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>
            </tbody>
        </table>
        """
        
        return html
    
    def _create_excel_report(self, products_data, output_dir):
        """Tạo file Excel report"""
        try:
            # Tạo DataFrame
            df_data = []
            for product in products_data:
                df_data.append({
                    'Mã sản phẩm': product.get('code', ''),
                    'Tên sản phẩm': product.get('name', ''),
                    'Ảnh': product.get('image_path', ''),
                    'Thông số kỹ thuật': product.get('specs_html', ''),
                    'URL sản phẩm': product.get('url', ''),
                    'Thời gian cào': product.get('crawl_time', '')
                })
            
            df = pd.DataFrame(df_data)
            
            # Lưu Excel
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_filename = f"Mitsubishi_Products_{timestamp}.xlsx"
            excel_path = os.path.join(output_dir, excel_filename)
            
            df.to_excel(excel_path, index=False, engine='openpyxl')
            
            logger.info(f"✅ Đã tạo Excel report: {excel_path}")
            return excel_path
            
        except Exception as e:
            logger.error(f"Lỗi khi tạo Excel report: {str(e)}")
            return None
