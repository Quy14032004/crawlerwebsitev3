"""
Crawler BAA Qlight - Cào dữ liệu sản phẩm từ https://baa.vn/vn/qlight/
Hỗ trợ đa luồng, chuyển đổi ảnh WebP và xử lý series
"""

import os
import shutil
import time
import zipfile
from datetime import datetime
import pandas as pd
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
import traceback
from queue import Queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import io
import hashlib
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
import openpyxl.styles
import tempfile

# Import các hàm từ module crawler hiện có
from app.crawler import (
    is_category_url, is_product_url, extract_product_urls, extract_product_info,
    download_baa_product_images_fixed, get_html_content, HEADERS,
    extract_baa_product_price, extract_baa_image_url_from_html, extract_product_code_from_url
)
from app.webp_converter import WebPConverter
from app import socketio

# Cấu hình logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BAAQlightCrawler:
    """Crawler chuyên dụng cho BAA Qlight với hỗ trợ đa luồng và xử lý series"""
    
    def __init__(self, base_url="https://baa.vn/vn/qlight/", max_workers=10):
        self.base_url = base_url
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # URL danh mục gốc QLIGHT để load filter Category/Series
        self.category_root_url = "https://baa.vn/vn/Category/cong-tac-den-bao-coi-bao-qlight_F_782/"
    
    def _create_webdriver(self):
        """Tạo WebDriver với Chrome"""
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')  # Chạy ẩn
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver
        except Exception as e:
            logger.error(f"❌ Lỗi tạo WebDriver: {str(e)}")
            return None

    def get_categories_from_filter_widget(self, category_root_url=None):
        """
        Lấy danh sách danh mục (Category) từ widget filter trên trang danh mục gốc QLIGHT
        Trả về list các dict: { name, url, original_href }
        """
        try:
            url = category_root_url or self.category_root_url
            html = get_html_content(url)
            if not html:
                logger.error("❌ Không thể tải trang danh mục gốc để lấy Category")
                return []

            soup = BeautifulSoup(html, 'html.parser')
            filter_container = soup.select_one('div.baaFilter')
            if not filter_container:
                logger.warning("⚠️ Không tìm thấy container .baaFilter")
                return []

            category_widget = filter_container.select_one('div.widget ul.widget_Items[data-id="Cate"]')
            if not category_widget:
                logger.warning("⚠️ Không tìm thấy widget Category (data-id=\"Cate\")")
                return []

            categories = []
            for li in category_widget.select('li.widget_Item a'):
                try:
                    href = li.get('href') or ''
                    label_el = li.select_one('span.itemLabel')
                    name = label_el.get_text(strip=True) if label_el else li.get_text(strip=True)
                    full_url = urljoin(self.base_url, href)
                    if name and href:
                        categories.append({
                            'name': name,
                            'url': full_url,
                            'original_href': href
                        })
                except Exception as e:
                    logger.error(f"Lỗi đọc 1 mục Category: {str(e)}")
                    continue

            logger.info(f"✅ Tìm thấy {len(categories)} danh mục Category từ filter")
            return categories
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy Category filter: {str(e)}")
            return []

    def get_brands_from_filter_widget(self, category_root_url=None):
        """
        Lấy danh sách Hãng (Brand) từ widget filter (data-id="Hang").
        Trả về list các dict: { name, url, original_href }
        """
        try:
            url = category_root_url or self.category_root_url
            html = get_html_content(url)
            if not html:
                return []
            soup = BeautifulSoup(html, 'html.parser')
            filter_container = soup.select_one('div.baaFilter')
            if not filter_container:
                return []
            brand_widget = filter_container.select_one('div.widget ul.widget_Items[data-id="Hang"]')
            if not brand_widget:
                return []
            brands = []
            for li in brand_widget.select('li.widget_Item a'):
                try:
                    href = li.get('href') or ''
                    label_el = li.select_one('span.itemLabel')
                    name = label_el.get_text(strip=True) if label_el else li.get_text(strip=True)
                    full_url = urljoin(self.base_url, href)
                    if name and href:
                        brands.append({
                            'name': name,
                            'url': full_url,
                            'original_href': href
                        })
                except Exception:
                    continue
            return brands
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy Brand filter: {str(e)}")
            return []

    def get_series_from_category(self, category_url):
        """
        Lấy danh sách series từ trang danh mục đã chọn (viết lại từ đầu, bền vững theo DOM mẫu).
        - Ưu tiên phân tích HTML bằng requests/BeautifulSoup.
        - Dò tìm widget có tiêu đề "Series" ở cả trong/ngoài `.baaFilter`.
        - Fallback: quét toàn trang các `ul[data-id="Seri"]`.
        - Không phụ thuộc Selenium (tránh lỗi ChromeDriver trên Windows).
        Args:
            category_url (str): URL danh mục (ví dụ: /vn/Category/den-thap-den-tang-qlight_279_782/)
        Returns:
            list[dict]: { name, url, original_href }
        """
        try:
            html = get_html_content(category_url)
            if not html:
                logger.error("❌ Không tải được HTML của trang danh mục")
                return []

            soup = BeautifulSoup(html, 'html.parser')

            # Xác định origin để join URL tuyệt đối
            try:
                parsed = urlparse(category_url)
                origin = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                origin = self.base_url

            def collect_from_ul(ul_tag):
                results = []
                if not ul_tag:
                    return results
                for a in ul_tag.select('li.widget_Item[data-input="Seri"] a, li.widget_Item a'):
                    href = a.get('href') or ''
                    if not href:
                        continue
                    label_el = a.select_one('span.itemLabel')
                    name = (label_el.get_text(strip=True) if label_el else a.get_text(strip=True)).strip()
                    if not name or name == 'Tìm từ khóa ...':
                        continue
                    results.append({
                        'name': name,
                        'url': urljoin(origin, href),
                        'original_href': href
                    })
                return results

            # 1) Tìm trong .baaFilter nếu có
            filter_container = soup.select_one('div.baaFilter')
            if filter_container:
                for widget in filter_container.select('div.widget'):
                    span = widget.find('span')
                    title = span.get_text(strip=True).lower() if span else ''
                    if title == 'series':
                        ul_tag = widget.select_one('ul.widget_Items[data-id="Seri"]') or widget.select_one('ul[data-id="Seri"]')
                        series_list = collect_from_ul(ul_tag)
                        if series_list:
                            logger.info(f"✅ HTML: Tìm thấy {len(series_list)} series (trong .baaFilter)")
                            return series_list

            # 2) Quét toàn trang các widget có tiêu đề Series
            for widget in soup.select('div.widget'):
                span = widget.find('span')
                title = span.get_text(strip=True).lower() if span else ''
                if title == 'series':
                    ul_tag = widget.select_one('ul.widget_Items[data-id="Seri"]') or widget.select_one('ul[data-id="Seri"]')
                    series_list = collect_from_ul(ul_tag)
                    if series_list:
                        logger.info(f"✅ HTML: Tìm thấy {len(series_list)} series (toàn trang)")
                        return series_list

            # 3) Fallback cuối: tìm mọi ul[data-id='Seri'] trên trang
            for ul_tag in soup.select('ul.widget_Items[data-id="Seri"], ul[data-id="Seri"]'):
                series_list = collect_from_ul(ul_tag)
                if series_list:
                    logger.info(f"✅ HTML: Tìm thấy {len(series_list)} series (fallback ul Seri)")
                    return series_list

            # 4) Fallback mở rộng: tìm trực tiếp mọi li[data-input='Seri'] trên toàn trang
            direct_items = []
            for a in soup.select("li.widget_Item[data-input='Seri'] a"):
                href = a.get('href') or ''
                if not href:
                    continue
                label_el = a.select_one('span.itemLabel')
                name = (label_el.get_text(strip=True) if label_el else a.get_text(strip=True)).strip()
                if not name or name == 'Tìm từ khóa ...':
                    continue
                direct_items.append({
                    'name': name,
                    'url': urljoin(origin, href),
                    'original_href': href
                })
            if direct_items:
                logger.info(f"✅ HTML: Tìm thấy {len(direct_items)} series (direct li[data-input='Seri'])")
                return direct_items

            # 5) Fallback bằng regex: trích khối <ul ... data-id="Seri"> ... </ul> rồi parse lại bằng BeautifulSoup
            try:
                ul_blocks = re.findall(r'<ul[^>]*data-id=["\']Seri["\'][^>]*>([\s\S]*?)</ul>', html, flags=re.IGNORECASE)
                extracted = []
                for block in ul_blocks:
                    block_soup = BeautifulSoup(block, 'html.parser')
                    for a in block_soup.select("li.widget_Item[data-input='Seri'] a, li.widget_Item a"):
                        href = a.get('href') or ''
                        if not href:
                            continue
                        label_el = a.select_one('span.itemLabel')
                        name = (label_el.get_text(strip=True) if label_el else a.get_text(strip=True)).strip()
                        if not name or name == 'Tìm từ khóa ...':
                            continue
                        extracted.append({
                            'name': name,
                            'url': urljoin(origin, href),
                            'original_href': href
                        })
                if extracted:
                    logger.info(f"✅ Regex: Tìm thấy {len(extracted)} series từ khối ul[data-id='Seri']")
                    return extracted
            except Exception as e:
                logger.error(f"❌ Lỗi regex fallback series: {str(e)}")

            logger.warning("⚠️ Không tìm thấy danh sách series trên trang")
            return []
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy Series theo danh mục: {str(e)}")
            return []
        
    def extract_series_info(self, url):
        """
        Trích xuất thông tin series từ trang sản phẩm
        
        Args:
            url (str): URL trang sản phẩm
            
        Returns:
            dict: Thông tin series bao gồm tên và URL
        """
        try:
            html = get_html_content(url)
            if not html:
                return None
                
            soup = BeautifulSoup(html, 'html.parser')
            
            # Phương pháp 1: Tìm theo cấu trúc HTML đã cung cấp
            series_header = soup.select_one('div.product__symbol-header.px-2.py-1.m-0.row')
            if not series_header:
                # Thử tìm với selector khác
                series_header = soup.select_one('div.product__symbol-header')
            
            if series_header:
                # Tìm label "Series:"
                series_label = series_header.select_one('span.product__symbol-label')
                if series_label and 'Series:' in series_label.text:
                    # Tìm link series trong span.product__symbol__value
                    series_value = series_header.select_one('span.product__symbol__value')
                    if series_value:
                        series_link = series_value.select_one('a.text-decoration-none.text-link')
                        if series_link:
                            series_name = series_link.get_text(strip=True)
                            series_url = urljoin(self.base_url, series_link.get('href', ''))
                            
                            return {
                                'name': series_name,
                                'url': series_url,
                                'original_url': url
                            }
            
            # Phương pháp 2: Tìm series từ breadcrumb
            breadcrumb = soup.select_one('.breadcrumb, .breadcrumb-item')
            if breadcrumb:
                breadcrumb_links = breadcrumb.select('a[href*="series"]')
                for link in breadcrumb_links:
                    href = link.get('href', '')
                    if 'series' in href.lower():
                        series_name = link.get_text(strip=True)
                        series_url = urljoin(self.base_url, href)
                        
                        return {
                            'name': series_name,
                            'url': series_url,
                            'original_url': url
                        }
            
            # Phương pháp 3: Tìm series từ URL pattern
            if 'series' in url.lower():
                # Trích xuất tên series từ URL
                url_parts = url.split('/')
                for i, part in enumerate(url_parts):
                    if 'series' in part.lower() and i + 1 < len(url_parts):
                        series_name = url_parts[i + 1].replace('_', ' ').title()
                        series_url = url
                        
                        return {
                            'name': series_name,
                            'url': series_url,
                            'original_url': url
                        }
            
            # Phương pháp 4: Tìm series từ meta tags
            meta_series = soup.select_one('meta[name="series"], meta[property="series"]')
            if meta_series:
                series_name = meta_series.get('content', '')
                if series_name:
                    # Tạo URL series từ tên
                    series_url = urljoin(self.base_url, f"/vn/series/{series_name.lower().replace(' ', '-')}/")
                    
                    return {
                        'name': series_name,
                        'url': series_url,
                        'original_url': url
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất thông tin series từ {url}: {str(e)}")
            return None
    
    def get_products_from_series(self, series_url):
        """
        Lấy danh sách sản phẩm từ một series với logic cải tiến từ BAA crawler chính
        
        Args:
            series_url (str): URL của series
            
        Returns:
            list: Danh sách URL sản phẩm trong series
        """
        try:
            # Sử dụng logic từ BAA crawler chính để lấy URL sản phẩm
            product_urls = extract_product_urls(series_url)
            
            if product_urls:
                logger.info(f"✓ Tìm thấy {len(product_urls)} sản phẩm trong series: {series_url}")
                return product_urls
            
            # Fallback: thử các phương pháp khác
            html = get_html_content(series_url)
            if not html:
                logger.warning(f"⚠️ Không thể tải HTML từ series: {series_url}")
                return []
                
            soup = BeautifulSoup(html, 'html.parser')
            
            # Tìm tất cả link sản phẩm trong series
            product_urls = []
            
            # Phương pháp 1: Tìm trong các container sản phẩm
            product_containers = soup.select('.product-item, .product-card, .product, [class*="product"], .item, .card')
            
            for container in product_containers:
                links = container.select('a[href*="/vn/"]')
                for link in links:
                    href = link.get('href')
                    if href and is_product_url(href):
                        full_url = urljoin(self.base_url, href)
                        if full_url not in product_urls:
                            product_urls.append(full_url)
            
            # Phương pháp 2: Tìm tất cả link có pattern sản phẩm
            if not product_urls:
                all_links = soup.select('a[href*="/vn/"]')
                for link in all_links:
                    href = link.get('href')
                    if href and is_product_url(href):
                        full_url = urljoin(self.base_url, href)
                        if full_url not in product_urls:
                            product_urls.append(full_url)
            
            # Phương pháp 3: Tìm theo pattern URL cụ thể của BAA
            if not product_urls:
                # Tìm các link có pattern _số ở cuối (ID sản phẩm)
                pattern_links = soup.select('a[href*="_"]')
                for link in pattern_links:
                    href = link.get('href')
                    if href and re.search(r'_\d+/?$', href):
                        full_url = urljoin(self.base_url, href)
                        if full_url not in product_urls:
                            product_urls.append(full_url)
            
            # Phương pháp 4: Tìm trong pagination nếu có
            if not product_urls:
                # Kiểm tra xem có phân trang không
                pagination = soup.select_one('.pagination, .page-list, nav[aria-label="Page navigation"]')
                if pagination:
                    # Lấy tất cả các trang
                    page_links = pagination.select('a.page-link')
                    page_urls = []
                    for link in page_links:
                        href = link.get('href')
                        if href and 'page=' in href:
                            page_url = urljoin(self.base_url, href)
                            page_urls.append(page_url)
                    
                    # Lấy sản phẩm từ các trang
                    for page_url in page_urls[:5]:  # Giới hạn 5 trang để tránh quá tải
                        try:
                            page_products = extract_product_urls(page_url)
                            product_urls.extend(page_products)
                        except Exception as e:
                            logger.warning(f"⚠️ Lỗi khi lấy sản phẩm từ trang {page_url}: {str(e)}")
                            continue
            
            # Loại bỏ các URL không phải sản phẩm và trùng lặp
            filtered_urls = []
            seen_urls = set()
            
            for url in product_urls:
                if is_product_url(url) and url not in seen_urls:
                    filtered_urls.append(url)
                    seen_urls.add(url)
            
            logger.info(f"✓ Tìm thấy {len(filtered_urls)} sản phẩm trong series: {series_url}")
            return filtered_urls
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy sản phẩm từ series {series_url}: {str(e)}")
            return []
    
    def crawl_product_info(self, url, index=1):
        """
        Cào thông tin một sản phẩm với logic cải tiến từ BAA crawler chính
        
        Args:
            url (str): URL sản phẩm
            index (int): Số thứ tự
            
        Returns:
            dict: Thông tin sản phẩm
        """
        try:
            # Sử dụng logic trích xuất thông tin cải tiến
            product_info = self._extract_product_info_enhanced(url, index)
            
            # Thêm thông tin series
            series_info = self.extract_series_info(url)
            if series_info:
                product_info['Series'] = series_info['name']
                product_info['Series_URL'] = series_info['url']
            else:
                product_info['Series'] = ''
                product_info['Series_URL'] = ''
            
            return product_info
            
        except Exception as e:
            logger.error(f"Lỗi khi cào thông tin sản phẩm {url}: {str(e)}")
            return None
    
    def _extract_product_info_enhanced(self, url, index=1):
        """
        Trích xuất thông tin sản phẩm với logic cải tiến từ BAA crawler chính
        """
        max_retries = 3
        current_retry = 0
        
        while current_retry < max_retries:
            try:
                # Tải HTML với HEADERS chuẩn từ module crawler
                response = self.session.get(url, headers=HEADERS, timeout=30, verify=False)
                response.raise_for_status()
                html_content = response.text
                soup = BeautifulSoup(html_content, 'html.parser')
                
                product_info = {
                    'STT': index,
                    'URL': url
                }
                
                # 1. Tên sản phẩm - thử nhiều selector
                name_element = soup.select_one('h1.product__name, h1.product-title, .product__name, .product-title')
                if name_element:
                    product_info['Tên sản phẩm'] = name_element.get_text(strip=True)
                else:
                    # Fallback: tìm trong title hoặc h1 khác
                    title_element = soup.select_one('title, h1')
                    if title_element:
                        product_info['Tên sản phẩm'] = title_element.get_text(strip=True)
                    else:
                        product_info['Tên sản phẩm'] = ''
                
                # 2. Mã sản phẩm - thử nhiều selector
                code_element = soup.select_one('span.product__symbol__value, .product-code, .product__code, .product-symbol')
                if code_element:
                    product_info['Mã sản phẩm'] = code_element.get_text(strip=True)
                else:
                    # Fallback: dùng helper chuẩn
                    product_code = extract_product_code_from_url(url) or url.split('/')[-1]
                    product_info['Mã sản phẩm'] = product_code
                
                # 3. Giá sản phẩm - dùng helper chuẩn (có giảm 5% và làm sạch)
                try:
                    price = extract_baa_product_price(soup, product_info.get('Tên sản phẩm', ''))
                except Exception:
                    price = ''
                product_info['Giá'] = price
                
                # 4. Thông số kỹ thuật - tìm bảng thông số
                spec_html = ''
                spec_table = soup.select_one('table.feature__metadata--tab.active, table.feature__metadata, table.specifications, .product-specs table')
                if spec_table:
                    spec_html = str(spec_table)
                else:
                    # Fallback: tìm tất cả bảng trong trang
                    all_tables = soup.find_all('table')
                    for table in all_tables:
                        if any(keyword in table.get_text().lower() for keyword in ['thông số', 'specification', 'đặc điểm', 'tính năng']):
                            spec_html = str(table)
                            break
                
                product_info['Tổng quan'] = spec_html
                
                # 5. Ảnh sản phẩm - dùng helper chuẩn để bắt nhiều pattern của BAA
                try:
                    img_info = extract_baa_image_url_from_html(html_content)
                    img_url = ''
                    if img_info and img_info.get('image_urls'):
                        img_url = img_info['image_urls'][0]
                    if not img_url:
                        img_url = self._extract_image_url_enhanced(soup)
                except Exception:
                    img_url = self._extract_image_url_enhanced(soup)
                product_info['Ảnh sản phẩm'] = img_url
                
                logger.info(f"✓ Đã trích xuất thông tin sản phẩm: {product_info.get('Mã sản phẩm', 'N/A')} - {product_info.get('Tên sản phẩm', 'N/A')[:50]}...")
                return product_info
                
            except Exception as e:
                current_retry += 1
                logger.warning(f"Lần thử {current_retry}/{max_retries} thất bại cho {url}: {str(e)}")
                if current_retry < max_retries:
                    time.sleep(1)  # Chờ 1 giây trước khi thử lại
                else:
                    logger.error(f"Không thể trích xuất thông tin từ {url} sau {max_retries} lần thử")
                    return None
        
        return None
    
    def _extract_product_price_enhanced(self, soup, product_name=""):
        """
        Trích xuất giá sản phẩm với logic cải tiến từ BAA crawler chính
        """
        try:
            # Thử nhiều selector cho giá
            price_selectors = [
                '.product__price .price',
                '.product-price .price',
                '.price-current',
                '.product__price',
                '.product-price',
                '.price',
                '[class*="price"]'
            ]
            
            for selector in price_selectors:
                price_element = soup.select_one(selector)
                if price_element:
                    price_text = price_element.get_text(strip=True)
                    if price_text and any(char.isdigit() for char in price_text):
                        # Làm sạch giá
                        cleaned_price = self._clean_price(price_text)
                        if cleaned_price:
                            return cleaned_price
            
            # Fallback: tìm trong toàn bộ trang
            page_text = soup.get_text()
            import re
            price_patterns = [
                r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:đ|₫|VND|VNĐ)',
                r'(?:đ|₫|VND|VNĐ)\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',
                r'Giá[:\s]*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',
            ]
            
            for pattern in price_patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    return f"{matches[0]} đ"
            
            return ''
            
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất giá: {str(e)}")
            return ''
    
    def _clean_price(self, price_str):
        """
        Làm sạch giá sản phẩm
        """
        if not price_str:
            return ''
        
        import re
        # Loại bỏ các ký tự không cần thiết, giữ lại số và đơn vị tiền tệ
        cleaned = re.sub(r'[^\d,.\sđ₫VND]', '', price_str.strip())
        
        # Kiểm tra xem có chứa số không
        if re.search(r'\d', cleaned):
            return cleaned
        else:
            return ''
    
    def _extract_image_url_enhanced(self, soup):
        """
        Trích xuất URL ảnh sản phẩm với logic cải tiến từ BAA crawler chính
        """
        try:
            # Thử nhiều selector cho ảnh
            image_selectors = [
                'div.modal-body-image.active img',
                'div.modal-body-image img',
                '.product-image img',
                '.product__image img',
                'img.btn-image-view-360',
                '.product-gallery img',
                '.product-photos img',
                'img[src*="product"]',
                'img[src*="image"]'
            ]
            
            for selector in image_selectors:
                img_element = soup.select_one(selector)
                if img_element and img_element.get('src'):
                    img_url = img_element['src']
                    if img_url and not img_url.startswith('data:'):
                        # Chuyển đổi URL tương đối thành tuyệt đối
                        if img_url.startswith('//'):
                            img_url = 'https:' + img_url
                        elif img_url.startswith('/'):
                            img_url = 'https://baa.vn' + img_url
                        
                        # Thử chuyển đổi sang kích thước lớn hơn
                        if 'thumb' in img_url or 'small' in img_url:
                            img_url = img_url.replace('thumb', 'large').replace('small', 'large')
                        
                        return img_url
            
            # Fallback: tìm tất cả ảnh trong trang
            all_images = soup.find_all('img')
            for img in all_images:
                src = img.get('src', '')
                if src and any(keyword in src.lower() for keyword in ['product', 'image', 'photo']):
                    if not src.startswith('data:'):
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif src.startswith('/'):
                            src = 'https://baa.vn' + src
                        return src
            
            return ''
            
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất URL ảnh: {str(e)}")
            return ''
    
    def crawl_products_multithread(self, product_urls):
        """
        Cào thông tin sản phẩm sử dụng đa luồng
        
        Args:
            product_urls (list): Danh sách URL sản phẩm
            
        Returns:
            list: Danh sách thông tin sản phẩm
        """
        results = []
        lock = threading.Lock()
        completed_count = 0
        total_count = len(product_urls)
        
        def process_product(args):
            nonlocal completed_count
            url, index = args
            try:
                product_info = self.crawl_product_info(url, index)
                if product_info:
                    with lock:
                        results.append(product_info)
                        completed_count += 1
                        progress = (completed_count / total_count) * 100
                        logger.info(f"✓ [{completed_count}/{total_count}] ({progress:.1f}%) - {product_info.get('Tên sản phẩm', 'Unknown')[:50]}...")
                else:
                    with lock:
                        completed_count += 1
                        logger.warning(f"⚠️ [{completed_count}/{total_count}] Không lấy được thông tin: {url}")
                return product_info
            except Exception as e:
                with lock:
                    completed_count += 1
                    logger.error(f"❌ [{completed_count}/{total_count}] Lỗi xử lý sản phẩm {url}: {str(e)}")
                return None
        
        logger.info(f"🔄 Bắt đầu cào thông tin {total_count} sản phẩm với {self.max_workers} luồng...")
        
        # Tạo danh sách arguments cho ThreadPoolExecutor
        args_list = [(url, i+1) for i, url in enumerate(product_urls)]
        
        # Sử dụng ThreadPoolExecutor để xử lý đa luồng
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(process_product, args) for args in args_list]
            
            # Chờ tất cả futures hoàn thành
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"❌ Lỗi trong thread: {str(e)}")
        
        logger.info(f"✅ Hoàn thành cào thông tin {len(results)}/{total_count} sản phẩm")
        return results
    
    def convert_images_to_webp(self, product_info_list, output_folder):
        """
        Tải và chuyển đổi tất cả ảnh sản phẩm sang định dạng WebP với logic cải tiến
        
        Args:
            product_info_list (list): Danh sách thông tin sản phẩm
            output_folder (str): Thư mục lưu ảnh
            
        Returns:
            list: Danh sách thông tin sản phẩm với đường dẫn ảnh WebP
        """
        webp_folder = os.path.join(output_folder, "webp_images")
        os.makedirs(webp_folder, exist_ok=True)
        
        converted_products = []
        total_products = len(product_info_list)
        
        logger.info(f"🖼️ Bắt đầu tải và chuyển đổi {total_products} ảnh sang WebP...")
        
        # Sử dụng đa luồng để tải ảnh
        with ThreadPoolExecutor(max_workers=min(5, self.max_workers)) as executor:
            futures = []
            
            for i, product_info in enumerate(product_info_list, 1):
                future = executor.submit(self._process_single_image, product_info, webp_folder, i, total_products)
                futures.append((future, product_info))
            
            # Xử lý kết quả khi hoàn thành
            for future, product_info in futures:
                try:
                    result = future.result()
                    if result:
                        converted_products.append(result)
                    else:
                        converted_products.append(product_info)
                except Exception as e:
                    logger.error(f"❌ Lỗi xử lý ảnh cho sản phẩm {product_info.get('Mã sản phẩm', 'unknown')}: {str(e)}")
                    converted_products.append(product_info)
        
        logger.info(f"✅ Hoàn thành tải và chuyển đổi {len(converted_products)} ảnh sang WebP")
        return converted_products
    
    def _process_single_image(self, product_info, webp_folder, index, total):
        """
        Xử lý một ảnh sản phẩm với logic cải tiến từ BAA crawler chính
        """
        try:
            img_url = product_info.get('Ảnh sản phẩm', '')
            if not img_url:
                product_info['Ảnh_WebP'] = ''
                logger.info(f"⏭️ Bỏ qua sản phẩm {index}/{total}: Không có ảnh")
                return product_info
            
            # Tạo tên file WebP an toàn
            product_code = product_info.get('Mã sản phẩm', 'unknown')
            safe_product_code = re.sub(r'[<>:"/\\|?*]', '_', product_code)
            webp_filename = f"{safe_product_code}.webp"
            webp_path = os.path.join(webp_folder, webp_filename)
            
            # Kiểm tra xem file đã tồn tại chưa
            if os.path.exists(webp_path) and os.path.getsize(webp_path) > 0:
                product_info['Ảnh_WebP'] = webp_path
                logger.info(f"✓ Sử dụng ảnh có sẵn {index}/{total}: {safe_product_code}")
                return product_info
            
            # Tải ảnh với retry logic
            max_retries = 3
            for retry in range(max_retries):
                try:
                    # Sử dụng headers ngẫu nhiên
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                        'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
                        'Referer': 'https://baa.vn/',
                    }
                    
                    response = self.session.get(img_url, headers=headers, timeout=30)
                    response.raise_for_status()
                    
                    # Kiểm tra content type
                    content_type = response.headers.get('content-type', '').lower()
                    if not content_type.startswith('image/'):
                        logger.warning(f"⚠️ URL không phải ảnh: {img_url} (content-type: {content_type})")
                        if retry < max_retries - 1:
                            time.sleep(1)
                            continue
                        else:
                            product_info['Ảnh_WebP'] = ''
                            return product_info
                    
                    # Kiểm tra kích thước ảnh
                    if len(response.content) < 1024:  # Nhỏ hơn 1KB
                        logger.warning(f"⚠️ Ảnh quá nhỏ: {img_url} ({len(response.content)} bytes)")
                        if retry < max_retries - 1:
                            time.sleep(1)
                            continue
                        else:
                            product_info['Ảnh_WebP'] = ''
                            return product_info
                    
                    # Mở ảnh và kiểm tra
                    img = Image.open(io.BytesIO(response.content))
                    
                    # Kiểm tra kích thước ảnh
                    if img.size[0] < 50 or img.size[1] < 50:
                        logger.warning(f"⚠️ Ảnh quá nhỏ: {img_url} ({img.size})")
                        if retry < max_retries - 1:
                            time.sleep(1)
                            continue
                        else:
                            product_info['Ảnh_WebP'] = ''
                            return product_info
                    
                    # Chuyển đổi sang RGB nếu cần
                    if img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGB')
                    
                    # Lưu ảnh tạm thời
                    temp_img_path = os.path.join(webp_folder, f"temp_{safe_product_code}.jpg")
                    img.save(temp_img_path, 'JPEG', quality=95)
                    
                    # Sử dụng WebPConverter để đảm bảo chất lượng
                    result = WebPConverter.convert_to_webp(
                        input_path=temp_img_path,
                        output_path=webp_path,
                        quality=90,
                        lossless=False,
                        method=6
                    )
                    
                    # Xóa file tạm
                    if os.path.exists(temp_img_path):
                        os.remove(temp_img_path)
                    
                    if result['success']:
                        product_info['Ảnh_WebP'] = webp_path
                        logger.info(f"✓ Đã tải và chuyển đổi ảnh {index}/{total}: {safe_product_code} ({result['compression_ratio']:.1f}%)")
                        return product_info
                    else:
                        logger.error(f"❌ Lỗi chuyển đổi WebP cho {safe_product_code}: {result.get('error', 'Unknown error')}")
                        product_info['Ảnh_WebP'] = ''
                        return product_info
                        
                except Exception as e:
                    logger.warning(f"⚠️ Lần thử {retry + 1}/{max_retries} thất bại cho {img_url}: {str(e)}")
                    if retry < max_retries - 1:
                        time.sleep(2)  # Chờ lâu hơn giữa các lần thử
                    else:
                        logger.error(f"❌ Không thể tải ảnh cho {safe_product_code} sau {max_retries} lần thử")
                        product_info['Ảnh_WebP'] = ''
                        return product_info
            
            return product_info
            
        except Exception as e:
            logger.error(f"❌ Lỗi xử lý ảnh cho sản phẩm {product_info.get('Mã sản phẩm', 'unknown')}: {str(e)}")
            product_info['Ảnh_WebP'] = ''
            return product_info
    
    def create_excel_by_series(self, product_info_list, output_folder, default_series_name=None):
        """
        Tạo file Excel riêng cho từng series
        
        Args:
            product_info_list (list): Danh sách thông tin sản phẩm
            output_folder (str): Thư mục lưu file Excel
            
        Returns:
            list: Danh sách đường dẫn file Excel đã tạo
        """
        # Nhóm sản phẩm theo series
        series_groups = {}
        
        for product in product_info_list:
            series_name = product.get('Series', '').strip()
            if not series_name and isinstance(default_series_name, str) and default_series_name.strip():
                # Gán fallback series cho dữ liệu thiếu Series
                series_name = default_series_name.strip()
                product['Series'] = series_name
            if not series_name:
                series_name = 'Unknown_Series'
            if series_name not in series_groups:
                series_groups[series_name] = []
            series_groups[series_name].append(product)
        
        excel_files = []
        total_series = len(series_groups)
        
        logger.info(f"📊 Bắt đầu tạo {total_series} file Excel theo series...")
        
        for i, (series_name, products) in enumerate(series_groups.items(), 1):
            try:
                # Tạo tên file Excel an toàn
                safe_series_name = re.sub(r'[<>:"/\\|?*]', '_', series_name)
                excel_filename = f"BAA_Qlight_{safe_series_name}.xlsx"
                excel_path = os.path.join(output_folder, excel_filename)
                
                # Tạo DataFrame với thứ tự cột hợp lý
                df = pd.DataFrame(products)
                
                # Sắp xếp lại thứ tự cột
                column_order = [
                    'STT', 'Mã sản phẩm', 'Tên sản phẩm', 'Series', 'Giá', 
                    'Tổng quan', 'Ảnh sản phẩm', 'Ảnh_WebP', 'Series_URL', 'URL'
                ]
                
                # Chỉ giữ lại các cột có trong dữ liệu
                available_columns = [col for col in column_order if col in df.columns]
                other_columns = [col for col in df.columns if col not in column_order]
                final_columns = available_columns + other_columns
                
                df = df[final_columns]
                
                # Lưu file Excel
                with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='Products', index=False)
                    
                    # Thêm ảnh vào sheet riêng nếu có
                    workbook = writer.book
                    if 'Ảnh_WebP' in df.columns:
                        self._add_images_to_excel(workbook, products, output_folder)
                    
                    # Thêm thông tin series vào sheet mới
                    self._add_series_info_sheet(workbook, series_name, len(products))
                
                excel_files.append(excel_path)
                logger.info(f"✓ [{i}/{total_series}] Đã tạo file Excel: {excel_filename} ({len(products)} sản phẩm)")
                
            except Exception as e:
                logger.error(f"❌ Lỗi tạo Excel cho series {series_name}: {str(e)}")
        
        logger.info(f"✅ Hoàn thành tạo {len(excel_files)} file Excel")
        return excel_files
    
    def _add_images_to_excel(self, workbook, products, output_folder):
        """
        Thêm ảnh vào file Excel
        
        Args:
            workbook: Workbook object
            products (list): Danh sách sản phẩm
            output_folder (str): Thư mục chứa ảnh
        """
        try:
            # Tạo sheet mới cho ảnh
            if 'Images' in workbook.sheetnames:
                workbook.remove(workbook['Images'])
            
            image_sheet = workbook.create_sheet('Images')
            
            # Thêm tiêu đề
            image_sheet['A1'] = 'Mã sản phẩm'
            image_sheet['B1'] = 'Tên sản phẩm'
            image_sheet['C1'] = 'Ảnh sản phẩm'
            
            # Thêm ảnh cho từng sản phẩm
            for i, product in enumerate(products, start=2):
                image_sheet[f'A{i}'] = product.get('Mã sản phẩm', '')
                image_sheet[f'B{i}'] = product.get('Tên sản phẩm', '')
                
                webp_path = product.get('Ảnh_WebP', '')
                if webp_path and os.path.exists(webp_path):
                    try:
                        # Thêm ảnh vào Excel
                        img = XLImage(webp_path)
                        img.width = 100
                        img.height = 100
                        image_sheet.add_image(img, f'C{i}')
                    except Exception as e:
                        logger.error(f"Lỗi thêm ảnh vào Excel: {str(e)}")
        
        except Exception as e:
            logger.error(f"Lỗi tạo sheet ảnh: {str(e)}")
    
    def _add_series_info_sheet(self, workbook, series_name, product_count):
        """
        Thêm sheet thông tin series vào workbook
        
        Args:
            workbook: Workbook object
            series_name (str): Tên series
            product_count (int): Số lượng sản phẩm trong series
        """
        try:
            # Tạo sheet mới cho thông tin series
            if 'Series_Info' in workbook.sheetnames:
                workbook.remove(workbook['Series_Info'])
            
            info_sheet = workbook.create_sheet('Series_Info')
            
            # Thêm thông tin series
            info_sheet['A1'] = 'Thông tin Series'
            info_sheet['A1'].font = openpyxl.styles.Font(bold=True, size=14)
            
            info_sheet['A3'] = 'Tên Series:'
            info_sheet['B3'] = series_name
            info_sheet['A3'].font = openpyxl.styles.Font(bold=True)
            
            info_sheet['A4'] = 'Số sản phẩm:'
            info_sheet['B4'] = product_count
            info_sheet['A4'].font = openpyxl.styles.Font(bold=True)
            
            info_sheet['A5'] = 'Ngày tạo:'
            info_sheet['B5'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            info_sheet['A5'].font = openpyxl.styles.Font(bold=True)
            
            # Điều chỉnh độ rộng cột
            info_sheet.column_dimensions['A'].width = 15
            info_sheet.column_dimensions['B'].width = 30
            
        except Exception as e:
            logger.error(f"Lỗi tạo sheet thông tin series: {str(e)}")
    
    def create_download_zip(self, output_folder, zip_filename=None):
        """
        Tạo file ZIP chứa tất cả kết quả cào dữ liệu để tải xuống
        
        Args:
            output_folder (str): Thư mục chứa kết quả cào dữ liệu
            zip_filename (str): Tên file ZIP (tùy chọn)
            
        Returns:
            dict: Kết quả tạo file ZIP
        """
        try:
            if not os.path.exists(output_folder):
                return {
                    'success': False,
                    'error': 'Thư mục kết quả không tồn tại',
                    'message': f'Không tìm thấy thư mục: {output_folder}'
                }
            
            # Tạo tên file ZIP nếu chưa có
            if not zip_filename:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                zip_filename = f"BAA_Qlight_Results_{timestamp}.zip"
            
            zip_path = os.path.join(output_folder, zip_filename)
            
            # Tạo file ZIP
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Thêm tất cả file Excel
                for root, dirs, files in os.walk(output_folder):
                    for file in files:
                        if file.endswith(('.xlsx', '.xls')):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, output_folder)
                            zipf.write(file_path, arcname)
                            logger.info(f"✓ Đã thêm file Excel: {arcname}")
                
                # Thêm thư mục ảnh WebP nếu có
                webp_folder = os.path.join(output_folder, "webp_images")
                if os.path.exists(webp_folder):
                    for root, dirs, files in os.walk(webp_folder):
                        for file in files:
                            if file.endswith(('.webp', '.jpg', '.jpeg', '.png')):
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, output_folder)
                                zipf.write(file_path, arcname)
                                logger.info(f"✓ Đã thêm ảnh: {arcname}")
                
                # Thêm file README.txt với thông tin về kết quả
                readme_content = f"""BAA Qlight Crawler - Kết quả cào dữ liệu
===============================================

Ngày tạo: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Thư mục nguồn: {output_folder}

Nội dung file:
- Các file Excel chứa dữ liệu sản phẩm theo series
- Thư mục webp_images chứa ảnh sản phẩm đã chuyển đổi sang WebP
- File tổng hợp và báo cáo

Cách sử dụng:
1. Giải nén file ZIP này
2. Mở các file Excel để xem dữ liệu sản phẩm
3. Xem ảnh sản phẩm trong thư mục webp_images

Lưu ý: Tất cả ảnh đã được tối ưu hóa và chuyển đổi sang định dạng WebP để tiết kiệm dung lượng.

Crawler được tạo bởi BAA Qlight Crawler v3.0
"""
                
                zipf.writestr("README.txt", readme_content)
                logger.info("✓ Đã thêm file README.txt")
            
            # Kiểm tra kích thước file ZIP
            zip_size = os.path.getsize(zip_path)
            zip_size_mb = zip_size / (1024 * 1024)
            
            result = {
                'success': True,
                'zip_path': zip_path,
                'zip_filename': zip_filename,
                'zip_size': zip_size,
                'zip_size_mb': round(zip_size_mb, 2),
                'message': f'Đã tạo file ZIP thành công: {zip_filename} ({zip_size_mb:.2f} MB)'
            }
            
            logger.info(f"✅ {result['message']}")
            return result
            
        except Exception as e:
            error_msg = f"Lỗi tạo file ZIP: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return {
                'success': False,
                'error': str(e),
                'message': error_msg
            }

    def crawl_categories(self, category_urls, output_folder=None, max_workers=10, limit_series_per_category=None):
        """
        Cào nhiều danh mục: tự lấy series từng danh mục rồi cào lần lượt, lưu theo cấu trúc
        output_root/<CategorySlug>/<SeriesName>/...
        """
        try:
            if not isinstance(category_urls, list) or not category_urls:
                return {'success': False, 'message': 'Danh sách danh mục trống'}

            base_output = output_folder or os.path.join(os.getcwd(), 'output_baa_qlight_selected')
            os.makedirs(base_output, exist_ok=True)

            all_results = []
            for cat_url in category_urls:
                try:
                    # Tạo slug danh mục từ URL
                    parsed = urlparse(cat_url)
                    slug = parsed.path.strip('/').split('/')[-1] or 'category'
                    cat_output = os.path.join(base_output, slug)
                    os.makedirs(cat_output, exist_ok=True)

                    # Lấy series theo danh mục
                    series_list = self.get_series_from_category(cat_url) or []
                    if isinstance(limit_series_per_category, int) and limit_series_per_category > 0:
                        series_list = series_list[:limit_series_per_category]

                    # Cào từng series
                    for idx, s in enumerate(series_list, start=1):
                        series_name = s.get('name') or f'Series_{idx}'
                        series_url = s.get('url')
                        if not series_url:
                            continue
                        safe_series = re.sub(r'[<>:"/\\|?*]+', '_', series_name)
                        series_output = os.path.join(cat_output, safe_series)
                        os.makedirs(series_output, exist_ok=True)
                        res = self.crawl_specific_series(series_url, series_name, series_output, create_zip=False)
                        all_results.append(res)
                except Exception as e:
                    logger.error(f"Lỗi xử lý danh mục {cat_url}: {str(e)}")
                    all_results.append({'success': False, 'category': cat_url, 'error': str(e)})

            # Tạo ZIP tổng
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            zip_filename = f"BAA_Qlight_Selected_{timestamp}.zip"
            zip_path = os.path.join(base_output, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(base_output):
                    for file in files:
                        if file == zip_filename:
                            continue
                        fpath = os.path.join(root, file)
                        arcname = os.path.relpath(fpath, base_output)
                        zipf.write(fpath, arcname)

            return {
                'success': True,
                'results': all_results,
                'base_output': base_output,
                'zip_filename': zip_filename,
                'zip_path': zip_path
            }
        except Exception as e:
            logger.error(f"Lỗi crawl nhiều danh mục: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def get_all_series_list(self):
        """
        Lấy danh sách tất cả series từ widget trên trang web
        
        Returns:
            list: Danh sách series với thông tin tên, URL và số lượng sản phẩm
        """
        try:
            logger.info("🔍 Đang lấy danh sách tất cả series...")
            
            # Lấy HTML từ trang chính
            html = get_html_content(self.base_url)
            if not html:
                logger.error("❌ Không thể tải trang chính")
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Tìm widget series theo cấu trúc HTML đã cung cấp
            series_widget = soup.select_one('div.widget ul.widget_Items[data-id="Seri"]')
            if not series_widget:
                logger.warning("⚠️ Không tìm thấy widget series")
                return []
            
            series_list = []
            
            # Tìm tất cả các item series
            series_items = series_widget.select('li.widget_Item[data-input="Seri"]')
            
            for item in series_items:
                try:
                    # Lấy link series
                    link = item.select_one('a')
                    if not link:
                        continue
                    
                    href = link.get('href', '')
                    if not href:
                        continue
                    
                    # Lấy tên series
                    label = item.select_one('span.itemLabel')
                    series_name = label.get_text(strip=True) if label else ''
                    
                    # Lấy số lượng sản phẩm
                    count_span = item.select_one('span.count')
                    count_text = count_span.get_text(strip=True) if count_span else '[0]'
                    # Trích xuất số từ text [123]
                    count_match = re.search(r'\[(\d+)\]', count_text)
                    product_count = int(count_match.group(1)) if count_match else 0
                    
                    # Tạo URL đầy đủ
                    full_url = urljoin(self.base_url, href)
                    
                    series_info = {
                        'name': series_name,
                        'url': full_url,
                        'product_count': product_count,
                        'original_href': href
                    }
                    
                    series_list.append(series_info)
                    
                except Exception as e:
                    logger.error(f"❌ Lỗi xử lý series item: {str(e)}")
                    continue
            
            # Sắp xếp theo số lượng sản phẩm giảm dần
            series_list.sort(key=lambda x: x['product_count'], reverse=True)
            
            logger.info(f"✅ Tìm thấy {len(series_list)} series")
            return series_list
            
        except Exception as e:
            logger.error(f"❌ Lỗi khi lấy danh sách series: {str(e)}")
            return []
    
    def crawl_specific_series(self, series_url, series_name, output_folder=None, create_zip=True):
        """
        Cào dữ liệu cho một series cụ thể
        
        Args:
            series_url (str): URL của series
            series_name (str): Tên series
            output_folder (str): Thư mục lưu kết quả
            
        Returns:
            dict: Kết quả cào dữ liệu cho series
        """
        if not output_folder:
            output_folder = os.path.join(os.getcwd(), f"output_baa_qlight_{series_name}")
        
        os.makedirs(output_folder, exist_ok=True)
        
        start_time = time.time()
        logger.info(f"🚀 Bắt đầu cào dữ liệu series: {series_name}")
        
        try:
            # Bước 1: Lấy danh sách sản phẩm từ series
            logger.info(f"📋 Đang lấy danh sách sản phẩm từ series {series_name}...")
            product_urls = self.get_products_from_series(series_url)
            logger.info(f"✓ Tìm thấy {len(product_urls)} sản phẩm từ series {series_name}")
            
            if not product_urls:
                return {
                    'success': False,
                    'error': 'Không tìm thấy sản phẩm nào trong series',
                    'message': f"Series {series_name} không có sản phẩm"
                }
            
            # Bước 2: Cào thông tin sản phẩm đa luồng
            logger.info(f"🔄 Đang cào thông tin sản phẩm từ series {series_name} (đa luồng)...")
            product_info_list = self.crawl_products_multithread(product_urls)
            logger.info(f"✓ Đã cào thông tin {len(product_info_list)} sản phẩm")
            
            # Bước 3: Chuyển đổi ảnh sang WebP
            logger.info(f"🖼️ Đang chuyển đổi ảnh sang WebP cho series {series_name}...")
            product_info_list = self.convert_images_to_webp(product_info_list, output_folder)
            logger.info("✓ Hoàn thành chuyển đổi ảnh WebP")
            
            # Bước 4: Tạo file Excel cho series
            logger.info(f"📊 Đang tạo file Excel cho series {series_name}...")
            excel_files = self.create_excel_by_series(product_info_list, output_folder, default_series_name=series_name)
            logger.info(f"✓ Đã tạo {len(excel_files)} file Excel")
            
            # Bước 5: Tạo file tổng hợp cho series
            logger.info(f"📋 Đang tạo file tổng hợp cho series {series_name}...")
            summary_df = pd.DataFrame(product_info_list)
            summary_path = os.path.join(output_folder, f"BAA_Qlight_{series_name}_Summary.xlsx")
            summary_df.to_excel(summary_path, index=False)
            
            # Bước 6: Tạo file ZIP để tải xuống (tùy chọn)
            zip_result = {'success': False}
            if create_zip:
                logger.info(f"📦 Đang tạo file ZIP cho series {series_name}...")
                zip_result = self.create_download_zip(output_folder)
            
            end_time = time.time()
            duration = end_time - start_time
            
            result = {
                'success': True,
                'series_name': series_name,
                'series_url': series_url,
                'total_products': len(product_info_list),
                'excel_files': excel_files,
                'summary_file': summary_path,
                'output_folder': output_folder,
                'zip_file': zip_result.get('zip_path') if zip_result.get('success') else None,
                'zip_filename': zip_result.get('zip_filename') if zip_result.get('success') else None,
                'zip_size_mb': zip_result.get('zip_size_mb') if zip_result.get('success') else None,
                'duration': duration,
                'message': f"Hoàn thành cào dữ liệu {len(product_info_list)} sản phẩm từ series {series_name} trong {duration:.2f} giây"
            }
            
            logger.info(f"✅ {result['message']}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Lỗi trong quá trình cào dữ liệu series {series_name}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'message': f"Lỗi cào series {series_name}: {str(e)}"
            }
    
    def crawl_baa_qlight(self, output_folder=None):
        """
        Hàm chính để cào dữ liệu BAA Qlight (tất cả series)
        
        Args:
            output_folder (str): Thư mục lưu kết quả
            
        Returns:
            dict: Kết quả cào dữ liệu
        """
        if not output_folder:
            output_folder = os.path.join(os.getcwd(), "output_baa_qlight")
        
        os.makedirs(output_folder, exist_ok=True)
        
        start_time = time.time()
        logger.info("🚀 Bắt đầu cào dữ liệu BAA Qlight...")
        
        try:
            # Bước 1: Lấy danh sách sản phẩm từ trang chính
            logger.info("📋 Đang lấy danh sách sản phẩm từ trang chính...")
            product_urls = extract_product_urls(self.base_url)
            logger.info(f"✓ Tìm thấy {len(product_urls)} sản phẩm từ trang chính")
            
            # Bước 2: Thu thập thông tin series từ các sản phẩm
            logger.info("🔍 Đang thu thập thông tin series...")
            series_info = {}
            series_products = {}
            
            # Lấy thông tin series từ một số sản phẩm đầu tiên để tìm các series
            sample_products = product_urls[:min(10, len(product_urls))]
            for url in sample_products:
                series_data = self.extract_series_info(url)
                if series_data and series_data['name'] not in series_info:
                    series_info[series_data['name']] = series_data
                    logger.info(f"✓ Tìm thấy series: {series_data['name']}")
            
            # Bước 3: Lấy sản phẩm từ từng series
            logger.info("📦 Đang lấy sản phẩm từ các series...")
            all_product_urls = set(product_urls)  # Bắt đầu với sản phẩm từ trang chính
            
            for series_name, series_data in series_info.items():
                series_products_list = self.get_products_from_series(series_data['url'])
                series_products[series_name] = series_products_list
                all_product_urls.update(series_products_list)
                logger.info(f"✓ Series '{series_name}': {len(series_products_list)} sản phẩm")
            
            # Chuyển về list và loại bỏ trùng lặp
            final_product_urls = list(all_product_urls)
            logger.info(f"✓ Tổng cộng {len(final_product_urls)} sản phẩm unique")
            
            # Bước 4: Cào thông tin sản phẩm đa luồng
            logger.info("🔄 Đang cào thông tin sản phẩm (đa luồng)...")
            product_info_list = self.crawl_products_multithread(final_product_urls)
            logger.info(f"✓ Đã cào thông tin {len(product_info_list)} sản phẩm")
            
            # Bước 5: Chuyển đổi ảnh sang WebP
            logger.info("🖼️ Đang chuyển đổi ảnh sang WebP...")
            product_info_list = self.convert_images_to_webp(product_info_list, output_folder)
            logger.info("✓ Hoàn thành chuyển đổi ảnh WebP")
            
            # Bước 6: Tạo file Excel theo series
            logger.info("📊 Đang tạo file Excel theo series...")
            excel_files = self.create_excel_by_series(product_info_list, output_folder)
            logger.info(f"✓ Đã tạo {len(excel_files)} file Excel")
            
            # Bước 7: Tạo file tổng hợp
            logger.info("📋 Đang tạo file tổng hợp...")
            summary_df = pd.DataFrame(product_info_list)
            summary_path = os.path.join(output_folder, "BAA_Qlight_Summary.xlsx")
            summary_df.to_excel(summary_path, index=False)
            
            # Bước 8: Tạo báo cáo series
            logger.info("📈 Đang tạo báo cáo series...")
            series_report = []
            for series_name, products in series_products.items():
                series_report.append({
                    'Series': series_name,
                    'Số sản phẩm': len(products),
                    'URL Series': series_info.get(series_name, {}).get('url', '')
                })
            
            series_df = pd.DataFrame(series_report)
            series_report_path = os.path.join(output_folder, "BAA_Qlight_Series_Report.xlsx")
            series_df.to_excel(series_report_path, index=False)
            
            # Bước 9: Tạo file ZIP để tải xuống
            logger.info("📦 Đang tạo file ZIP tổng hợp...")
            zip_result = self.create_download_zip(output_folder)
            
            end_time = time.time()
            duration = end_time - start_time
            
            result = {
                'success': True,
                'total_products': len(product_info_list),
                'total_series': len(series_info),
                'excel_files': excel_files,
                'summary_file': summary_path,
                'series_report': series_report_path,
                'output_folder': output_folder,
                'zip_file': zip_result.get('zip_path') if zip_result['success'] else None,
                'zip_filename': zip_result.get('zip_filename') if zip_result['success'] else None,
                'zip_size_mb': zip_result.get('zip_size_mb') if zip_result['success'] else None,
                'duration': duration,
                'series_info': series_info,
                'message': f"Hoàn thành cào dữ liệu {len(product_info_list)} sản phẩm từ {len(series_info)} series trong {duration:.2f} giây"
            }
            
            logger.info(f"✅ {result['message']}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Lỗi trong quá trình cào dữ liệu: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'message': f"Lỗi: {str(e)}"
            }

# Hàm tiện ích để sử dụng từ bên ngoài
def crawl_baa_qlight(output_folder=None, max_workers=10):
    """
    Hàm tiện ích để cào dữ liệu BAA Qlight (tất cả series)
    
    Args:
        output_folder (str): Thư mục lưu kết quả
        max_workers (int): Số luồng tối đa
        
    Returns:
        dict: Kết quả cào dữ liệu
    """
    crawler = BAAQlightCrawler(max_workers=max_workers)
    return crawler.crawl_baa_qlight(output_folder)

def get_all_series_list(max_workers=5):
    """
    Hàm tiện ích để lấy danh sách tất cả series
    
    Args:
        max_workers (int): Số luồng tối đa
        
    Returns:
        list: Danh sách series với thông tin tên, URL và số lượng sản phẩm
    """
    crawler = BAAQlightCrawler(max_workers=max_workers)
    return crawler.get_all_series_list()

def crawl_specific_series(series_url, series_name, output_folder=None, max_workers=10, create_zip=True):
    """
    Hàm tiện ích để cào dữ liệu cho một series cụ thể
    
    Args:
        series_url (str): URL của series
        series_name (str): Tên series
        output_folder (str): Thư mục lưu kết quả
        max_workers (int): Số luồng tối đa
        
    Returns:
        dict: Kết quả cào dữ liệu cho series
    """
    crawler = BAAQlightCrawler(max_workers=max_workers)
    return crawler.crawl_specific_series(series_url, series_name, output_folder, create_zip=create_zip)

def get_qlight_categories(max_workers=5):
    """
    Hàm tiện ích: Lấy danh sách Category từ trang danh mục gốc QLIGHT
    Returns: list[{ name, url, original_href }]
    """
    crawler = BAAQlightCrawler(max_workers=max_workers)
    return crawler.get_categories_from_filter_widget()

def get_series_by_category(category_url, max_workers=5):
    """
    Hàm tiện ích: Lấy danh sách Series theo URL danh mục đã chọn
    """
    crawler = BAAQlightCrawler(max_workers=max_workers)
    return crawler.get_series_from_category(category_url)

def crawl_categories(category_urls, output_folder=None, max_workers=10, limit_series_per_category=None):
    crawler = BAAQlightCrawler(max_workers=max_workers)
    return crawler.crawl_categories(category_urls, output_folder, max_workers, limit_series_per_category)

def crawl_selected_series(series_list, output_folder=None, max_workers=10):
    """
    Hàm tiện ích: Cào dữ liệu theo danh sách series được chọn.
    series_list: list[{ name, url }]
    """
    crawler = BAAQlightCrawler(max_workers=max_workers)
    results = []
    # Tạo thư mục gốc để chứa các series đã chọn
    import re, os
    base_output = output_folder or os.path.join(os.getcwd(), 'output_baa_qlight_selected')
    os.makedirs(base_output, exist_ok=True)
    for idx, s in enumerate(series_list, start=1):
        name = s.get('name') or f'Series_{idx}'
        url = s.get('url') or ''
        if not name or not url:
            continue
        safe_name = re.sub(r'[<>:"/\\|?*]+', '_', name)
        series_output = os.path.join(base_output, safe_name)
        os.makedirs(series_output, exist_ok=True)
        # Không tạo ZIP riêng từng series để tránh đè, ZIP tổng hợp sẽ tạo ngoài
        result = crawler.crawl_specific_series(url, name, series_output, create_zip=False)
        results.append(result)
    return results

def create_download_zip(output_folder, zip_filename=None):
    """
    Hàm tiện ích: Tạo file ZIP chứa tất cả kết quả cào dữ liệu
    
    Args:
        output_folder (str): Thư mục chứa kết quả cào dữ liệu
        zip_filename (str): Tên file ZIP (tùy chọn)
        
    Returns:
        dict: Kết quả tạo file ZIP
    """
    crawler = BAAQlightCrawler()
    return crawler.create_download_zip(output_folder, zip_filename)

def test_series_extraction():
    """
    Hàm test để kiểm tra việc trích xuất series
    """
    crawler = BAAQlightCrawler()
    test_url = "https://baa.vn/vn/den-thap-led-sang-tinh-chop-nhay-d45mm-qlight-st45l-and-st45ml-series_4779/"
    
    print("🔍 Testing series extraction...")
    series_info = crawler.extract_series_info(test_url)
    if series_info:
        print(f"✓ Tìm thấy series: {series_info['name']}")
        print(f"  URL: {series_info['url']}")
    else:
        print("❌ Không tìm thấy thông tin series")
    
    return series_info

def test_category_series_extraction():
    """
    Hàm test để kiểm tra việc lấy series từ danh mục
    """
    crawler = BAAQlightCrawler()
    test_category_url = "https://baa.vn/vn/Category/den-thap-den-tang-qlight_279_782/"
    
    print("🔍 Testing category series extraction...")
    print(f"URL: {test_category_url}")
    
    series_list = crawler.get_series_from_category(test_category_url)
    if series_list:
        print(f"✓ Tìm thấy {len(series_list)} series:")
        for i, series in enumerate(series_list[:5], 1):  # Chỉ hiển thị 5 series đầu
            print(f"  {i}. {series['name']} - {series['url']}")
        if len(series_list) > 5:
            print(f"  ... và {len(series_list) - 5} series khác")
    else:
        print("❌ Không tìm thấy series nào")
    
    return series_list

def test_product_extraction():
    """
    Hàm test để kiểm tra việc trích xuất sản phẩm
    """
    crawler = BAAQlightCrawler()
    test_url = "https://baa.vn/vn/den-thap-led-sang-tinh-chop-nhay-d45mm-qlight-st45l-and-st45ml-series_4779/"
    
    print("🔍 Testing product extraction...")
    product_info = crawler.crawl_product_info(test_url, 1)
    if product_info:
        print(f"✓ Tìm thấy sản phẩm: {product_info.get('Tên sản phẩm', 'Unknown')}")
        print(f"  Mã: {product_info.get('Mã sản phẩm', 'Unknown')}")
        print(f"  Series: {product_info.get('Series', 'Unknown')}")
    else:
        print("❌ Không tìm thấy thông tin sản phẩm")
    
    return product_info

if __name__ == "__main__":
    print("🚀 BAA Qlight Crawler - Test Mode")
    print("=" * 50)
    
    # Test 1: Trích xuất series từ danh mục
    print("\n1. Testing Category Series Extraction:")
    category_series_result = test_category_series_extraction()
    
    # Test 2: Trích xuất series từ sản phẩm
    print("\n2. Testing Product Series Extraction:")
    series_result = test_series_extraction()
    
    # Test 3: Trích xuất sản phẩm
    print("\n3. Testing Product Extraction:")
    product_result = test_product_extraction()
    
    # Test 4: Chạy crawler đầy đủ (tùy chọn)
    print("\n4. Full Crawler Test (tùy chọn):")
    response = input("Bạn có muốn chạy crawler đầy đủ không? (y/n): ")
    if response.lower() == 'y':
        print("🔄 Bắt đầu crawler đầy đủ...")
        result = crawl_baa_qlight(max_workers=5)  # Giảm số luồng để test
        print(f"✅ Kết quả: {result}")
    else:
        print("⏭️ Bỏ qua test crawler đầy đủ")
    
    print("\n✅ Test hoàn thành!")

# Socket.IO handlers để tích hợp UI hiện có
try:
    from flask import request

    @socketio.on('qlight:get_categories')
    def socket_get_categories(data=None):
        try:
            data = data or {}
            q = (data.get('q') or '').strip().lower()
            limit = data.get('limit')
            categories = get_qlight_categories()
            if q:
                categories = [c for c in categories if q in (c.get('name','').lower())]
            if isinstance(limit, int) and limit > 0:
                categories = categories[:limit]
            socketio.emit('qlight:categories', {
                'items': categories,
                'source': 'filter_widget'
            })
        except Exception as e:
            socketio.emit('qlight:error', {'message': f'Lỗi lấy Category: {str(e)}'})

    @socketio.on('qlight:get_series_by_category')
    def socket_get_series_by_category(data=None):
        try:
            data = data or {}
            category_url = data.get('category_url') or ''
            q = (data.get('q') or '').strip().lower()
            limit = data.get('limit')
            if not category_url:
                socketio.emit('qlight:error', {'message': 'Thiếu category_url'})
                return
            series = get_series_by_category(category_url)
            if q:
                series = [s for s in series if q in (s.get('name','').lower())]
            if isinstance(limit, int) and limit > 0:
                series = series[:limit]
            socketio.emit('qlight:series', {
                'category_url': category_url,
                'items': series
            })
        except Exception as e:
            socketio.emit('qlight:error', {'message': f'Lỗi lấy Series: {str(e)}'})

    @socketio.on('qlight:crawl_selected_series')
    def socket_crawl_selected_series(data=None):
        try:
            data = data or {}
            series_list = data.get('series_list', [])
            output_folder = data.get('output_folder')
            max_workers = data.get('max_workers', 10)
            
            # Tạo thư mục gốc chứa tất cả series được chọn
            import os, re
            base_output = output_folder or os.path.join(os.getcwd(), 'output_baa_qlight_selected')
            os.makedirs(base_output, exist_ok=True)

            results = []
            for s in series_list:
                series_name = s.get('name')
                series_url = s.get('url')
                if not series_name or not series_url:
                    continue

                safe_name = re.sub(r'[<>:"/\\|?*]+', '_', series_name)
                series_output = os.path.join(base_output, safe_name)
                os.makedirs(series_output, exist_ok=True)

                socketio.emit('qlight:crawl:series:start', {
                    'name': series_name,
                    'url': series_url,
                    'output_folder': series_output
                })
                # Không nén ZIP riêng từng series
                res = crawl_specific_series(series_url, series_name, output_folder=series_output, max_workers=max_workers, create_zip=False)
                results.append(res)
                socketio.emit('qlight:crawl:series:done', res)

            # Trả về thư mục gốc để client có thể yêu cầu nén tổng
            # Tạo ZIP tổng hợp ngay tại server sau khi xong
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                zip_filename = f"BAA_Qlight_Selected_{timestamp}.zip"
                zip_path = os.path.join(base_output, zip_filename)
                import zipfile
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(base_output):
                        for file in files:
                            if file == zip_filename:
                                continue
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, base_output)
                            zipf.write(file_path, arcname)
                socketio.emit('qlight:crawl:done', {
                    'results': results,
                    'base_output': base_output,
                    'zip_filename': zip_filename,
                    'zip_path': zip_path
                })
            except Exception as e:
                socketio.emit('qlight:crawl:done', {'results': results, 'base_output': base_output, 'zip_error': str(e)})
        except Exception as e:
            socketio.emit('qlight:error', {'message': f'Lỗi cào series đã chọn: {str(e)}'})

    @socketio.on('qlight:create_download_zip')
    def socket_create_download_zip(data=None):
        try:
            data = data or {}
            output_folder = data.get('output_folder')
            zip_filename = data.get('zip_filename')
            
            if not output_folder:
                socketio.emit('qlight:error', {'message': 'Thiếu output_folder'})
                return
            
            socketio.emit('qlight:zip:creating', {'message': 'Đang tạo file ZIP...'})
            result = create_download_zip(output_folder, zip_filename)
            
            if result['success']:
                socketio.emit('qlight:zip:created', {
                    'zip_filename': result['zip_filename'],
                    'zip_size_mb': result['zip_size_mb'],
                    'zip_path': result['zip_path'],
                    'message': result['message']
                })
            else:
                socketio.emit('qlight:error', {'message': result['message']})
                
        except Exception as e:
            socketio.emit('qlight:error', {'message': f'Lỗi tạo file ZIP: {str(e)}'})
    
    @socketio.on('qlight:crawl_categories')
    def socket_crawl_categories(data=None):
        try:
            data = data or {}
            category_urls = data.get('category_urls', []) or []
            output_folder = data.get('output_folder')
            max_workers = data.get('max_workers', 10)
            limit_series = data.get('limit_series_per_category')
            result = crawl_categories(category_urls, output_folder=output_folder, max_workers=max_workers, limit_series_per_category=limit_series)
            socketio.emit('qlight:crawl:categories:done', result)
        except Exception as e:
            socketio.emit('qlight:error', {'message': f'Lỗi cào nhiều danh mục: {str(e)}'})
except Exception:
    # Nếu môi trường không có socketio/app, bỏ qua để tránh lỗi khi chạy script đơn lẻ
    pass
