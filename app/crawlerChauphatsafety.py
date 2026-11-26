"""
Chauphatsafety Product Crawler - Cào dữ liệu sản phẩm từ website chauphatsafety.vn
Phiên bản: 1.0
Ngày tạo: 2024
"""

import os
import shutil
import time
import zipfile
import html as html_module
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

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Gemini AI Translator
try:
    from app.utils import init_gemini_translator, translate_with_gemini, translate_specifications_with_gemini
    GEMINI_TRANSLATE_AVAILABLE = True
except Exception:
    GEMINI_TRANSLATE_AVAILABLE = False

def parse_html_with_encoding(html_content):
    """
    Parse HTML với encoding đúng để xử lý tiếng Việt
    
    Args:
        html_content: HTML content (string hoặc bytes)
    
    Returns:
        BeautifulSoup object
    """
    if isinstance(html_content, bytes):
        # Thử decode với UTF-8 trước
        try:
            html_content = html_content.decode('utf-8')
        except UnicodeDecodeError:
            # Fallback sang các encoding khác
            try:
                html_content = html_content.decode('latin-1')
            except:
                html_content = html_content.decode('utf-8', errors='replace')
    
    # Parse với BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    return soup

def safe_socketio_emit(event: str, data: dict):
    """
    Emit Socket.IO một cách an toàn.
    """
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"Lỗi khi emit Socket.IO ({event}): {e}")

def extract_chauphatsafety_product_urls(category_url):
    """
    Trích xuất danh sách URL sản phẩm từ trang danh mục chauphatsafety.vn
    Quét toàn bộ trang danh mục và hỗ trợ phân trang nếu có
    
    Args:
        category_url: URL trang danh mục, ví dụ:
            https://chauphatsafety.vn/vn/khoa-canh-bao-an-toan-lockout.html/p-1,brand-1
    
    Returns:
        list: Danh sách URL sản phẩm unique
    """
    product_urls = []
    
    try:
        # Phát hiện phân trang từ URL
        # URL có dạng: .../p-1,brand-1 hoặc .../p-2,brand-1
        base_url = category_url.split('/p-')[0] if '/p-' in category_url else category_url
        
        # Thử lấy sản phẩm từ trang đầu tiên
        page = 1
        max_pages = 100  # Giới hạn tối đa để tránh vòng lặp vô hạn
        
        while page <= max_pages:
            # Tạo URL phân trang
            if page == 1:
                page_url = base_url
            else:
                # Tìm pattern phân trang trong URL gốc
                if '/p-' in category_url:
                    # Thay thế số trang
                    page_url = re.sub(r'/p-\d+', f'/p-{page}', category_url)
                else:
                    # Thêm phân trang vào cuối
                    if base_url.endswith('.html'):
                        page_url = f"{base_url}/p-{page}"
                    else:
                        page_url = f"{base_url}?page={page}"
            
            logger.info(f"🔍 Đang quét trang {page}: {page_url}")
            
            html = get_html_content(page_url)
            if not html:
                logger.warning(f"⚠️  Không thể tải trang {page}")
                break
            
            # Parse HTML với encoding đúng
            soup = parse_html_with_encoding(html)
            
            # Tìm các link sản phẩm
            # Pattern: https://chauphatsafety.vn/vn/khoa-co-lap-van-bi-tay-cam-chu-t-1-25-2-t-handle-ball-valve-lockout-151749-brady.html
            found_links = []
            
            # Tìm tất cả link có chứa pattern sản phẩm
            all_links = soup.find_all('a', href=True)
            for link in all_links:
                href = link.get('href', '').strip()
                if not href:
                    continue
                
                # Chuyển đổi relative URL thành absolute
                if href.startswith('/'):
                    full_url = urljoin('https://chauphatsafety.vn', href)
                elif href.startswith('http'):
                    full_url = href
                else:
                    full_url = urljoin(page_url, href)
                
                # Kiểm tra xem có phải là link sản phẩm không
                # Link sản phẩm thường có dạng: .../vn/...-số-brady.html hoặc tương tự
                if ('chauphatsafety.vn/vn/' in full_url and 
                    full_url.endswith('.html') and 
                    '/p-' not in full_url and  # Loại bỏ link phân trang
                    full_url not in product_urls):
                    
                    # Kiểm tra thêm: link sản phẩm thường có số ở cuối trước .html
                    # Ví dụ: ...-151749-brady.html
                    if re.search(r'-\d+-[^/]+\.html$', full_url) or re.search(r'-\d+\.html$', full_url):
                        product_urls.append(full_url)
                        found_links.append(full_url)
                        logger.debug(f"  ✅ Tìm thấy sản phẩm: {full_url}")
            
            logger.info(f"   📊 Trang {page}: Tìm thấy {len(found_links)} sản phẩm")
            
            # Nếu không tìm thấy sản phẩm nào, dừng lại
            if not found_links:
                logger.info(f"   ⚠️  Không tìm thấy sản phẩm ở trang {page}, dừng quét phân trang")
                break
            
            page += 1
            
            # Kiểm tra xem có link "Trang sau" hoặc "Next" không
            next_link = soup.find('a', string=re.compile(r'(Trang sau|Next|Tiếp|>|»)', re.I))
            if not next_link:
                # Thử tìm link có href chứa số trang lớn hơn
                next_page_links = soup.find_all('a', href=re.compile(r'/p-' + str(page)))
                if not next_page_links:
                    logger.info(f"   ✅ Không còn trang tiếp theo, dừng quét")
                    break
        
        # Loại bỏ trùng lặp
        product_urls = list(set(product_urls))
        logger.info(f"✅ Tìm thấy tổng cộng {len(product_urls)} sản phẩm unique")
        
    except Exception as e:
        logger.error(f"❌ Lỗi khi trích xuất URL sản phẩm: {str(e)}")
        traceback.print_exc()
    
    return product_urls

