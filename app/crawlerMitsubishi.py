"""
Mitsubishi Electric Product Crawler - Cào dữ liệu sản phẩm từ website emea.mitsubishielectric.com
Phiên bản: 3.0
Tác giả: Auto-generated based on BAA crawler patterns
Ngày tạo: 2025
"""

import os
import shutil
import time
import zipfile
from datetime import datetime
from app.crawler import get_html_content
try:
    from app import socketio
except ImportError:
    socketio = None
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
import traceback
from queue import Queue
import logging
from app.webp_converter import WebPConverter

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MitsubishiCrawler:
    """Crawler cho website Mitsubishi Electric"""
    
    def __init__(self, max_workers=6):
        self.base_url = "https://emea.mitsubishielectric.com"
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
    def crawl_category(self, category_url):
        """
        Entry point - nhận 1 URL danh mục và cào tất cả sản phẩm
        """
        try:
            # Tạo output folder với timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_folder = f"app/downloads/output_mitsubishi/Mitsubishi_ngay{timestamp}"
            os.makedirs(output_folder, exist_ok=True)
            images_folder = os.path.join(output_folder, "images")
            os.makedirs(images_folder, exist_ok=True)
            
            logger.info(f"Bắt đầu cào dữ liệu từ danh mục: {category_url}")
            if socketio and hasattr(socketio, 'server') and socketio.server:
                socketio.emit('progress_update', {
                    'percent': 0, 
                    'message': f'Bắt đầu cào dữ liệu từ danh mục...'
                })
            
            # Detect pagination
            max_pages = self.detect_pagination(category_url)
            logger.info(f"Phát hiện {max_pages} trang")
            
            all_products = []
            total_pages = max_pages
            
            # Cào từng trang
            for page_num in range(1, max_pages + 1):
                try:
                    logger.info(f"Đang xử lý trang {page_num}/{total_pages}")
                    if socketio and hasattr(socketio, 'server') and socketio.server:
                        socketio.emit('progress_update', {
                            'percent': int((page_num / total_pages) * 30), 
                            'message': f'Đang xử lý trang {page_num}/{total_pages}...'
                        })
                    
                    # Tạo URL cho trang
                    if page_num == 1:
                        page_url = category_url
                    else:
                        page_url = f"{category_url}?page={page_num}"
                    
                    # Extract product URLs từ trang này
                    product_urls = self.extract_product_urls_from_page(page_url)
                    logger.info(f"Trang {page_num} có {len(product_urls)} sản phẩm")
                    
                    # Cào chi tiết từng sản phẩm
                    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        future_to_url = {
                            executor.submit(self.extract_product_details, url, images_folder): url 
                            for url in product_urls
                        }
                        for future in as_completed(future_to_url):
                            try:
                                product_data = future.result()
                                if product_data:
                                    all_products.append(product_data)
                            except Exception as e:
                                url = future_to_url[future]
                                logger.error(f"Lỗi khi cào sản phẩm {url}: {str(e)}")
                    
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý trang {page_num}: {str(e)}")
                    continue
            
            logger.info(f"Tổng cộng thu thập được {len(all_products)} sản phẩm")
            
            # Tạo Excel report
            if all_products:
                excel_path = self.create_excel_report(all_products, output_folder)
                logger.info(f"Đã tạo Excel report: {excel_path}")
                
                if socketio and hasattr(socketio, 'server') and socketio.server:
                    socketio.emit('progress_update', {
                        'percent': 100, 
                        'message': f'Hoàn thành! Thu thập được {len(all_products)} sản phẩm. File Excel: {os.path.basename(excel_path)}'
                    })
                
                return {
                    'success': True,
                    'total_products': len(all_products),
                    'output_folder': output_folder,
                    'excel_file': excel_path
                }
            else:
                logger.warning("Không thu thập được sản phẩm nào")
                return {
                    'success': False,
                    'message': 'Không thu thập được sản phẩm nào'
                }
                
        except Exception as e:
            logger.error(f"Lỗi trong crawl_category: {str(e)}")
            traceback.print_exc()
            return {
                'success': False,
                'message': f'Lỗi: {str(e)}'
            }
    
    def detect_pagination(self, category_url):
        """
        Phát hiện số trang tối đa từ pagination
        """
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
    
    def extract_product_urls_from_page(self, page_url):
        """
        Lấy danh sách product URLs từ 1 trang (bao gồm cả tabs)
        """
        try:
            html = get_html_content(page_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            product_urls = []
            
            # Debug: Log HTML structure
            logger.info(f"HTML length: {len(html)}")
            
            # 0. Thử lấy dữ liệu từ __NEXT_DATA__ (Next.js)
            try:
                next_script = soup.find('script', id='__NEXT_DATA__')
                if next_script and next_script.string:
                    import json
                    data = json.loads(next_script.string)
                    def collect_links(obj, acc):
                        if isinstance(obj, dict):
                            for v in obj.values():
                                collect_links(v, acc)
                        elif isinstance(obj, list):
                            for it in obj:
                                collect_links(it, acc)
                        elif isinstance(obj, str):
                            if obj.endswith('.html') and ('/fa/products/' in obj or obj.startswith('http')):
                                acc.add(obj)
                        return acc
                    raw_links = collect_links(data, set())
                    for href in raw_links:
                        if href.startswith('//'):
                            full_url = 'https:' + href
                        elif href.startswith('/'):
                            full_url = urljoin(self.base_url, href)
                        elif href.startswith('http'):
                            full_url = href
                        else:
                            continue
                        if self._is_product_url(full_url) and full_url not in product_urls:
                            product_urls.append(full_url)
                    if product_urls:
                        logger.info(f"NEXT_DATA tìm thấy {len(product_urls)} product URLs")
            except Exception as e:
                logger.warning(f"Lỗi parse __NEXT_DATA__: {str(e)}")

            # 1. Tìm sản phẩm chính trong trang với cấu trúc thực tế
            selectors = [
                # Cấu trúc chính từ HTML thực tế
                'div.ProductList_content__cJlAf a.ProductItem_titleLink__HjeDG',
                'div.SearchResultItem_root__Q_IQM a.ProductItem_titleLink__HjeDG',
                'a.ProductItem_titleLink__HjeDG',
                'a[class*="ProductItem_titleLink"]',
                'div.ProductItem_root__roXdu a[href*=".html"]',
                'div.SearchResultItem_root__Q_IQM a[href*=".html"]',
                'a[href*="/fa/products/"][href$=".html"]',
                'a[href*="mr-"][href$=".html"]',
                'a[href*="/productdetails/"]',
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
            
            # 2. Fallback: Tìm trong tất cả links với điều kiện nghiêm ngặt hơn
            all_links = soup.find_all('a', href=True)
            logger.info(f"Tìm kiếm fallback trong {len(all_links)} links")
            
            for link in all_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)
                
                # Chỉ tìm các link có .html và có text hợp lệ
                if (href and 
                    href.endswith('.html') and
                    text and len(text) > 3 and
                    not any(skip in href.lower() for skip in ['#', 'javascript:', 'mailto:', 'tel:'])):
                    
                    if href.startswith('/'):
                        full_url = urljoin(self.base_url, href)
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        continue
                    
                    if self._is_product_url(full_url) and full_url not in product_urls:
                        product_urls.append(full_url)
                        logger.info(f"✓ Thêm product URL (fallback): {full_url}")
            
            # 3. Tìm sản phẩm trong các tab khác (COMPATIBLE PRODUCTS, SERVICE PARTS)
            # Tìm tất cả các tab content
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
            
            # 4. Tìm kiếm mở rộng: Tìm các link có pattern sản phẩm cụ thể
            if not product_urls:
                logger.info("Không tìm thấy sản phẩm, thử tìm kiếm mở rộng...")
                # Tìm các link có chứa model code cụ thể
                model_patterns = ['mr-j5-', 'mr-jet-', 'mr-j4-', 'mr-je-']
                
                for link in all_links:
                    href = link.get('href', '')
                    if not href or not href.endswith('.html'):
                        continue
                    
                    # Kiểm tra nếu href chứa model pattern
                    if any(pattern in href.lower() for pattern in model_patterns):
                        if href.startswith('/'):
                            full_url = urljoin(self.base_url, href)
                        elif href.startswith('http'):
                            full_url = href
                        else:
                            continue
                        
                        if self._is_product_url(full_url) and full_url not in product_urls:
                            product_urls.append(full_url)
                            logger.info(f"✓ Thêm product URL (pattern search): {full_url}")
            
            # 5. Fallback cuối cùng: Tìm tất cả link .html và kiểm tra từng cái
            if not product_urls:
                logger.info("Thử fallback cuối cùng: tìm tất cả link .html...")
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
                    
                    # Kiểm tra nếu có thể là sản phẩm (có chứa model code hoặc /fa/products/)
                    if ('/fa/products/' in full_url.lower() or 
                        any(model in full_url.lower() for model in ['mr-', 'mf-', 'servo', 'amplifier'])):
                        product_urls.append(full_url)
                        logger.info(f"✓ Thêm product URL (fallback cuối): {full_url}")
            
            # 6. Lọc chỉ giữ URL sản phẩm hợp lệ (kết thúc .html) và loại duplicate
            product_urls = [u for u in product_urls if u.lower().endswith('.html')]
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
    
    def extract_product_details(self, product_url, images_folder):
        """
        Trích xuất chi tiết 1 sản phẩm
        """
        try:
            html = get_html_content(product_url)
            if not html:
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Lấy mã sản phẩm từ h2
            product_code = self._extract_product_code(soup, product_url)
            if not product_code:
                logger.warning(f"Không tìm thấy mã sản phẩm trong {product_url}")
                return None
            
            # Lấy tên sản phẩm
            product_name = self._extract_product_name(soup, product_url)
            
            # Lấy ảnh sản phẩm
            image_path = self._extract_and_download_image(soup, product_code, images_folder)
            
            # Lấy thông số kỹ thuật
            specs_list = self._extract_specifications(soup)
            
            # Tạo HTML bảng thông số
            specs_html = self.create_specifications_table_html(product_code, product_name, specs_list)
            
            return {
                'code': product_code,
                'name': product_name,
                'image_path': image_path,
                'specs_html': specs_html,
                'url': product_url,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            logger.error(f"Lỗi khi extract product details từ {product_url}: {str(e)}")
            return None
    
    def _extract_product_code(self, soup, product_url=None):
        """Lấy mã sản phẩm từ h2 tag hoặc từ URL"""
        try:
            # Tìm h2 tag chứa mã sản phẩm (thường là MR-J5-xxx)
            h2_tags = soup.find_all('h2')
            for h2 in h2_tags:
                text = h2.get_text(strip=True)
                # Kiểm tra nếu text chứa pattern mã sản phẩm MR-
                if 'MR-' in text and len(text) > 5:
                    return text
            
            # Fallback: tìm trong main content
            main = soup.find('main')
            if main:
                h2_tags = main.find_all('h2')
                for h2 in h2_tags:
                    text = h2.get_text(strip=True)
                    if 'MR-' in text and len(text) > 5:
                        return text
            
            # Fallback: Extract từ URL nếu có
            if product_url:
                # Tìm model code trong URL
                import re
                model_patterns = [
                    r'mr-j5-[a-zA-Z0-9\-]+',
                    r'mr-jet-[a-zA-Z0-9\-]+', 
                    r'mr-j4-[a-zA-Z0-9\-]+',
                    r'mr-je-[a-zA-Z0-9\-]+',
                    r'mr-configurator[a-zA-Z0-9\-]*',
                    r'mr-d05[a-zA-Z0-9\-]*',
                    r'mr-pwr[a-zA-Z0-9\-]*',
                    r'mf-3f[a-zA-Z0-9\-]*'
                ]
                
                for pattern in model_patterns:
                    match = re.search(pattern, product_url.lower())
                    if match:
                        return match.group(0).upper()
                
                # Fallback: Extract từ filename nếu không tìm thấy pattern
                filename = product_url.split('/')[-1]
                if filename.endswith('.html'):
                    filename = filename[:-5]  # Remove .html
                    # Nếu filename có vẻ là model code
                    if len(filename) > 5 and any(char.isdigit() for char in filename):
                        return filename.upper()
            
            # Fallback cuối cùng: Tạo mã từ URL path
            if product_url:
                path_parts = product_url.split('/')
                # Tìm phần cuối có thể là model code
                for part in reversed(path_parts):
                    if part and part.endswith('.html'):
                        part = part[:-5]  # Remove .html
                    if part and len(part) > 3 and any(char.isdigit() for char in part):
                        return part.upper()
            
            return None
        except:
            return None
    
    def _extract_product_name(self, soup, product_url=None):
        """Lấy tên sản phẩm từ ProductOverview_description hoặc các nguồn khác"""
        try:
            # Tìm div với class ProductOverview_description__CHeZm
            desc_div = soup.find('div', class_='ProductOverview_description__CHeZm')
            if desc_div:
                return desc_div.get_text(strip=True)
            
            # Fallback: tìm các class khác có thể chứa description
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
                    if text and len(text) > 10:  # Đảm bảo có nội dung đủ dài
                        return text
            
            # Fallback: Tạo tên từ URL nếu có
            if product_url:
                filename = product_url.split('/')[-1]
                if filename.endswith('.html'):
                    filename = filename[:-5]  # Remove .html
                if filename and len(filename) > 3:
                    return filename.replace('-', ' ').replace('_', ' ').title()
            
            return "Unknown Product"
        except:
            return "Unknown Product"
    
    def _extract_and_download_image(self, soup, product_code, images_folder):
        """Lấy ảnh sản phẩm và download + convert sang WebP"""
        try:
            # Tìm ảnh sản phẩm
            img = soup.find('img', class_='Gallery_slideImg__TqcGx')
            if not img:
                # Fallback: tìm các class khác
                img_selectors = [
                    'img[class*="Gallery"]',
                    'img[class*="gallery"]',
                    'img[class*="ProductImage"]',
                    'img[class*="LazyImage"]',
                    'img',
                ]
                for sel in img_selectors:
                    img = soup.select_one(sel)
                    if img:
                        break
            
            if not img:
                logger.warning(f"Không tìm thấy ảnh cho sản phẩm {product_code}")
                return None
            
            # Lấy URL ảnh
            img_url = img.get('src') or img.get('data-src') or img.get('data-large_image')
            if not img_url:
                logger.warning(f"Không tìm thấy URL ảnh cho sản phẩm {product_code}")
                return None
            
            # Chuẩn hóa URL
            if img_url.startswith('//'):
                img_url = 'https:' + img_url
            elif img_url.startswith('/'):
                img_url = urljoin(self.base_url, img_url)
            
            # Download và convert sang WebP
            return self.download_and_convert_image(img_url, product_code, images_folder)
            
        except Exception as e:
            logger.error(f"Lỗi khi extract image cho {product_code}: {str(e)}")
            return None
    
    def download_and_convert_image(self, image_url, product_code, output_dir):
        """
        Tải ảnh và chuyển sang WebP với chất lượng cao
        """
        try:
            # Download image với headers phù hợp
            headers = {
                'User-Agent': self.session.headers.get('User-Agent', ''),
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': self.base_url,
            }
            response = self.session.get(image_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Tạo tên file
            safe_code = re.sub(r'[^\w\-_.]', '_', product_code)
            webp_filename = f"{safe_code}.webp"
            webp_path = os.path.join(output_dir, webp_filename)
            
            # Convert sang WebP sử dụng WebPConverter
            try:
                success = WebPConverter.convert_to_webp(
                    image_data=response.content,
                    output_path=webp_path,
                    quality=90
                )
                
                if success:
                    logger.info(f"Đã convert ảnh {product_code} sang WebP")
                    return webp_path
                else:
                    logger.error(f"Lỗi khi convert ảnh {product_code} sang WebP")
                    return None
                    
            except Exception as convert_err:
                logger.warning(f"WebPConverter lỗi, dùng PIL fallback: {convert_err}")
                # Fallback bằng PIL
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(response.content))
                img.save(webp_path, 'WEBP', quality=90)
                logger.info(f"Đã convert ảnh {product_code} sang WebP bằng PIL")
                return webp_path
                
        except Exception as e:
            logger.error(f"Lỗi khi download/convert ảnh {product_code}: {str(e)}")
            return None
    
    def _extract_specifications(self, soup):
        """
        Lấy thông số kỹ thuật từ tab TECHNICAL INFORMATION
        """
        try:
            specs_list = []
            
            # Tìm tất cả tables có class ProductDetail_charTable__m3dh8
            tables = soup.find_all('table', class_='ProductDetail_charTable__m3dh8')
            
            for table in tables:
                specs_list.extend(self._parse_specs_table(table))
            
            return specs_list
            
        except Exception as e:
            logger.error(f"Lỗi khi extract specifications: {str(e)}")
            return []
    
    def _parse_specs_table(self, table):
        """Parse một bảng thông số"""
        specs = []
        try:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['th', 'td'])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    if key and value:
                        specs.append((key, value))
        except Exception as e:
            logger.error(f"Lỗi khi parse specs table: {str(e)}")
        
        return specs
    
    def create_specifications_table_html(self, product_code, product_name, specs_list):
        """
        Tạo HTML bảng theo format yêu cầu
        """
        try:
            html = f"""
            <table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">
                <thead>
                    <tr style="background-color: #f2f2f2;">
                        <th>Thông số</th>
                        <th>Giá trị</th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product_code}</td></tr>
                    <tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product_name}</td></tr>
            """
            
            # Thêm các thông số kỹ thuật
            for key, value in specs_list:
                html += f'<tr><td style="font-weight: bold;">{key}</td><td>{value}</td></tr>\n'
            
            # Thêm dòng Copyright bắt buộc
            html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>\n'
            
            html += """
                </tbody>
            </table>
            """
            
            return html
            
        except Exception as e:
            logger.error(f"Lỗi khi tạo HTML table: {str(e)}")
            return f"<p>Lỗi khi tạo bảng thông số: {str(e)}</p>"
    
    def create_excel_report(self, products_data, output_folder):
        """
        Tạo Excel tổng hợp
        """
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
                    'Thời gian cào': product.get('timestamp', '')
                })
            
            df = pd.DataFrame(df_data)
            
            # Tạo tên file Excel
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_filename = f"Mitsubishi_Products_{timestamp}.xlsx"
            excel_path = os.path.join(output_folder, excel_filename)
            
            # Lưu Excel
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Products', index=False)
                
                # Điều chỉnh độ rộng cột
                worksheet = writer.sheets['Products']
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
            
            logger.info(f"Đã tạo Excel report: {excel_path}")
            return excel_path
            
        except Exception as e:
            logger.error(f"Lỗi khi tạo Excel report: {str(e)}")
            return None
