import os
import time
import zipfile
from datetime import datetime
import pandas as pd
from bs4 import BeautifulSoup
from curl_cffi import requests
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
from app import socketio
from io import BytesIO
from PIL import Image
from queue import Queue
import logging

def safe_socketio_emit(event: str, data: dict):
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
    except Exception:
        pass

class AllenBradleyCrawler:
    def __init__(self, output_root="data", max_workers=5, max_retries=3, init_translator=True):
        self.output_root = output_root
        self.max_workers = max_workers
        self.max_retries = max_retries
        
        # Sử dụng curl_cffi để vượt qua Akamai WAF (KHÔNG đè header để giữ nguyên fingerprint)
        self.session = requests.Session(impersonate="chrome120")
        
        # Tự động tải và áp dụng Cloudflare WARP hoặc Proxy từ proxies.txt
        self.proxies_list = self._load_proxies()
        if self.proxies_list:
            self.session.proxies = self.proxies_list[0]
            print(f"[INFO] Đã áp dụng Proxy cho AllenBradleyCrawler: {self.session.proxies}")
            
    def _load_proxies(self):
        proxies_list = []
        
        # 1. Phát hiện Cloudflare WARP proxy tự động (cổng 40000 hoặc 1080)
        import socket
        for port in [40000, 1080]:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    warp_proxy = f"socks5h://127.0.0.1:{port}"
                    proxies_list.append({'http': warp_proxy, 'https': warp_proxy})
                    print(f"[INFO] Phát hiện Cloudflare WARP VPN đang chạy trên cổng {port}.")
            except Exception:
                pass
                
        # 2. Đọc thêm proxy từ file proxies.txt nếu có
        proxy_file = os.path.join(os.path.dirname(__file__), 'proxies.txt')
        if os.path.exists(proxy_file):
            try:
                with open(proxy_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            if not line.startswith(('http://', 'https://', 'socks5://', 'socks5h://')):
                                line = f"http://{line}"
                            proxies_list.append({'http': line, 'https': line})
            except Exception as e:
                print(f"[WARN] Lỗi khi đọc file proxy: {e}")
        return proxies_list

    def crawl_products(self, product_urls):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_dir = os.path.join(self.output_root, f"AllenBradley_{timestamp}")
        os.makedirs(result_dir, exist_ok=True)
        images_dir = os.path.join(result_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        if not product_urls:
            safe_socketio_emit('progress_update', {
                'percent': 100,
                'message': 'Lỗi',
                'detail': 'Không có URL sản phẩm nào được nhập.'
            })
            return [], self.output_root
            
        # 1.5 Phân loại URL (Danh mục vs Sản phẩm)
        final_product_urls = []
        for url in product_urls:
            url_str = url.strip()
            if not url_str:
                continue
            # Nhận diện nếu là mã nguồn HTML
            is_html = '<' in url_str and '>' in url_str and any(tag in url_str.lower() for tag in ['<div', '<html', '<body', '<span', '<a'])
            if is_html:
                safe_socketio_emit('progress_update', {
                    'percent': 10,
                    'message': 'Đang quét HTML danh mục',
                    'detail': 'Đang trích xuất link từ mã HTML'
                })
                links = self._get_product_links_from_category(url_str)
                for link in links:
                    if link not in final_product_urls:
                        final_product_urls.append(link)
            # Nhận diện trang danh mục nếu không chứa '/products/details.' nhưng là URL .html
            elif url_str.startswith('http') and '/products/details.' not in url_str.lower() and '.html' in url_str.lower():
                safe_socketio_emit('progress_update', {
                    'percent': 10,
                    'message': 'Đang quét danh mục',
                    'detail': f'Tìm sản phẩm trong {url_str}'
                })
                links = self._get_product_links_from_category(url_str)
                for link in links:
                    if link not in final_product_urls:
                        final_product_urls.append(link)
            elif url_str.startswith('http'):
                # Nếu là URL sản phẩm hoặc danh mục có chứa giao thức http
                if url_str not in final_product_urls:
                    final_product_urls.append(url_str)
            else:
                # Nếu là mã sản phẩm thông thường (ví dụ: 5069-OF8)
                prod_url = f"https://www.rockwellautomation.com/en-us/products/details.{url_str}.html"
                if prod_url not in final_product_urls:
                    final_product_urls.append(prod_url)
                    
        product_urls = final_product_urls
        if not product_urls:
            safe_socketio_emit('progress_update', {
                'percent': 100,
                'message': 'Lỗi',
                'detail': 'Không tìm thấy URL sản phẩm hợp lệ nào.'
            })
            return [], self.output_root

        # 2. Cào chi tiết từng sản phẩm
        all_products = []
        total_links = len(product_urls)
        
        safe_socketio_emit('progress_update', {
            'percent': 15,
            'message': 'Bắt đầu cào chi tiết sản phẩm',
            'detail': f'Sẽ cào {total_links} sản phẩm'
        })

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(self._extract_product_detail, url, images_dir): url
                for url in product_urls
            }
            
            processed = 0
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                processed += 1
                try:
                    product_info = future.result()
                    if product_info:
                        all_products.append(product_info)
                        
                    progress = 15 + int(75 * (processed / total_links))
                    safe_socketio_emit('progress_update', {
                        'percent': progress,
                        'message': f'Đang xử lý {processed}/{total_links}',
                        'detail': product_info['name'] if product_info else url
                    })
                except Exception as e:
                    print(f"Lỗi khi cào {url}: {e}")

        # Create Excel report
        if all_products:
            excel_path = os.path.join(result_dir, f"AllenBradley_Result_{timestamp}.xlsx")
            self._create_excel_report(all_products, excel_path)

            # Create ZIP file
            zip_path = os.path.join(self.output_root, f"AllenBradley_Result_{timestamp}.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(result_dir):
                    for file in files:
                        if not file.endswith('.zip'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arcname)
            
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': 'Hoàn thành!',
            'detail': f'Đã cào {len(all_products)} sản phẩm'
        })

        return all_products, self.output_root

    def _get_product_links_from_category(self, url_or_html):
        """Trích xuất tất cả các link sản phẩm từ trang danh mục (category) hoặc mã nguồn HTML"""
        product_links = []
        
        # Nhận diện nếu là mã nguồn HTML (chứa các thẻ)
        is_html_input = '<' in url_or_html and '>' in url_or_html and any(tag in url_or_html.lower() for tag in ['<div', '<html', '<body', '<span', '<a', '<table'])
        if is_html_input:
            try:
                soup = BeautifulSoup(url_or_html, 'html.parser')
                # Tìm tất cả thẻ a
                link_elems = soup.find_all('a')
                for elem in link_elems:
                    href = elem.get('href')
                    if href:
                        # Ghép với domain gốc nếu cần
                        full_url = urljoin('https://www.rockwellautomation.com/', href)
                        if ('/products/details.' in full_url.lower() or '/details.' in full_url.lower()) and full_url not in product_links:
                            product_links.append(full_url)
            except Exception as e:
                print(f"Lỗi khi quét HTML danh mục: {e}")
            return product_links

        # Nếu đầu vào là URL
        url = url_or_html
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, timeout=30)
                # Kiểm tra xem có bị chặn 403 không
                if response.status_code == 403:
                    safe_socketio_emit('progress_update', {
                        'percent': 50,
                        'message': 'Lỗi truy cập (403)',
                        'detail': 'Yêu cầu bị chặn bởi bảo mật của Rockwell. Vui lòng Inspect trang trên trình duyệt của bạn và copy mã nguồn HTML dán vào đây.'
                    })
                    print(f"Bị chặn 403 khi quét danh mục {url}")
                    break
                    
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                link_elems = soup.find_all('a')
                for elem in link_elems:
                    href = elem.get('href')
                    if href:
                        full_url = urljoin(url, href)
                        if ('/products/details.' in full_url.lower() or '/details.' in full_url.lower()) and full_url not in product_links:
                            product_links.append(full_url)
                
                if product_links:
                    break
            except Exception as e:
                if attempt == self.max_retries - 1:
                    print(f"Lỗi khi quét danh mục {url}: {e}")
                time.sleep(1)
        return product_links

    def crawl_category_links(self, category_urls):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(self.output_root, exist_ok=True)
        
        all_links = []
        total_urls = len(category_urls)
        
        for i, url in enumerate(category_urls):
            safe_socketio_emit('progress_update', {
                'percent': int(10 + 80 * (i / total_urls)),
                'message': f'Đang quét danh mục {i+1}/{total_urls}',
                'detail': url
            })
            
            links = self._get_product_links_from_category(url)
            for link in links:
                if link not in all_links:
                    all_links.append(link)
                    
        # Chuyển đổi lưu thành file Excel thay vì txt
        excel_path = os.path.join(self.output_root, f"AllenBradley_Links_{timestamp}.xlsx")
        if all_links:
            import pandas as pd
            df = pd.DataFrame({'Link Sản Phẩm': all_links})
            df.to_excel(excel_path, index=False)
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': 'Hoàn thành!',
            'detail': f'Đã tìm thấy {len(all_links)} link sản phẩm'
        })
        
        return excel_path if all_links else None, len(all_links)


    def _extract_product_detail(self, url, images_dir):
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')

                # 1. Mã sản phẩm
                catalog_elem = soup.select_one('.ra-product-new__catalog-number')
                catalog_number = catalog_elem.get_text(strip=True) if catalog_elem else ''

                # 2. Tên sản phẩm cơ bản
                desc_elem = soup.select_one('.ra-product-new__description')
                base_desc = desc_elem.get_text(strip=True) if desc_elem else ''
                
                if not catalog_number and not base_desc:
                    # Thử các selector khác nếu trang dùng cấu trúc khác
                    h1 = soup.select_one('h1')
                    if h1:
                        base_desc = h1.get_text(strip=True)
                
                # Ghép tên sản phẩm
                name = ""
                if base_desc and catalog_number:
                    name = f"{base_desc} {catalog_number}"
                elif base_desc:
                    name = base_desc
                else:
                    name = catalog_number
                    
                if not name:
                    return None

                # 3. Ảnh sản phẩm
                image_url = ''
                local_image = ''
                img_elem = soup.select_one('.multimedia-grid__gallery-modal-image, .multimedia-grid__product-image, .ra-product-image')
                if img_elem:
                    src = img_elem.get('src') or img_elem.get('data-src')
                    if src:
                        image_url = urljoin(url, src)
                        local_image = self._download_and_convert_webp(image_url, images_dir, catalog_number)

                # 4. Thông số kỹ thuật & Tất cả dữ liệu
                spec_html = ""
                content_container = soup.select_one('.ra-product-new, .ra-product-new__content-container, main') or soup
                
                pairs = []
                if content_container:
                    # Gỡ bỏ các script nếu có để an toàn
                    for script in content_container.select('script'):
                        script.decompose()
                        
                    # Trích xuất từ các bảng
                    for table in content_container.find_all('table'):
                        # Lọc những bảng nào mà dữ liệu có link điều hướng thì loại
                        has_navigation_link = False
                        for a in table.find_all('a'):
                            href = a.get('href', '')
                            if href and not href.startswith('#'):
                                has_navigation_link = True
                                break
                        if has_navigation_link:
                            continue

                        for tr in table.find_all('tr'):
                            # Lọc dòng có chứa link điều hướng
                            has_row_link = False
                            for a in tr.find_all('a'):
                                href = a.get('href', '')
                                if href and not href.startswith('#'):
                                    has_row_link = True
                                    break
                            if has_row_link:
                                continue

                            tds = tr.find_all(['td', 'th'])
                            if len(tds) >= 2:
                                k = tds[0].get_text(strip=True)
                                v = " ".join([t.get_text(strip=True) for t in tds[1:]])
                                if k and v:
                                    pairs.append((k, v))
                    
                    # Trích xuất từ dl/dt/dd
                    for dl in content_container.find_all('dl'):
                        # Lọc những dl nào mà dữ liệu có link điều hướng thì loại
                        has_dl_link = False
                        for a in dl.find_all('a'):
                            href = a.get('href', '')
                            if href and not href.startswith('#'):
                                has_dl_link = True
                                break
                        if has_dl_link:
                            continue

                        dts = dl.find_all('dt')
                        dds = dl.find_all('dd')
                        for dt, dd in zip(dts, dds):
                            k = dt.get_text(strip=True)
                            v = dd.get_text(strip=True)
                            if k and v:
                                pairs.append((k, v))
                                
                    # Trích xuất từ li
                    for li in content_container.find_all('li'):
                        # Lọc những li nào mà dữ liệu có link điều hướng thì loại
                        has_li_link = False
                        for a in li.find_all('a'):
                            href = a.get('href', '')
                            if href and not href.startswith('#'):
                                has_li_link = True
                                break
                        if has_li_link:
                            continue

                        text = li.get_text(strip=True)
                        if ':' in text and not li.find('table'):
                            parts = text.split(':', 1)
                            k = parts[0].strip()
                            v = parts[1].strip()
                            if k and v and len(k) < 50:
                                pairs.append((k, v))

                tbody_rows = []
                if catalog_number:
                    tbody_rows.append(f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{catalog_number}</td></tr>')
                if name:
                    tbody_rows.append(f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{name}</td></tr>')
                
                exclude_keys = {"mã sản phẩm", "tên sản phẩm", "copyright"}
                seen = set()
                for k, v in pairs:
                    k_clean = k.lower().strip()
                    if k_clean in exclude_keys or k_clean in seen:
                        continue
                    seen.add(k_clean)
                    k_esc = k.replace('<', '&lt;').replace('>', '&gt;')
                    v_esc = v.replace('<', '&lt;').replace('>', '&gt;')
                    tbody_rows.append(f'<tr><td style="font-weight: bold;">{k_esc}</td><td>{v_esc}</td></tr>')
                    
                tbody_rows.append('<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>')
                
                spec_html = (
                    '<table id="specifications" border="1" cellpadding="8" cellspacing="0" '
                    'style="border-collapse: collapse; font-family: Arial; width: 100%;">'
                    '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
                    f'<tbody>{"".join(tbody_rows)}</tbody></table>'
                )

                return {
                    'name': name,
                    'catalog_number': catalog_number,
                    'url': url,
                    'image_url': image_url,
                    'local_image': local_image,
                    'specifications': spec_html
                }

            except Exception as e:
                if attempt == self.max_retries - 1:
                    print(f"Thất bại lấy chi tiết {url} sau {self.max_retries} lần: {e}")
                time.sleep(1)
        return None

    def _download_and_convert_webp(self, url, folder, catalog_number):
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            # Use catalog number or timestamp for filename
            clean_name = "".join([c if c.isalnum() else "_" for c in (catalog_number or str(int(time.time())))])
            filename = f"{clean_name}.webp"
            file_path = os.path.join(folder, filename)
            
            # Prevent overwriting if multiple images have same name
            counter = 1
            while os.path.exists(file_path):
                filename = f"{clean_name}_{counter}.webp"
                file_path = os.path.join(folder, filename)
                counter += 1
                
            img = Image.open(BytesIO(response.content))
            # Convert to RGB if RGBA/P to save as WebP without issues
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
                
            img.save(file_path, "WEBP", quality=90)
            
            return filename
        except Exception as e:
            print(f"Lỗi tải/chuyển đổi ảnh {url}: {e}")
            return ''

    def _create_excel_report(self, products, file_path):
        if not products:
            return
            
        df = pd.DataFrame(products)
        df = df.rename(columns={
            'catalog_number': 'Mã Sản Phẩm',
            'name': 'Tên Sản Phẩm',
            'url': 'Đường Dẫn',
            'image_url': 'Ảnh Gốc',
            'local_image': 'Tên File Ảnh',
            'specifications': 'Thông Số (HTML)'
        })
        
        columns_order = ['Mã Sản Phẩm', 'Tên Sản Phẩm', 'Thông Số (HTML)', 'Đường Dẫn', 'Tên File Ảnh', 'Ảnh Gốc']
        df = df[[col for col in columns_order if col in df.columns]]
        
        df.to_excel(file_path, index=False)

    def crawl_links_via_api(self, api_url, page_size=200, total_expected=3000, pagination_type='from_size', offset_param=None, limit_param=None):
        """Trích xuất link sản phẩm bằng cách phân trang trực tiếp qua API nội bộ của Rockwell"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(self.output_root, exist_ok=True)
        
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        
        parsed_url = urlparse(api_url)
        query_params_original = parse_qs(parsed_url.query)
        
        # 1. Tự động phát hiện tham số phân trang từ URL nếu không được truyền từ form
        if not offset_param or not limit_param:
            detected_offset = None
            detected_limit = None
            pagination_pairs = [
                ('startIndex', 'numResults'),
                ('from', 'size'),
                ('offset', 'limit'),
                ('page', 'pageSize'),
                ('page', 'size')
            ]
            for off_k, lim_k in pagination_pairs:
                if off_k in query_params_original and lim_k in query_params_original:
                    detected_offset = off_k
                    detected_limit = lim_k
                    break
            
            if detected_offset and detected_limit:
                offset_param = detected_offset
                limit_param = detected_limit
                if offset_param == 'startIndex':
                    pagination_type = 'startIndex_numResults'
                elif offset_param == 'offset':
                    pagination_type = 'offset_limit'
                elif offset_param == 'page':
                    pagination_type = 'page_size_0'
                elif offset_param == 'from':
                    pagination_type = 'from_size'
                print(f"[INFO] Auto-detected pagination params: {offset_param} & {limit_param}")

        # Thiết lập mặc định tham số phân trang nếu vẫn chưa tìm thấy
        if not offset_param:
            if pagination_type == 'from_size':
                offset_param = 'from'
            elif pagination_type == 'offset_limit':
                offset_param = 'offset'
            elif pagination_type == 'startIndex_numResults':
                offset_param = 'startIndex'
            else:
                offset_param = 'page'
                
        if not limit_param:
            if pagination_type == 'from_size':
                limit_param = 'size'
            elif pagination_type == 'offset_limit':
                limit_param = 'limit'
            elif pagination_type == 'startIndex_numResults':
                limit_param = 'numResults'
            else:
                limit_param = 'size'

        # 2. Tự động lấy Page Size từ URL ban đầu nếu có giá trị số
        if limit_param in query_params_original:
            try:
                url_val = int(query_params_original[limit_param][0])
                if url_val > 0:
                    page_size = url_val
                    print(f"[INFO] Auto-detected Page Size from URL: {page_size}")
            except Exception:
                pass
                
        all_links = []
        seen_urls = set()
        self._total_raw_processed = 0
        
        # Phân tích URL để tách các tham số gốc và tham số phân trang
        query_params = parse_qs(parsed_url.query)
        
        # Xóa các tham số phân trang cũ trong URL để tránh bị đè dữ liệu
        for key in ['from', 'size', 'offset', 'limit', 'page', 'pageSize', 'startIndex', 'numResults', offset_param, limit_param]:
            query_params.pop(key, None)
            
        base_api_url = urlunparse((
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            '', # Xây dựng lại query động ở phần params của request.get
            parsed_url.fragment
        ))
        
        # Cập nhật headers giống trình duyệt thực tế
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.rockwellautomation.com/",
            "Origin": "https://www.rockwellautomation.com"
        })
        
        safe_socketio_emit('progress_update', {
            'percent': 10,
            'message': 'Đang chuẩn bị gọi API',
            'detail': 'Bắt đầu gửi request phân trang'
        })
        
        # Thiết lập khoảng phân trang
        is_offset_based = pagination_type in ['from_size', 'offset_limit', 'startIndex_numResults']
        
        if is_offset_based:
            offsets = range(0, total_expected, page_size)
            total_steps = len(offsets)
        else: # page_size_0 hoặc page_size_1
            total_pages = (total_expected + page_size - 1) // page_size
            offsets = range(total_pages)
            total_steps = len(offsets)
            
        for index, step_val in enumerate(offsets):
            params = {}
            for k, v in query_params.items():
                params[k] = v[0] if len(v) == 1 else v
                
            # Đặt giá trị phân trang động theo tham số tùy chỉnh hoặc mặc định
            if is_offset_based:
                params[offset_param] = step_val
                params[limit_param] = page_size
            else:
                if pagination_type == 'page_size_1':
                    params[offset_param] = step_val + 1
                else:
                    params[offset_param] = step_val
                params[limit_param] = page_size
                
            progress_percent = int(10 + 80 * (index / max(total_steps, 1)))
            safe_socketio_emit('progress_update', {
                'percent': progress_percent,
                'message': f'Đang lấy dữ liệu trang {index + 1}/{total_steps}',
                'detail': f'{offset_param}: {params[offset_param]}'
            })
            
            success = False
            end_of_data = False
            for attempt in range(self.max_retries):
                try:
                    response = self.session.get(base_api_url, params=params, timeout=30)
                    if response.status_code != 200:
                        print(f"API Error (Status {response.status_code}) at step {index}, attempt {attempt+1}: {response.text[:200]}")
                        time.sleep(2)
                        continue
                        
                    data = response.json()
                    
                    # Trích xuất danh sách kết quả từ JSON động
                    docs = []
                    if isinstance(data, dict):
                        docs = data.get("docs") or data.get("results") or data.get("items") or data.get("data")
                        if not docs:
                            # Thử tìm list bất kỳ trong dict
                            for k, v in data.items():
                                if isinstance(v, list) and len(v) > 0:
                                    docs = v
                                    break
                    elif isinstance(data, list):
                        docs = data
                        
                    if not docs:
                        print(f"No products found or end of data reached at step {index}.")
                        end_of_data = True
                        success = True
                        break
                        
                    for product in docs:
                        if not isinstance(product, dict):
                            continue
                        self._total_raw_processed += 1
                        url = product.get("url")
                        catalog_number = product.get("catalogNumber") or product.get("catalog_number") or product.get("sku") or ""
                        
                        if not url and catalog_number:
                            url = f"https://www.rockwellautomation.com/en-us/products/details.{catalog_number}.html"
                        
                        if not url:
                            continue
                            
                        full_url = urljoin('https://www.rockwellautomation.com/', url)
                        if full_url not in seen_urls:
                            seen_urls.add(full_url)
                            all_links.append(full_url)
                            
                    print(f"Retrieved {len(all_links)} unique product links (total raw processed: {self._total_raw_processed})...")
                    success = True
                    break
                    
                except Exception as e:
                    print(f"API query error at step {index}, attempt {attempt+1}: {str(e)}")
                    time.sleep(2)
            
            if end_of_data:
                break
                
            if not success:
                print(f"Failed step {index} after {self.max_retries} attempts. Continuing to next step.")
            
            time.sleep(0.5)
                
        excel_path = os.path.join(self.output_root, f"AllenBradley_Links_API_{timestamp}.xlsx")
        if all_links:
            df = pd.DataFrame({'Link Sản Phẩm': all_links})
            df.to_excel(excel_path, index=False)
            
        safe_socketio_emit('progress_update', {
            'percent': 100,
            'message': 'Hoàn thành!',
            'detail': f'Đã tìm thấy {len(all_links)} link sản phẩm qua API'
        })
        
        return excel_path if all_links else None, len(all_links)
