"""
Cognex Product Crawler - Cào dữ liệu sản phẩm từ website thientruongpc.vn
Sử dụng Sapo search_filter API để lấy danh sách sản phẩm,
trích xuất thông tin từ JavaScript inline (meta.product) và og:image.
"""

import os
import re
import shutil
import time
import json
import zipfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
import traceback
import logging

from app.crawler import get_html_content
from app import socketio

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def safe_socketio_emit(event: str, data: dict):
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"Lỗi khi emit Socket.IO ({event}): {e}")


# ──────────────────────────────────────────────
# Tiện ích lấy tên danh mục
# ──────────────────────────────────────────────
def get_category_vn_name(url):
    """Lấy tên danh mục từ URL thientruongpc.vn"""
    html = get_html_content(url)
    if html:
        soup = BeautifulSoup(html, 'html.parser')
        h1 = soup.select_one('h1.title-page, h1.title-head')
        if h1 and h1.text.strip():
            return re.sub(r'[\\/:*?"<>|]', '_', h1.text.strip())
        # Thử og:title
        og = soup.select_one('meta[property="og:title"]')
        if og and og.get('content', '').strip():
            return re.sub(r'[\\/:*?"<>|]', '_', og['content'].strip())

    parsed = urlparse(url)
    path = parsed.path.strip('/').split('/')
    if path:
        last = path[-1]
        name = re.sub(r'_\d+$', '', last)
        return name
    return 'danh_muc'


