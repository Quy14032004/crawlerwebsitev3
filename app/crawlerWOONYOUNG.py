"""
WOONYOUNG Product Crawler - Cào dữ liệu sản phẩm từ website woonyoung.com
Phiên bản: 1.0
Ngày tạo: 2024
"""

import os
import time
import zipfile
import shutil
from datetime import datetime
from app.crawler import get_html_content
from app import socketio
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import re
from urllib.parse import urlparse, urljoin, parse_qs, unquote
from bs4 import BeautifulSoup
import requests
import traceback
import logging
import json

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def standardize_for_image_filename(code: str) -> str:
    """
    Chuẩn hóa mã sản phẩm để dùng LÀM TÊN FILE ẢNH theo logic GAS:
    - Loại bỏ ghi chú: (with special coating), [with special coating], add-on kit
    - Thay mọi ký tự đặc biệt bằng '-'
    - Gom nhiều '-' liên tiếp
    - Bỏ '-' đầu/cuối
    - CHUYỂN THÀNH UPPERCASE
    """
    if code is None:
        return ""
    s = str(code)
    # Loại bỏ ghi chú
    s = re.sub(r"\(with\s+special\s+coating\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[with\s+special\s+coating\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"add[\s\-]*on[\s\-]*kit", "", s, flags=re.IGNORECASE)
    s = s.strip()
    # Chuẩn hóa ký tự
    s = re.sub(r"[^a-zA-Z0-9\-_]", "-", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s.upper()

def parse_product_codes(codes_string):
    """
    Tách chuỗi mã sản phẩm thành danh sách các mã riêng biệt.
    
    Args:
        codes_string (str): Chuỗi chứa nhiều mã sản phẩm, ví dụ:
            "WYF21-40A WYF21-60A WYF21-100A" hoặc "WY11-100UL<br>WY11-200UL<br>WY11-300UL"
    
    Returns:
        list: Danh sách các mã sản phẩm đã tách, ví dụ:
            ["WYF21-40A", "WYF21-60A", "WYF21-100A"]
    """
    if not codes_string:
        return []
    
    # Loại bỏ khoảng trắng đầu/cuối
    codes_string = codes_string.strip()
    
    if not codes_string:
        return []
    
    # Tách theo <br> tag trước (HTML)
    if '<br>' in codes_string.lower():
        # Xử lý cả <br>, <BR>, <Br>
        codes_list = re.split(r'<br\s*/?>', codes_string, flags=re.IGNORECASE)
    else:
        # Tách theo khoảng trắng
        codes_list = codes_string.split()
    
    # Làm sạch và lọc mã sản phẩm
    product_codes = []
    for code in codes_list:
        # Loại bỏ khoảng trắng đầu/cuối và HTML tags còn sót
        code = re.sub(r'<[^>]+>', '', code)  # Xóa HTML tags
        code = code.strip()
        
        # Chỉ giữ lại mã hợp lệ (có ít nhất 3 ký tự và chứa chữ/số)
        if code and len(code) >= 3 and re.search(r'[A-Za-z0-9]', code):
            product_codes.append(code)
    
    return product_codes

def extract_series_info_from_tr(tr_element, base_url):
    """
    Lấy thông tin từ một phần tử <tr> chứa thông tin series sản phẩm.
    
    Args:
        tr_element: BeautifulSoup element chứa <tr>
        base_url (str): URL gốc để chuyển đổi relative URL thành absolute
    
    Returns:
        dict: Dictionary chứa thông tin series với các key:
            - series_name: Tên series sản phẩm
            - product_link: Link chi tiết (URL đầy đủ)
            - product_codes: Danh sách mã sản phẩm (list)
            - image_url: URL ảnh (URL đầy đủ) hoặc None
    """
    try:
        # Lấy tất cả các <td> trong <tr>
        tds = tr_element.find_all('td')
        
        if len(tds) < 3:
            return None
        
        series_info = {
            'series_name': '',
            'product_link': '',
            'product_codes': [],
            'image_url': None
        }
        
        # Cột 1: Ảnh và link
        td1 = tds[0] if len(tds) > 0 else None
        if td1:
            # Lấy link từ <a>
            link_elem = td1.find('a')
            if link_elem and link_elem.get('href'):
                href = link_elem.get('href')
                if href.startswith('/'):
                    series_info['product_link'] = urljoin(base_url, href)
                elif href.startswith('http'):
                    series_info['product_link'] = href
                else:
                    series_info['product_link'] = urljoin(base_url, '/' + href)
            
            # Lấy ảnh từ <img>
            img_elem = td1.find('img')
            if img_elem and img_elem.get('src'):
                img_src = img_elem.get('src')
                if img_src.startswith('/'):
                    series_info['image_url'] = urljoin(base_url, img_src)
                elif img_src.startswith('http'):
                    series_info['image_url'] = img_src
                else:
                    series_info['image_url'] = urljoin(base_url, '/' + img_src)
        
        # Cột 2: Tên series (td class="bold")
        td2 = tds[1] if len(tds) > 1 else None
        if td2:
            # Lấy text từ <a> trong td class="bold"
            link_elem = td2.find('a')
            if link_elem:
                series_info['series_name'] = link_elem.get_text(strip=True)
            
            # Nếu không có link, lấy text trực tiếp
            if not series_info['series_name']:
                series_info['series_name'] = td2.get_text(strip=True)
        
        # Cột 3: Mã sản phẩm
        td3 = tds[2] if len(tds) > 2 else None
        if td3:
            # Lấy text từ <a> hoặc trực tiếp từ td
            # BeautifulSoup tự động chuyển <br> thành newline trong get_text()
            codes_text = ''
            link_elem = td3.find('a')
            if link_elem:
                # Dùng separator='\n' để giữ newline từ <br>
                codes_text = link_elem.get_text(separator='\n', strip=False)
            else:
                codes_text = td3.get_text(separator='\n', strip=False)
            
            # Chuyển newline thành <br> để parse_product_codes có thể xử lý
            codes_text = codes_text.replace('\n', '<br>')
            
            # Parse mã sản phẩm
            series_info['product_codes'] = parse_product_codes(codes_text)
        
        # Kiểm tra nếu có đủ thông tin cơ bản
        if series_info['series_name'] or series_info['product_link']:
            return series_info
        
        return None
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất thông tin từ <tr>: {str(e)}")
        traceback.print_exc()
        return None

def extract_woonyoung_series_from_category(category_url):
    """
    Trích xuất tất cả series sản phẩm từ trang danh mục.
    
    Args:
        category_url (str): URL trang danh mục, ví dụ:
            https://woonyoung.com/eng/page/s0201_list.php?catetopcode=...
    
    Returns:
        list: Danh sách các dictionary chứa thông tin series
    """
    series_list = []
    
    try:
        html = get_html_content(category_url)
        if not html:
            logger.error(f"Không thể tải HTML từ URL: {category_url}")
            return series_list
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Tìm tất cả các <tr> chứa thông tin sản phẩm
        # Có thể cần điều chỉnh selector tùy theo cấu trúc HTML thực tế
        tr_elements = soup.find_all('tr')
        
        logger.info(f"Tìm thấy {len(tr_elements)} phần tử <tr> trong trang danh mục")
        
        for tr in tr_elements:
            # Kiểm tra xem <tr> có chứa link sản phẩm không
            # (loại bỏ header row nếu có)
            link_elem = tr.find('a', href=re.compile(r's0201_view\.php'))
            if not link_elem:
                continue
            
            # Trích xuất thông tin từ <tr>
            series_info = extract_series_info_from_tr(tr, category_url)
            
            if series_info:
                series_list.append(series_info)
                logger.info(f"Đã lấy thông tin series: {series_info.get('series_name', 'Unknown')}")
        
        logger.info(f"Tổng cộng tìm thấy {len(series_list)} series sản phẩm")
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất series từ danh mục: {str(e)}")
        traceback.print_exc()
    
    return series_list

def extract_child_categories_from_parent_mid(parent_url):
    """
    Nhận diện URL danh mục cha (/s0201_mid.php) và trích xuất toàn bộ danh mục con (/s0201_list.php).
    Chỉ quét các link trong phần tử <li class="on"><div class="dep3">.
    Trả về cấu trúc JSON theo yêu cầu.
    """
    result = {
        'parent_category': '',
        'url_parent': parent_url,
        'child_categories': []
    }
    try:
        # Xác định parent_category từ query (gcname hoặc gn_sub)
        parsed = urlparse(parent_url)
        qs = parse_qs(parsed.query)
        parent_cat = unquote(qs.get('gcname', [''])[0]).strip() or unquote(qs.get('gn_sub', [''])[0]).strip()
        result['parent_category'] = parent_cat or 'WOONYOUNG'

        html = get_html_content(parent_url)
        if not html:
            logger.error(f"Không thể tải HTML từ URL cha: {parent_url}")
            return result

        soup = BeautifulSoup(html, 'html.parser')

        # Tìm phần tử <li class="on"> chứa danh mục cha
        li_on = soup.find('li', class_='on')
        if not li_on:
            logger.warning(f"Không tìm thấy <li class='on'> trong trang: {parent_url}")
            # Fallback: quét toàn trang như cũ
            raw_links = soup.find_all('a', href=True)
        else:
            # Tìm <div class="dep3"> bên trong <li class="on">
            dep3_div = li_on.find('div', class_='dep3')
            if not dep3_div:
                logger.warning(f"Không tìm thấy <div class='dep3'> trong <li class='on'>: {parent_url}")
                # Fallback: quét toàn trang như cũ
                raw_links = soup.find_all('a', href=True)
            else:

                raw_links = dep3_div.find_all('a', href=True)
                logger.info(f"Tìm thấy {len(raw_links)} link trong <div class='dep3'>")

        seen = set()
        child_links = []
        for a in raw_links:
            href = (a.get('href') or '').strip()
            if not href:
                continue
            if 's0201_list.php' not in href:
                continue
            # Xây dựng URL tuyệt đối
            full_url = urljoin(parent_url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            # Tên: text của thẻ <a> (gộp khoảng trắng, bỏ <br>), nếu có <em> thì thêm (em)
            base_text = a.get_text(separator=' ', strip=True)
            # Dọn sạch khoảng trắng thừa
            base_text = re.sub(r'\s+', ' ', base_text).strip()
            em = a.find('em')
            em_text = ''
            if em:
                em_text = em.get_text(strip=True)
            name = base_text if base_text else full_url
            if em_text and em_text not in base_text:
                name = f"{base_text} ({em_text})"

            # Nếu URL con có ogcname thì cập nhật parent nếu chưa có
            try:
                parsed_child = urlparse(full_url)
                qs_child = parse_qs(parsed_child.query)
                ogcname = unquote(qs_child.get('ogcname', [''])[0]).strip()
                if ogcname and not result['parent_category']:
                    result['parent_category'] = ogcname
            except Exception:
                pass

            child_links.append({'name': name, 'url': full_url})

        result['child_categories'] = child_links
        logger.info(f"Đã trích xuất {len(child_links)} danh mục con từ danh mục cha: {result['parent_category']}")
        return result
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất danh mục con từ URL cha: {str(e)}")
        traceback.print_exc()
        return result

def extract_product_detail(product_link):
    """
    Trích xuất thông tin chi tiết từ trang sản phẩm.
    
    Args:
        product_link (str): URL trang chi tiết sản phẩm
    
    Returns:
        dict: Dictionary chứa:
            - type: TYPE của sản phẩm (từ <tr><th>TYPE</th><td>...</td></tr>)
            - product_image_url: URL ảnh sản phẩm
            - spec_image_url: URL ảnh thông số kỹ thuật
    """
    try:
        html = get_html_content(product_link)
        if not html:
            logger.error(f"Không thể tải HTML từ: {product_link}")
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        
        detail = {
            'type': '',
            'product_image_url': None,  # ảnh chính (một ảnh)
            'spec_image_urls': []      # danh sách ảnh thông số kỹ thuật
        }
        
        # 1. Lấy TYPE từ bảng thông tin
        # Tìm tất cả <tr> và kiểm tra <th> chứa "TYPE"
        trs = soup.find_all('tr')
        for tr in trs:
            th = tr.find('th')
            td = tr.find('td')
            if th and td:
                th_text = th.get_text(strip=True).upper()
                if 'TYPE' in th_text:
                    detail['type'] = td.get_text(strip=True)
                    logger.info(f"Tìm thấy TYPE: {detail['type']}")
                    break
        
        # 2. Lấy ảnh sản phẩm: ưu tiên dt > a.fancybox > img, sau đó fallback /data/item hoặc /upload/item
        product_img = None
        fancy_img = soup.select_one('dt a.fancybox img')
        if fancy_img and fancy_img.get('src'):
            product_img = fancy_img
        if not product_img:
            product_img = soup.find('img', src=re.compile(r'/(data|upload)/item/'))
        if product_img and product_img.get('src'):
            img_src = product_img.get('src')
            if img_src.startswith('/'):
                detail['product_image_url'] = urljoin(product_link, img_src)
            elif img_src.startswith('http'):
                detail['product_image_url'] = img_src
            else:
                detail['product_image_url'] = urljoin(product_link, '/' + img_src)
            logger.info(f"Tìm thấy ảnh sản phẩm: {detail['product_image_url']}")
        
        # 3. Lấy toàn bộ ảnh thông số kỹ thuật trong khối div.mt60.pro_box
        spec_imgs = []
        # Thử nhiều selector để tìm container
        spec_container = None
        for selector in ['div.mt60.pro_box', 'div.pro_box', 'div[class*="pro_box"]', 'div[class*="mt60"]']:
            spec_container = soup.select_one(selector)
            if spec_container:
                logger.info(f"Tìm thấy container spec bằng selector: {selector}")
                break
        
        if spec_container:
            # Lấy tất cả ảnh trong container
            spec_imgs = spec_container.find_all('img', src=True)
            logger.info(f"Tìm thấy {len(spec_imgs)} ảnh trong container div.mt60.pro_box")
        else:
            # fallback: tìm tất cả ảnh /data/ckeditor_img khắp trang nếu không có container
            logger.warning("Không tìm thấy container div.mt60.pro_box, dùng fallback tìm toàn trang")
            spec_imgs = soup.find_all('img', src=re.compile(r'/data/ckeditor_img/'))
        
        for img in spec_imgs:
            src = img.get('src', '')
            if not src:
                continue
            # Chỉ lấy ảnh từ /data/ckeditor_img/
            if '/data/ckeditor_img/' not in src:
                continue
            # Chuyển đổi URL tương đối thành tuyệt đối
            full_url = src
            if src.startswith('/'):
                full_url = urljoin(product_link, src)
            elif not src.startswith('http'):
                full_url = urljoin(product_link, '/' + src)
            # Tránh trùng lặp
            if full_url not in detail['spec_image_urls']:
                detail['spec_image_urls'].append(full_url)
                logger.info(f"  → Thêm ảnh SPEC: {full_url}")
        
        if detail['spec_image_urls']:
            logger.info(f"Tổng cộng tìm thấy {len(detail['spec_image_urls'])} ảnh thông số kỹ thuật")
        else:
            logger.warning("Không tìm thấy ảnh thông số kỹ thuật nào")
        
        return detail
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất chi tiết sản phẩm từ {product_link}: {str(e)}")
        traceback.print_exc()
        return None

def generate_product_url_and_image_links(product_code, max_spec_images=5):
    """
    Tạo link ảnh sản phẩm và ảnh SPEC từ mã sản phẩm nguyên bản.
    KHÔNG xử lý/biến đổi nội dung mã sản phẩm, chỉ chuẩn hóa để dùng làm tên file.
    
    Args:
        product_code (str): Mã sản phẩm gốc (ví dụ: "WY11-100UL")
        max_spec_images (int): Số lượng ảnh spec tối đa để tạo link (mặc định: 5)
    
    Returns:
        dict: Dictionary chứa:
            - product_url: (để trống, không tạo URL sản phẩm)
            - product_image_url: Link ảnh sản phẩm (dự đoán theo quy tắc đặt tên)
            - spec_image_urls: Danh sách link ảnh spec (phân cách bằng "; ")
    """
    image_base = "https://haiphongtech.vn/wp-content/uploads/temp-images/"
    
    if not product_code:
        return {
            'product_url': '',
            'product_image_url': '',
            'spec_image_urls': ''
        }
    
    code = str(product_code).strip()
    # Áp dụng logic GAS cho tên file ảnh (UPPERCASE + chuẩn hóa '-')
    filename_code = standardize_for_image_filename(code)
    
    # Link ảnh sản phẩm
    product_image_url = image_base + filename_code + ".webp"
    
    # Link ảnh SPEC
    spec_links = []
    for i in range(1, max_spec_images + 1):
        spec_links.append(image_base + filename_code + f"-SPEC-{i}.webp")
    spec_image_urls = "; ".join(spec_links)
    
    return {
        'product_url': '',
        'product_image_url': product_image_url,
        'spec_image_urls': spec_image_urls
    }

def download_woonyoung_image(image_url, output_path, referer_url=None, max_retries=3):
    """
    Tải ảnh từ URL và chuyển đổi sang WebP.
    
    Args:
        image_url (str): URL ảnh cần tải
        output_path (str): Đường dẫn file WebP đầu ra
        referer_url (str): URL referer (tùy chọn)
        max_retries (int): Số lần thử lại tối đa
    
    Returns:
        str: Đường dẫn file WebP đã tạo, hoặc None nếu thất bại
    """
    try:
        # Kiểm tra file đã tồn tại chưa
        if os.path.exists(output_path):
            logger.info(f"Ảnh đã tồn tại: {output_path}")
            return output_path
        
        # Import WebP converter
        from app.webp_converter import WebPConverter
        
        # Tải ảnh với retry logic
        for retry in range(max_retries):
            try:
                logger.info(f"Đang tải ảnh {retry+1}/{max_retries}: {image_url[:60]}...")
                
                # Headers để tránh bị chặn
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                    'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
                }
                if referer_url:
                    headers['Referer'] = referer_url
                
                response = requests.get(image_url, timeout=30, headers=headers, stream=True)
                response.raise_for_status()
                
                # Kiểm tra content type
                content_type = response.headers.get('content-type', '').lower()
                if not any(img_type in content_type for img_type in ['image/', 'jpeg', 'jpg', 'png', 'webp', 'gif']):
                    logger.warning(f"Không phải ảnh: {content_type}")
                    if retry < max_retries - 1:
                        time.sleep(1)
                        continue
                    return None
                
                # Chuyển đổi bytes ảnh sang WebP trực tiếp
                webp_path = WebPConverter.convert_bytes_to_webp(response.content, output_path)
                if webp_path and os.path.exists(webp_path):
                    logger.info(f"✅ Tải và chuyển đổi thành công: {os.path.basename(output_path)}")
                    return webp_path
                else:
                    logger.warning(f"Chuyển đổi WebP thất bại: {output_path}")
                    if retry < max_retries - 1:
                        time.sleep(1)
                        continue
                    return None
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Lỗi tải ảnh lần {retry + 1}: {str(e)[:50]}...")
                if retry < max_retries - 1:
                    time.sleep(2)
                else:
                    return None
            except Exception as e:
                logger.error(f"Lỗi không xác định: {str(e)[:50]}...")
                return None
        
        return None
        
    except Exception as e:
        logger.error(f"Lỗi khi tải ảnh: {str(e)}")
        traceback.print_exc()
        return None

def process_product_details(product_code, product_link, output_images_dir, output_spec_dir):
    """
    Xử lý chi tiết cho một mã sản phẩm: lấy TYPE, tải ảnh, tạo tên sản phẩm.
    
    Args:
        product_code (str): Mã sản phẩm (ví dụ: "WY11-100UL")
        product_link (str): Link trang chi tiết sản phẩm
        output_images_dir (str): Thư mục lưu ảnh sản phẩm
        output_spec_dir (str): Thư mục lưu ảnh thông số kỹ thuật
    
    Returns:
        dict: Thông tin sản phẩm đã xử lý:
            - product_name: Tên sản phẩm (TYPE + mã)
            - product_code: Mã sản phẩm
            - product_image_path: Đường dẫn ảnh sản phẩm (WebP)
            - spec_image_paths: Danh sách đường dẫn ảnh thông số kỹ thuật (WebP)
            - product_url: URL sản phẩm trên haiphongtech.vn
            - product_image_url: Link ảnh sản phẩm trên haiphongtech.vn
            - spec_image_urls: Danh sách link ảnh spec trên haiphongtech.vn (phân cách bằng "; ")
    """
    try:
        # Lấy chi tiết từ trang sản phẩm
        detail = extract_product_detail(product_link)
        
        if not detail:
            logger.warning(f"Không lấy được chi tiết cho {product_code}")
            # Tạo URL sản phẩm và link ảnh từ mã sản phẩm
            url_data = generate_product_url_and_image_links(product_code)
            return {
                'product_name': product_code,  # Fallback: chỉ dùng mã
                'product_code': product_code,
                'product_image_path': None,
                'spec_image_paths': [],  # Trả về list rỗng thay vì None
                'product_url': url_data['product_url'],
                'product_image_url': url_data['product_image_url'],
                'spec_image_urls': url_data['spec_image_urls']
            }
        
        # Tạo tên sản phẩm: TYPE + mã sản phẩm
        type_name = detail.get('type', '').strip()
        if type_name:
            product_name = f"{type_name} {product_code}"
        else:
            product_name = product_code  # Fallback nếu không có TYPE
        
        result = {
            'product_name': product_name,
            'product_code': product_code,
            'product_image_path': None,
            'spec_image_paths': []
        }
        
        # Tạo URL sản phẩm và link ảnh từ mã sản phẩm
        url_data = generate_product_url_and_image_links(product_code)
        result['product_url'] = url_data['product_url']
        result['product_image_url'] = url_data['product_image_url']
        result['spec_image_urls'] = url_data['spec_image_urls']
        
        # Tạo thư mục nếu chưa có
        os.makedirs(output_images_dir, exist_ok=True)
        os.makedirs(output_spec_dir, exist_ok=True)
        
        # Làm sạch mã sản phẩm để dùng làm tên file ảnh theo logic GAS (UPPERCASE)
        clean_code = standardize_for_image_filename(product_code)
        
        # 1. Tải và chuyển đổi ảnh sản phẩm
        if detail.get('product_image_url'):
            product_image_path = os.path.join(output_images_dir, f"{clean_code}.webp")
            downloaded = download_woonyoung_image(
                detail['product_image_url'],
                product_image_path,
                referer_url=product_link
            )
            if downloaded:
                result['product_image_path'] = downloaded
        
        # 2. Tải và chuyển đổi toàn bộ ảnh thông số kỹ thuật, đặt tên {CODE}-SPEC-{i}.webp
        spec_urls = detail.get('spec_image_urls', [])
        if spec_urls:
            logger.info(f"Bắt đầu tải {len(spec_urls)} ảnh SPEC cho {product_code}")
            for idx, spec_url in enumerate(spec_urls, start=1):
                spec_filename = f"{clean_code}-SPEC-{idx}.webp"
                spec_image_path = os.path.join(output_spec_dir, spec_filename)
                logger.info(f"  [{idx}/{len(spec_urls)}] Tải ảnh SPEC: {spec_filename}")
                downloaded = download_woonyoung_image(
                    spec_url,
                    spec_image_path,
                    referer_url=product_link
                )
                if downloaded:
                    result['spec_image_paths'].append(downloaded)
                    logger.info(f"  ✅ Đã tải thành công: {spec_filename}")
                else:
                    logger.warning(f"  ❌ Không tải được: {spec_filename}")
        else:
            logger.warning(f"Không có ảnh SPEC nào cho {product_code}")
        
        return result
        
    except Exception as e:
        logger.error(f"Lỗi khi xử lý chi tiết sản phẩm {product_code}: {str(e)}")
        traceback.print_exc()
        # Tạo URL sản phẩm và link ảnh từ mã sản phẩm
        url_data = generate_product_url_and_image_links(product_code)
        return {
            'product_name': product_code,
            'product_code': product_code,
            'product_image_path': None,
            'spec_image_paths': [],  # Trả về list rỗng thay vì None
            'product_url': url_data['product_url'],
            'product_image_url': url_data['product_image_url'],
            'spec_image_urls': url_data['spec_image_urls']
        }

def create_woonyoung_excel_report(products_list, output_file):
    """
    Tạo báo cáo Excel cho danh sách sản phẩm WOONYOUNG đã xử lý.
    Mỗi mã sản phẩm là một dòng riêng trong Excel.
    
    Args:
        products_list (list): Danh sách các dictionary chứa thông tin sản phẩm đã xử lý:
            - product_name: Tên sản phẩm (TYPE + mã)
            - product_code: Mã sản phẩm
            - product_link: Link chi tiết
            - product_image_path: Đường dẫn ảnh sản phẩm
            - spec_image_path: Đường dẫn ảnh thông số kỹ thuật
        output_file (str): Đường dẫn file Excel đầu ra
    
    Returns:
        bool: True nếu thành công, False nếu có lỗi
    """
    try:
        # Chuẩn bị dữ liệu cho Excel
        excel_data = []
        
        for product in products_list:
            spec_paths = product.get('spec_image_paths') or product.get('spec_image_path') or []
            if isinstance(spec_paths, list):
                spec_paths_str = "; ".join(spec_paths)
            else:
                spec_paths_str = str(spec_paths)
            
            # Lấy URL sản phẩm và link ảnh (ưu tiên từ result, nếu không có thì tạo mới)
            product_url = product.get('product_url', '')
            product_image_url = product.get('product_image_url', '')
            spec_image_urls = product.get('spec_image_urls', '')
            
            # Nếu chưa có, tạo từ mã sản phẩm
            if not product_url or not product_image_url:
                url_data = generate_product_url_and_image_links(product.get('product_code', ''))
                product_url = product_url or url_data['product_url']
                product_image_url = product_image_url or url_data['product_image_url']
                spec_image_urls = spec_image_urls or url_data['spec_image_urls']
            
            excel_data.append({
                'Tên sản phẩm': product.get('product_name', ''),
                'Mã sản phẩm': product.get('product_code', ''),
                'Link sản phẩm': product.get('product_link', ''),
                'Đường dẫn sản phẩm': product_url,  # URL trên haiphongtech.vn
                'Ảnh sản phẩm': product.get('product_image_path', ''),
                'Link ảnh đã xử lý': product_image_url,  # Link ảnh sản phẩm trên haiphongtech.vn
                'Ảnh thông số kỹ thuật': spec_paths_str,
                'Link ảnh thông số kỹ thuật': spec_image_urls  # Link ảnh spec trên haiphongtech.vn
            })
        
        # Tạo DataFrame và lưu Excel
        df = pd.DataFrame(excel_data)
        df.to_excel(output_file, index=False, engine='openpyxl')
        
        logger.info(f"Đã tạo file Excel: {output_file} với {len(excel_data)} dòng")
        return True
        
    except Exception as e:
        logger.error(f"Lỗi khi tạo Excel: {str(e)}")
        traceback.print_exc()
        return False

class WOONYOUNGCrawler:
    """Crawler chuyên dụng cho website woonyoung.com"""
    
    def __init__(self, output_root="output_woonyoung", max_workers=8, max_retries=3):
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        os.makedirs(self.output_root, exist_ok=True)
    
    def crawl_category(self, category_url):
        """
        Cào dữ liệu từ một URL danh mục.
        
        Args:
            category_url (str): URL trang danh mục
        
        Returns:
            tuple: (list các series, đường dẫn folder kết quả)
        """
        start_time = time.time()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"WOONYOUNG_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        # Thông báo bắt đầu (chỉ emit nếu socketio đã được khởi tạo)
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 0,
                    'message': f'Bắt đầu cào dữ liệu từ woonyoung.com',
                    'detail': f'Đang xử lý URL: {category_url}'
                })
                
                # Trích xuất series từ danh mục
                socketio.emit('progress_update', {
                    'percent': 20,
                    'message': 'Đang quét danh sách series sản phẩm...',
                    'detail': ''
                })
            except Exception:
                pass  # Bỏ qua nếu không thể emit
        
        series_list = extract_woonyoung_series_from_category(category_url)
        
        if not series_list:
            if socketio and hasattr(socketio, 'emit'):
                try:
                    socketio.emit('progress_update', {
                        'percent': 100,
                        'message': 'Không tìm thấy series nào',
                        'detail': 'Vui lòng kiểm tra lại URL danh mục'
                    })
                except Exception:
                    pass
            return [], result_dir
        
        # Tạo thư mục cho ảnh
        images_dir = os.path.join(result_dir, "images")
        specs_dir = os.path.join(result_dir, "specs")
        os.makedirs(images_dir, exist_ok=True)
        os.makedirs(specs_dir, exist_ok=True)
        
        # Thu thập tất cả mã sản phẩm cần xử lý
        all_products_to_process = []
        for series in series_list:
            product_link = series.get('product_link', '')
            product_codes = series.get('product_codes', [])
            for code in product_codes:
                all_products_to_process.append({
                    'product_code': code,
                    'product_link': product_link
                })
        
        total_products = len(all_products_to_process)
        logger.info(f"Tổng số sản phẩm cần xử lý: {total_products}")
        
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 30,
                    'message': f'Đã tìm thấy {len(series_list)} series, {total_products} sản phẩm',
                    'detail': 'Đang xử lý chi tiết từng sản phẩm...'
                })
            except Exception:
                pass
        
        # Xử lý từng sản phẩm với đa luồng
        processed_products = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_product = {
                executor.submit(
                    process_product_details,
                    prod['product_code'],
                    prod['product_link'],
                    images_dir,
                    specs_dir
                ): prod
                for prod in all_products_to_process
            }
            
            for i, future in enumerate(as_completed(future_to_product)):
                product_info = future_to_product[future]
                try:
                    result = future.result()
                    # Thêm link sản phẩm vào kết quả
                    result['product_link'] = product_info['product_link']
                    processed_products.append(result)
                    
                    progress = 30 + int((i + 1) / total_products * 50)
                    if socketio and hasattr(socketio, 'emit'):
                        try:
                            socketio.emit('progress_update', {
                                'percent': progress,
                                'message': f'Đang xử lý sản phẩm {i+1}/{total_products}',
                                'detail': f'Sản phẩm: {result.get("product_code", "Unknown")}'
                            })
                        except Exception:
                            pass
                    
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý sản phẩm {product_info.get('product_code')}: {str(e)}")
                    # Vẫn thêm sản phẩm với thông tin cơ bản
                    # Tạo URL sản phẩm và link ảnh từ mã sản phẩm
                    product_code = product_info.get('product_code', '')
                    url_data = generate_product_url_and_image_links(product_code)
                    processed_products.append({
                        'product_name': product_code,
                        'product_code': product_code,
                        'product_link': product_info.get('product_link', ''),
                        'product_image_path': None,
                        'spec_image_paths': [],
                        'product_url': url_data['product_url'],
                        'product_image_url': url_data['product_image_url'],
                        'spec_image_urls': url_data['spec_image_urls']
                    })
        
        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 85,
                    'message': f'Đã xử lý {len(processed_products)} sản phẩm',
                    'detail': 'Đang tạo báo cáo Excel...'
                })
            except Exception:
                pass
        
        # Tạo báo cáo Excel
        excel_file = os.path.join(result_dir, f"WOONYOUNG_ngay{timestamp}.xlsx")
        create_woonyoung_excel_report(processed_products, excel_file)
        
        # Tạo tên ZIP theo đúng danh mục: gnsub + (em text trong link danh mục hiện hành)
        def _sanitize_filename(name):
            # Loại ký tự không hợp lệ với tên file Windows và chuẩn hóa khoảng trắng
            name = re.sub(r"[\\/*?:\"<>|]", " ", name)
            name = re.sub(r"\s+", " ", name).strip()
            return name or f"WOONYOUNG_{timestamp}"

        def _extract_category_em_text(category_url):
            try:
                html = get_html_content(category_url)
                if not html:
                    return ''
                soup = BeautifulSoup(html, 'html.parser')
                current_link = soup.select_one('a.on')
                if not current_link:
                    return ''
                em = current_link.find('em')
                if em:
                    return em.get_text(strip=True)
                return ''
            except Exception:
                return ''

        def _build_series_zip_name(category_url, products):
            # Lấy gnsub từ query và em-text từ trang
            parsed = urlparse(category_url)
            qs = parse_qs(parsed.query)
            gnsub = unquote(qs.get('gnsub', [''])[0]).strip()
            em_text = _extract_category_em_text(category_url)

            # Quy tắc: "<gnsub><em_text>" không chèn thêm khoảng trắng
            # Ví dụ: gnsub = "UL Transformer", em = "1Phase" => "UL Transformer1Phase"
            raw_title = f"{gnsub}{em_text}" if gnsub or em_text else f"WOONYOUNG_{timestamp}"
            return _sanitize_filename(raw_title)

        zip_name = _build_series_zip_name(category_url, processed_products)  
        zip_file = os.path.join(result_dir, f"{zip_name}.zip")
        
        # Log thống kê file trước khi đóng gói ZIP
        spec_files_count = 0
        image_files_count = 0
        for root, dirs, files in os.walk(result_dir):
            for file in files:
                if '-SPEC-' in file:
                    spec_files_count += 1
                elif file.endswith('.webp') and '-SPEC-' not in file:
                    image_files_count += 1
        logger.info(f"Trước khi đóng gói ZIP: {spec_files_count} file SPEC, {image_files_count} file ảnh sản phẩm")
        
        with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:  
            added_files = {}
            spec_files_added = 0
            for root, dirs, files in os.walk(result_dir):
                for file in files:
                    if not file.endswith('.zip'):
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, result_dir)
                        # Kiểm tra trùng lặp arcname
                        if arcname in added_files:
                            logger.warning(f"⚠️ File trùng tên trong ZIP: {arcname} (file cũ: {added_files[arcname]}, file mới: {file_path})")
                        else:
                            added_files[arcname] = file_path
                            if '-SPEC-' in file:
                                spec_files_added += 1
                        zipf.write(file_path, arcname)
            
            logger.info(f"Đã đóng gói ZIP: {spec_files_added} file SPEC, tổng {len(added_files)} file")
        
        end_time = time.time()
        duration = end_time - start_time

        if socketio and hasattr(socketio, 'emit'):
            try:
                socketio.emit('progress_update', {
                    'percent': 100,
                    'message': f'Hoàn thành! Đã xử lý {len(processed_products)} sản phẩm',
                    'detail': f'Thời gian: {duration:.2f}s'
                })
            except Exception:
                pass

        logger.info(f"WOONYOUNG HOÀN THÀNH: {len(processed_products)} sản phẩm | {duration:.1f}s")

        # Ghi metadata để chế độ nhiều danh mục có thể đọc zip_file mà không đổi chữ ký hàm
        try:
            meta_path = os.path.join(result_dir, 'result_meta.json')        
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump({'zip_file': zip_file}, f, ensure_ascii=False)    
        except Exception:
            pass

        # Dọn dẹp: xóa thư mục ảnh để rút gọn output (giữ lại ZIP/Excel/metadata)
        try:
            for folder in ('images', 'specs'):
                folder_path = os.path.join(result_dir, folder)
                if os.path.isdir(folder_path):
                    shutil.rmtree(folder_path, ignore_errors=True)
            logger.info("Đã dọn dẹp thư mục images/ và specs/ sau khi nén")
        except Exception as cleanup_err:
            logger.warning(f"Không thể dọn dẹp thư mục ảnh: {cleanup_err}")

        # Giữ nguyên chữ ký cũ để không làm hỏng nơi gọi hiện có
        return processed_products, result_dir

    def crawl_categories(self, category_urls):
        """
        Cào dữ liệu từ nhiều URL danh mục trong một lần.
        
        Args:
            category_urls (list|str): Danh sách URL hoặc chuỗi URL (ngăn cách bằng xuống dòng hoặc dấu phẩy)
        
        Returns:
            list: Danh sách kết quả cho từng danh mục, mỗi phần tử là dict:
                {
                    'category_url': str,
                    'products': list,
                    'result_dir': str,
                    'zip_file': str,
                    'success': bool,
                    'error': str|None
                }
        """
        # Chuẩn hóa input về list URL
        urls = []
        if isinstance(category_urls, str):
            # Tách theo xuống dòng hoặc dấu phẩy
            parts = re.split(r"[\n,]", category_urls)
            urls = [u.strip() for u in parts if u and u.strip()]
        elif isinstance(category_urls, (list, tuple, set)):
            urls = [str(u).strip() for u in category_urls if str(u).strip()]
        else:
            urls = []

        # Mở rộng: nếu có URL cha (/s0201_mid.php), trích xuất toàn bộ URL con /s0201_list.php
        expanded_urls = []
        parent_groups = []  # lưu JSON kết quả danh mục cha
        child_url_to_name = {}
        parent_to_children_urls = {}  # map parent_category -> list of child urls
        for u in urls:
            if '/s0201_mid.php' in u:
                parent_json = extract_child_categories_from_parent_mid(u)
                parent_groups.append(parent_json)
                parent_cat = parent_json.get('parent_category') or 'WOONYOUNG'
                for child in parent_json.get('child_categories', []):
                    child_url = child.get('url')
                    child_name = child.get('name') or child_url
                    if not child_url:
                        continue
                    expanded_urls.append(child_url)
                    child_url_to_name[child_url] = child_name
                    parent_to_children_urls.setdefault(parent_cat, []).append(child_url)
            else:
                expanded_urls.append(u)

        total = len(expanded_urls)
        results = []

        if total == 0:
            return results

        for idx, url in enumerate(expanded_urls, start=1):
            # Cập nhật tiến độ tổng nếu có socketio
            if socketio and hasattr(socketio, 'emit'):
                try:
                    socketio.emit('progress_update', {
                        'percent': int((idx - 1) / total * 100),
                        'message': f'Đang xử lý danh mục {idx}/{total}',
                        'detail': url
                    })
                except Exception:
                    pass

            try:
                products, result_dir = self.crawl_category(url)
                # Đọc zip_file từ metadata nếu có
                zip_file = ''
                try:
                    meta_path = os.path.join(result_dir, 'result_meta.json')
                    if os.path.exists(meta_path):
                        with open(meta_path, 'r', encoding='utf-8') as f:
                            meta = json.load(f)
                            zip_file = meta.get('zip_file', '')
                except Exception:
                    zip_file = ''
                results.append({
                    'category_url': url,
                    'products': products,
                    'result_dir': result_dir,
                    'zip_file': zip_file,
                    'success': True,
                    'error': None
                })
            except Exception as e:
                logger.error(f"Lỗi khi cào danh mục: {url} - {str(e)}")
                traceback.print_exc()
                results.append({
                    'category_url': url,
                    'products': [],
                    'result_dir': '',
                    'zip_file': '',
                    'success': False,
                    'error': str(e)
                })

            # Cập nhật tiến độ sau khi xong từng danh mục
            if socketio and hasattr(socketio, 'emit'):
                try:
                    socketio.emit('progress_update', {
                        'percent': int(idx / total * 100),
                        'message': f'Hoàn tất danh mục {idx}/{total}',
                        'detail': url
                    })
                except Exception:
                    pass

        # Nếu có nhóm parent, tạo ZIP cha chứa các file Excel và ảnh của từng danh mục con
        try:
            if parent_to_children_urls:
                # Hàm sanitize tên file an toàn
                def _sanitize_filename(name):
                    name = re.sub(r"[\\/*?:\"<>|]", " ", name)
                    name = re.sub(r"\s+", " ", name).strip()
                    return name or "WOONYOUNG"

                for parent_cat, child_urls in parent_to_children_urls.items():
                    parent_zip_name = f"{_sanitize_filename(parent_cat)}.zip"
                    parent_zip_path = os.path.join(self.output_root, parent_zip_name)
                    logger.info(f"Đang tạo ZIP cha: {parent_zip_name} chứa {len(child_urls)} danh mục con")
                    
                    with zipfile.ZipFile(parent_zip_path, 'w', zipfile.ZIP_DEFLATED) as parent_zip:
                        for child_url in child_urls:
                            # Tìm zip_file và result_dir tương ứng trong results
                            matching = next((r for r in results if r.get('category_url') == child_url), None)
                            if not matching:
                                continue
                            
                            child_zip_path = matching.get('zip_file')
                            child_result_dir = matching.get('result_dir')
                            
                            # Tên hiển thị của child (dùng để tạo thư mục con trong ZIP cha)
                            child_name = child_url_to_name.get(child_url, 'Unknown')
                            child_folder_name = _sanitize_filename(child_name)
                            
                            # Nếu có ZIP con, giải nén và thêm các file vào ZIP cha theo thư mục con
                            if child_zip_path and os.path.exists(child_zip_path):
                                try:
                                    with zipfile.ZipFile(child_zip_path, 'r') as child_zip:
                                        for member in child_zip.namelist():
                                            # Bỏ qua thư mục (kết thúc bằng /)
                                            if member.endswith('/'):
                                                continue
                                            # Đọc nội dung file từ ZIP con
                                            file_data = child_zip.read(member)
                                            # Giữ lại cấu trúc thư mục trong ZIP con
                                            # Tạo đường dẫn mới trong ZIP cha: {child_folder_name}/{member}
                                            new_arcname = f"{child_folder_name}/{member}"
                                            # Thêm file vào ZIP cha
                                            parent_zip.writestr(new_arcname, file_data)
                                    logger.info(f"  ✅ Đã thêm {child_folder_name} vào ZIP cha")
                                except Exception as extract_err:
                                    logger.warning(f"  ⚠️ Không thể giải nén từ ZIP con {child_zip_path}: {extract_err}")
                                    # Fallback: thêm ZIP con vào ZIP cha như cũ
                                    child_zip_name = f"{child_folder_name}.zip"
                                    parent_zip.write(child_zip_path, arcname=child_zip_name)
                            # Nếu không có ZIP con nhưng có result_dir, thêm trực tiếp các file
                            elif child_result_dir and os.path.exists(child_result_dir):
                                try:
                                    for root, dirs, files in os.walk(child_result_dir):
                                        for file in files:
                                            if file.endswith('.zip'):
                                                continue  # Bỏ qua file ZIP
                                            file_path = os.path.join(root, file)
                                            # Tạo đường dẫn tương đối từ result_dir
                                            rel_path = os.path.relpath(file_path, child_result_dir)
                                            # Tạo đường dẫn mới trong ZIP cha
                                            new_arcname = f"{child_folder_name}/{rel_path}"
                                            parent_zip.write(file_path, arcname=new_arcname)
                                    logger.info(f"  ✅ Đã thêm {child_folder_name} vào ZIP cha từ result_dir")
                                except Exception as dir_err:
                                    logger.warning(f"  ⚠️ Không thể thêm từ result_dir {child_result_dir}: {dir_err}")

                    logger.info(f"✅ Đã tạo ZIP cha: {parent_zip_name}")
                    
                    # Ghi lại đường dẫn ZIP cha vào kết quả tổng (thêm một entry tổng hợp)
                    results.append({
                        'category_url': f'[PARENT]{parent_cat}',
                        'products': [],
                        'result_dir': self.output_root,
                        'zip_file': parent_zip_path,
                        'success': True,
                        'error': None
                    })
        except Exception as bundle_err:
            logger.warning(f"Không thể tạo ZIP cha: {bundle_err}")
            traceback.print_exc()

        return results