def extract_chauphatsafety_product_info(product_url):
    """
    Trích xuất thông tin chi tiết sản phẩm từ chauphatsafety.vn
    
    Args:
        product_url: URL trang sản phẩm
    
    Returns:
        dict: {
            'url': str,
            'code': str,  # Mã sản phẩm
            'name': str,  # Tên sản phẩm
            'image_url': str,  # URL ảnh sản phẩm (chỉ 1 ảnh)
            'specifications': dict  # Thông số kỹ thuật dạng dict
        }
    """
    try:
        html = get_html_content(product_url)
        if not html:
            logger.error(f"Không thể tải HTML từ: {product_url}")
            return None
            
        # Parse HTML với encoding đúng
        # Parse HTML với encoding đúng
        soup = parse_html_with_encoding(html)
        
        product_info = {
            'url': product_url,
            'code': '',
            'name': '',
            'image_url': '',
            'image_urls': [],
            'specifications': {}
        }
        
        # 1. Lấy mã sản phẩm
        # Selector chính xác: <li><div class="at">Mã sản phẩm</div><div class="as">BS07A-YW</div></li>
        product_code = ''
        
        # Ưu tiên 1: Tìm chính xác trong cấu trúc li > div.at + div.as
        # Tìm tất cả li có chứa div.at với text "Mã sản phẩm"
        all_lis = soup.find_all('li')
        for li in all_lis:
            # Tìm div.at trong li này
            at_div = li.find('div', class_='at')
            if at_div:
                at_text = at_div.get_text(strip=True)
                # Kiểm tra chính xác "Mã sản phẩm"
                if at_text == 'Mã sản phẩm' or 'Mã sản phẩm' in at_text:
                    # Tìm div.as ngay sau div.at trong cùng li
                    as_div = li.find('div', class_='as')
                    if as_div:
                        product_code = as_div.get_text(strip=True)
                        # Chấp nhận mã sản phẩm (có thể là số, chữ-số, hoặc chữ-số-gạch ngang như BS07A-YW)
                        if product_code and len(product_code) >= 2:
                            product_info['code'] = product_code
                            logger.info(f"  ✅ Mã sản phẩm: {product_code}")
                            break
        
        # Ưu tiên 2: Tìm trong div.the-attr > ul > li (cấu trúc chính xác)
        if not product_code:
            the_attr = soup.find('div', class_='the-attr')
            if the_attr:
                ul = the_attr.find('ul')
                if ul:
                    lis = ul.find_all('li')
                    for li in lis:
                        at_div = li.find('div', class_='at')
                        if at_div:
                            at_text = at_div.get_text(strip=True)
                            if at_text.strip() == 'Mã sản phẩm' or 'Mã sản phẩm' in at_text:
                                as_div = li.find('div', class_='as')
                                if as_div:
                                    product_code = as_div.get_text(strip=True)
                                    if product_code and len(product_code) >= 2:
                                        product_info['code'] = product_code
                                        logger.info(f"  ✅ Mã sản phẩm (the-attr): {product_code}")
                                        break
                        if product_code:
                            break
        
        # Ưu tiên 3: Tìm bằng regex trong HTML (fallback)
        if not product_code:
            html_str = str(soup)
            # Tìm pattern chính xác: <div class="at">Mã sản phẩm</div><div class="as">BS07A-YW</div>
            patterns = [
                r'<div[^>]*class=["\']at["\'][^>]*>\s*Mã\s+sản\s+phẩm\s*</div>\s*<div[^>]*class=["\']as["\'][^>]*>\s*([^<\n]+?)\s*</div>',
                r'class=["\']at["\'][^>]*>Mã\s+sản\s+phẩm[^<]*</div>[^<]*<div[^>]*class=["\']as["\'][^>]*>([^<\n]+?)</div>',
            ]
            for pattern in patterns:
                match = re.search(pattern, html_str, re.IGNORECASE | re.DOTALL)
                if match:
                    product_code = match.group(1).strip()
                    product_code = re.sub(r'<[^>]+>', '', product_code).strip()
                    if product_code and len(product_code) >= 2:
                        product_info['code'] = product_code
                        logger.info(f"  ✅ Mã sản phẩm (regex): {product_code}")
                        break
        
        # Ưu tiên 4: Tìm bằng regex trong HTML nếu vẫn không tìm thấy
        if not product_code:
            html_str = str(soup)
            # Tìm pattern: <div class="at">Mã sản phẩm</div><div class="as">151749</div>
            # Xử lý cả trường hợp có khoảng trắng và không có
            patterns = [
                r'<div\s+class=["\']at["\']>\s*Mã\s+sản\s+phẩm\s*</div>\s*<div\s+class=["\']as["\']>\s*([^<\n]+?)\s*</div>',
                r'<div\s+class=["\']at["\'][^>]*>\s*Mã\s+sản\s+phẩm\s*</div>\s*<div\s+class=["\']as["\'][^>]*>\s*([^<\n]+?)\s*</div>',
                r'class=["\']at["\'][^>]*>Mã\s+sản\s+phẩm[^<]*</div>[^<]*<div[^>]*class=["\']as["\'][^>]*>([^<\n]+?)</div>',
                r'Mã\s+sản\s+phẩm[^<]*<[^>]*class=["\']as["\'][^>]*>([^<\n]+?)</div>',
                r'<div[^>]*class=["\']as["\'][^>]*>([0-9]{4,})</div>'
            ]
            for pattern in patterns:
                match = re.search(pattern, html_str, re.IGNORECASE | re.DOTALL)
                if match:
                    product_code = match.group(1).strip()
                    # Loại bỏ các thẻ HTML nếu có
                    product_code = re.sub(r'<[^>]+>', '', product_code).strip()
                    # Chấp nhận mã sản phẩm (có thể là số hoặc chữ-số như PLO23, 151749)
                    if product_code and len(product_code) >= 2:
                        product_info['code'] = product_code
                        logger.info(f"  ✅ Mã sản phẩm (regex): {product_code}")
                        break
        
        # Ưu tiên 5: Trích mã sản phẩm từ URL (ví dụ: ...-151749-brady.html hoặc ...-PLO23-...)
        if not product_code:
            # Tìm pattern số hoặc chữ-số trong URL: -151749- hoặc -PLO23-
            url_match = re.search(r'-([A-Z0-9]{3,})[-.]', product_url, re.IGNORECASE)
            if url_match:
                potential_code = url_match.group(1)
                # Chỉ lấy nếu có vẻ là mã sản phẩm (ít nhất 3 ký tự)
                if len(potential_code) >= 3:
                    product_code = potential_code
                    product_info['code'] = product_code
                    logger.info(f"  ✅ Mã sản phẩm (từ URL): {product_code}")
        
        # Ưu tiên 6: Tìm trong tất cả text có chứa "Mã sản phẩm"
        if not product_code:
            # Tìm tất cả text chứa "Mã sản phẩm"
            for element in soup.find_all(text=re.compile(r'Mã\s+sản\s+phẩm', re.I)):
                parent = element.parent
                if parent:
                    # Tìm div.as gần nhất
                    next_sibling = parent.find_next_sibling()
                    if next_sibling:
                        as_div = next_sibling.find('div', class_='as')
                        if as_div:
                            product_code = as_div.get_text(strip=True)
                            # Chấp nhận mã sản phẩm (có thể là số hoặc chữ-số)
                            if product_code and len(product_code) >= 2:
                                product_info['code'] = product_code
                                logger.info(f"  ✅ Mã sản phẩm (text search): {product_code}")
                                break
        
        if not product_code:
            logger.warning(f"  ⚠️  Không tìm thấy mã sản phẩm trong HTML")
        
        # 2. Lấy tên sản phẩm (xử lý encoding tiếng Việt đúng cách)
        # Selector: <div class="the-title hidden-sm hidden-xs"><h1>...</h1></div>
        product_name = ''
        
        title_div = soup.find('div', class_='the-title')
        if not title_div:
            title_div = soup.find('div', class_=lambda x: x and 'the-title' in ' '.join(x) if isinstance(x, list) else 'the-title' in str(x))
        
        if title_div:
            h1 = title_div.find('h1')
            if h1:
                # Lấy text và xử lý encoding đúng cách
                product_name = h1.get_text(strip=True)
                # BeautifulSoup tự động decode, nhưng đảm bảo là string
                if isinstance(product_name, bytes):
                    try:
                        product_name = product_name.decode('utf-8', errors='replace')
                    except:
                        product_name = product_name.decode('latin-1', errors='replace')
                # Đảm bảo là string unicode
                product_name = str(product_name)
                product_info['name'] = product_name
                logger.info(f"  ✅ Tên sản phẩm: {product_name[:50]}...")
        
        # Fallback: tìm bất kỳ h1 nào
        if not product_info['name']:
            h1 = soup.find('h1')
            if h1:
                product_name = h1.get_text(strip=True)
                # Xử lý encoding
                if isinstance(product_name, bytes):
                    try:
                        product_name = product_name.decode('utf-8', errors='replace')
                    except:
                        product_name = product_name.decode('latin-1', errors='replace')
                product_name = str(product_name)
                product_info['name'] = product_name
        
        # 3. Lấy ảnh sản phẩm (lấy TẤT CẢ ảnh trong .productThumbnail)
        # Nguồn ảnh gốc nằm ở a[data-fancybox][data-src] bên trong #thumbnail-for
        image_urls = []

        def _normalize_img_url(u: str) -> str:
            if not u:
                return ''
            u = u.strip()
            if not u:
                return ''
            if u.startswith('/'):
                return urljoin('https://chauphatsafety.vn', u)
            if not u.startswith('http'):
                return urljoin(product_url, u)
            return u

        try:
            product_thumb = soup.find('div', class_='productThumbnail')
            thumb_for = None
            if product_thumb:
                thumb_for = product_thumb.find(id='thumbnail-for')
            if not thumb_for:
                thumb_for = soup.find(id='thumbnail-for')

            if thumb_for:
                seen = set()
                # Lấy theo thứ tự data-slick-index tăng dần, chỉ lấy slide thật (index >= 0)
                slides = thumb_for.select('[data-slick-index]')

                def _slick_index(el):
                    try:
                        return int(el.get('data-slick-index'))
                    except Exception:
                        return 10**9

                slides_sorted = sorted(slides, key=_slick_index)
                for slide in slides_sorted:
                    try:
                        idx = int(slide.get('data-slick-index'))
                    except Exception:
                        continue
                    if idx < 0:
                        continue
                    a = slide.select_one('a[data-fancybox][data-src]')
                    if not a:
                        continue
                    src = _normalize_img_url(a.get('data-src', ''))
                    if not src:
                        continue
                    if src in seen:
                        continue
                    seen.add(src)
                    image_urls.append(src)

                # Fallback: nếu không tìm được theo slide, lấy tất cả a[data-src] trong thumb_for
                if not image_urls:
                    for a in thumb_for.select('a[data-fancybox][data-src]'):
                        src = _normalize_img_url(a.get('data-src', ''))
                        if src and src not in seen:
                            seen.add(src)
                            image_urls.append(src)

        except Exception as e:
            logger.warning(f"  ⚠️  Lỗi khi lấy danh sách ảnh: {e}")

        if image_urls:
            product_info['image_urls'] = image_urls
            product_info['image_url'] = image_urls[0]
            logger.info(f"  ✅ Tìm thấy {len(image_urls)} ảnh sản phẩm")
        else:
            product_info['image_urls'] = []
            product_info['image_url'] = ''
            logger.warning(f"  ⚠️  Không tìm thấy ảnh sản phẩm")
        
        # 4. Lấy thông số kỹ thuật
        # Lấy gemini_api_key từ environment hoặc config nếu có
        gemini_api_key = os.getenv('GEMINI_API_KEY')
        specifications = extract_chauphatsafety_specifications(
            soup, 
            product_info['code'], 
            product_info['name'],
            translate=True,  # Bật dịch sang tiếng Việt
            gemini_api_key=gemini_api_key
        )
        product_info['specifications'] = specifications
        
        return product_info
        
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
        traceback.print_exc()
        return None

