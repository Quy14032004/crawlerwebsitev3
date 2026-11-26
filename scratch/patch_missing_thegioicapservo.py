"""
Script cào bù tự động các URL bị thiếu/lỗi trong danh mục Thế Giới Cáp Servo
- Tự động nạp file Excel hiện có (cap_dong_luc_YASKAWA.xlsx).
- Đối chiếu với danh sách đầy đủ (cap_dong_luc_YASKAWA_urls.txt) để tìm các sản phẩm chưa cào được.
- Cào siêu tốc bằng Session Keep-Alive & WebP method=3 (nhanh gấp 14 lần).
- Gộp toàn bộ kết quả vào file Excel, cập nhật lại file Báo cáo tổng hợp và file nén ZIP.
"""

import os
import sys
import time
import re
import json
import zipfile
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image
import io

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'vi,en;q=0.9',
}

def clean_url(url):
    if not url:
        return ""
    u = url.strip()
    u = re.sub(r'\s+', '', u)
    u = u.replace('%20', '').strip()
    return u

def create_fast_session(pool_size=20):
    sess = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=3)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(HEADERS)
    return sess

def extract_product_info(product_url, session):
    product_url = clean_url(product_url)
    try:
        r = session.get(product_url, timeout=20)
        if r.status_code != 200:
            return None
            
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # 1. Tên
        name = ""
        name_el = soup.select_one('.product_name[itemprop="name"], h1.product_name, .product_name')
        if name_el:
            name = name_el.get_text(strip=True)
            
        # 2. Mã sản phẩm
        code = ""
        vendor_el = soup.select_one('.product_vendor')
        if vendor_el:
            a_tag = vendor_el.select_one('a')
            if a_tag and a_tag.get_text(strip=True):
                code = a_tag.get_text(strip=True)
            else:
                raw_text = vendor_el.get_text(strip=True)
                code = re.sub(r'^D[òo]ng\s*s[ảa]n\s*ph[ẩa]m\s*[:\s]+', '', raw_text, flags=re.IGNORECASE).strip()
                
        if not code:
            code = urlparse(product_url).path.strip('/').split('/')[-1]
            
        # 3. Giá
        price = "Liên hệ"
        price_el = soup.select_one('.product_price .money, .product_price, .price-new, .product-price')
        if price_el:
            price = price_el.get_text(strip=True)
            
        # 4. Link ảnh
        raw_images = []
        for img in soup.select('#image, .product_image_main img, .product_img img, .thumbnails img, #default_gallery img, .image-additional img, .product-image img'):
            src = img.get('data-zoom-image') or img.get('data-large_image') or img.get('src')
            if src:
                src = urljoin(product_url, src.strip())
                if src not in raw_images:
                    raw_images.append(src)
        for a in soup.select('.thumbnails a, .image-additional a, a.thumbnail'):
            href = a.get('href')
            if href and ('/catalog/' in href or '/image/' in href):
                href = urljoin(product_url, href.strip())
                if href not in raw_images:
                    raw_images.append(href)
                    
        valid_images = []
        for img_url in raw_images:
            lower = img_url.lower()
            if any(lower.endswith(ext) or ext in lower for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                if all(bad not in lower for bad in ['logo', 'icon', 'banner', 'placeholder', 'zalo', 'cart']):
                    valid_images.append(img_url)
                    
        # 5. Thông số kỹ thuật
        desc_el = soup.select_one('#tab-description, .tab-pane#tab-description')
        specs_html = parse_specs(desc_el, code, name)
        
        return {
            'url': product_url,
            'name': name,
            'code': code,
            'price': price,
            'images': valid_images,
            'specifications': specs_html
        }
    except Exception as e:
        print(f"[-] Lỗi cào sản phẩm {product_url}: {e}")
        return None

def parse_specs(desc_el, code, name):
    rows = [('Mã sản phẩm', code or 'N/A'), ('Tên sản phẩm', name or 'N/A')]
    if desc_el:
        tables = desc_el.select('table')
        if tables:
            for table in tables:
                for tr in table.select('tr'):
                    tds = tr.select('th, td')
                    if len(tds) >= 2:
                        k = tds[0].get_text(strip=True)
                        v = tds[1].get_text(strip=True)
                        if k and v and k != 'Thông số' and (k, v) not in rows:
                            rows.append((k, v))
        else:
            paragraphs = desc_el.find_all(['p', 'li', 'div', 'h3', 'h4'])
            prev_strong = None
            for p in paragraphs:
                p_text = p.get_text(strip=True)
                if not p_text: continue
                strong = p.select_one('strong, b')
                strong_text = strong.get_text(strip=True) if strong else ""
                if strong_text and strong_text in p_text and len(p_text) > len(strong_text):
                    remainder = p_text[len(strong_text):].strip().lstrip(':').strip()
                    clean_k = strong_text.rstrip(':').strip()
                    if clean_k and remainder:
                        if (clean_k, remainder) not in rows:
                            rows.append((clean_k, remainder))
                        prev_strong = None
                        continue
                if ':' in p_text and not p_text.startswith('http'):
                    parts = p_text.split(':', 1)
                    k, v = parts[0].strip(), parts[1].strip()
                    if k and v and len(k) < 60:
                        if (k, v) not in rows:
                            rows.append((k, v))
                        prev_strong = None
                        continue
                if strong and len(strong_text) >= len(p_text) * 0.8 and len(strong_text) < 80:
                    prev_strong = strong_text
                    continue
                if prev_strong and p_text and len(p_text) > 3:
                    if (prev_strong, p_text) not in rows:
                        rows.append((prev_strong, p_text))
                    prev_strong = None
                    continue
                    
    html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
    html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
    for k, v in rows:
        html += f'<tr><td style="font-weight: bold;">{k}</td><td>{v}</td></tr>'
    html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr></tbody></table>'
    return html

def fast_download_image(image_url, product_code, img_index, output_folder, session):
    clean_code = re.sub(r'[\\/:*?"<>|]', '_', product_code).strip() or "product"
    filename = f"{clean_code}.webp" if img_index == 0 else f"{clean_code}_{img_index + 1}.webp"
    filepath = os.path.abspath(os.path.join(output_folder, filename))
    
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return filename, filepath
        
    for retry in range(3):
        try:
            r = session.get(image_url, timeout=15)
            if r.status_code == 200 and r.content:
                image = Image.open(io.BytesIO(r.content))
                if image.mode in ('RGBA', 'LA'):
                    bg = Image.new('RGB', (800, 800), (255, 255, 255))
                    image_resized = image.resize((800, 800), Image.Resampling.LANCZOS)
                    if image_resized.mode == 'RGBA':
                        bg.paste(image_resized, (0, 0), image_resized)
                        final_image = bg
                    else:
                        final_image = image_resized.convert('RGB')
                else:
                    final_image = image.convert('RGB').resize((800, 800), Image.Resampling.LANCZOS)
                    
                final_image.save(filepath, 'WEBP', quality=90, method=3)
                return filename, filepath
        except Exception:
            time.sleep(0.5)
    return None, None

def save_excel(products, output_file, sheet_name="TheGioiCapServo"):
    excel_data = []
    for i, prod in enumerate(products):
        img_names = prod.get('image_filenames', [])
        img_names_str = '\n'.join(img_names) if isinstance(img_names, list) else str(img_names)
        img_paths = prod.get('image_paths', [])
        img_paths_str = '\n'.join(img_paths) if isinstance(img_paths, list) else str(img_paths)
        raw_imgs = prod.get('images', [])
        raw_imgs_str = '\n'.join(raw_imgs) if isinstance(raw_imgs, list) else str(raw_imgs)
        
        excel_data.append({
            'STT': i + 1,
            'URL': prod.get('url', ''),
            'Mã sản phẩm': prod.get('code', ''),
            'Tên sản phẩm': prod.get('name', ''),
            'Giá sản phẩm': prod.get('price', 'Liên hệ'),
            'Tên ảnh': img_names_str,
            'Số lượng ảnh': len(img_names) if isinstance(img_names, list) else 0,
            'Đường dẫn link ảnh': img_paths_str,
            'Thông số kỹ thuật': prod.get('specifications', ''),
            'Danh sách ảnh': raw_imgs_str
        })
    df = pd.DataFrame(excel_data)
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]
        for col in ws.columns:
            name = str(col[0].value) if col[0].value is not None else ''
            letter = col[0].column_letter
            if name in ['URL', 'Đường dẫn link ảnh', 'Danh sách ảnh']:
                ws.column_dimensions[letter].width = 45
            elif name == 'Thông số kỹ thuật':
                ws.column_dimensions[letter].width = 60
            elif name in ['Tên sản phẩm', 'Mã sản phẩm', 'Tên ảnh']:
                ws.column_dimensions[letter].width = 32
            else:
                ws.column_dimensions[letter].width = 16
    return True