# ──────────────────────────────────────────────
# Lấy collection_id từ URL danh mục
# ──────────────────────────────────────────────
def get_collection_id(category_url):
    """
    Lấy collection_id từ Sapo .json endpoint.
    VD: https://thientruongpc.vn/cap-dieu-khien-cognex-dataman-barcode-scanner.json
    → {"collection":{"id":2455850, ...}}
    """
    json_url = category_url.rstrip('/') + '.json'
    try:
        import cloudscraper
        sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
    except ImportError:
        sc = requests.Session()

    try:
        resp = sc.get(json_url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            col = data.get('collection', {})
            return col.get('id')
    except Exception as e:
        logger.warning(f"Lỗi lấy collection_id từ {json_url}: {e}")
    return None


# ──────────────────────────────────────────────
# Lấy danh sách URL sản phẩm qua Sapo search_filter API
# ──────────────────────────────────────────────
def fetch_product_urls_via_search_filter(category_url, collection_id=None):
    """
    Dùng Sapo search_filter API để lấy tất cả URL sản phẩm trong danh mục.
    Endpoint: /s/search_filter_ajax?collection_id=XXXX&limit=54&page=N
    Trả về JSON: {"products": [...], "pagination": {...}}
    """
    product_urls = []
    seen = set()

    # Lấy collection_id nếu chưa có
    if not collection_id:
        collection_id = get_collection_id(category_url)

    parsed = urlparse(category_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    try:
        import cloudscraper
        sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
    except ImportError:
        sc = requests.Session()

    # ------ Phương pháp 1: search_filter_ajax ------
    if collection_id:
        page = 1
        limit = 54
        while True:
            api_url = (
                f"{base}/s/search_filter_ajax"
                f"?collection_id={collection_id}"
                f"&limit={limit}&page={page}&sort=0"
            )
            try:
                resp = sc.get(api_url, timeout=30)
                if resp.status_code != 200:
                    logger.warning(f"search_filter_ajax trả về {resp.status_code} ở trang {page}")
                    break
                data = resp.json()
            except Exception as e:
                logger.warning(f"Lỗi search_filter_ajax trang {page}: {e}")
                break

            products = data.get('products') or data.get('items') or []
            if not products:
                # Thử key khác
                if isinstance(data, list):
                    products = data
                else:
                    break

            for p in products:
                # URL có thể là p['url'] hoặc p['handle']
                url = p.get('url') or p.get('link')
                if not url:
                    handle = p.get('handle') or p.get('alias')
                    if handle:
                        url = f"/{handle}"
                if url:
                    full_url = urljoin(base, url)
                    if full_url not in seen:
                        seen.add(full_url)
                        product_urls.append(full_url)

            # Phân trang
            pagination = data.get('pagination', {})
            total_pages = pagination.get('total_pages') or pagination.get('totalPages') or 1
            logger.info(f"  search_filter trang {page}/{total_pages}: +{len(products)} sản phẩm")

            if page >= total_pages:
                break
            page += 1
            time.sleep(0.5)

    # ------ Phương pháp 2: search_filter (không ajax) ------
    if not product_urls and collection_id:
        for page in range(1, 10):
            api_url = (
                f"{base}/search_filter"
                f"?collection_id={collection_id}"
                f"&limit=54&page={page}&sort=0&view=json"
            )
            try:
                resp = sc.get(api_url, timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                products = data.get('products', [])
                if not products:
                    break
                for p in products:
                    url = p.get('url') or p.get('link')
                    if not url:
                        handle = p.get('handle') or p.get('alias')
                        if handle:
                            url = f"/{handle}"
                    if url:
                        full_url = urljoin(base, url)
                        if full_url not in seen:
                            seen.add(full_url)
                            product_urls.append(full_url)
                logger.info(f"  search_filter (json view) trang {page}: +{len(products)}")
                if len(products) < 54:
                    break
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"search_filter json view page {page}: {e}")
                break

    logger.info(f"✅ Tổng URL từ search_filter: {len(product_urls)}")
    return product_urls


# ──────────────────────────────────────────────
# Phân trang HTML trực tiếp (fallback)
# ──────────────────────────────────────────────
def detect_pagination(url):
    """Phát hiện số trang tối đa cho danh mục thientruongpc.vn"""
    try:
        html = get_html_content(url)
        if not html:
            return 1
        soup = BeautifulSoup(html, 'html.parser')
        max_page = 1
        for selector in ['.pagenav .page-item a.page-link', '.pagination a', 'a.page-node', '.page-num']:
            for el in soup.select(selector):
                text = el.get_text(strip=True)
                try:
                    max_page = max(max_page, int(text))
                except ValueError:
                    href = el.get('href', '')
                    m = re.search(r'[?&]page=(\d+)', href)
                    if m:
                        max_page = max(max_page, int(m.group(1)))
        return max(max_page, 1)
    except Exception as e:
        logger.warning(f"Lỗi detect_pagination: {e}")
        return 1


def extract_product_urls_from_html(category_url):
    """Trích xuất URL sản phẩm từ HTML trang danh mục (fallback dùng phân trang)"""
    product_urls = []
    seen = set()
    max_pages = detect_pagination(category_url)
    parsed = urlparse(category_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    logger.info(f"📄 HTML fallback: {max_pages} trang")

    for page in range(1, max_pages + 1):
        page_url = category_url if page == 1 else f"{category_url}?page={page}"
        html = get_html_content(page_url)
        if not html:
            continue
        soup = BeautifulSoup(html, 'html.parser')

        # Nhiều selector khác nhau
        selectors = [
            'a.text1line',
            'div.col-item .product-name a',
            'div.product-box .product-name a',
            '.product-item a.product-name',
            '.product-list .item a',
            'h3.product-name a',
            'a.product-name',
            '.product-item--title a',
        ]
        found = []
        for sel in selectors:
            found = soup.select(sel)
            if found:
                break

        for link in found:
            href = link.get('href', '')
            if href:
                full_url = urljoin(base, href)
                if full_url not in seen:
                    seen.add(full_url)
                    product_urls.append(full_url)

        if not found:
            logger.warning(f"⚠️  Không tìm thấy sản phẩm ở trang {page}")

    logger.info(f"✅ HTML fallback: {len(product_urls)} URL")
    return product_urls


# ──────────────────────────────────────────────
# Hàm trích xuất URL sản phẩm tổng hợp
# ──────────────────────────────────────────────
def extract_cognex_product_urls(category_url):
    """Trích xuất tất cả URL sản phẩm từ URL danh mục Cognex"""
    collection_id = get_collection_id(category_url)
    logger.info(f"📁 Collection ID: {collection_id}")

    # Thử API trước
    urls = fetch_product_urls_via_search_filter(category_url, collection_id)

    # Nếu API không trả về → fallback HTML
    if not urls:
        logger.warning("API không trả về kết quả, thử HTML scraping...")
        urls = extract_product_urls_from_html(category_url)

    # Deduplicate + giữ thứ tự
    seen = set()
    deduped = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


# ──────────────────────────────────────────────
# Trích xuất thông tin sản phẩm
# ──────────────────────────────────────────────
def extract_cognex_product_info(product_url):
    """
    Trích xuất thông tin chi tiết sản phẩm từ thientruongpc.vn.

    Nguồn dữ liệu:
    - Tên sản phẩm  : meta.product.name (JavaScript inline) hoặc og:title
    - Mã sản phẩm   : meta.product.type (JavaScript inline) hoặc p.cl_mobile_old span.cl_main
    - Ảnh sản phẩm  : og:image (1 ảnh duy nhất)
    - Giá           : meta.product.price hoặc span.price
    """
    logger.info(f"📦 Cào sản phẩm: {product_url}")
    try:
        html = get_html_content(product_url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')

        # --- Giá trị mặc định ---
        product_name = ""
        product_code = ""
        product_price = "Liên hệ"
        image_url = ""

        # ====== 1. Trích xuất từ JavaScript inline (ưu tiên cao nhất) ======
        # meta.product = {"id": ..., "vendor": "...", "name": "...", "type": "...", "price": ...}
        js_match = re.search(
            r'meta\.product\s*=\s*(\{[^;]+\});',
            html,
            re.DOTALL
        )
        if js_match:
            try:
                js_obj_str = js_match.group(1)
                # Làm sạch để parse JSON
                js_obj_str = re.sub(r',\s*\}', '}', js_obj_str)
                product_js = json.loads(js_obj_str)
                product_name = product_js.get('name', '').strip()
                product_code = product_js.get('type', '').strip()
                price_val = product_js.get('price')
                if price_val:
                    try:
                        product_price = f"{int(price_val):,}".replace(',', '.') + " VNĐ"
                    except Exception:
                        product_price = str(price_val)
                logger.debug(f"  JS → name={product_name[:40]}, type={product_code}")
            except Exception as e:
                logger.warning(f"  Lỗi parse JS meta.product: {e}")

        # ====== 2. Fallback: og:title, og:description ======
        if not product_name:
            og_title = soup.select_one('meta[property="og:title"]')
            if og_title:
                product_name = og_title.get('content', '').strip()

        if not product_name:
            title_tag = soup.select_one('title')
            if title_tag:
                product_name = title_tag.get_text(strip=True)

        # ====== 3. Fallback: Mã từ HTML p.cl_mobile_old ======
        if not product_code:
            vendor_span = soup.find('span', class_='txt_vendor',
                                    string=re.compile(r'Loại sản phẩm', re.I))
            if vendor_span and vendor_span.parent:
                cl_main = vendor_span.parent.find('span', class_='cl_main')
                if cl_main:
                    product_code = cl_main.get_text(strip=True)

        if not product_code:
            code_elem = soup.select_one('p.cl_mobile_old span.cl_main')
            if code_elem:
                product_code = code_elem.get_text(strip=True)

        if not product_code:
            sku_elem = soup.select_one('.sku, [itemprop="sku"]')
            if sku_elem:
                product_code = sku_elem.get_text(strip=True)

        if not product_code:
            # Dự phòng từ URL
            parts = product_url.rstrip('/').split('/')
            if parts:
                product_code = parts[-1].split('-')[0].upper()

        # ====== 4. Ảnh sản phẩm: lấy 1 ảnh DUY NHẤT từ og:image ======
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img:
            img_src = og_img.get('content', '').strip()
            if img_src:
                if img_src.startswith('//'):
                    img_src = 'https:' + img_src
                image_url = img_src

        # Nếu chưa có ảnh, thử secure_url
        if not image_url:
            og_img_sec = soup.select_one('meta[property="og:image:secure_url"]')
            if og_img_sec:
                image_url = og_img_sec.get('content', '').strip()

        # ====== 5. Làm sạch mã sản phẩm ======
        product_code = re.sub(r'[\\/:*?"<>|]', '_', product_code).strip()

        return {
            'url': product_url,
            'name': product_name,
            'code': product_code,
            'price': product_price,
            'image': image_url,    # chỉ 1 ảnh duy nhất
        }

    except Exception as e:
        logger.error(f"Lỗi khi trích xuất {product_url}: {str(e)}\n{traceback.format_exc()}")
        return None


# ──────────────────────────────────────────────
# Tải ảnh và chuyển sang WebP
# ──────────────────────────────────────────────
def download_cognex_image(product_info, output_folder):
    """
    Tải 1 ảnh duy nhất, chuyển sang WebP, đặt tên file theo mã sản phẩm.
    Trả về tên file đã lưu (hoặc '' nếu thất bại).
    """
    if not product_info:
        return ''

    image_url = product_info.get('image', '')
    if not image_url:
        logger.warning(f"  Không có URL ảnh cho sản phẩm {product_info.get('code', '?')}")
        return ''

    from app.webp_converter import WebPConverter

    product_code = product_info.get('code', 'SP_KhongTen')
    # Tên file WebP theo mã sản phẩm
    filename = f"{product_code}.webp"
    filepath = os.path.join(output_folder, filename)

    # Nếu file đã tồn tại thì bỏ qua
    if os.path.exists(filepath):
        return filename

    try:
        import cloudscraper
        sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
    except ImportError:
        sc = requests.Session()

    try:
        resp = sc.get(image_url, stream=True, timeout=30)
        if resp.status_code == 200:
            webp_path = WebPConverter.convert_bytes_to_webp(resp.content, filepath)
            if webp_path:
                logger.info(f"  ✅ Đã lưu ảnh: {filename}")
                return filename
    except Exception as e:
        logger.warning(f"  Lỗi tải ảnh {image_url}: {e}")

    return ''


# ──────────────────────────────────────────────
# Tạo báo cáo Excel
# ──────────────────────────────────────────────
def create_cognex_excel_report(products, output_file):
    """Tạo báo cáo Excel với danh sách sản phẩm"""
    try:
        excel_data = []
        for p in products:
            excel_data.append({
                'URL': p.get('url', ''),
                'Tên sản phẩm': p.get('name', ''),
                'Mã sản phẩm': p.get('code', ''),
                'Giá sản phẩm': p.get('price', ''),
                'File ảnh WebP': p.get('saved_image', ''),
            })
        df = pd.DataFrame(excel_data)
        df.to_excel(output_file, index=False, engine='openpyxl')
        logger.info(f"✅ Excel: {output_file}")
        return True
    except Exception as e:
        logger.error(f"Lỗi tạo Excel: {e}")
        return False


# ──────────────────────────────────────────────
# Class Crawler chính
# ──────────────────────────────────────────────
class CognexCrawler:
    """Crawler chuyên dụng cho website thientruongpc.vn (COGNEX)"""

    def __init__(self, output_root="output_cognex", max_workers=4, max_retries=3):
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        os.makedirs(self.output_root, exist_ok=True)

    def crawl_products(self, input_urls):
        start_time = time.time()
        stats = {"urls_processed": 0, "products_found": 0, "products_processed": 0}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"COGNEX_ngay{timestamp}")
        report_dir = os.path.join(result_dir, "Bao_cao")
        os.makedirs(report_dir, exist_ok=True)

        all_products = []

        safe_socketio_emit('progress_update', {
            'percent': 0,
            'message': f'Bắt đầu xử lý {len(input_urls)} URL từ thientruongpc.vn',
            'detail': 'Đang phân tích danh mục...'
        })

        # Nhóm URL theo tên danh mục
        category_map = {}
        for url in input_urls:
            cat_name = get_category_vn_name(url)
            category_map.setdefault(cat_name, []).append(url)
            stats["urls_processed"] += 1

        def process_category(cat_name, cat_urls):
            cat_folder = os.path.join(result_dir, cat_name)
            images_folder = os.path.join(cat_folder, "Anh_San_Pham")
            os.makedirs(images_folder, exist_ok=True)

            # Lấy tất cả URL sản phẩm
            all_product_urls = []
            for url in cat_urls:
                urls = extract_cognex_product_urls(url)
                all_product_urls.extend(urls)

            # Deduplicate
            seen = set()
            deduped = []
            for u in all_product_urls:
                if u not in seen:
                    seen.add(u)
                    deduped.append(u)
            all_product_urls = deduped

            stats["products_found"] += len(all_product_urls)
            logger.info(f"📂 [{cat_name}] Tổng {len(all_product_urls)} sản phẩm")

            if not all_product_urls:
                return []

            cat_products = []
            total = len(all_product_urls)
            for i, prod_url in enumerate(all_product_urls):
                try:
                    info = extract_cognex_product_info(prod_url)
                    if info:
                        # Tải 1 ảnh → WebP
                        saved_img = download_cognex_image(info, images_folder)
                        info['saved_image'] = saved_img
                        cat_products.append(info)
                        stats["products_processed"] += 1

                        progress = 10 + int((i + 1) / total * 80)
                        safe_socketio_emit('progress_update', {
                            'percent': progress,
                            'message': f'Danh mục: {cat_name}',
                            'detail': f'{i + 1}/{total} – {info.get("code", "")} | {info.get("name", "")[:50]}'
                        })
                except Exception as e:
                    logger.error(f"Lỗi xử lý {prod_url}: {e}")

            # Lưu Excel từng danh mục
            if cat_products:
                excel_file = os.path.join(report_dir, f"{cat_name}.xlsx")
                create_cognex_excel_report(cat_products, excel_file)

            return cat_products

        # Xử lý từng danh mục
        for cat_name, cat_urls in category_map.items():
            try:
                products = process_category(cat_name, cat_urls)
                all_products.extend(products)
            except Exception as e:
                logger.error(f"Lỗi xử lý danh mục {cat_name}: {e}")

        # Excel tổng hợp
        if all_products:
            summary_excel = os.path.join(report_dir, f"COGNEX_Tong_hop_{timestamp}.xlsx")
            create_cognex_excel_report(all_products, summary_excel)

            # Nén ZIP
            zip_file = os.path.join(result_dir, f"COGNEX_ket_qua_{timestamp}.zip")
            with zipfile.ZipFile(zip_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        if not file.endswith('.zip'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)

        duration = time.time() - start_time
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': f'Hoàn thành! Đã xử lý {len(all_products)} sản phẩm',
            'detail': f'Thời gian: {duration:.2f}s'
        })

        logger.info(f"🎉 Hoàn tất: {len(all_products)} sản phẩm trong {duration:.1f}s")
        return all_products, result_dir


if __name__ == "__main__":
    crawler = CognexCrawler()
    # Test với 1 URL danh mục
    # crawler.crawl_products(['https://thientruongpc.vn/cap-dieu-khien-cognex-dataman-barcode-scanner'])