def extract_chauphatsafety_specifications(soup, product_code, product_name, translate=False, gemini_api_key=None):
    """
    Trích xuất thông số kỹ thuật từ bảng HTML phức tạp
    
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
        # Tìm container thông số kỹ thuật
        # Selector: <div class="boxProduct" id="flag2">...<div class="content">...<table>...</table></div></div>
        box_product = soup.find('div', {'id': 'flag2', 'class': 'boxProduct'})
        if not box_product:
            box_product = soup.find('div', class_='boxProduct')
        
        if not box_product:
            logger.warning("  ⚠️  Không tìm thấy boxProduct#flag2")
            # Vẫn thêm mã sản phẩm, tên sản phẩm và Copyright
            specifications['Mã sản phẩm'] = product_code
            specifications['Tên sản phẩm'] = product_name
            specifications['Copyright'] = 'Haiphongtech.vn'
            return specifications
        
        # Tìm div.content bên trong
        content_div = box_product.find('div', class_='content')
        if not content_div:
            content_div = box_product
        
        # Tìm tất cả các bảng thông số
        # Bảng có class: BR-Table--SpecTable
        spec_tables = content_div.find_all('table', class_=lambda x: x and 'BR-Table--SpecTable' in ' '.join(x) if isinstance(x, list) else 'BR-Table--SpecTable' in str(x))
        
        if not spec_tables:
            # Fallback: tìm tất cả table
            spec_tables = content_div.find_all('table')
        
        logger.info(f"  📋 Tìm thấy {len(spec_tables)} bảng thông số")
        
        # Parse từng bảng
        for table in spec_tables:
            tbody = table.find('tbody') or table
            
            # Tìm tất cả các hàng
            rows = tbody.find_all('tr')
            
            current_section = None  # Lưu section hiện tại (Key Characteristics, Dimensions, etc.)
            
            for row in rows:
                # Tìm các td trong hàng
                tds = row.find_all('td')
                
                if len(tds) >= 2:
                    # Có thể là hàng thông số: td[0] = tên, td[1] = giá trị
                    spec_name_td = tds[0]
                    spec_value_td = tds[1]
                    
                    # Kiểm tra xem có phải là section header không
                    section_name = spec_name_td.find(class_=lambda x: x and 'BR-Table__SpecSectionName' in ' '.join(x) if isinstance(x, list) else 'BR-Table__SpecSectionName' in str(x))
                    if section_name:
                        current_section = section_name.get_text(strip=True)
                        logger.debug(f"    📌 Section: {current_section}")
                        continue
                    
                    # Lấy tên thông số
                    spec_name_elem = spec_name_td.find(class_=lambda x: x and 'BR-Table__SpecName' in ' '.join(x) if isinstance(x, list) else 'BR-Table__SpecName' in str(x))
                    if not spec_name_elem:
                        spec_name_elem = spec_name_td
                    
                    spec_name = spec_name_elem.get_text(strip=True)
                    
                    # Lấy giá trị thông số - lấy toàn bộ nội dung HTML để giữ định dạng
                    spec_value_elem = spec_value_td.find(class_=lambda x: x and 'BR-Table__SpecValue' in ' '.join(x) if isinstance(x, list) else 'BR-Table__SpecValue' in str(x))
                    if not spec_value_elem:
                        spec_value_elem = spec_value_td
                    
                    # Lấy text từ element, xử lý các thẻ HTML bên trong
                    spec_value = spec_value_elem.get_text(' ', strip=True)
                    # Loại bỏ khoảng trắng thừa
                    spec_value = ' '.join(spec_value.split())
                    
                    # Nếu có section, thêm vào tên thông số
                    if current_section and spec_name:
                        full_spec_name = f"{current_section} - {spec_name}"
                    else:
                        full_spec_name = spec_name
                    
                    if spec_name and spec_value:
                        # Tránh trùng lặp - nếu đã có thì ghép thêm với dấu phẩy
                        if full_spec_name not in specifications:
                            specifications[full_spec_name] = spec_value
                        else:
                            # Nếu đã có, ghép thêm
                            specifications[full_spec_name] += f"; {spec_value}"
                
                elif len(tds) == 1:
                    # Có thể là hàng tiếp theo của section (rowspan)
                    # Giá trị này thuộc về thông số trước đó
                    spec_value = tds[0].get_text(strip=True)
                    if spec_value and current_section:
                        # Tìm thông số cuối cùng trong section này
                        last_spec = None
                        for key in reversed(list(specifications.keys())):
                            if current_section in key:
                                last_spec = key
                                break
                        if last_spec:
                            specifications[last_spec] += f"; {spec_value}"
        
        # Đảm bảo có mã sản phẩm và tên sản phẩm
        # Tạo dict mới với thứ tự: Mã sản phẩm, Tên sản phẩm, các thông số khác, Copyright
        final_specs = {}
        
        # Thêm mã sản phẩm vào đầu
        if product_code:
            final_specs['Mã sản phẩm'] = product_code
        else:
            final_specs['Mã sản phẩm'] = ''
        
        # Thêm tên sản phẩm
        if product_name:
            final_specs['Tên sản phẩm'] = product_name
        else:
            final_specs['Tên sản phẩm'] = ''
        
        # Thêm tất cả các thông số kỹ thuật đã parse
        for key, value in specifications.items():
            # Bỏ qua nếu đã có trong final_specs
            if key not in final_specs:
                final_specs[key] = value
        
        # Thêm Copyright vào cuối (bắt buộc)
        final_specs['Copyright'] = 'Haiphongtech.vn'
        
        specifications = final_specs
        
        # Dịch thông số kỹ thuật sang tiếng Việt nếu được yêu cầu
        if translate and GEMINI_TRANSLATE_AVAILABLE:
            logger.info(f"  🌐 Đang dịch {len(specifications)} thông số kỹ thuật sang tiếng Việt bằng Gemini...")
            # Khởi tạo Gemini nếu chưa có
            if gemini_api_key:
                init_gemini_translator(gemini_api_key)
            specifications = translate_specifications_with_gemini(specifications, translate_keys=True, translate_values=True)
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
    <tr><td style="font-weight: bold;">Mã sản phẩm</td><td>151749</td></tr>
    ...
    <tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>
    </tbody></table>
    """
    if not specifications:
        return ""
    
    html_table = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html_table += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
    html_table += '<tbody>'
    
    for key, value in specifications.items():
        # Escape HTML để tránh lỗi, nhưng giữ nguyên tiếng Việt
        # Sử dụng html_module.escape để xử lý đúng encoding
        key_escaped = html_module.escape(str(key), quote=False)
        value_escaped = html_module.escape(str(value), quote=False)
        html_table += f'<tr><td style="font-weight: bold;">{key_escaped}</td><td>{value_escaped}</td></tr>'
    
    html_table += '</tbody></table>'
    return html_table