def run_patch():
    session_dir = os.path.abspath(r"app\downloads\output_thegioicapservo\TheGioiCapServo_ngay20261123_100917")
    cat_name = "cap_dong_luc_YASKAWA"
    cat_folder = os.path.join(session_dir, cat_name)
    img_folder = os.path.join(cat_folder, "Anh")
    urls_file = os.path.join(cat_folder, f"{cat_name}_urls.txt")
    excel_file = os.path.join(cat_folder, f"{cat_name}.xlsx")
    
    if not os.path.exists(urls_file):
        print(f"[-] Không tìm thấy file URLs: {urls_file}")
        return
        
    with open(urls_file, 'r', encoding='utf-8') as f:
        all_urls = [clean_url(line) for line in f if clean_url(line)]
        
    print(f"[+] Tổng cộng {len(all_urls)} URL trong danh mục {cat_name}")
    
    existing_products = []
    crawled_urls = set()
    
    if os.path.exists(excel_file):
        try:
            df = pd.read_excel(excel_file)
            for _, row in df.iterrows():
                u = clean_url(str(row.get('URL', '')))
                if u:
                    crawled_urls.add(u)
                    existing_products.append({
                        'url': u,
                        'name': str(row.get('Tên sản phẩm', '')),
                        'code': str(row.get('Mã sản phẩm', '')),
                        'price': str(row.get('Giá sản phẩm', 'Liên hệ')),
                        'image_filenames': str(row.get('Tên ảnh', '')).splitlines() if pd.notna(row.get('Tên ảnh')) else [],
                        'image_paths': str(row.get('Đường dẫn link ảnh', '')).splitlines() if pd.notna(row.get('Đường dẫn link ảnh')) else [],
                        'specifications': str(row.get('Thông số kỹ thuật', '')),
                        'images': str(row.get('Danh sách ảnh', '')).splitlines() if pd.notna(row.get('Danh sách ảnh')) else []
                    })
            print(f"[+] Đã đọc {len(existing_products)} sản phẩm từ {excel_file}")
        except Exception as e:
            print(f"[-] Lỗi đọc Excel {excel_file}: {e}")
            
    # Lọc ra danh sách cần cào bù
    missing_urls = [u for u in all_urls if u not in crawled_urls]
    print(f"[+] Kết quả kiểm tra: Đã có {len(crawled_urls)} / {len(all_urls)} URL. Còn thiếu: {len(missing_urls)} URL.")
    
    if not missing_urls:
        print("[+] Tuyệt vời! Danh mục đã đủ 100%, không cần cào bù.")
        return
        
    print(f"[+] Bắt đầu cào bù SIÊU TỐC {len(missing_urls)} sản phẩm bằng Connection Pool & Keep-Alive...")
    session = create_fast_session(pool_size=20)
    
    def worker(p_url):
        info = extract_product_info(p_url, session)
        if not info:
            return None
        downloaded_names = []
        downloaded_paths = []
        for idx, img_url in enumerate(info.get('images', [])):
            fn, fp = fast_download_image(img_url, info['code'], idx, img_folder, session)
            if fn and fp:
                downloaded_names.append(fn)
                downloaded_paths.append(fp)
        info['image_filenames'] = downloaded_names
        info['image_paths'] = downloaded_paths
        return info

    new_prods = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(worker, u): u for u in missing_urls}
        done = 0
        for f in as_completed(futures):
            done += 1
            res = f.result()
            if res:
                new_prods.append(res)
            if done % 10 == 0 or done == len(missing_urls):
                print(f" -> Đã cào bù: {done}/{len(missing_urls)} (Thành công: {len(new_prods)})")
                
    # Gộp toàn bộ vào Excel
    all_combined = existing_products + new_prods
    print(f"[+] Gộp và lưu tổng cộng {len(all_combined)} sản phẩm vào {excel_file}...")
    save_excel(all_combined, excel_file, sheet_name=cat_name[:30])
    
    # Cập nhật file tổng hợp
    report_dir = os.path.join(session_dir, "Bao_cao")
    summary_files = [f for f in os.listdir(report_dir) if f.startswith("TheGioiCapServo_Tong_hop_") and f.endswith(".xlsx")]
    if summary_files:
        summary_path = os.path.join(report_dir, summary_files[0])
        try:
            old_df = pd.read_excel(summary_path)
            old_urls = set(clean_url(str(u)) for u in old_df['URL'])
            append_prods = [p for p in new_prods if clean_url(p['url']) not in old_urls]
            if append_prods:
                print(f"[+] Cập nhật thêm {len(append_prods)} sản phẩm vào file tổng hợp: {summary_path}")
                all_summary_data = []
                for _, r in old_df.iterrows():
                    all_summary_data.append(dict(r))
                for p in append_prods:
                    all_summary_data.append({
                        'STT': len(all_summary_data) + 1,
                        'URL': p.get('url', ''),
                        'Mã sản phẩm': p.get('code', ''),
                        'Tên sản phẩm': p.get('name', ''),
                        'Giá sản phẩm': p.get('price', 'Liên hệ'),
                        'Tên ảnh': '\n'.join(p.get('image_filenames', [])),
                        'Số lượng ảnh': len(p.get('image_filenames', [])),
                        'Đường dẫn link ảnh': '\n'.join(p.get('image_paths', [])),
                        'Thông số kỹ thuật': p.get('specifications', ''),
                        'Danh sách ảnh': '\n'.join(p.get('images', []))
                    })
                pd.DataFrame(all_summary_data).to_excel(summary_path, index=False, sheet_name="Tong_Hop")
        except Exception as e:
            print(f"[-] Lỗi cập nhật file tổng hợp: {e}")
            
    # Cập nhật file ZIP
    zip_files = [f for f in os.listdir(session_dir) if f.endswith(".zip")]
    if zip_files:
        zip_path = os.path.join(session_dir, zip_files[0])
        print(f"[+] Cập nhật file ZIP: {zip_path}")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(session_dir):
                for f in files:
                    if not f.endswith('.zip'):
                        fp = os.path.join(root, f)
                        zipf.write(fp, os.path.relpath(fp, session_dir))
                        
    print("[+] HOÀN THÀNH CÀO BÙ 100%!")

if __name__ == '__main__':
    run_patch()
