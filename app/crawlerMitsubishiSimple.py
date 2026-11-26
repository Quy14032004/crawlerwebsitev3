"""
Mitsubishi Electric Product Crawler - Simple Version
Sử dụng requests + BeautifulSoup cho các URL sản phẩm cụ thể
Phiên bản: 5.0 - Simple & Reliable
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

# Import existing modules
try:
    from app import socketio
except ImportError:
    socketio = None

from app.crawler import get_html_content
from app.webp_converter import WebPConverter

logger = logging.getLogger(__name__)

class MitsubishiCrawlerSimple:
    """Mitsubishi Electric Crawler - Simple version cho URL sản phẩm cụ thể"""
    
    def __init__(self, max_workers=6):
        self.max_workers = max_workers
        self.base_url = "https://emea.mitsubishielectric.com"
        
    def crawl_products(self, product_urls):
        """
        Cào dữ liệu từ danh sách URL sản phẩm cụ thể
        """
        try:
            logger.info(f"Bắt đầu cào dữ liệu từ {len(product_urls)} sản phẩm")
            
            # Tạo thư mục output
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder = f"Mitsubishi_{timestamp}"
            output_dir = os.path.join('app', 'downloads', 'output_mitsubishi', output_folder)
            images_dir = os.path.join(output_dir, 'images')
            
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(images_dir, exist_ok=True)
            
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
            logger.error(f"Lỗi trong crawl_products: {str(e)}")
            return {
                'success': False,
                'message': f'Lỗi: {str(e)}'
            }
    
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
        """Lấy thông số kỹ thuật"""
        try:
            # Tìm tất cả tables với nhiều selector
            table_selectors = [
                'table.ProductDetail_charTable__m3dh8',
                'table[class*="charTable"]',
                'table[class*="ProductDetail"]',
                'table',
                'div[class*="RichTextBlock"] table',
                'div[class*="ProductDetail"] table'
            ]
            
            tables = []
            for selector in table_selectors:
                found_tables = soup.select(selector)
                if found_tables:
                    logger.info(f"Tìm thấy {len(found_tables)} bảng với selector: {selector}")
                    tables.extend(found_tables)
                    break
            
            if not tables:
                logger.warning("Không tìm thấy bảng thông số kỹ thuật")
                return self._create_specifications_table_html(product_code, product_name, [])
            
            # Parse tables
            all_specs = []
            for table in tables:
                specs = self._parse_specs_table(table)
                if specs:  # Chỉ thêm nếu có dữ liệu
                    all_specs.extend(specs)
                    logger.info(f"Parse được {len(specs)} thông số từ bảng")
            
            logger.info(f"Tổng cộng tìm thấy {len(all_specs)} thông số kỹ thuật")
            return self._create_specifications_table_html(product_code, product_name, all_specs)
            
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