def standardize_chauphatsafety_image_filename(product_code: str) -> str:
    """
    Chuẩn hóa mã sản phẩm để dùng làm tên file ảnh
    
    Args:
        product_code: Mã sản phẩm gốc (ví dụ: "151749")
    
    Returns:
        str: Mã sản phẩm đã chuẩn hóa (ví dụ: "151749")
    """
    if not product_code:
        return "product"
    
    s = str(product_code).strip()
    if not s:
        return "product"
    
    # Thay mọi ký tự đặc biệt (trừ chữ, số, dấu gạch ngang, dấu gạch dưới) bằng dấu gạch dưới
    s = re.sub(r'[^\w\-_]', '_', s)
    
    # Gom nhiều dấu gạch dưới liên tiếp thành một
    s = re.sub(r'_+', '_', s)
    
    # Bỏ dấu gạch dưới đầu/cuối
    s = re.sub(r'^_+|_+$', '', s)
    
    # Đảm bảo không rỗng
    if not s:
        return "product"
    
    return s

def add_white_background(image):
    """
    Thêm nền trắng vào ảnh nếu cần, giữ nguyên kích thước gốc.
    
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

def download_chauphatsafety_product_image(product_info, output_folder):
    """
    Tải ảnh sản phẩm từ chauphatsafety.vn, thêm nền trắng và chuyển đổi sang WebP.

    Nếu `product_info['image_urls']` có dữ liệu thì sẽ tải TẤT CẢ ảnh và đặt tên theo:
    <mã_sản_phẩm>-1.webp, <mã_sản_phẩm>-2.webp...

    Fallback: nếu không có `image_urls` thì dùng `image_url` (1 ảnh).

    Args:
        product_info: Dictionary chứa thông tin sản phẩm
        output_folder: Thư mục lưu ảnh

    Returns:
        list[str]: Danh sách đường dẫn file ảnh WebP đã tải (có thể rỗng)
    """
    downloaded_images = []

    if not product_info:
        return downloaded_images

    image_urls = product_info.get('image_urls')
    if not image_urls:
        one = product_info.get('image_url')
        image_urls = [one] if one else []

    if not image_urls:
        logger.warning(f"⚠️  Không có ảnh để tải cho sản phẩm: {product_info.get('name', 'Unknown')}")
        return downloaded_images

    os.makedirs(output_folder, exist_ok=True)

    # Lấy mã sản phẩm (bắt buộc để đặt tên file)
    product_code = product_info.get('code', '').strip()
    if not product_code:
        logger.warning(f"⚠️  Không có mã sản phẩm, không thể đặt tên file ảnh")
        return downloaded_images

    clean_code = standardize_chauphatsafety_image_filename(product_code)

    # Headers để tránh bị chặn
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
        'Referer': product_info.get('url', ''),
    }

    def _download_one(image_url: str, index: int) -> str:
        if not image_url:
            return ''

        filename = f"{clean_code}-{index}.webp"
        filepath = os.path.join(output_folder, filename)

        logger.info(f"📸 Tên file ảnh: {filename} (từ mã sản phẩm: {product_code})")

        if os.path.exists(filepath):
            logger.info(f"⏭️  Ảnh đã tồn tại: {filename}")
            return filepath

        max_retries = 3
        for retry in range(max_retries):
            try:
                logger.info(f"📥 Đang tải ảnh ({index}/{len(image_urls)}): {image_url[:50]}...")

                response = requests.get(image_url, timeout=30, headers=headers, stream=True)
                response.raise_for_status()

                content_type = response.headers.get('content-type', '').lower()
                if not any(img_type in content_type for img_type in ['image/', 'jpeg', 'jpg', 'png', 'webp', 'gif']):
                    logger.warning(f"⚠️  Không phải ảnh: {content_type}")
                    return ''

                if len(response.content) < 1024:
                    logger.warning(f"⚠️  Ảnh quá nhỏ: {len(response.content)} bytes")
                    if retry < max_retries - 1:
                        time.sleep(1)
                        continue
                    return ''

                original_image = Image.open(io.BytesIO(response.content))
                if original_image.size[0] < 30 or original_image.size[1] < 30:
                    logger.warning(f"⚠️  Ảnh kích thước nhỏ: {original_image.size}")
                    if retry < max_retries - 1:
                        time.sleep(1)
                        continue
                    return ''

                processed_image = add_white_background(original_image)
                os.makedirs(output_folder, exist_ok=True)
                processed_image.save(filepath, 'WebP', quality=95, method=6)

                if os.path.exists(filepath):
                    logger.info(f"✅ Tải thành công: {filename}")
                    return filepath

                logger.warning(f"❌ Không tạo được file WebP: {filename}")
                if retry < max_retries - 1:
                    time.sleep(1)

            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Lỗi tải ảnh lần {retry + 1}: {str(e)[:80]}...")
                if retry < max_retries - 1:
                    time.sleep(2)
                else:
                    logger.error(f"❌ Thất bại hoàn toàn: {filename}")
            except Exception as e:
                logger.error(f"❌ Lỗi không xác định: {str(e)[:80]}...")
                if retry < max_retries - 1:
                    time.sleep(1)
                return ''

        return ''

    for i, u in enumerate(image_urls, 1):
        path = _download_one(u, i)
        if path:
            downloaded_images.append(path)

    return downloaded_images

def create_chauphatsafety_excel_report(products, output_file):
    """
    Tạo báo cáo Excel cho sản phẩm Chauphatsafety
    
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
            
            # Xử lý danh sách ảnh WebP đã tải (nối thành chuỗi)
            downloaded_imgs = product.get('downloaded_images', [])
            if not downloaded_imgs and product.get('downloaded_image'):
                downloaded_imgs = [product.get('downloaded_image')]
            
            imgs_str = "\n".join(downloaded_imgs) if isinstance(downloaded_imgs, list) else str(downloaded_imgs)
            
            # Xử lý danh sách URL ảnh gốc
            orig_urls = product.get('image_urls', [])
            if not orig_urls and product.get('image_url'):
                orig_urls = [product.get('image_url')]
            
            urls_str = "\n".join(orig_urls) if isinstance(orig_urls, list) else str(orig_urls)

            excel_data.append({
                'URL': product.get('url', ''),
                'Tên sản phẩm': product.get('name', ''),
                'Mã sản phẩm': product.get('code', ''),
                'Thông số kỹ thuật': spec_html,
                'Ảnh sản phẩm (Gốc)': urls_str,
                'Ảnh WebP đã tải': imgs_str
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

class ChauphatsafetyCrawler:
    """Crawler chuyên dụng cho website chauphatsafety.vn"""
    
    def __init__(self, output_root="output_chauphatsafety", max_workers=8, max_retries=3):
        """
        Args:
            output_root: Thư mục lưu kết quả
            max_workers: Số luồng tối đa
            max_retries: Số lần thử lại tối đa
        """
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        os.makedirs(self.output_root, exist_ok=True)
    
    def crawl_products(self, input_urls):
        """
        Cào dữ liệu từ danh sách URL (danh mục hoặc sản phẩm)
        Cấu trúc lưu tương tự BAA Qlight: theo danh mục
        
        Args:
            input_urls: Danh sách URL danh mục hoặc sản phẩm
        
        Returns:
            tuple: (list các dict sản phẩm, đường dẫn folder kết quả)
        """
        start_time = time.time()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"Chauphatsafety_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        
        all_products = []
        category_folders = []
        
        # Thông báo bắt đầu
        safe_socketio_emit('progress_update', {
            'percent': 0,
            'message': f'Bắt đầu cào dữ liệu từ chauphatsafety.vn',
            'detail': f'Đang xử lý {len(input_urls)} URL...'
        })
        
        # Xử lý từng URL (có thể là danh mục hoặc sản phẩm)
        for idx, url in enumerate(input_urls):
            try:
                # Tạo tên danh mục từ URL
                parsed = urlparse(url)
                path_parts = parsed.path.strip('/').split('/')
                category_name = path_parts[-1] if path_parts else 'danh_muc'
                # Loại bỏ phần mở rộng .html
                category_name = re.sub(r'\.html.*$', '', category_name)
                # Làm sạch tên danh mục
                category_name = re.sub(r'[\\/:*?"<>|]', '_', category_name)
                
                cat_folder = os.path.join(result_dir, category_name)
                os.makedirs(cat_folder, exist_ok=True)
                images_folder = os.path.join(cat_folder, "images")
                os.makedirs(images_folder, exist_ok=True)
                category_folders.append(cat_folder)
                
                safe_socketio_emit('progress_update', {
                    'percent': int((idx / len(input_urls)) * 20),
                    'message': f'Đang xử lý danh mục {idx+1}/{len(input_urls)}: {category_name}',
                    'detail': f'Đang quét danh sách sản phẩm...'
                })
                
                # Lấy danh sách URL sản phẩm từ danh mục
                product_urls = extract_chauphatsafety_product_urls(url)
                
                if not product_urls:
                    logger.warning(f"⚠️  Không tìm thấy sản phẩm trong danh mục: {url}")
                    continue
                
                logger.info(f"Tìm thấy {len(product_urls)} sản phẩm trong danh mục: {category_name}")
                
                # Xử lý từng sản phẩm với đa luồng
                safe_socketio_emit('progress_update', {
                    'percent': int((idx / len(input_urls)) * 20 + 10),
                    'message': f'Đã tìm thấy {len(product_urls)} sản phẩm',
                    'detail': 'Đang xử lý chi tiết từng sản phẩm...'
                })
                
                category_products = []
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    future_to_url = {
                        executor.submit(extract_chauphatsafety_product_info, url): url 
                        for url in product_urls
                    }
                    
                    for i, future in enumerate(as_completed(future_to_url)):
                        url = future_to_url[future]
                        try:
                            product_info = future.result()
                            if product_info:
                                # Tải tất cả ảnh sản phẩm
                                downloaded_images = download_chauphatsafety_product_image(product_info, images_folder)
                                product_info['downloaded_images'] = downloaded_images
                                
                                category_products.append(product_info)
                                all_products.append(product_info)
                                
                                progress = int((idx / len(input_urls)) * 20 + 10 + ((i + 1) / len(product_urls)) * 70)
                                safe_socketio_emit('progress_update', {
                                    'percent': progress,
                                    'message': f'Đang xử lý sản phẩm {i+1}/{len(product_urls)} trong danh mục {category_name}',
                                    'detail': f'Sản phẩm: {product_info.get("name", "Unknown")[:50]}...'
                                })
                            else:
                                logger.warning(f"Không lấy được thông tin sản phẩm: {url}")
                        except Exception as e:
                            logger.error(f"Lỗi khi xử lý sản phẩm {url}: {str(e)}")
                            traceback.print_exc()
                
                # Tạo báo cáo Excel cho danh mục
                if category_products:
                    excel_file = os.path.join(cat_folder, f"{category_name}.xlsx")
                    create_chauphatsafety_excel_report(category_products, excel_file)
                    logger.info(f"✅ {category_name}: {len(category_products)} sản phẩm")
                
            except Exception as e:
                logger.error(f"❌ Lỗi khi xử lý URL {url}: {str(e)}")
                traceback.print_exc()
        
        # Tạo báo cáo tổng hợp
        if all_products:
            report_dir = os.path.join(result_dir, "Bao_cao")
            os.makedirs(report_dir, exist_ok=True)
            summary_excel = os.path.join(report_dir, f"Chauphatsafety_Tong_hop_{timestamp}.xlsx")
            create_chauphatsafety_excel_report(all_products, summary_excel)
            
            # Tạo file ZIP
            zip_file = os.path.join(result_dir, f"Chauphatsafety_ket_qua_{timestamp}.zip")
            with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        if not file.endswith('.zip'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)
        
        end_time = time.time()
        duration = end_time - start_time
        
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': f'Hoàn thành! Đã xử lý {len(all_products)} sản phẩm',
            'detail': f'Thời gian: {duration:.2f}s'
        })
        
        logger.info(f"Chauphatsafety HOÀN THÀNH: {len(all_products)} sản phẩm | {duration:.1f}s")
        
        return all_products, result_dir

