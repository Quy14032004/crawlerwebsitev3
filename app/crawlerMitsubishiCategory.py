"""
Mitsubishi Electric Product Crawler - Category Version
Cào từ link danh mục và tìm tất cả sản phẩm trong danh mục
Phiên bản: 7.0 - Category Crawler
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
import requests

# Import existing modules
try:
    from app import socketio
except ImportError:
    socketio = None

from app.crawler import get_html_content
from app.webp_converter import WebPConverter

logger = logging.getLogger(__name__)

class MitsubishiCrawlerCategory:
    """Mitsubishi Electric Crawler - Cào từ danh mục và tìm tất cả sản phẩm"""
    
    def __init__(self, max_workers=6):
        self.max_workers = max_workers
        self.base_url = "https://emea.mitsubishielectric.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
    def crawl_category(self, category_url):
        """
        Cào từ link danh mục và tìm tất cả sản phẩm
        """
        try:
            logger.info(f"Bắt đầu cào danh mục: {category_url}")
            
            # Tạo thư mục output
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder = f"Mitsubishi_{timestamp}"
            output_dir = os.path.join('app', 'downloads', 'output_mitsubishi', output_folder)
            images_dir = os.path.join(output_dir, 'images')
            
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(images_dir, exist_ok=True)
            
            # 1. Detect pagination để biết có bao nhiêu trang
            max_pages = self._detect_pagination(category_url)
            logger.info(f"Phát hiện {max_pages} trang trong danh mục")
            
            # 2. Lấy tất cả product URLs từ tất cả các trang
            all_product_urls = []
            
            for page in range(1, max_pages + 1):
                logger.info(f"Đang xử lý trang {page}/{max_pages}")
                
                if page == 1:
                    page_url = category_url
                else:
                    page_url = f"{category_url}?page={page}"
                
                # Lấy product URLs từ trang này
                page_products = self._extract_product_urls_from_page(page_url)
                all_product_urls.extend(page_products)
                logger.info(f"Trang {page} có {len(page_products)} sản phẩm")
            
            # Loại bỏ duplicate
            unique_product_urls = list(set(all_product_urls))
            logger.info(f"Tổng cộng tìm thấy {len(unique_product_urls)} sản phẩm unique")
            
            if not unique_product_urls:
                return {
                    'success': False,
                    'message': 'Không tìm thấy sản phẩm nào trong danh mục!'
                }
            
            # 3. Cào chi tiết từng sản phẩm
            products_data = []
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_url = {
                    executor.submit(self._extract_product_details, url, images_dir): url 
                    for url in unique_product_urls
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
            
            # 4. Tạo Excel report
            if products_data:
                excel_path = self._create_excel_report(products_data, output_dir)
                
                return {
                    'success': True,
                    'total_products': len(products_data),
                    'output_folder': output_folder,
                    'excel_file': os.path.basename(excel_path),
                    'message': f'Hoàn thành! Cào được {len(products_data)} sản phẩm từ danh mục'
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
    
    def _detect_pagination(self, category_url):
        """Phát hiện số trang từ pagination"""
        try:
            html = get_html_content(category_url)
            if not html:
                return 1
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Tìm pagination element
            pagination = soup.find('ul', class_='Pagination_list__nT87c')
            if not pagination:
                logger.info(f"Không tìm thấy pagination cho {category_url}")
                return 1
            
            max_page = 1
            
            # Tìm tất cả page links
            page_links = pagination.find_all('a', class_='Pagination_pageLink__eW_ES')
            for link in page_links:
                aria_label = link.get('aria-label', '')
                if 'Page' in aria_label:
                    try:
                        # Extract page number từ aria-label="Page X"
                        page_num = int(re.search(r'Page (\d+)', aria_label).group(1))
                        max_page = max(max_page, page_num)
                    except:
                        continue
                
                # Fallback: lấy từ text content
                try:
                    text = link.get_text(strip=True)
                    if text.isdigit():
                        page_num = int(text)
                        max_page = max(max_page, page_num)
                except:
                    continue
            
            logger.info(f"Phát hiện {max_page} trang cho {category_url}")
            return max_page
            
        except Exception as e:
            logger.error(f"Lỗi khi detect pagination cho {category_url}: {str(e)}")
            return 1
    
    def _extract_product_urls_from_page(self, page_url):
        """Lấy danh sách product URLs từ 1 trang danh mục"""
        try:
            html = get_html_content(page_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            product_urls = []
            
            logger.info(f"Đang extract product URLs từ: {page_url}")
            
            # 1. Tìm sản phẩm chính trong trang
            selectors = [
                'div.ProductList_content__cJlAf a.ProductItem_titleLink__HjeDG',
                'div.SearchResultItem_root__Q_IQM a.ProductItem_titleLink__HjeDG',
                'a.ProductItem_titleLink__HjeDG',
                'a[class*="ProductItem_titleLink"]',
                'div.ProductItem_root__roXdu a[href*=".html"]',
                'div.SearchResultItem_root__Q_IQM a[href*=".html"]',
                'a[href*="/fa/products/"][href$=".html"]',
                'a[href*="mr-"][href$=".html"]',
                'a[href*="mitsubishielectric.com"][href$=".html"]',
            ]
            
            for selector in selectors:
                try:
                    links = soup.select(selector)
                    logger.info(f"Selector '{selector}' tìm thấy {len(links)} links")
                    
                    for link in links:
                        href = link.get('href', '')
                        if not href:
                            continue
                            
                        # Chuẩn hóa URL
                        if href.startswith('//'):
                            full_url = 'https:' + href
                        elif href.startswith('/'):
                            full_url = urljoin(self.base_url, href)
                        elif href.startswith('http'):
                            full_url = href
                        else:
                            continue
                        
                        # Kiểm tra xem có phải product URL hợp lệ không
                        if self._is_product_url(full_url):
                            if full_url not in product_urls:
                                product_urls.append(full_url)
                                logger.info(f"✓ Thêm product URL: {full_url}")
                except Exception as e:
                    logger.warning(f"Lỗi với selector '{selector}': {str(e)}")
                    continue
            
            # 2. Tìm sản phẩm trong các tab khác (COMPATIBLE PRODUCTS, SERVICE PARTS)
            tab_contents = soup.find_all('div', class_='Tabs_childWrapper__vpjRP')
            logger.info(f"Tìm thấy {len(tab_contents)} tab contents")
            
            for tab_content in tab_contents:
                # Kiểm tra nếu tab không bị ẩn
                if 'Tabs_is-hidden__ZGvUT' not in tab_content.get('class', []):
                    logger.info("Tìm thấy tab không bị ẩn")
                    # Tìm sản phẩm trong tab này
                    tab_products = tab_content.find_all('a', class_='ProductItem_titleLink__HjeDG')
                    logger.info(f"Tab có {len(tab_products)} sản phẩm")
                    
                    for link in tab_products:
                        href = link.get('href', '')
                        if href:
                            if href.startswith('/'):
                                full_url = urljoin(self.base_url, href)
                            elif href.startswith('http'):
                                full_url = href
                            else:
                                continue
                            
                            if self._is_product_url(full_url):
                                product_urls.append(full_url)
                                logger.info(f"✓ Thêm product URL từ tab: {full_url}")
            
            # 3. Fallback: Tìm tất cả link .html và kiểm tra từng cái
            if not product_urls:
                logger.info("Thử fallback: tìm tất cả link .html...")
                all_links = soup.find_all('a', href=True)
                
                for link in all_links:
                    href = link.get('href', '')
                    if not href or not href.endswith('.html'):
                        continue
                    
                    # Bỏ qua các URL không phải sản phẩm
                    if any(skip in href.lower() for skip in [
                        'index.html', 'contact.html', 'about.html', 'support.html',
                        'download.html', 'manual.html', 'catalog.html'
                    ]):
                        continue
                    
                    if href.startswith('/'):
                        full_url = urljoin(self.base_url, href)
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        continue
                    
                    # Kiểm tra nếu có thể là sản phẩm
                    if ('/fa/products/' in full_url.lower() or 
                        any(model in full_url.lower() for model in ['mr-', 'mf-', 'servo', 'amplifier'])):
                        product_urls.append(full_url)
                        logger.info(f"✓ Thêm product URL (fallback): {full_url}")
            
            # Loại bỏ duplicate
            product_urls = list(set(product_urls))
            
            logger.info(f"Tìm thấy {len(product_urls)} product URLs trong {page_url}")
            return product_urls
            
        except Exception as e:
            logger.error(f"Lỗi khi extract product URLs từ {page_url}: {str(e)}")
            return []
    
    def _is_product_url(self, url):
        """Kiểm tra URL chi tiết sản phẩm hợp lệ"""
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(self.base_url)
            # Chỉ chấp nhận cùng domain
            if parsed.netloc and base_parsed.netloc and parsed.netloc != base_parsed.netloc:
                return False
            
            # Loại bỏ các URL danh mục (không phải sản phẩm cụ thể)
            lower_url = url.lower()
            if any(skip in lower_url for skip in [
                '/servo-amplifier',  # Danh mục servo-amplifier
                '/rotary-servo-motor',  # Danh mục rotary-servo-motor
                '/servo',  # Danh mục servo chung
                '/drv/servo',  # Danh mục servo chung
                '/mr_j5',  # Danh mục mr_j5
                '/mr_jet',  # Danh mục mr_jet
                '/mr_j4',  # Danh mục mr_j4
                '/mr_je',  # Danh mục mr_je
            ]):
                return False
            
            # Chấp nhận các loại URL sản phẩm cụ thể:
            # 1. Có .html và chứa model code MR- cụ thể
            if url.endswith('.html') and any(model in url.lower() for model in [
                'mr-j5-', 'mr-jet-', 'mr-j4-', 'mr-je-', 'mr-configurator', 'mr-d05', 'mr-pwr', 'mf-3f'
            ]):
                return True
            # 2. Có /productdetails/
            if '/productdetails/' in url.lower():
                return True
            # 3. URL có pattern sản phẩm cụ thể với model code
            if any(model in url.lower() for model in [
                'mr-j5-', 'mr-jet-', 'mr-j4-', 'mr-je-', 'mr-configurator', 'mr-d05', 'mr-pwr', 'mf-3f'
            ]) and url.endswith('.html'):
                return True
            # 4. URL có chứa /fa/products/ và có .html (có thể là sản phẩm)
            if '/fa/products/' in url.lower() and url.endswith('.html'):
                return True
            # 5. URL có chứa từ khóa sản phẩm và có .html
            if url.endswith('.html') and any(keyword in url.lower() for keyword in [
                'servo', 'amplifier', 'motor', 'controller', 'drive'
            ]):
                return True
                
            return False
        except:
            return False
    
    def _extract_product_details(self, product_url, images_dir):
        """Trích xuất chi tiết sản phẩm"""
        try:
            logger.info(f"Đang cào sản phẩm: {product_url}")
            
            # Lấy HTML
            html = get_html_content(product_url)
            if not html:
                logger.warning(f"Không thể tải HTML từ {product_url}")
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Lấy mã sản phẩm
            product_code = self._extract_product_code(soup, product_url)
            if not product_code:
                logger.warning(f"Không tìm thấy mã sản phẩm trong {product_url}")
                return None
            
            # Lấy tên sản phẩm
            product_name = self._extract_product_name(soup, product_url)
            
            # Lấy ảnh sản phẩm
            image_path = self._extract_and_download_image(soup, product_code, images_dir)
            
            # Lấy thông số kỹ thuật
            specs_html = self._extract_specifications(soup, product_code, product_name)
            
            return {
                'code': product_code,
                'name': product_name,
                'image_path': image_path,
                'specs_html': specs_html,
                'url': product_url,
                'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
                
        except Exception as e:
            logger.error(f"Lỗi khi extract product details từ {product_url}: {str(e)}")
            return None
    
    def _extract_product_code(self, soup, product_url):
        """Lấy mã sản phẩm"""
        try:
            # Tìm h2 tag chứa mã sản phẩm
            h2_tags = soup.find_all('h2')
            for h2 in h2_tags:
                text = h2.get_text(strip=True)
                if 'MR-' in text and len(text) > 5:
                    return text
            
            # Fallback: extract từ URL
            filename = product_url.split('/')[-1]
            if filename.endswith('.html'):
                filename = filename[:-5]
            if filename and len(filename) > 3:
                return filename.upper()
            
            # Fallback: tìm trong title
            title = soup.find('title')
            if title:
                title_text = title.get_text(strip=True)
                if 'MR-' in title_text:
                    match = re.search(r'MR-[A-Z0-9\-]+', title_text)
                    if match:
                        return match.group(0)
            
            return None
        except:
            return None
    
    def _extract_product_name(self, soup, product_url):
        """Lấy tên sản phẩm"""
        try:
            # Tìm description div
            desc_div = soup.find('div', class_='ProductOverview_description__CHeZm')
            if desc_div:
                return desc_div.get_text(strip=True)
            
            # Fallback: tìm các class khác
            fallback_selectors = [
                'div[class*="description"]',
                'div[class*="overview"]',
                'div[class*="title"]',
                'h1',
                'h2',
                'title'
            ]
            
            for selector in fallback_selectors:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.get_text(strip=True)
                    if text and len(text) > 10:
                        return text
            
            # Fallback: tạo từ URL
            filename = product_url.split('/')[-1]
            if filename.endswith('.html'):
                filename = filename[:-5]
            if filename and len(filename) > 3:
                return filename.replace('-', ' ').replace('_', ' ').title()
            
            return "Unknown Product"
        except:
            return "Unknown Product"
    
    def _extract_and_download_image(self, soup, product_code, images_dir):
        """Lấy và download ảnh sản phẩm"""
        try:
            # Tìm ảnh sản phẩm với nhiều selector
            img_selectors = [
                'img.Gallery_slideImg__TqcGx',
                'img.LazyImage_imageElement__JKU7j',
                'img.ProductItem_image__SDgmM',
                'img[src*="contentstack.com"]',
                'img[src*=".jpg"]',
                'img[src*=".png"]',
                'img[src*=".jpeg"]'
            ]
            
            img_element = None
            for selector in img_selectors:
                img_element = soup.select_one(selector)
                if img_element:
                    logger.info(f"Tìm thấy ảnh với selector: {selector}")
                    break
            
            if not img_element:
                logger.warning("Không tìm thấy ảnh sản phẩm")
                return None
            
            image_url = img_element.get('src')
            if not image_url:
                return None
            
            # Chuẩn hóa URL
            if image_url.startswith('//'):
                image_url = 'https:' + image_url
            elif image_url.startswith('/'):
                image_url = urljoin(self.base_url, image_url)
            
            # Kiểm tra nếu URL hợp lệ (không phải logo)
            if any(skip in image_url.lower() for skip in ['logo', 'icon', 'favicon', 'theme']):
                logger.warning(f"Bỏ qua ảnh không phải sản phẩm: {image_url}")
                return None
            
            # Download và convert sang WebP
            webp_path = os.path.join(images_dir, f"{product_code}.webp")
            
            # Sử dụng WebPConverter
            converter = WebPConverter()
            success = converter.convert_to_webp(image_url, webp_path, quality=90)
            
            if success:
                logger.info(f"✅ Đã download ảnh: {product_code}.webp")
                return f"images/{product_code}.webp"
            else:
                logger.warning(f"❌ Không thể download ảnh: {image_url}")
                return None
                
        except Exception as e:
            logger.error(f"Lỗi khi download ảnh: {str(e)}")
            return None
    
    def _extract_specifications(self, soup, product_code, product_name):
        """Lấy thông số kỹ thuật với xử lý tab TECHNICAL INFORMATION"""
        try:
            logger.info(f"Đang extract thông số kỹ thuật cho {product_code}")
            
            # 1. Tìm tab TECHNICAL INFORMATION
            tech_tab = soup.find('button', class_='Nav_link__yt4y8 Tabs_navLink__SEkZx')
            if tech_tab and 'TECHNICAL INFORMATION' in tech_tab.get_text():
                logger.info("Tìm thấy tab TECHNICAL INFORMATION")
                
                # 2. Tìm tab content (có thể bị ẩn)
                tab_contents = soup.find_all('div', class_='Tabs_childWrapper__vpjRP')
                logger.info(f"Tìm thấy {len(tab_contents)} tab contents")
                
                all_specs = []
                
                for tab_content in tab_contents:
                    # Kiểm tra nếu tab không bị ẩn
                    classes = tab_content.get('class', [])
                    if 'Tabs_is-hidden__ZGvUT' not in classes:
                        logger.info("Tìm thấy tab không bị ẩn")
                        
                        # Tìm tables trong tab này
                        tables = tab_content.find_all('table', class_='ProductDetail_charTable__m3dh8')
                        logger.info(f"Tab có {len(tables)} bảng thông số")
                        
                        for table in tables:
                            specs = self._parse_specs_table(table)
                            if specs:
                                all_specs.extend(specs)
                                logger.info(f"Parse được {len(specs)} thông số từ bảng")
                
                if all_specs:
                    logger.info(f"Tổng cộng tìm thấy {len(all_specs)} thông số kỹ thuật")
                    return self._create_specifications_table_html(product_code, product_name, all_specs)
            
            # 3. Fallback: Tìm tất cả tables có thể có
            logger.info("Fallback: Tìm tất cả tables có thể có")
            table_selectors = [
                'table.ProductDetail_charTable__m3dh8',
                'table[class*="charTable"]',
                'table[class*="ProductDetail"]',
                'table',
                'div[class*="RichTextBlock"] table',
                'div[class*="ProductDetail"] table'
            ]
            
            all_specs = []
            for selector in table_selectors:
                tables = soup.select(selector)
                if tables:
                    logger.info(f"Tìm thấy {len(tables)} bảng với selector: {selector}")
                    for table in tables:
                        specs = self._parse_specs_table(table)
                        if specs:
                            all_specs.extend(specs)
                            logger.info(f"Parse được {len(specs)} thông số từ bảng")
                    break
            
            if all_specs:
                logger.info(f"Fallback: Tổng cộng tìm thấy {len(all_specs)} thông số kỹ thuật")
                return self._create_specifications_table_html(product_code, product_name, all_specs)
            else:
                logger.warning("Không tìm thấy thông số kỹ thuật nào")
                return self._create_specifications_table_html(product_code, product_name, [])
            
        except Exception as e:
            logger.error(f"Lỗi khi extract specifications: {str(e)}")
            return self._create_specifications_table_html(product_code, product_name, [])
    
    def _parse_specs_table(self, table):
        """Parse table thông số"""
        try:
            specs = []
            rows = table.find_all('tr')
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
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
