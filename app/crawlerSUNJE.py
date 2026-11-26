"""
SUNJE Product Crawler - Cào dữ liệu sản phẩm từ website sunstat.com
Phiên bản: 1.0
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
import logging
from PIL import Image
import io

# Import Google Translate (fallback)
GOOGLE_TRANSLATE_AVAILABLE = False
try:
    # pyrefly: ignore [missing-import]
    from googletrans import Translator
    GOOGLE_TRANSLATE_AVAILABLE = True
except ImportError:
    pass  # googletrans không được cài đặt, chức năng dịch sẽ bị tắt

# Gemini AI Translator (ưu tiên)
try:
    from app.utils import init_gemini_translator, translate_with_gemini, translate_specifications_with_gemini
    GEMINI_TRANSLATE_AVAILABLE = True
except Exception:
    GEMINI_TRANSLATE_AVAILABLE = False

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_sunje_product_urls(category_url):
    """
    Trích xuất danh sách URL sản phẩm từ trang danh mục sunstat.com
    Quét toàn bộ trang https://sunstat.com/business-area/electrostatic-solution/
    để lấy các link sản phẩm từ phần tử <div class="post-thumbnail-wrap">
    """
    product_urls = []
    
    try:
        html = get_html_content(category_url)
        if not html:
            logger.error(f"Không thể tải HTML từ URL: {category_url}")
            return product_urls
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm tất cả các link sản phẩm từ phần tử post-thumbnail-wrap
        # Cấu trúc: <div class="post-thumbnail-wrap"> ... <a href="..."> ... </a> ... </div>
        thumbnail_wraps = soup.find_all('div', class_='post-thumbnail-wrap')
        
        logger.info(f"Tìm thấy {len(thumbnail_wraps)} phần tử post-thumbnail-wrap")
        
        seen_urls = set()
        
        for wrap in thumbnail_wraps:
            # Tìm link <a> bên trong
            link_elem = wrap.find('a', href=True)
            if link_elem:
                href = link_elem.get('href', '').strip()
                if href and href not in seen_urls:
                    # Chuyển đổi relative URL thành absolute URL
                    if href.startswith('/'):
                        full_url = urljoin(category_url, href)
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        full_url = urljoin(category_url, '/' + href)
                    
                    # Chỉ lấy URL sản phẩm (có dạng /sxf-5010/ hoặc tương tự)
                    if full_url and 'sunstat.com' in full_url:
                        product_urls.append(full_url)
                        seen_urls.add(full_url)
                        logger.info(f"  ✅ Tìm thấy sản phẩm: {full_url}")
        
        # Fallback: Tìm tất cả link có chứa sunstat.com và không phải là link danh mục
        if not product_urls:
            logger.warning("Không tìm thấy sản phẩm bằng post-thumbnail-wrap, thử fallback...")
            all_links = soup.find_all('a', href=True)
            for link in all_links:
                href = link.get('href', '').strip()
                if href and 'sunstat.com' in href:
                    # Loại bỏ link danh mục và link trang chủ
                    if '/business-area/' not in href and href != 'https://sunstat.com/' and href != 'https://sunstat.com':
                        if href.startswith('/'):
                            full_url = urljoin(category_url, href)
                        elif href.startswith('http'):
                            full_url = href
                        else:
                            continue
                        
                        if full_url not in seen_urls and full_url not in product_urls:
                            product_urls.append(full_url)
                            seen_urls.add(full_url)
        
        # Loại bỏ trùng lặp
        product_urls = list(set(product_urls))
        logger.info(f"✅ Tìm thấy tổng cộng {len(product_urls)} sản phẩm")
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất URL sản phẩm: {str(e)}")
        traceback.print_exc()
    
    return product_urls

def extract_sunje_product_info(product_url, translate_specs=False, gemini_api_key=None):
    """
    Trích xuất thông tin chi tiết sản phẩm từ sunstat.com
    
    Args:
        product_url: URL trang sản phẩm
        translate_specs: Có dịch thông số kỹ thuật sang tiếng Việt không (mặc định: False)
        gemini_api_key: Gemini API key để dịch (nếu None, sẽ lấy từ environment)
    
    Returns:
        dict: {
            'url': str,
            'name': str,  # Tên sản phẩm (mã + danh mục, ví dụ: "SXF-5010-C Optical")
            'code': str,  # Mã sản phẩm (ví dụ: "SXF-5010-C")
            'category': str,  # Tên danh mục (ví dụ: "Optical")
            'images': list,  # Danh sách URL ảnh
            'specifications': dict  # Thông số kỹ thuật dạng dict
        }
    """
    try:
        html = get_html_content(product_url)
        if not html:
            logger.error(f"Không thể tải HTML từ: {product_url}")
            return None
            
        soup = BeautifulSoup(html, 'html.parser')
        
        product_info = {
            'url': product_url,
            'name': '',
            'code': '',
            'category': '',
            'images': [],
            'specifications': {}
        }
        
        # 1. Lấy mã sản phẩm từ h1.elementor-heading-title
        # <div class="elementor-element ... elementor-widget-theme-post-title ...">
        #   <h1 class="elementor-heading-title elementor-size-default">SXF-5010-C</h1>
        # </div>
        code_element = soup.select_one('h1.elementor-heading-title.elementor-size-default')
        if not code_element:
            # Fallback: tìm bất kỳ h1 nào
            code_element = soup.find('h1', class_='elementor-heading-title')
        if code_element:
            product_code = code_element.get_text(strip=True)
            product_info['code'] = product_code
            logger.info(f"  ✅ Mã sản phẩm: {product_code}")
        
        # 2. Lấy tên danh mục từ filter-categories
        # <div class="filter-categories"> ... <a class="filter-item optical act" ...>Optical</a> ... </div>
        category = ''
        filter_categories = soup.find('div', class_='filter-categories')
        if filter_categories:
            # Tìm link có class chứa "act" (active)
            active_link = filter_categories.find('a', class_=lambda x: x and 'act' in x)
            if not active_link:
                # Fallback: tìm link không phải "View all"
                all_links = filter_categories.find_all('a', class_='filter-item')
                for link in all_links:
                    link_text = link.get_text(strip=True)
                    if link_text and link_text.lower() != 'view all':
                        # Lấy link đầu tiên không phải "View all"
                        active_link = link
                        break
            
            if active_link:
                category = active_link.get_text(strip=True)
                product_info['category'] = category
                logger.info(f"  ✅ Danh mục: {category}")
        
        # 3. Tạo tên sản phẩm: mã + danh mục (ví dụ: "SXF-5010-C Optical")
        if product_info['code'] and category:
            product_info['name'] = f"{product_info['code']} {category}"
        elif product_info['code']:
            product_info['name'] = product_info['code']
        else:
            product_info['name'] = 'Unknown Product'
        
        # 4. Lấy ảnh sản phẩm
        # Theo yêu cầu: <img fetchpriority="high" width="400" height="400" 
        # class="attachment-large size-large wp-image-28408" ...>
        images = []
        
        # Ưu tiên 1: Tìm ảnh có fetchpriority="high" và class chứa "attachment-large"
        priority_img = soup.find('img', {
            'fetchpriority': 'high',
            'class': lambda x: x and 'attachment-large' in ' '.join(x) if isinstance(x, list) else 'attachment-large' in str(x)
        }, src=True)
        
        if not priority_img:
            # Ưu tiên 2: Tìm ảnh có class="attachment-large size-large"
            priority_img = soup.find('img', class_=lambda x: x and 'attachment-large' in ' '.join(x) if isinstance(x, list) else 'attachment-large' in str(x), src=True)
        
        if not priority_img:
            # Ưu tiên 3: Tìm ảnh có width="400" và height="400"
            priority_img = soup.find('img', {'width': '400', 'height': '400'}, src=True)
        
        if priority_img:
            src = priority_img.get('src', '')
            if src:
                # Chuyển đổi relative URL thành absolute
                if src.startswith('/'):
                    full_img_url = urljoin(product_url, src)
                elif src.startswith('http'):
                    full_img_url = src
                else:
                    full_img_url = urljoin(product_url, '/' + src)
                
                # Loại bỏ kích thước trong URL (nếu có) - ưu tiên ảnh gốc
                # Ví dụ: ...-300x300.png -> ...png
                full_img_url = re.sub(r'-\d+x\d+\.(png|jpg|jpeg|webp)$', r'.\1', full_img_url, flags=re.IGNORECASE)
                
                if 'wp-content/uploads' in full_img_url:
                    images.append(full_img_url)
                    logger.info(f"  ✅ Tìm thấy ảnh chính: {full_img_url}")
        
        # Fallback: Nếu không tìm thấy, tìm ảnh có class chứa "attachment-large" hoặc "size-large"
        if not images:
            fallback_img = soup.find('img', class_=lambda x: x and ('attachment-large' in ' '.join(x) or 'size-large' in ' '.join(x)) if isinstance(x, list) else ('attachment-large' in str(x) or 'size-large' in str(x)), src=True)
            if fallback_img:
                src = fallback_img.get('src', '')
                if src:
                    if src.startswith('/'):
                        full_img_url = urljoin(product_url, src)
                    elif src.startswith('http'):
                        full_img_url = src
                    else:
                        full_img_url = urljoin(product_url, '/' + src)
                    
                    # Loại bỏ kích thước trong URL
                    full_img_url = re.sub(r'-\d+x\d+\.(png|jpg|jpeg|webp)$', r'.\1', full_img_url, flags=re.IGNORECASE)
                    
                    if 'wp-content/uploads' in full_img_url:
                        images.append(full_img_url)
                        logger.info(f"  ✅ Tìm thấy ảnh (fallback): {full_img_url}")
        
        # Fallback cuối cùng: Lấy ảnh đầu tiên có wp-content/uploads và không có kích thước nhỏ trong URL
        if not images:
            all_images = soup.find_all('img', src=True)
            for img in all_images:
                src = img.get('src', '')
                if src and 'wp-content/uploads' in src:
                    # Chuyển đổi relative URL thành absolute
                    if src.startswith('/'):
                        full_img_url = urljoin(product_url, src)
                    elif src.startswith('http'):
                        full_img_url = src
                    else:
                        full_img_url = urljoin(product_url, '/' + src)
                    
                    # Loại bỏ kích thước trong URL để lấy ảnh gốc
                    full_img_url = re.sub(r'-\d+x\d+\.(png|jpg|jpeg|webp)$', r'.\1', full_img_url, flags=re.IGNORECASE)
                    
                    # Bỏ qua ảnh placeholder, logo, icon
                    lower_url = full_img_url.lower()
                    if any(skip in lower_url for skip in ['placeholder', 'logo', 'icon', 'banner']):
                        continue
                    
                    images.append(full_img_url)
                    logger.info(f"  ✅ Tìm thấy ảnh (fallback cuối): {full_img_url}")
                    break
        
        product_info['images'] = images
        if images:
            logger.info(f"  ✅ Tìm thấy {len(images)} ảnh sản phẩm")
        else:
            logger.warning(f"  ⚠️  Không tìm thấy ảnh sản phẩm")
        
        # 5. Lấy thông số kỹ thuật từ bảng tablepress
        specifications = extract_sunje_specifications(
            soup, 
            product_info['code'], 
            product_info['name'], 
            translate=translate_specs,
            gemini_api_key=gemini_api_key
        )
        product_info['specifications'] = specifications
        
        return product_info
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
        traceback.print_exc()
        return None

def translate_text(text, target_lang='vi', source_lang='en', gemini_api_key=None):
    """
    Dịch text từ source_lang sang target_lang (ưu tiên Gemini, fallback Google Translate)
    
    Args:
        text: Text cần dịch
        target_lang: Ngôn ngữ đích (mặc định: 'vi' - tiếng Việt)
        source_lang: Ngôn ngữ nguồn (mặc định: 'en' - tiếng Anh)
        gemini_api_key: Gemini API key (nếu None, sẽ lấy từ environment)
    
    Returns:
        str: Text đã dịch, hoặc text gốc nếu dịch thất bại
    """
    if not text or not text.strip():
        return text
    
    # Ưu tiên sử dụng Gemini
    if GEMINI_TRANSLATE_AVAILABLE:
        try:
            # Khởi tạo Gemini nếu chưa có
            if gemini_api_key:
                init_gemini_translator(gemini_api_key)
            return translate_with_gemini(text, target_lang=target_lang, source_lang=source_lang)
        except Exception as e:
            logger.warning(f"  ⚠️  Gemini dịch lỗi '{text[:50]}...': {str(e)}, thử Google Translate...")
    
    # Fallback: Google Translate
    if GOOGLE_TRANSLATE_AVAILABLE:
        try:
            translator = Translator()
            # Phát hiện ngôn ngữ tự động nếu source_lang là 'auto'
            if source_lang == 'auto':
                result = translator.translate(text, dest=target_lang)
            else:
                result = translator.translate(text, src=source_lang, dest=target_lang)
            
            translated_text = result.text
            logger.debug(f"  Dịch (Google): '{text[:50]}...' -> '{translated_text[:50]}...'")
            return translated_text
        except Exception as e:
            logger.warning(f"  ⚠️  Không thể dịch '{text[:50]}...': {str(e)}")
    
    return text  # Trả về text gốc nếu dịch thất bại

def translate_specifications(specifications, translate_keys=True, translate_values=True, gemini_api_key=None):
    """
    Dịch các thông số kỹ thuật từ tiếng Anh sang tiếng Việt (ưu tiên Gemini)
    
    Args:
        specifications: Dictionary chứa thông số kỹ thuật
        translate_keys: Có dịch tên thông số (key) không
        translate_values: Có dịch giá trị (value) không
        gemini_api_key: Gemini API key (nếu None, sẽ lấy từ environment)
    
    Returns:
        dict: Dictionary chứa thông số kỹ thuật đã dịch
    """
    if not specifications:
        return specifications
    
    # Ưu tiên sử dụng Gemini
    if GEMINI_TRANSLATE_AVAILABLE:
        try:
            # Khởi tạo Gemini nếu chưa có
            if gemini_api_key:
                init_gemini_translator(gemini_api_key)
            return translate_specifications_with_gemini(specifications, translate_keys=translate_keys, translate_values=translate_values)
        except Exception as e:
            logger.warning(f"Gemini dịch lỗi, thử Google Translate: {str(e)}")
    
    # Fallback: Google Translate
    if not GOOGLE_TRANSLATE_AVAILABLE:
        logger.warning("Google Translate không khả dụng, bỏ qua dịch thông số")
        return specifications
    
    translated_specs = {}
    
    # Các key không cần dịch
    skip_keys = ['Mã sản phẩm', 'Tên sản phẩm', 'Copyright', 'Product Code', 'Product Name']
    
    for key, value in specifications.items():
        # Không dịch các key đặc biệt
        if key in skip_keys:
            translated_specs[key] = value
            continue
        
        # Dịch key nếu cần
        translated_key = translate_text(key, target_lang='vi', source_lang='auto', gemini_api_key=gemini_api_key) if translate_keys else key
        
        # Dịch value nếu cần
        if translate_values and value:
            translated_value = translate_text(value, target_lang='vi', source_lang='auto', gemini_api_key=gemini_api_key)
        else:
            translated_value = value
        
        translated_specs[translated_key] = translated_value
    
    return translated_specs

def extract_sunje_specifications(soup, product_code, product_name, translate=False, gemini_api_key=None):
    """
    Trích xuất thông số kỹ thuật từ bảng tablepress và chuyển đổi sang dict
    
    Args:
        soup: BeautifulSoup object
        product_code: Mã sản phẩm
        product_name: Tên sản phẩm
        translate: Có dịch thông số sang tiếng Việt không (mặc định: False)
        gemini_api_key: Gemini API key để dịch (nếu None, sẽ lấy từ environment)
    
    Returns:
        dict: Dictionary chứa thông số kỹ thuật
    """
    specifications = {}
    
    try:
        # Tìm bảng tablepress
        # <table id="tablepress-156" class="tablepress tablepress-id-156 spec tbody-has-connected-cells">
        spec_table = soup.find('table', class_='tablepress')
        if not spec_table:
            logger.warning("  ⚠️  Không tìm thấy bảng thông số kỹ thuật")
            # Vẫn thêm mã sản phẩm, tên sản phẩm và Copyright
            specifications['Mã sản phẩm'] = product_code
            specifications['Tên sản phẩm'] = product_name
            specifications['Copyright'] = 'Haiphongtech.vn'
            return specifications
        
        # Lấy tất cả các hàng trong tbody
        tbody = spec_table.find('tbody')
        if not tbody:
            tbody = spec_table
        
        rows = tbody.find_all('tr')
        
        current_param = None  # Lưu thông số hiện tại khi có rowspan
        
        for row in rows:
            # Lấy các cột
            cols = row.find_all(['td', 'th'])
            
            if len(cols) >= 2:
                # Cột 1: Parameter (Thông số)
                param = cols[0].get_text(strip=True)
                # Cột 2: Description / Value (Giá trị)
                value = cols[1].get_text(strip=True)
                
                # Kiểm tra xem cột 1 có rowspan không
                rowspan = cols[0].get('rowspan')
                if rowspan:
                    try:
                        rowspan_count = int(rowspan)
                        # Lưu thông số này để dùng cho các hàng tiếp theo
                        current_param = param
                    except ValueError:
                        pass
                
                if param and value:
                    # Nếu có nhiều giá trị cho cùng một thông số (do rowspan),
                    # ghép chúng lại với nhau
                    if param in specifications:
                        specifications[param] += f"; {value}"
                    else:
                        specifications[param] = value
            elif len(cols) == 1:
                # Trường hợp rowspan: chỉ có một cột (giá trị)
                # Giá trị này thuộc về thông số trước đó (current_param)
                value = cols[0].get_text(strip=True)
                if value and current_param:
                    # Ghép giá trị vào thông số hiện tại
                    if current_param in specifications:
                        specifications[current_param] += f"; {value}"
                    else:
                        specifications[current_param] = value
        
        # Thêm mã sản phẩm và tên sản phẩm vào đầu
        if product_code:
            specifications = {'Mã sản phẩm': product_code, **specifications}
        if product_name:
            specifications['Tên sản phẩm'] = product_name
        
        # Thêm Copyright vào cuối (bắt buộc)
        specifications['Copyright'] = 'Haiphongtech.vn'
        
        # Dịch thông số kỹ thuật nếu được yêu cầu
        if translate:
            logger.info(f"  🌐 Đang dịch {len(specifications)} thông số kỹ thuật sang tiếng Việt bằng Gemini...")
            specifications = translate_specifications(specifications, translate_keys=True, translate_values=True, gemini_api_key=gemini_api_key)
            logger.info(f"  ✅ Đã dịch xong thông số kỹ thuật")
        
        logger.info(f"  ✅ Đã trích xuất {len(specifications)} thông số kỹ thuật")
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất thông số kỹ thuật: {str(e)}")
        traceback.print_exc()
        # Vẫn thêm thông tin cơ bản
        specifications['Mã sản phẩm'] = product_code
        specifications['Tên sản phẩm'] = product_name
        specifications['Copyright'] = 'Haiphongtech.vn'
    
    return specifications

def create_specifications_html_table(specifications):
    """
    Tạo HTML table cho thông số kỹ thuật theo định dạng yêu cầu
    
    Format:
    <table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">
    <thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>
    <tbody>
    <tr><td style="font-weight: bold;">Mã sản phẩm</td><td>VPL06N</td></tr>
    ...
    <tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>
    </tbody></table>
    """
    if not specifications:
        return ""
    
    html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
    html += '<tbody>'
    
    for key, value in specifications.items():
        # Escape HTML để tránh lỗi
        key_escaped = str(key).replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
        value_escaped = str(value).replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
        html += f'<tr><td style="font-weight: bold;">{key_escaped}</td><td>{value_escaped}</td></tr>'
    
    html += '</tbody></table>'
    return html

def standardize_sunje_image_filename(product_code: str) -> str:
    """
    Chuẩn hóa mã sản phẩm để dùng làm tên file ảnh
    Giữ lại dấu gạch ngang (-) và dấu gạch dưới (_) trong mã sản phẩm
    
    Args:
        product_code: Mã sản phẩm gốc (ví dụ: "SXF-5010-C")
    
    Returns:
        str: Mã sản phẩm đã chuẩn hóa (ví dụ: "SXF-5010-C")
    """
    if not product_code:
        return "product"
    
    s = str(product_code).strip()
    if not s:
        return "product"
    
    # Thay mọi ký tự đặc biệt (trừ chữ, số, dấu gạch ngang, dấu gạch dưới) bằng dấu gạch dưới
    s = re.sub(r'[^\w\-_]', '_', s)
    
    # Gom nhiều dấu gạch dưới liên tiếp thành một (giữ dấu gạch ngang)
    s = re.sub(r'_+', '_', s)
    
    # Bỏ dấu gạch dưới đầu/cuối
    s = re.sub(r'^_+|_+$', '', s)
    
    # Đảm bảo không rỗng
    if not s:
        return "product"
    
    return s

def standardize_filename(filename: str) -> str:
    """
    Chuẩn hóa tên file ảnh theo logic JavaScript standardize
    Không áp dụng cho mã sản phẩm
    
    Args:
        filename: Tên file gốc
    
    Returns:
        str: Tên file đã chuẩn hóa
    """
    if not filename:
        return "image"
    
    s = str(filename).strip()
    if not s:
        return "image"
    
    # 1. Thay ký tự không hợp lệ [\\/:*?"<>|,=\s] bằng '-'
    s = re.sub(r'[\\/:*?"<>|,=\s]', '-', s)
    
    # 2. Loại bỏ gạch ngang trùng lặp
    s = re.sub(r'-+', '-', s)
    
    # 3. Chỉ giữ lại a-z, A-Z, 0-9, -, _
    s = re.sub(r'[^a-zA-Z0-9\-_]', '', s)
    
    # 4. Loại bỏ gạch đầu/cuối
    s = re.sub(r'^-+|-+$', '', s)
    
    # Đảm bảo không rỗng
    if not s:
        return "image"
    
    return s

def add_white_background(image):
    """
    Thêm nền trắng vào ảnh nếu cần, giữ nguyên kích thước gốc.
    Tương tự logic trong crawlerHIOKI và crawlerKeyence để tránh bị nhòe và vỡ ảnh.
    
    Args:
        image: PIL Image object
        
    Returns:
        PIL Image: Ảnh đã được xử lý với nền trắng, giữ nguyên kích thước
    """
    try:
        # Lấy kích thước gốc
        original_size = image.size
        
        # Nếu ảnh đã có nền RGB rồi thì return luôn
        if image.mode == 'RGB':
            return image
        
        # Convert ảnh sang RGBA để preserve transparency
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        
        # Tạo ảnh nền trắng với kích thước gốc
        background = Image.new('RGB', original_size, (255, 255, 255))
        
        # Paste ảnh gốc lên nền trắng (giữ nguyên kích thước)
        background.paste(image, (0, 0), image)
        
        return background
        
    except Exception as e:
        logger.error(f"Lỗi khi thêm white background: {str(e)}")
        # Return ảnh gốc convert sang RGB nếu có lỗi
        return image.convert('RGB') if image.mode != 'RGB' else image

def download_sunje_product_images(product_info, output_folder):
    """
    Tải ảnh sản phẩm từ sunstat.com, thêm nền trắng và chuyển đổi sang WebP.
    Tên file ảnh sẽ được đặt theo mã sản phẩm.
    Logic tương tự crawlerHIOKI: thêm nền trắng và lưu trực tiếp sang WebP.
    
    Args:
        product_info: Dictionary chứa thông tin sản phẩm
        output_folder: Thư mục lưu ảnh
    
    Returns:
        list: Danh sách đường dẫn file ảnh WebP đã tải
    """
    downloaded_images = []
    
    if not product_info or not product_info.get('images'):
        logger.warning(f"⚠️  Không có ảnh để tải cho sản phẩm: {product_info.get('name', 'Unknown')}")
        return downloaded_images
    
    os.makedirs(output_folder, exist_ok=True)
    
    # Lấy mã sản phẩm - ưu tiên code, nếu không có thì lấy từ name
    product_code = product_info.get('code', '').strip()
    if not product_code:
        # Thử lấy từ tên sản phẩm (có thể chứa mã ở đầu)
        product_name = product_info.get('name', '').strip()
        if product_name:
            # Lấy phần đầu tiên của tên (thường là mã sản phẩm)
            product_code = product_name.split()[0] if product_name.split() else 'product'
        else:
            product_code = 'product'
    
    # Chuẩn hóa mã sản phẩm để dùng làm tên file
    clean_code = standardize_sunje_image_filename(product_code)
    
    logger.info(f"🖼️  Bắt đầu tải {len(product_info['images'])} ảnh cho sản phẩm: {product_info.get('name', 'Unknown')}")
    logger.info(f"   Mã sản phẩm (tên file): {clean_code}")
    
    for i, image_url in enumerate(product_info['images']):
        try:
            # Tên file: mã sản phẩm.webp (ảnh đầu tiên) hoặc mã_sản_phẩm_2.webp, ...
            # Mã sản phẩm đã được chuẩn hóa bằng standardize_sunje_image_filename()
            # Sau đó áp dụng standardize_filename() cho tên file cuối cùng để đảm bảo hợp lệ
            if i == 0:
                base_filename = clean_code
            else:
                base_filename = f"{clean_code}_{i+1}"
            
            # Áp dụng hàm standardize_filename() cho tên file (không áp dụng cho mã sản phẩm)
            standardized_base = standardize_filename(base_filename)
            filename = f"{standardized_base}.webp"
            
            filepath = os.path.join(output_folder, filename)
            
            # Kiểm tra xem file đã tồn tại chưa
            if os.path.exists(filepath):
                logger.info(f"⏭️  Ảnh đã tồn tại: {filename}")
                downloaded_images.append(filepath)
                continue
            
            # Tải ảnh với retry logic
            max_retries = 3
            for retry in range(max_retries):
                try:
                    logger.info(f"📥 Đang tải ảnh {i+1}/{len(product_info['images'])}: {image_url[:50]}...")
                    
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
                        logger.warning(f"⚠️  Không phải ảnh: {content_type}")
                        break
                    
                    # Kiểm tra kích thước file
                    if len(response.content) < 1024:
                        logger.warning(f"⚠️  Ảnh quá nhỏ: {len(response.content)} bytes")
                        if retry < max_retries - 1:
                            time.sleep(1)
                            continue
                        break
                    
                    # Mở ảnh từ bytes
                    original_image = Image.open(io.BytesIO(response.content))
                    if original_image.size[0] < 30 or original_image.size[1] < 30:
                        logger.warning(f"⚠️  Ảnh kích thước nhỏ: {original_image.size}")
                        if retry < max_retries - 1:
                            time.sleep(1)
                            continue
                        break
                    
                    # Thêm nền trắng nếu cần (giữ nguyên kích thước gốc)
                    processed_image = add_white_background(original_image)
                    
                    # Tạo thư mục nếu chưa tồn tại
                    os.makedirs(output_folder, exist_ok=True)
                    
                    # Lưu trực tiếp sang WebP để giữ nguyên chất lượng (tương tự HIOKI)
                    processed_image.save(filepath, 'WebP', quality=95, method=6)
                    
                    # Kiểm tra file đã được tạo
                    if os.path.exists(filepath):
                        downloaded_images.append(filepath)
                        logger.info(f"✅ Tải thành công: {filename}")
                        break
                    else:
                        logger.warning(f"❌ Không tạo được file WebP: {filename}")
                        if retry < max_retries - 1:
                            logger.info(f"🔄 Thử lại lần {retry + 2}...")
                            time.sleep(1)
                        
                except requests.exceptions.RequestException as e:
                    logger.error(f"❌ Lỗi tải ảnh lần {retry + 1}: {str(e)[:50]}...")
                    if retry < max_retries - 1:
                        logger.info(f"🔄 Thử lại lần {retry + 2}...")
                        time.sleep(2)
                    else:
                        logger.error(f"❌ Thất bại hoàn toàn: {filename}")
                except Exception as e:
                    logger.error(f"❌ Lỗi không xác định: {str(e)[:50]}...")
                    if retry < max_retries - 1:
                        time.sleep(1)
                    break
            
        except Exception as e:
            logger.error(f"❌ Lỗi xử lý ảnh: {str(e)[:50]}...")
    
    logger.info(f"📊 Kết quả tải ảnh: {len(downloaded_images)}/{len(product_info['images'])} thành công")
    return downloaded_images

def create_sunje_excel_report(products, output_file):
    """
    Tạo báo cáo Excel cho sản phẩm SUNJE
    
    Args:
        products: Danh sách dictionary chứa thông tin sản phẩm
        output_file: Đường dẫn file Excel đầu ra
    
    Returns:
        bool: True nếu thành công, False nếu có lỗi
    """
    try:
        # Chuẩn bị dữ liệu cho Excel
        excel_data = []
        
        for product in products:
            # Tạo HTML cho bảng thông số kỹ thuật
            spec_html = create_specifications_html_table(product.get('specifications', {}))
            
            excel_data.append({
                'URL': product.get('url', ''),
                'Tên sản phẩm': product.get('name', ''),
                'Mã sản phẩm': product.get('code', ''),
                'Danh mục': product.get('category', ''),
                'Số lượng ảnh': len(product.get('images', [])),
                'Thông số kỹ thuật': spec_html,
                'Danh sách ảnh': '\n'.join(product.get('images', []))
            })
        
        # Tạo DataFrame và lưu Excel
        df = pd.DataFrame(excel_data)
        df.to_excel(output_file, index=False, engine='openpyxl')
        
        logger.info(f"📊 Đã tạo file Excel: {os.path.basename(output_file)} với {len(excel_data)} sản phẩm")
        return True
        
    except Exception as e:
        logger.error(f"❌ Lỗi khi tạo Excel: {str(e)}")
        traceback.print_exc()
        return False

class SUNJECrawler:
    """Crawler chuyên dụng cho website sunstat.com"""
    
    def __init__(self, output_root="output_sunje", max_workers=8, max_retries=3, translate_specs=False, gemini_api_key=None):
        """
        Args:
            output_root: Thư mục lưu kết quả
            max_workers: Số luồng tối đa
            max_retries: Số lần thử lại tối đa
            translate_specs: Có dịch thông số kỹ thuật sang tiếng Việt không (mặc định: False)
            gemini_api_key: Gemini API key để dịch (nếu None, sẽ lấy từ environment)
        """
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.translate_specs = translate_specs
        self.gemini_api_key = gemini_api_key or os.getenv('GEMINI_API_KEY')
        os.makedirs(self.output_root, exist_ok=True)
    
    def crawl_products(self, category_url):
        """
        Cào dữ liệu từ URL danh mục sunstat.com
        
        Args:
            category_url (str): URL trang danh mục, ví dụ:
                https://sunstat.com/business-area/electrostatic-solution/
        
        Returns:
            tuple: (list các dict sản phẩm, đường dẫn folder kết quả)
        """
        start_time = time.time()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"SUNJE_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        # Thông báo bắt đầu
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 0,
                    'message': f'Bắt đầu cào dữ liệu từ sunstat.com',
                    'detail': f'Đang xử lý URL: {category_url}'
                })
            except Exception:
                pass
        
        # 1. Lấy danh sách URL sản phẩm từ danh mục
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 10,
                    'message': 'Đang quét danh sách sản phẩm...',
                    'detail': ''
                })
            except Exception:
                pass
        
        product_urls = extract_sunje_product_urls(category_url)
        
        if not product_urls:
            if socketio and hasattr(socketio, 'emit'):
                try:
                    socketio.emit('progress_update', {
                        'percent': 100,
                        'message': 'Không tìm thấy sản phẩm nào',
                        'detail': 'Vui lòng kiểm tra lại URL danh mục'
                    })
                except Exception:
                    pass
            return [], result_dir
        
        logger.info(f"Tìm thấy {len(product_urls)} sản phẩm cần xử lý")
        
        # Tạo thư mục cho ảnh
        images_dir = os.path.join(result_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        
        # 2. Xử lý từng sản phẩm với đa luồng
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 20,
                    'message': f'Đã tìm thấy {len(product_urls)} sản phẩm',
                    'detail': 'Đang xử lý chi tiết từng sản phẩm...'
                })
            except Exception:
                pass
        
        all_products = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(extract_sunje_product_info, url, self.translate_specs, self.gemini_api_key): url 
                for url in product_urls
            }
            
            for i, future in enumerate(as_completed(future_to_url)):
                url = future_to_url[future]
                try:
                    product_info = future.result()
                    if product_info:
                        # Tải ảnh sản phẩm
                        downloaded_images = download_sunje_product_images(product_info, images_dir)
                        product_info['downloaded_images'] = downloaded_images
                        
                        all_products.append(product_info)
                        
                        progress = 20 + int((i + 1) / len(product_urls) * 70)
                        if socketio and hasattr(socketio, 'emit'):
                            try:
                                socketio.emit('progress_update', {
                                    'percent': progress,
                                    'message': f'Đang xử lý sản phẩm {i+1}/{len(product_urls)}',
                                    'detail': f'Sản phẩm: {product_info.get("name", "Unknown")}'
                                })
                            except Exception:
                                pass
                    else:
                        logger.warning(f"Không lấy được thông tin sản phẩm: {url}")
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý sản phẩm {url}: {str(e)}")
                    traceback.print_exc()
        
        # 3. Tạo báo cáo Excel
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 95,
                    'message': f'Đã xử lý {len(all_products)} sản phẩm',
                    'detail': 'Đang tạo báo cáo Excel...'
                })
            except Exception:
                pass
        
        if all_products:
            excel_file = os.path.join(result_dir, f"SUNJE_ngay{timestamp}.xlsx")
            create_sunje_excel_report(all_products, excel_file)
        
        # 4. Tạo file ZIP
        zip_file = os.path.join(result_dir, f"SUNJE_ket_qua_{timestamp}.zip")
        with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(result_dir):
                for file in files:
                    if not file.endswith('.zip'):
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, result_dir)
                        zipf.write(file_path, arcname)
        
        end_time = time.time()
        duration = end_time - start_time
        
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 100,
                    'message': f'Hoàn thành! Đã xử lý {len(all_products)} sản phẩm',
                    'detail': f'Thời gian: {duration:.2f}s'
                })
            except Exception:
                pass
        
        logger.info(f"SUNJE HOÀN THÀNH: {len(all_products)} sản phẩm | {duration:.1f}s")
        
        # Dọn dẹp: xóa thư mục ảnh để rút gọn output (giữ lại ZIP/Excel)
        try:
            if os.path.isdir(images_dir):
                shutil.rmtree(images_dir, ignore_errors=True)
            logger.info("Đã dọn dẹp thư mục images/ sau khi nén")
        except Exception as cleanup_err:
            logger.warning(f"Không thể dọn dẹp thư mục ảnh: {cleanup_err}")
        
        return all_products, result_dir

def test_single_product(gemini_api_key=None):
    """Test với một sản phẩm đơn lẻ để debug với Gemini translation"""
    print("🧪 Test với một sản phẩm đơn lẻ (có dịch bằng Gemini)...")
    
    # Test với 2 sản phẩm
    test_urls = [
        "https://sunstat.com/sxf-5010/",
        "https://sunstat.com/sxf-5010/"  # Có thể thay URL khác
    ]
    
    if gemini_api_key:
        print(f"🔑 Sử dụng Gemini API key: {gemini_api_key[:20]}...")
    
    for i, test_url in enumerate(test_urls[:2], 1):  # Chỉ test 2 sản phẩm đầu
        print(f"\n{'='*60}")
        print(f"📦 Test sản phẩm {i}/{len(test_urls[:2])}")
        print(f"🔗 Test URL: {test_url}")
        
        product_info = extract_sunje_product_info(test_url, translate_specs=True, gemini_api_key=gemini_api_key)
        
        if product_info:
            print(f"\n✅ THÔNG TIN SẢN PHẨM:")
            print(f"   • Tên: {product_info.get('name', 'N/A')}")
            print(f"   • Mã: {product_info.get('code', 'N/A')}")
            print(f"   • Danh mục: {product_info.get('category', 'N/A')}")
            print(f"   • Số ảnh: {len(product_info.get('images', []))}")
            print(f"   • Số thông số: {len(product_info.get('specifications', {}))}")
            
            if product_info.get('images'):
                print(f"\n🖼️  DANH SÁCH ẢNH:")
                for j, img_url in enumerate(product_info['images'][:3]):
                    print(f"   {j+1}. {img_url}")
            
            if product_info.get('specifications'):
                print(f"\n📋 THÔNG SỐ KỸ THUẬT (đã dịch sang tiếng Việt - 10 đầu tiên):")
                for key, value in list(product_info['specifications'].items())[:10]:
                    print(f"   • {key}: {value[:100] if len(str(value)) > 100 else value}")
        else:
            print(f"\n❌ Không thể lấy thông tin sản phẩm!")

def test_category_urls():
    """Test lấy danh sách URL sản phẩm từ danh mục"""
    print("🧪 Test lấy danh sách URL sản phẩm từ danh mục...")
    
    category_url = "https://sunstat.com/business-area/electrostatic-solution/"
    print(f"🔗 Danh mục: {category_url}")
    
    product_urls = extract_sunje_product_urls(category_url)
    
    print(f"\n📊 KẾT QUẢ:")
    print(f"   • Tổng URL sản phẩm: {len(product_urls)}")
    
    if product_urls:
        print(f"\n🔗 DANH SÁCH URL SẢN PHẨM (5 đầu tiên):")
        for i, url in enumerate(product_urls[:5]):
            print(f"   {i+1}. {url}")
        if len(product_urls) > 5:
            print(f"   ... và {len(product_urls) - 5} URL khác")
    else:
        print(f"\n❌ Không tìm thấy URL sản phẩm nào!")

if __name__ == "__main__":
    # Test với Gemini API key
    GEMINI_API_KEY = "AIzaSyD0j8Mhf_1grariQdXB0OmCiZJ_WbyojQU"
    
    print("🚀 Bắt đầu test crawler SUNJE với Gemini translation...")
    
    # Test với một sản phẩm đơn lẻ (có dịch)
    test_single_product(gemini_api_key=GEMINI_API_KEY)
    
    # Test lấy danh sách URL từ danh mục (comment out nếu chỉ muốn test sản phẩm)
    # print("\n" + "="*50 + "\n")
    # test_category_urls()