def test_single_product(gemini_api_key=None):
    """Test với một sản phẩm đơn lẻ để debug với Gemini translation"""
    print("🧪 Test với một sản phẩm đơn lẻ (có dịch bằng Gemini)...")
    
    # Test với 2 sản phẩm
    test_urls = [
        "https://chauphatsafety.vn/vn/khoa-co-lap-van-bi-tay-cam-chu-t-1-25-2-t-handle-ball-valve-lockout-151749-brady.html",
        "https://chauphatsafety.vn/vn/khoa-co-lap-van-bi-tay-cam-chu-t-1-25-2-t-handle-ball-valve-lockout-151749-brady.html"  # Có thể thay URL khác
    ]
    
    if gemini_api_key:
        print(f"🔑 Sử dụng Gemini API key: {gemini_api_key[:20]}...")
        # Khởi tạo Gemini
        if GEMINI_TRANSLATE_AVAILABLE:
            init_gemini_translator(gemini_api_key)
    
    for i, test_url in enumerate(test_urls[:2], 1):  # Chỉ test 2 sản phẩm đầu
        print(f"\n{'='*60}")
        print(f"📦 Test sản phẩm {i}/{len(test_urls[:2])}")
        print(f"🔗 Test URL: {test_url}")
        
        product_info = extract_chauphatsafety_product_info(test_url)
        
        if product_info:
            print(f"\n✅ THÔNG TIN SẢN PHẨM:")
            print(f"   • Tên: {product_info.get('name', 'N/A')}")
            print(f"   • Mã: {product_info.get('code', 'N/A')}")
            print(f"   • Ảnh: {product_info.get('image_url', 'N/A')[:50]}...")
            print(f"   • Số thông số: {len(product_info.get('specifications', {}))}")
            
            if product_info.get('specifications'):
                print(f"\n📋 THÔNG SỐ KỸ THUẬT (đã dịch sang tiếng Việt - 10 đầu tiên):")
                for key, value in list(product_info['specifications'].items())[:10]:
                    print(f"   • {key}: {value[:100] if len(str(value)) > 100 else value}")
        else:
            print(f"\n❌ Không thể lấy thông tin sản phẩm!")

def test_category_urls():
    """Test lấy danh sách URL sản phẩm từ danh mục"""
    print("🧪 Test lấy danh sách URL sản phẩm từ danh mục...")
    
    category_url = "https://chauphatsafety.vn/vn/khoa-canh-bao-an-toan-lockout.html/p-1,brand-1"
    print(f"🔗 Danh mục: {category_url}")
    
    product_urls = extract_chauphatsafety_product_urls(category_url)
    
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
    
    print("🚀 Bắt đầu test crawler Chauphatsafety với Gemini translation...")
    
    # Test với một sản phẩm đơn lẻ (có dịch)
    test_single_product(gemini_api_key=GEMINI_API_KEY)
    
    # Test lấy danh sách URL từ danh mục (comment out nếu chỉ muốn test sản phẩm)
    # print("\n" + "="*50 + "\n")
    # test_category_urls()