def test_parse_product_codes():
    """Test hàm parse_product_codes"""
    print("🧪 Test hàm parse_product_codes()...")
    
    test_cases = [
        ("WYF21-40A WYF21-60A WYF21-100A", ["WYF21-40A", "WYF21-60A", "WYF21-100A"]),
        ("WY11-100UL<br>WY11-200UL<br>WY11-300UL", ["WY11-100UL", "WY11-200UL", "WY11-300UL"]),
        ("WYF21-40A  WYF21-60A  ", ["WYF21-40A", "WYF21-60A"]),
        ("", []),
    ]
    
    for input_str, expected in test_cases:
        result = parse_product_codes(input_str)
        print(f"Input: {repr(input_str)}")
        print(f"Expected: {expected}")
        print(f"Result: {result}")
        print(f"Match: {result == expected}")
        print("-" * 50)

if __name__ == "__main__":
    print("🚀 Bắt đầu test crawler WOONYOUNG...")
    
    # Test 1: Test hàm parse_product_codes
    test_parse_product_codes()
    
    print("\n" + "="*50 + "\n")
    
    # Test 2: Test với URL thực tế (nếu có)
    # category_url = "https://woonyoung.com/eng/page/s0201_list.php?catetopcode=1337583451&ocatecode=1337583451&gcatecode=1535699731&gcname=%EC%88%98%EB%B0%B0%EC%A0%84%EB%B0%98+%EC%A0%84%EB%A0%A5%EA%B8%B0%EA%B8%B0+Solution&gnsub=1Phase&ogcname=Transformer"
    # series_list = extract_woonyoung_series_from_category(category_url)
    # print(f"Tìm thấy {len(series_list)} series")
