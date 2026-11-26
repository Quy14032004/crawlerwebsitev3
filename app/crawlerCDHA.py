"""
Cơ Điện Hải Âu Product Crawler - Cào dữ liệu sản phẩm từ website codienhaiau.com
Phiên bản: 1.0
Tác giả: Auto-generated based on BAA crawler patterns
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

def safe_socketio_emit(event: str, data: dict):
    """
    Emit Socket.IO một cách an toàn.
    - Nếu socketio/server chưa khởi tạo (chạy bằng Flask dev server thường) thì chỉ log,
      tránh raise AttributeError: 'NoneType' object has no attribute 'emit'.
    """
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            # Chế độ không có Socket.IO server – chỉ ghi log để debug, không crash
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"Lỗi khi emit Socket.IO ({event}): {e}")

def normalize_cdha_image_url(image_url):
    """Loại bỏ hậu tố kích thước -WxH trước đuôi file (vd: -300x300)"""
    try:
        if not image_url:
            return image_url
        # Chỉ xử lý các định dạng ảnh phổ biến
        lower = image_url.lower()
        if any(lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
            # Xóa mẫu -<width>x<height> trước phần mở rộng
            return re.sub(r"-\d+x\d+(?=\.[a-zA-Z]{3,4}$)", "", image_url)
        return image_url
    except Exception:
        return image_url

def extract_best_image_url_from_element(element, base_url):
    """Lấy URL ảnh chất lượng cao nhất từ 1 thẻ img (ưu tiên: data-large_image -> data-src -> srcset lớn nhất -> src)."""
    if element is None:
        return None

    # 1) data-large_image
    img_url = element.get('data-large_image')
    if img_url:
        if img_url.startswith('/'):
            img_url = urljoin(base_url, img_url)
        return normalize_cdha_image_url(img_url)

    # 2) data-src
    img_url = element.get('data-src')
    if img_url:
        if img_url.startswith('/'):
            img_url = urljoin(base_url, img_url)
        return normalize_cdha_image_url(img_url)

    # 3) srcset (chọn URL có width lớn nhất)
    srcset = element.get('srcset') or element.get('data-srcset')
    if srcset:
        try:
            largest_url = None
            largest_w = -1
            for part in srcset.split(','):
                part = part.strip()
                if ' ' in part:
                    url_part, size_part = part.rsplit(' ', 1)
                    w = -1
                    try:
                        if size_part.endswith('w'):
                            w = int(size_part[:-1])
                    except Exception:
                        pass
                    if w > largest_w:
                        largest_w = w
                        largest_url = url_part.strip()
            if largest_url:
                if largest_url.startswith('/'):
                    largest_url = urljoin(base_url, largest_url)
                return normalize_cdha_image_url(largest_url)
        except Exception:
            pass

    # 4) src cuối cùng
    img_url = element.get('src')
    if img_url:
        if img_url.startswith('/'):
            img_url = urljoin(base_url, img_url)
        return normalize_cdha_image_url(img_url)

    return None

def get_category_vn_name(url):
    """Lấy tên danh mục từ URL codienhaiau.com"""
    name = None
    html = get_html_content(url)
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        # Ưu tiên lấy từ breadcrumb
        breadcrumb = soup.select_one('.breadcrumb li.active, .breadcrumb-item.active, .breadcrumb li:last-child, .breadcrumb-item:last-child')
        if breadcrumb and breadcrumb.text.strip():
            name = re.sub(r'[\\/:*?"<>|]', '_', breadcrumb.text.strip())
        else:
            # Hoặc lấy từ thẻ h1
            h1 = soup.select_one('h1, .category-title, .page-title')
            if h1 and h1.text.strip():
                name = re.sub(r'[\\/:*?"<>|]', '_', h1.text.strip())
    
    # Nếu không lấy được thì fallback sang tên từ URL
    if not name:
        parsed = urlparse(url)
        path = parsed.path.strip('/').split('/')
        if path:
            last = path[-1]
            name = re.sub(r'_\d+$', '', last)
        else:
            name = 'danh_muc'

    # Nếu URL có tham số filter_brand, gắn thêm tên thương hiệu vào thư mục
    if 'filter_brand=' in url.lower():
        m = re.search(r'filter_brand=([^&]+)', url, re.IGNORECASE)
        if m:
            brand = m.group(1).strip()
            if brand.lower() not in name.lower():
                name = f"{name}_{brand}"

    return name

def detect_pagination(url, session=None):
    """Phát hiện số trang tối đa cho danh mục codienhaiau.com"""
    print(f"🔍 Đang phân tích phân trang: {url}")
    try:
        html = get_html_content(url, session=session)
        if not html:
            return 1
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Bước 1: Thử lấy tổng số sản phẩm từ HTML để tính số trang
        total_products = None
        count_selectors = [
            '.woocommerce-result-count',
            'mark.count',
            '.woocommerce-loop-category__title mark.count',
            'h2.woocommerce-loop-category__title mark.count',
            '.product-count'
        ]
        
        for selector in count_selectors:
            count_elems = soup.select(selector)
            for elem in count_elems:
                # Bỏ qua nếu thuộc sidebar hoặc widget bộ lọc
                if elem.find_parent(class_=re.compile(r'sidebar|widget|filter', re.I)):
                    continue
                count_text = elem.get_text(strip=True)
                match = re.search(r'\(?(\d+)\)?', count_text)
                if match:
                    parsed_val = int(match.group(1))
                    if parsed_val > 0:
                        total_products = parsed_val
                        print(f"📊 Tìm thấy tổng số sản phẩm: {total_products}")
                        break
            if total_products:
                break
        
        # Đếm số sản phẩm trên trang đầu tiên để tính số trang
        products_per_page = None
        if total_products:
            # Đếm sản phẩm trên trang đầu - sử dụng logic tương tự extract_cdha_product_urls
            # Tìm container chính chứa sản phẩm
            main_content = None
            content_selectors = [
                '.woocommerce-loop',
                '.products',
                'main .products',
                '.content-area .products',
                'ul.products',
                '.product-list',
                'main .woocommerce-loop',
                '.woocommerce-products-header + .products'
            ]
            
            for selector in content_selectors:
                main_content = soup.select_one(selector)
                if main_content:
                    break
            
            if not main_content:
                main_content = soup.select_one('main, .content-area, #content, .site-content')
            
            # Đếm sản phẩm trong container chính
            if main_content:
                first_page_products = main_content.select('li.product, .product-item, .woocommerce-loop-product__link, li.product a')
                # Loại bỏ trùng lặp bằng cách lấy href
                seen_hrefs = set()
                unique_products = []
                for product in first_page_products:
                    if product.name == 'a':
                        href = product.get('href', '')
                    else:
                        link = product.select_one('a')
                        href = link.get('href', '') if link else ''
                    if href and '/product/' in href and href not in seen_hrefs:
                        seen_hrefs.add(href)
                        unique_products.append(product)
                
                if unique_products:
                    products_per_page = len(unique_products)
                    print(f"📊 Số sản phẩm mỗi trang: {products_per_page}")
                    if products_per_page > 0:
                        calculated_pages = (total_products + products_per_page - 1) // products_per_page  # Làm tròn lên
                        print(f"📊 Tính toán số trang: {calculated_pages} (từ {total_products} sản phẩm / {products_per_page} mỗi trang)")
            else:
                # Fallback: đếm tất cả link sản phẩm
                all_products = soup.select('a[href*="/product/"]')
                seen_hrefs = set()
                unique_products = []
                for product in all_products:
                    href = product.get('href', '')
                    if href and href not in seen_hrefs:
                        seen_hrefs.add(href)
                        unique_products.append(product)
                if unique_products:
                    products_per_page = len(unique_products)
                    print(f"📊 Số sản phẩm mỗi trang (fallback): {products_per_page}")
                    if products_per_page > 0:
                        calculated_pages = (total_products + products_per_page - 1) // products_per_page
                        print(f"📊 Tính toán số trang: {calculated_pages} (từ {total_products} sản phẩm / {products_per_page} mỗi trang)")
        
        # Tìm thành phần phân trang cho codienhaiau.com với nhiều selector
        pagination_selectors = [
            '.paging .wp-pagenavi',
            '.pagination', 
            '.page-list', 
            'nav[aria-label="Page navigation"]', 
            '.woocommerce-pagination',
            '.wp-pagenavi',
            '.paging'
        ]
        
        pagination = None
        for selector in pagination_selectors:
            pagination = soup.select_one(selector)
            if pagination:
                print(f"🔍 Tìm thấy phân trang với selector: {selector}")
                break
        
        if not pagination:
            print("⚠️  Không tìm thấy thành phần phân trang")
            # Nếu đã tính được số trang từ tổng sản phẩm, sử dụng nó
            if total_products and products_per_page and products_per_page > 0:
                calculated_pages = (total_products + products_per_page - 1) // products_per_page
                print(f"✅ Sử dụng số trang tính toán: {calculated_pages}")
                return calculated_pages
            return 1
        
        # Tìm số trang lớn nhất với nhiều selector khác nhau
        max_page = 1
        page_selectors = [
            'a.page-link', 
            'a.page-numbers', 
            '.page-numbers',
            'a.page',
            'a[class*="page"]',
            'a[title*="Page"]',
            'span.current'
        ]
        
        all_page_elements = []
        for selector in page_selectors:
            elements = pagination.select(selector)
            all_page_elements.extend(elements)
            if elements:
                print(f"   📄 Selector '{selector}': {len(elements)} elements")
        
        # Loại bỏ trùng lặp
        unique_elements = []
        seen_texts = set()
        for element in all_page_elements:
            text = element.get_text(strip=True)
            if text and text not in seen_texts:
                seen_texts.add(text)
                unique_elements.append((element, text))
        
        print(f"   📊 Tổng cộng: {len(unique_elements)} page elements duy nhất")
        
        for element, text in unique_elements:
            # Thử chuyển đổi văn bản thành số
            try:
                page_num = int(text)
                max_page = max(max_page, page_num)
                print(f"   ✅ Trang {page_num}")
            except ValueError:
                # Kiểm tra title attribute
                title = element.get('title', '')
                if 'Page' in title:
                    try:
                        match = re.search(r'Page (\d+)', title)
                        if match:
                            page_num = int(match.group(1))
                            max_page = max(max_page, page_num)
                            print(f"   ✅ Trang {page_num} (từ title)")
                    except:
                        pass
        
        # Nếu không tìm thấy trang nào, thử tìm trong toàn bộ trang
        if max_page == 1:
            print("⚠️  Không tìm thấy trang nào, tìm trong toàn bộ trang...")
            all_links = soup.select('a[href*="/page/"]')
            for link in all_links:
                href = link.get('href', '')
                try:
                    # Lấy số từ URL như /page/2/
                    match = re.search(r'/page/(\d+)/', href)
                    if match:
                        page_num = int(match.group(1))
                        max_page = max(max_page, page_num)
                        print(f"   ✅ Trang {page_num} (từ URL)")
                except:
                    pass
        
        # Nếu vẫn chỉ tìm thấy ít trang, thử tìm trang cuối cùng bằng cách kiểm tra "Next" hoặc "Last"
        if max_page <= 5:
            print("⚠️  Chỉ tìm thấy ít trang, thử tìm trang cuối cùng...")
            
            # Tìm link "Next" hoặc "Last"
            next_links = soup.select('a[class*="next"], a[class*="last"], a[title*="Next"], a[title*="Last"]')
            for link in next_links:
                href = link.get('href', '')
                if '/page/' in href:
                    try:
                        match = re.search(r'/page/(\d+)/', href)
                        if match:
                            page_num = int(match.group(1))
                            max_page = max(max_page, page_num)
                            print(f"   ✅ Trang {page_num} (từ Next/Last link)")
                    except:
                        pass
            
            # Thử tìm trong text "Page X of Y" hoặc tương tự
            page_text = soup.get_text()
            # Tìm pattern như "Page 1 of 18" hoặc "1 / 18"
            patterns = [
                r'Page \d+ of (\d+)',
                r'(\d+) / (\d+)',
                r'Trang \d+ của (\d+)',
                r'(\d+) trong (\d+)'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                for match in matches:
                    try:
                        if isinstance(match, tuple):
                            page_num = int(match[-1])  # Lấy số cuối cùng
                        else:
                            page_num = int(match)
                        max_page = max(max_page, page_num)
                        print(f"   ✅ Trang {page_num} (từ text pattern)")
                    except:
                        pass
        
        # Kiểm tra xem có dấu hiệu phân trang còn dài (dấu "...", "dots", "last") không
        has_more_indicator = bool(
            pagination and (
                pagination.select('.dots, .extend, a.last, a[class*="last"]') or
                pagination.select('a[title*="Last"], a[title*="Cuối"]')
            )
        )

        # Nếu đã tính được số trang từ tổng sản phẩm, ưu tiên sử dụng nó
        if total_products and products_per_page and products_per_page > 0:
            calculated_pages = (total_products + products_per_page - 1) // products_per_page
            if calculated_pages > max_page:
                print(f"✅ Sử dụng số trang tính toán: {calculated_pages} (lớn hơn số trang từ HTML: {max_page})")
                max_page = calculated_pages
        
        # Chỉ kiểm tra động khi không có pagination element, hoặc có dấu "..." / last,
        # hoặc tổng sản phẩm đã tính toán lớn hơn nhiều so với số trang hiện tại
        should_check_dynamically = (
            (not pagination and max_page == 1) or 
            has_more_indicator or
            (total_products and products_per_page and max_page * products_per_page < total_products * 0.8)
        )
        
        if should_check_dynamically:
            print("⚠️  Thử tìm trang bằng cách kiểm tra từng trang...")
            current_page = max_page
            last_product_signature = None
            duplicate_signature_count = 0
            consecutive_empty = 0
            max_dynamic_pages = 1000  # Tăng giới hạn để đảm bảo lấy đủ dữ liệu
            
            # Nếu đã có số trang tính toán, sử dụng nó làm mục tiêu
            target_pages = None
            if total_products and products_per_page and products_per_page > 0:
                target_pages = (total_products + products_per_page - 1) // products_per_page
            
            while True:
                try:
                    next_page = current_page + 1
                    
                    # Nếu đã đạt mục tiêu và đã kiểm tra thêm một số trang, dừng lại
                    if target_pages and next_page > target_pages + 5:
                        print(f"   ✅ Đã đạt mục tiêu {target_pages} trang, dừng kiểm tra.")
                        break
                    
                    test_url = make_pagination_url(url, next_page)
                    print(f"   🔍 Kiểm tra trang {next_page}: {test_url}")
                    
                    test_html = get_html_content(test_url, session=session)
                    if not test_html:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            print(f"   ❌ Liên tiếp {consecutive_empty} trang không tồn tại, dừng lại.")
                            break
                        continue
                    
                    consecutive_empty = 0
                    test_soup = BeautifulSoup(test_html, 'html.parser')
                    
                    # Kiểm tra xem có bị redirect về trang chủ không (WordPress thường redirect trang quá giới hạn về trang chủ)
                    is_redirected = False
                    if test_soup.body and 'home' in test_soup.body.get('class', []):
                        is_redirected = True
                    canon = test_soup.find('link', rel='canonical')
                    if canon and canon.get('href', '').rstrip('/') == 'https://codienhaiau.com':
                        is_redirected = True
                        
                    if is_redirected:
                        print(f"   🛑 Trang {next_page} bị redirect về trang chủ -> Đã hết trang danh mục.")
                        break
                    
                    # Kiểm tra xem có sản phẩm nào không
                    raw_links = test_soup.select('a[href*=\"/product/\"], a[href*=\"/san-pham/\"]')
                    product_links = []
                    seen_links = set()
                    for link in raw_links:
                        href = link.get('href')
                        if not href:
                            continue
                        if href.startswith('/'):
                            href = urljoin(test_url, href)
                        if href not in seen_links:
                            seen_links.add(href)
                            product_links.append(href)
                    
                    if not product_links:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            print(f"   ❌ Liên tiếp {consecutive_empty} trang không có sản phẩm, dừng lại.")
                            break
                        continue
                    
                    consecutive_empty = 0
                    product_count = len(product_links)
                    print(f"   ✅ Trang {next_page} có {product_count} sản phẩm")
                    max_page = next_page
                    current_page = next_page
                    
                    # Phát hiện lặp nội dung để dừng khi trang giống hệt nhau (kiểm tra nhiều hơn)
                    product_signature = tuple(product_links[:30])  # Tăng từ 20 lên 30
                    if product_signature == last_product_signature:
                        duplicate_signature_count += 1
                        if duplicate_signature_count >= 2:  # Yêu cầu 2 lần lặp mới dừng
                            print("   ⚠️  Phát hiện nội dung lặp lại nhiều lần, dừng kiểm tra trang động.")
                            break
                    else:
                        last_product_signature = product_signature
                        duplicate_signature_count = 0
                    
                    # Kiểm tra giới hạn động
                    if next_page >= max_dynamic_pages:
                        print(f"   ⚠️  Đạt giới hạn {max_dynamic_pages} trang động, dừng lại.")
                        break
                    
                    continue
                        
                except Exception as e:
                    print(f"   ❌ Lỗi khi kiểm tra trang {next_page}: {str(e)}")
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        break
        
        result = max(max_page, 1)
        print(f"📄 Tổng số trang phát hiện: {result}")
        return result
        
    except Exception as e:
        print(f"❌ Lỗi khi phát hiện số trang: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

def make_pagination_url(base_url, page_number):
    """Tạo URL phân trang cho codienhaiau.com"""
    if page_number <= 1:
        return base_url
        
    try:
        # Phân tích URL gốc
        parsed_url = urlparse(base_url)
        path = parsed_url.path
        query = parsed_url.query
        
        # Kiểm tra xem URL đã có /page/ chưa
        if '/page/' in path:
            # Thay thế số trang hiện tại
            import re
            new_path = re.sub(r'/page/\d+/', f'/page/{page_number}/', path)
        else:
            # Thêm /page/X/ vào cuối path
            if path.endswith('/'):
                new_path = f"{path}page/{page_number}/"
            else:
                new_path = f"{path}/page/{page_number}/"
        
        # Tạo URL mới
        new_url = f"{parsed_url.scheme}://{parsed_url.netloc}{new_path}"
        if query:
            new_url += f"?{query}"
        
        return new_url
        
    except Exception as e:
        print(f"Lỗi khi tạo URL phân trang: {str(e)}")
        # Fallback: thử thêm /page/X/ vào cuối URL
        try:
            if base_url.endswith('/'):
                return f"{base_url}page/{page_number}/"
            else:
                return f"{base_url}/page/{page_number}/"
        except:
            # Fallback cuối cùng: thêm ?page=X
            if '?' in base_url:
                return f"{base_url}&page={page_number}"
            else:
                return f"{base_url}?page={page_number}"

def is_cdha_category_url(url):
    """Kiểm tra xem URL có phải là danh mục của codienhaiau.com không"""
    if not url or 'codienhaiau.com' not in url:
        return False
    
    # Các pattern cho danh mục
    category_patterns = [
        r'/category/',
        r'/product-category/',
        r'/danh-muc/',
        r'/brand/',
    ]
    
    for pattern in category_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    
    return False

def is_cdha_product_url(url):
    """Kiểm tra xem URL có phải là sản phẩm của codienhaiau.com không"""
    if not url or 'codienhaiau.com' not in url:
        return False
    
    # Pattern cho sản phẩm
    product_patterns = [
        r'/product/',
        r'/san-pham/',
    ]
    
    for pattern in product_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    
    return False

SPECIAL_SECTION_URLS = {
    'https://codienhaiau.com/category/ls/bien-tan-ls/'
}


def is_special_cdha_section(url):
    """Kiểm tra các danh mục cần lọc sản phẩm theo container tùy chỉnh"""
    if not url:
        return False
    normalized = url.split('?')[0].rstrip('/')
    for special in SPECIAL_SECTION_URLS:
        if normalized == special.rstrip('/'):
            return True
    return False


def extract_cdha_product_urls(category_url, session=None):
    """Trích xuất danh sách URL sản phẩm từ danh mục codienhaiau.com"""
    product_urls = []
    
    # Kiểm tra xem danh mục có cấu trúc đặc biệt cần giới hạn vùng cào hay không
    special_section = is_special_cdha_section(category_url)

    # Phát hiện số trang
    max_pages = detect_pagination(category_url, session=session)
    print(f"📁 Danh mục: {max_pages} trang")
    
    for page in range(1, max_pages + 1):
        page_url = make_pagination_url(category_url, page)
        print(f"🔍 Đang xử lý trang {page}/{max_pages}: {page_url}")
        
        html = get_html_content(page_url, session=session)
        if not html:
            print(f"❌ Không thể tải trang {page}")
            continue
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Nếu từ trang 2 trở đi mà bị redirect về trang chủ, dừng lại ngay (tránh cào nhầm sản phẩm trang chủ)
        if page > 1:
            is_redirected = False
            if soup.body and 'home' in soup.body.get('class', []):
                is_redirected = True
            canon = soup.find('link', rel='canonical')
            if canon and canon.get('href', '').rstrip('/') == 'https://codienhaiau.com':
                is_redirected = True
            if is_redirected:
                print(f"   🛑 Trang {page} bị chuyển hướng về trang chủ -> Dừng duyệt các trang tiếp theo.")
                break
        
        found_links = []

        if special_section:
            # Chỉ lấy sản phẩm trong grid .row > .motsanpham > li.product_list_widget
            special_selector = 'div.row div.motsanpham li.product_list_widget a.woocommerce-LoopProduct-link'
            links = soup.select(special_selector)
            print(f"   🎯 Selector đặc biệt '{special_selector}': {len(links)} links")
            found_links.extend(links)
        else:
            # Tìm container chính chứa sản phẩm của danh mục (loại bỏ sidebar, footer, widget)
            main_content = None
            
            # Ưu tiên tìm container WooCommerce products
            content_selectors = [
                '.woocommerce-loop',
                '.products',
                'main .products',
                '.content-area .products',
                'ul.products',
                '.product-list',
                'main .woocommerce-loop',
                '.woocommerce-products-header + .products'
            ]
            
            for selector in content_selectors:
                main_content = soup.select_one(selector)
                if main_content:
                    print(f"   ✅ Tìm thấy container sản phẩm: {selector}")
                    break
            
            # Nếu không tìm thấy container cụ thể, thử tìm trong main hoặc content-area
            if not main_content:
                main_content = soup.select_one('main, .content-area, #content, .site-content')
                if main_content:
                    print(f"   ✅ Sử dụng container chính: {main_content.name}")
            
            # Nếu vẫn không có, sử dụng toàn bộ soup nhưng loại bỏ sidebar/footer
            if not main_content:
                main_content = soup
                # Loại bỏ sidebar và footer
                for element in soup.select('aside, .sidebar, .widget-area, footer, .site-footer'):
                    element.decompose()
                print(f"   ⚠️  Không tìm thấy container cụ thể, sử dụng toàn bộ trang (đã loại bỏ sidebar/footer)")
            
            # Chỉ tìm link sản phẩm trong container chính với selector cụ thể
            product_selectors = [
                '.woocommerce-loop-product__link',
                '.product-item a',
                'li.product a.woocommerce-LoopProduct-link',
                'li.product a',
                '.product-title a',
                'h2.product-title a',
                'h3.product-title a'
            ]
            
            for selector in product_selectors:
                links = main_content.select(selector) if main_content else []
                if links:
                    print(f"   🔍 Selector '{selector}': {len(links)} links")
                    found_links.extend(links)
            
            # Nếu vẫn không tìm thấy, thử tìm trong container chính với selector rộng hơn
            if not found_links and main_content:
                # Chỉ lấy link có /product/ trong container chính
                all_links = main_content.select('a[href*="/product/"]')
                # Lọc chỉ lấy link từ các phần tử product
                for link in all_links:
                    # Kiểm tra xem link có nằm trong phần tử product không
                    parent = link.find_parent(['li', 'div', 'article'])
                    if parent and ('product' in parent.get('class', []) or 
                                 'woocommerce' in ' '.join(parent.get('class', [])).lower()):
                        found_links.append(link)
                if found_links:
                    print(f"   🔍 Tìm thấy {len(found_links)} links từ container chính")
        
        # Loại bỏ trùng lặp
        unique_links = []
        seen_hrefs = set()
        for link in found_links:
            href = link.get('href')
            if href and href not in seen_hrefs:
                seen_hrefs.add(href)
                unique_links.append(link)
        
        print(f"   📊 Tổng cộng: {len(unique_links)} links duy nhất")
        
        for link in unique_links:
            href = link.get('href')
            if href and ('/product/' in href or '/san-pham/' in href):
                # Đảm bảo URL đầy đủ
                if href.startswith('/'):
                    href = urljoin(page_url, href)
                product_urls.append(href)
                print(f"   ✅ Sản phẩm: {href}")
        
        # Nếu không tìm thấy sản phẩm nào, dừng lại
        if not unique_links:
            print(f"   ⚠️  Không tìm thấy sản phẩm nào ở trang {page}")
            break
                
    # Loại bỏ trùng lặp
    product_urls = list(set(product_urls))
    print(f"[OK] Tìm thấy tổng cộng {len(product_urls)} sản phẩm")
    
    return product_urls

def extract_cdha_product_info(product_url, session=None):
    """Trích xuất thông tin sản phẩm từ codienhaiau.com"""
    print(f"📦 Đang cào sản phẩm: {product_url}")
    try:
        html = get_html_content(product_url, session=session)
        if not html:
            return None
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Tên sản phẩm
        product_name = ""
        name_element = soup.select_one('h1.product_title.entry-title')
        if name_element:
            product_name = name_element.get_text(strip=True)
        
        # 2. Mã sản phẩm
        product_code = ""
        code_element = soup.select_one('span.sku_wrapper span.sku')
        if code_element:
            product_code = code_element.get_text(strip=True)
            
        # 2.1 Xử lý RIÊNG cho danh mục Cáp điều khiển Sangjin
        # Nếu URL thuộc danh mục sangjin, trích xuất mã từ tên sản phẩm
        is_sangjin = "day-cap-dieu-khien-sangjin" in product_url.lower()
        if is_sangjin and product_name:
            # Tìm pattern dạng số x số (hoặc số × số)
            # Ví dụ: sangjin 4x0.75 mm2 hoặc 4×0.75 (chống nhiễu)
            import re
            match = re.search(r'(\d+[\s]*[x×][\s]*\d+(?:\.\d+)?)', product_name, re.IGNORECASE)
            if match:
                # Lấy phần match đầu tiên (vd: 4×0.75)
                extracted_code = match.group(0).strip()
                # Nếu sku đang trống hoặc SKU mặc định không phải dạng này, ưu tiên cái trích xuất được
                if not product_code or product_code == "N/A":
                    product_code = extracted_code
                else:
                    # Nếu có SKU nhưng user muốn dùng định dạng này cho danh mục này
                    print(f"      [DEBUG] Sangjin override SKU '{product_code}' with '{extracted_code}'")
                    product_code = extracted_code
        
        # 3. Giá sản phẩm (p.price bdi)
        product_price = ""
        try:
            bdi = soup.select_one('p.price bdi')
            if bdi:
                price_text = bdi.get_text(strip=True)
                # Giữ lại số và phân tách thập phân/dấu ngăn cách
                price_clean = re.sub(r'[^\d.,]', '', price_text)
                product_price = price_clean
            else:
                # Fallback: tìm bất kỳ bdi nào chứa ₫
                bdi_any = soup.find('bdi', string=lambda t: t and '₫' in t)
                if bdi_any:
                    price_text = bdi_any.get_text(strip=True)
                    product_price = re.sub(r'[^\d.,]', '', price_text)
        except Exception:
            pass
        
        # 4. Ảnh sản phẩm - Lấy đúng ảnh chính theo phần tử img.wp-post-image
        product_images = []

        print(f"[SEARCH] Bắt đầu tìm ảnh cho sản phẩm: {product_name}")

        # Ưu tiên chính xác phần tử ảnh đại diện sản phẩm của WooCommerce/WordPress
        main_img = soup.select_one(
            'div.woocommerce-product-gallery__image img.wp-post-image, '
            'figure.woocommerce-product-gallery__wrapper img.wp-post-image, '
            '.woocommerce-product-gallery__image img.wp-post-image, '
            '.woocommerce-product-gallery img.wp-post-image, '
            'img.wp-post-image'
        )

        best_url = extract_best_image_url_from_element(main_img, product_url) if main_img else None

        # Nếu không có, thử fallback: bất kỳ img có data-large_image (chỉ lấy cái đầu)
        if not best_url:
            fallback_img = soup.select_one('img[data-large_image]')
            best_url = extract_best_image_url_from_element(fallback_img, product_url) if fallback_img else None

        # Chuẩn hóa và lọc ảnh không liên quan
        if best_url:
            if '?' in best_url:
                best_url = best_url.split('?')[0]
            lower = best_url.lower()
            if (any(lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]) and
                all(bad not in lower for bad in ["placeholder", "logo", "banner", "icon", "zalo", "dang-ky", "bo-cong-thuong"])):
                product_images.append(best_url)
                print(f"      [OK] Ảnh chính: {best_url}")
        else:
            print("[WARN] Không tìm thấy ảnh chính phù hợp")

        print(f"[IMG] Tổng cộng tìm thấy {len(product_images)} ảnh cho sản phẩm")
        
        # 5. Thông số kỹ thuật
        specifications = extract_cdha_specifications(soup)
        
        return {
            'url': product_url,
            'name': product_name,
            'code': product_code,
            'price': product_price,
            'images': product_images,
            'specifications': specifications
        }
        
    except Exception as e:
        print(f"Lỗi khi trích xuất thông tin sản phẩm từ {product_url}: {str(e)}")
        return None

def extract_cdha_specifications(soup):
    """Trích xuất thông số kỹ thuật từ bảng thông tin bổ sung"""
    specifications = {}
    
    try:
        # Tìm bảng thông tin bổ sung
        spec_table = soup.select_one('table.woocommerce-product-attributes.shop_attributes')
        if not spec_table:
            return specifications
        
        # Trích xuất từng hàng trong bảng - cấu trúc: <tr><td>Label</td><td>Value</td></tr>
        # Thử nhiều cách để tìm các hàng
        rows = spec_table.select('tbody tr')
        if not rows:
            # Fallback: tìm tất cả tr trong bảng
            rows = spec_table.select('tr')
        
        for row in rows:
            # Lấy tất cả các td trong hàng
            tds = row.select('td')
            
            # Cấu trúc: <tr><td>Label</td><td>Value</td></tr>
            if len(tds) >= 2:
                label = tds[0].get_text(strip=True)
                value = tds[1].get_text(strip=True)
                if label and value:
                    specifications[label] = value
            # Fallback: nếu có th/th td
            else:
                # Thử tìm th (label) và td (value)
                label_element = row.select_one('th, td:first-child')
                value_element = row.select_one('td:last-child')
                
                if label_element and value_element:
                    label = label_element.get_text(strip=True)
                    value = value_element.get_text(strip=True)
                    if label and value:
                        specifications[label] = value
        
        # Thêm Copyright như yêu cầu
        specifications['Copyright'] = 'Haiphongtech.vn'
        
    except Exception as e:
        print(f"Lỗi khi trích xuất thông số kỹ thuật: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return specifications

def download_cdha_product_images(product_info, output_folder):
    """Tải ảnh sản phẩm từ codienhaiau.com và chuyển đổi sang WebP"""
    downloaded_images = []
    
    if not product_info or not product_info.get('images'):
        print(f"[WARN] Không có ảnh để tải cho sản phẩm: {product_info.get('name', 'Unknown') if product_info else 'Unknown'}")
        if product_info is not None:
            product_info['downloaded_images'] = []
            product_info['image_paths'] = []
        return downloaded_images
    
    output_folder = os.path.abspath(output_folder)
    os.makedirs(output_folder, exist_ok=True)
    
    # Import WebP converter
    from app.webp_converter import WebPConverter
    webp_converter = WebPConverter()
    
    print(f"[IMG] Bắt đầu tải {len(product_info['images'])} ảnh cho sản phẩm: {product_info.get('name', 'Unknown')}")
    
    for i, image_url in enumerate(product_info['images']):
        try:
            # Tạo tên file WebP dựa trên mã sản phẩm
            product_code = product_info.get('code', '').strip()
            if product_code:
                # Đặc biệt cho Sangjin: Giữ nguyên mã hoặc chỉ xóa ký tự cấm của OS
                is_sangjin = "day-cap-dieu-khien-sangjin" in product_info.get('url', '').lower()
                
                if is_sangjin:
                    # Chỉ loại bỏ các ký tự cấm đặt tên file trên Windows: < > : " / \ | ? *
                    clean_code = re.sub(r'[<>:"/\\|?*]', '_', product_code)
                    print(f"      [FILE] Sangjin raw filename: {clean_code}")
                else:
                    # Làm sạch mã sản phẩm thông thường (loại bỏ ký tự không phải chữ/số/gạch ngang)
                    clean_code = re.sub(r'[^\w\-_]', '_', product_code)
                if i == 0:
                    # Ảnh đầu tiên dùng tên mã sản phẩm
                    filename = f"{clean_code}.webp"
                else:
                    # Ảnh thứ 2, 3... thêm số thứ tự
                    filename = f"{clean_code}_{i+1}.webp"
            else:
                # Fallback: sử dụng tên sản phẩm nếu không có mã
                product_name = product_info.get('name', 'product')
                safe_name = re.sub(r'[^\w\-_\.]', '_', product_name)[:30]
                if i == 0:
                    filename = f"{safe_name}.webp"
                else:
                    filename = f"{safe_name}_{i+1}.webp"
            
            filepath = os.path.abspath(os.path.join(output_folder, filename))
            
            # Kiểm tra xem file đã tồn tại chưa
            if os.path.exists(filepath):
                print(f"[SKIP] Ảnh đã tồn tại: {filename}")
                downloaded_images.append(filepath)
                continue
            
            # Tải ảnh với retry logic (dùng cloudscraper để bypass chặn IP)
            max_retries = 3
            import random, time

            # Khởi tạo cloudscraper một lần ngoài vòng retry
            try:
                import cloudscraper as _cs
                _img_scraper = _cs.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
                use_cloudscraper = True
            except ImportError:
                use_cloudscraper = False

            for retry in range(max_retries):
                try:
                    print(f"[DL] Đang tải ảnh {i+1}/{len(product_info['images'])}: {image_url[:50]}...")
                    
                    if use_cloudscraper:
                        response = _img_scraper.get(image_url, timeout=60, stream=True)
                    else:
                        user_agents = [
                            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
                        ]
                        headers = {
                            'User-Agent': random.choice(user_agents),
                            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
                            'Referer': product_info.get('url', ''),
                        }
                        response = requests.get(image_url, timeout=60, headers=headers, stream=True)
                    
                    response.raise_for_status()
                    
                    # Rate-limit ngẫu nhiên sau khi request thành công
                    time.sleep(random.uniform(1.5, 3.0))
                    
                    # Kiểm tra content type
                    content_type = response.headers.get('content-type', '').lower()
                    if not any(img_type in content_type for img_type in ['image/', 'jpeg', 'jpg', 'png', 'webp', 'gif']):
                        print(f"[WARN] Không phải ảnh: {content_type}")
                        break
                    
                    # Chuyển đổi bytes ảnh sang WebP trực tiếp (không cần lưu tạm)
                    webp_path = WebPConverter.convert_bytes_to_webp(response.content, filepath)
                    if webp_path and os.path.exists(webp_path):
                        downloaded_images.append(os.path.abspath(webp_path))
                        print(f"[OK] Tải thành công: {filename}")
                        break
                    else:
                        print(f"❌ Chuyển đổi WebP thất bại: {filename}")
                        if retry < max_retries - 1:
                            print(f"[RETRY] Thử lại lần {retry + 2}...")
                            time.sleep(3 + retry * 2)
                        
                except requests.exceptions.RequestException as e:
                    print(f"[ERR] Lỗi tải ảnh lần {retry + 1}: {str(e)[:50]}...")
                    if retry < max_retries - 1:
                        print(f"[RETRY] Thử lại lần {retry + 2}...")
                        time.sleep(4 + retry * 2)
                    else:
                        print(f"[ERR] Thất bại hoàn toàn: {filename}")
                except Exception as e:
                    print(f"[ERR] Lỗi không xác định: {str(e)[:50]}...")
                    break
            
        except Exception as e:
            print(f"[ERR] Lỗi xử lý ảnh: {str(e)[:50]}...")
    
    # Chuẩn hóa đường dẫn thành đường dẫn tuyệt đối
    downloaded_images = [os.path.abspath(p) for p in downloaded_images if p]
    if product_info is not None:
        product_info['downloaded_images'] = downloaded_images
        product_info['image_paths'] = downloaded_images
    print(f"[STATS] Kết quả tải ảnh: {len(downloaded_images)}/{len(product_info.get('images', []))} thành công")
    return downloaded_images

def create_cdha_excel_report(products, output_file):
    """Tạo báo cáo Excel cho sản phẩm CDHA kèm đường dẫn ảnh cục bộ"""
    try:
        # Chuẩn bị dữ liệu cho Excel
        excel_data = []
        
        for product in products:
            # Tạo HTML cho bảng thông số kỹ thuật
            spec_html = create_specifications_html_table(product.get('specifications', {}))
            
            # Lấy danh sách đường dẫn file ảnh cục bộ (tuyệt đối)
            downloaded = product.get('downloaded_images') or product.get('image_paths') or []
            if isinstance(downloaded, str):
                image_paths_str = downloaded
            elif isinstance(downloaded, list):
                abs_paths = [os.path.abspath(p) for p in downloaded if p]
                image_paths_str = '\n'.join(abs_paths)
            else:
                image_paths_str = ''
            
            excel_data.append({
                'URL': product.get('url', ''),
                'Tên sản phẩm': product.get('name', ''),
                'Mã sản phẩm': product.get('code', ''),
                'Giá sản phẩm': product.get('price', 'Không có giá'),
                'Số lượng ảnh': len(product.get('images', [])),
                'Đường dẫn link ảnh': image_paths_str,
                'Thông số kỹ thuật': spec_html,
                'Danh sách ảnh': '\n'.join(product.get('images', []))
            })
        
        # Tạo DataFrame và lưu Excel
        df = pd.DataFrame(excel_data)
        
        # Đảm bảo thư mục cha tồn tại
        output_abs = os.path.abspath(output_file)
        os.makedirs(os.path.dirname(output_abs), exist_ok=True)
        
        # Lưu Excel với định dạng và chỉnh độ rộng cột
        with pd.ExcelWriter(output_abs, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='CDHA_SanPham')
            worksheet = writer.sheets['CDHA_SanPham']
            
            # Tự động điều chỉnh độ rộng cột
            for col in worksheet.columns:
                col_name = str(col[0].value) if col[0].value is not None else ''
                col_letter = col[0].column_letter
                
                if col_name in ['URL', 'Đường dẫn link ảnh', 'Danh sách ảnh']:
                    worksheet.column_dimensions[col_letter].width = 50
                elif col_name == 'Thông số kỹ thuật':
                    worksheet.column_dimensions[col_letter].width = 60
                elif col_name in ['Tên sản phẩm', 'Mã sản phẩm']:
                    worksheet.column_dimensions[col_letter].width = 30
                else:
                    worksheet.column_dimensions[col_letter].width = 18
        
        print(f"[STATS] Excel: {os.path.basename(output_file)}")
        return True
        
    except Exception as e:
        print(f"[ERR] Excel error: {str(e)[:50]}...")
        import traceback
        traceback.print_exc()
        return False

def create_specifications_html_table(specifications):
    """Tạo HTML table cho thông số kỹ thuật"""
    if not specifications:
        return ""
    
    html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
    html += '<tbody>'
    
    for key, value in specifications.items():
        html += f'<tr><td style="font-weight: bold;">{key}</td><td>{value}</td></tr>'
    
    html += '</tbody></table>'
    return html

class CDHACrawler:
    """Crawler chuyên dụng cho website codienhaiau.com"""
    
    def __init__(self, output_root="output_cdha", max_workers=1, max_retries=3):
        self.output_root = os.path.abspath(output_root)
        # Giới hạn an toàn tối đa 5 luồng để tránh bị chặn IP
        self.max_workers = max(1, min(int(max_workers), 5))
        self.max_retries = max_retries
        self.session = None
        
        # Khởi tạo session/scraper
        try:
            import cloudscraper
            self.session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )
        except ImportError:
            self.session = requests.Session()
            
        os.makedirs(self.output_root, exist_ok=True)

    def crawl_products(self, input_urls):
        """
        Nhận vào danh sách URL (danh mục hoặc sản phẩm), trả về tuple (list dict sản phẩm, đường dẫn folder kết quả)
        """
        # Đo thời gian thực hiện
        start_time = time.time()
        
        # Thống kê hiệu suất
        stats = {
            "urls_processed": 0,
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "categories": 0,
            "single_products": 0,
            "failed_products": 0,
            "failed_images": 0
        }
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"CDHA_ngay{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        all_products = []
        category_folders = []
        
        # Thông báo bắt đầu
        safe_socketio_emit('progress_update', {
            'percent': 0, 
            'message': f'Bắt đầu xử lý {len(input_urls)} URL từ codienhaiau.com',
            'detail': 'Đang phân tích và phân loại các URL...'
        })
        
        # Sử dụng Queue để truyền dữ liệu giữa các luồng xử lý
        category_queue = Queue()  # Hàng đợi cho các danh mục đã phân loại
        product_url_queue = Queue()  # Hàng đợi cho các URL sản phẩm từ danh mục
        
        # 1. Phân loại URL và tạo cấu trúc thư mục
        def classify_urls_worker():
            """Phân loại URL thành danh mục và sản phẩm đơn lẻ"""
            category_map = {}  # Lưu ánh xạ tên danh mục -> danh sách URL
            single_products = []  # Lưu danh sách URL sản phẩm đơn lẻ
            
            for i, url in enumerate(input_urls):
                try:
                    stats["urls_processed"] += 1
                    if is_cdha_category_url(url):
                        cat_name = get_category_vn_name(url)
                        if cat_name not in category_map:
                            category_map[cat_name] = []
                            stats["categories"] += 1
                        category_map[cat_name].append(url)
                        safe_socketio_emit('progress_update', {
                            'percent': 2 * i // len(input_urls), 
                            'message': f'Đang phân tích URL {i+1}/{len(input_urls)}',
                            'detail': f'Đã xác định danh mục: {cat_name}'
                        })
                    elif is_cdha_product_url(url):
                        single_products.append(url)
                        stats["single_products"] += 1
                        safe_socketio_emit('progress_update', {
                            'percent': 2 * i // len(input_urls), 
                            'message': f'Đang phân tích URL {i+1}/{len(input_urls)}',
                            'detail': f'Đã xác định sản phẩm đơn lẻ'
                        })
                except Exception as e:
                    print(f"Lỗi khi phân tích URL {url}: {str(e)}")
            
            # Đặt các danh mục vào hàng đợi
            for cat_name, cat_urls in category_map.items():
                category_queue.put((cat_name, cat_urls))
            
            # Đặt sản phẩm đơn lẻ vào hàng đợi nếu có
            if single_products:
                category_queue.put(('san_pham_le', single_products))
            
            return category_map, single_products
        
        # 2. Tạo các thư mục báo cáo
        report_dir = os.path.join(result_dir, "Bao_cao")
        os.makedirs(report_dir, exist_ok=True)
        
        # Thống kê tổng số lượng và thiết lập
        category_map, single_products = classify_urls_worker()
        total_categories = len(category_map)
        total_steps = total_categories + (1 if single_products else 0)
        
        # Tạo các thư mục danh mục trước (thư mục ảnh chuẩn là "Anh")
        for cat_name in category_map.keys():
            cat_folder = os.path.join(result_dir, cat_name)
            os.makedirs(cat_folder, exist_ok=True)
            os.makedirs(os.path.join(cat_folder, "Anh"), exist_ok=True)
            category_folders.append(cat_folder)
        
        # Xử lý sản phẩm đơn lẻ nếu có
        if single_products:
            single_folder = os.path.join(result_dir, "san_pham_le")
            os.makedirs(single_folder, exist_ok=True)
            os.makedirs(os.path.join(single_folder, "Anh"), exist_ok=True)
            category_folders.append(single_folder)
        
        # 3. Xử lý từng danh mục với đa luồng
        def process_category_worker(cat_name, cat_urls):
            """Xử lý một danh mục cụ thể"""
            try:
                cat_folder = os.path.join(result_dir, cat_name)
                images_folder = os.path.join(cat_folder, "Anh")
                
                # Thu thập tất cả URL sản phẩm từ danh mục (hoặc lấy trực tiếp nếu là sản phẩm đơn lẻ)
                all_product_urls = []
                if cat_name == "san_pham_le":
                    all_product_urls = list(cat_urls)
                else:
                    for cat_url in cat_urls:
                        product_urls = extract_cdha_product_urls(cat_url, session=self.session)
                        all_product_urls.extend(product_urls)
                
                # Loại bỏ trùng lặp
                all_product_urls = list(set(all_product_urls))
                stats["products_found"] += len(all_product_urls)
                
                if not all_product_urls:
                    print(f"[WARN] Danh mục trống: {cat_name}")
                    return []
                
                # Xử lý sản phẩm với đa luồng
                category_products = []
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    future_to_url = {
                        executor.submit(extract_cdha_product_info, url, session=self.session): url 
                        for url in all_product_urls
                    }
                    
                    for i, future in enumerate(as_completed(future_to_url)):
                        url = future_to_url[future]
                        try:
                            product_info = future.result()
                            if product_info:
                                category_products.append(product_info)
                                stats["products_processed"] += 1
                                
                                # Tải ảnh sản phẩm và lưu đường dẫn ảnh
                                downloaded_images = download_cdha_product_images(product_info, images_folder)
                                product_info['downloaded_images'] = downloaded_images
                                product_info['image_paths'] = downloaded_images
                                stats["images_downloaded"] += len(downloaded_images)
                                
                                # Cập nhật progress
                                progress = 20 + (i / len(all_product_urls)) * 60
                                safe_socketio_emit('progress_update', {
                                    'percent': int(progress),
                                    'message': f'Đang xử lý danh mục: {cat_name}',
                                    'detail': f'Đã xử lý {i+1}/{len(all_product_urls)} sản phẩm'
                                })
                            else:
                                stats["failed_products"] += 1
                        except Exception as e:
                            print(f"Lỗi khi xử lý sản phẩm {url}: {str(e)}")
                            stats["failed_products"] += 1
                
                # Tạo báo cáo Excel cho danh mục
                if category_products:
                    excel_file = os.path.join(cat_folder, f"{cat_name}.xlsx")
                    create_cdha_excel_report(category_products, excel_file)
                    print(f"[OK] {cat_name}: {len(category_products)} sản phẩm")
                
                return category_products
                
            except Exception as e:
                print(f"[ERR] Danh mục lỗi: {cat_name} - {str(e)[:50]}...")
                return []
        
        # 4. Chạy xử lý đa luồng cho các danh mục
        with ThreadPoolExecutor(max_workers=min(self.max_workers, total_steps)) as executor:
            future_to_category = {}
            
            # Xử lý danh mục
            for cat_name, cat_urls in category_map.items():
                future = executor.submit(process_category_worker, cat_name, cat_urls)
                future_to_category[future] = cat_name
            
            # Xử lý sản phẩm đơn lẻ
            if single_products:
                future = executor.submit(process_category_worker, "san_pham_le", single_products)
                future_to_category[future] = "san_pham_le"
            
            # Thu thập kết quả
            for future in as_completed(future_to_category):
                cat_name = future_to_category[future]
                try:
                    category_products = future.result()
                    all_products.extend(category_products)
                except Exception as e:
                    print(f"Lỗi khi xử lý danh mục {cat_name}: {str(e)}")
        
        # 5. Tạo báo cáo tổng hợp
        if all_products:
            # Tạo báo cáo tổng hợp
            summary_excel = os.path.join(report_dir, f"CDHA_Tong_hop_{timestamp}.xlsx")
            create_cdha_excel_report(all_products, summary_excel)
            
            # Tạo file ZIP
            zip_file = os.path.join(result_dir, f"CDHA_ket_qua_{timestamp}.zip")
            with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        if not file.endswith('.zip'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)
        
        # 6. Hoàn thành
        end_time = time.time()
        duration = end_time - start_time
        
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': f'Hoàn thành! Đã xử lý {len(all_products)} sản phẩm',
            'detail': f'Thời gian: {duration:.2f}s, Ảnh: {stats["images_downloaded"]}'
        })
        
        # Log thống kê cuối cùng
        products_with_price = sum(1 for p in all_products if p.get('price'))
        print(f"\n[DONE] CDHA HOÀN THÀNH: {stats['products_processed']} sản phẩm | {stats['images_downloaded']} ảnh | {products_with_price} có giá | {duration:.1f}s")
        if stats['failed_products'] > 0:
            print(f"[WARN] Thất bại: {stats['failed_products']} sản phẩm")
        
        return all_products, result_dir

def test_single_product():
    """Test với một sản phẩm đơn lẻ để debug"""
    print("[TEST] Test với một sản phẩm đơn lẻ...")
    
    # Test URL sản phẩm cụ thể (lấy từ danh mục)
    test_url = "https://codienhaiau.com/product/1svr427036r0000-bo-nguon-abb-cp-e-24v-20a/"
    
    print(f"🔗 Test URL: {test_url}")
    
    # Trích xuất thông tin sản phẩm
    product_info = extract_cdha_product_info(test_url)
    
    if product_info:
        print(f"\n[OK] THÔNG TIN SẢN PHẨM:")
        print(f"   • Tên: {product_info.get('name', 'N/A')}")
        print(f"   • Mã: {product_info.get('code', 'N/A')}")
        print(f"   • Giá: {product_info.get('price', 'N/A')}")
        print(f"   • Số ảnh: {len(product_info.get('images', []))}")
        print(f"   • URL: {product_info.get('url', 'N/A')}")
        
        if product_info.get('images'):
            print(f"\n[IMG] DANH SÁCH ẢNH:")
            for i, img_url in enumerate(product_info['images']):
                print(f"   {i+1}. {img_url}")
        else:
            print(f"\n[ERR] Không tìm thấy ảnh nào!")
    else:
        print(f"\n[ERR] Không thể lấy thông tin sản phẩm!")

def test_category_urls():
    """Test lấy danh sách URL sản phẩm từ danh mục"""
    print("[TEST] Test lấy danh sách URL sản phẩm từ danh mục...")
    
    category_url = "https://codienhaiau.com/category/abb/bo-nguon-abb/"
    print(f"🔗 Danh mục: {category_url}")
    
    product_urls = extract_cdha_product_urls(category_url)
    
    print(f"\n[STATS] KẾT QUẢ:")
    print(f"   • Tổng URL sản phẩm: {len(product_urls)}")
    
    if product_urls:
        print(f"\n🔗 DANH SÁCH URL SẢN PHẨM:")
        for i, url in enumerate(product_urls[:5]):  # Chỉ hiển thị 5 URL đầu
            print(f"   {i+1}. {url}")
        if len(product_urls) > 5:
            print(f"   ... và {len(product_urls) - 5} URL khác")
    else:
        print(f"\n[ERR] Không tìm thấy URL sản phẩm nào!")

if __name__ == "__main__":
    print("[START] Bat dau test crawler CDHA...")
    
    # Test 1: Lay danh sach URL tu danh muc
    test_category_urls()
    
    print("\n" + "="*50 + "\n")
    
    # Test 2: Test voi mot san pham don le
    test_single_product()
 