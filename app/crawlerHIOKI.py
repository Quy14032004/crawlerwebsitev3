"""
Crawler HIOKI - Cào dữ liệu sản phẩm từ https://www.hioki.com/vn-vi/products
- Cào: Tên sản phẩm, mã sản phẩm (nhiều mã), thông số kỹ thuật, ảnh sản phẩm
- Đa luồng: tăng tốc độ cào
- Ảnh: tải và chuyển sang WebP, đặt tên theo mã sản phẩm
- Thông số: build bảng HTML `#specifications` kèm dòng Copyright
"""

import io
import json
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image

# Google Translate (tùy chọn - fallback)
GOOGLE_TRANSLATE_AVAILABLE = False
try:
    from googletrans import Translator  # type: ignore
    GOOGLE_TRANSLATE_AVAILABLE = True
except Exception:
    pass

# Gemini AI Translator (ưu tiên)
try:
    from app.utils import init_gemini_translator, translate_with_gemini, translate_specifications_with_gemini
    GEMINI_TRANSLATE_AVAILABLE = True
except Exception:
    GEMINI_TRANSLATE_AVAILABLE = False

# Thử import HEADERS từ module crawler chung, nếu không có thì dùng fallback
try:
    from app.crawler import HEADERS  # type: ignore
except Exception:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0 Safari/537.36"
        ),
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    }

# Thử import WebPConverter cả khi chạy trong package app hoặc chạy trực tiếp từ thư mục app
try:
    from app.webp_converter import WebPConverter  # type: ignore
except Exception:
    from webp_converter import WebPConverter  # type: ignore

import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class HIOKICrawler:
    """
    Crawler chuyên dụng cho Hioki.

    Luồng chính:
    - Người dùng nhập URL danh mục cha (vd: https://www.hioki.com/vn-vi/products/data-acquisition)
    - Lấy danh mục con
    - Lấy URL sản phẩm trong danh mục con
    - Đa luồng cào chi tiết từng sản phẩm
    - Mỗi sản phẩm có thể có nhiều mã -> tách thành nhiều bản ghi
    - Tải ảnh, convert WebP, đặt tên theo từng mã
    - Build bảng thông số HTML chuẩn
    - Lưu Excel + ZIP theo cấu trúc gần giống BAA Qlight (theo danh mục con)
    """

    def __init__(self,
                 base_url: str = "https://www.hioki.com/vn-vi/products",
                 max_workers: int = 10,
                 translate_to_vi: bool = True,
                 gemini_api_key: str = None):
        # Luôn dùng base URL tiếng Việt
        self.base_url = base_url.split("?")[0].rstrip("/")
        self.max_workers = max_workers
        self.translate_to_vi = translate_to_vi

        self.session = requests.Session()
        # Ép Accept-Language tiếng Việt để lấy thông số gốc tiếng Việt
        self.session.headers.update(HEADERS)
        self.session.headers.update({
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.4",
        })

        # Khởi tạo Gemini translator (ưu tiên)
        self.use_gemini = False
        if GEMINI_TRANSLATE_AVAILABLE:
            if init_gemini_translator(gemini_api_key):
                self.use_gemini = True
                logger.info("Sử dụng Gemini AI để dịch thông số kỹ thuật")
        
        # Fallback: Khởi tạo Google Translate translator nếu Gemini không khả dụng
        if not self.use_gemini:
            self.translator = Translator() if GOOGLE_TRANSLATE_AVAILABLE else None
            if self.translator:
                logger.info("Sử dụng Google Translate để dịch thông số kỹ thuật (fallback)")

        parsed = urlparse(self.base_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        # Lưu locale mục tiêu (vd: vn-vi)
        path_parts = parsed.path.split("/")
        self.target_locale = path_parts[1] if len(path_parts) >= 2 else "vn-vi"

    def normalize_locale(self, url: str, target_locale: str = None) -> str:
        """Chuẩn hóa locale của URL về target_locale (mặc định vn-vi)."""
        target_locale = target_locale or self.target_locale
        if not url:
            return url
        parsed = urlparse(url)
        path_parts = parsed.path.split("/")
        if len(path_parts) >= 2 and path_parts[1] in ["vn-vi", "sg-en", "en-us", "ja-jp"]:
            path_parts[1] = target_locale
            new_path = "/".join(path_parts)
            new_url = f"{parsed.scheme}://{parsed.netloc}{new_path}"
            if parsed.query:
                new_url += f"?{parsed.query}"
            return new_url.rstrip("/")
        return url.rstrip("/")

    # ============================================================
    # 1. Lấy danh mục con từ URL danh mục cha
    # ============================================================

    def get_subcategories(self, parent_category_url: str):
        """
        Lấy danh sách danh mục con từ URL danh mục cha Hioki.
        Trả về: list[{ 'name', 'url' }]

        Ví dụ đầu vào:
        https://www.hioki.com/vn-vi/products/data-acquisition
        -> Tìm các link như:
        https://www.hioki.com/vn-vi/products/data-acquisition/daq-testing
        https://www.hioki.com/vn-vi/products/data-acquisition/daq-maintenance
        """
        try:
            logger.info(f"🔍 Lấy danh mục con từ: {parent_category_url}")
            html = self._get_html(parent_category_url)
            if not html:
                logger.warning("⚠️ Không tải được HTML")
                return []

            # Debug: Kiểm tra xem HTML có chứa nav-index-02 không
            if 'nav-index-02' in html:
                logger.info("✓ Tìm thấy 'nav-index-02' trong HTML")
            else:
                logger.warning("⚠️ Không tìm thấy 'nav-index-02' trong HTML, sẽ thử các cách khác")

            soup = BeautifulSoup(html, "html.parser")
            subcategories = []
            seen_urls = set()

            # Pattern: danh mục con có URL dạng parent_url + "/" + tên_danh_mục
            parent_url_normalized = self.normalize_locale(parent_category_url.rstrip("/"))
            parent_path = urlparse(parent_url_normalized).path.rstrip("/")
            
            # Lấy base path (ví dụ: /vn-vi/products/data-acquisition)
            # Bỏ qua locale để có thể match với các locale khác (sg-en, vn-vi, ...)
            path_parts = parent_path.split("/")
            if len(path_parts) >= 3 and path_parts[1] in ["vn-vi", "sg-en", "en-us", "ja-jp"]:
                # Bỏ locale, chỉ lấy phần sau: /products/data-acquisition
                base_path_after_locale = "/" + "/".join(path_parts[2:])
            else:
                base_path_after_locale = parent_path

            # ===== CÁCH 1: Tìm trong <ul class="nav-index-02"> =====
            nav_selectors = [
                'ul.nav-index-02',
                'ul[class*="nav-index"]',
                'ul.nav-index-02.col3',
                'ul.nav-index-02.sp-col2',
                '.nav-index-02',
            ]
            
            found_nav = False
            for selector in nav_selectors:
                nav_index = soup.select_one(selector)
                if nav_index:
                    logger.info(f"✓ Tìm thấy nav bằng selector: {selector}")
                    found_nav = True
                    li_count = len(nav_index.select('li'))
                    logger.info(f"   Tìm thấy {li_count} thẻ <li> trong nav")
                    
                    for idx, li in enumerate(nav_index.select('li'), 1):
                        a = li.select_one('a[href]')
                        if not a:
                            logger.debug(f"   Li {idx}: Không có thẻ <a>")
                            continue

                        href = (a.get("href") or "").strip()
                        if not href:
                            logger.debug(f"   Li {idx}: href rỗng")
                            continue

                        full_url = urljoin(self.origin, href)
                        full_url_clean = full_url.split("?")[0].split("#")[0].rstrip("/")
                        href_path = urlparse(full_url_clean).path.rstrip("/")
                        
                        logger.info(f"   Li {idx}: href={href}, full_url={full_url_clean}, href_path={href_path}")

                        # Kiểm tra path: bỏ qua locale, chỉ kiểm tra phần sau /products/
                        # Ví dụ: /vn-vi/products/data-acquisition/daq-testing -> /products/data-acquisition/daq-testing
                        href_path_parts = href_path.split("/")
                        if len(href_path_parts) >= 3 and href_path_parts[1] in ["vn-vi", "sg-en", "en-us", "ja-jp"]:
                            href_path_after_locale = "/" + "/".join(href_path_parts[2:])
                        else:
                            href_path_after_locale = href_path
                        
                        # Kiểm tra: path phải bắt đầu bằng base_path_after_locale + "/" và có thêm segment
                        if not href_path_after_locale.startswith(base_path_after_locale + "/"):
                            logger.info(f"   Li {idx}: Path không match ({href_path_after_locale} vs {base_path_after_locale}/) - SKIP")
                            continue

                        # Loại bỏ nếu là chính parent path
                        if href_path_after_locale == base_path_after_locale:
                            logger.debug(f"   Li {idx}: Path trùng với parent")
                            continue

                        # Loại bỏ nếu có "id_" (đây là link sản phẩm, không phải danh mục con)
                        if "/id_" in href_path_after_locale:
                            logger.debug(f"   Li {idx}: Path có /id_ (sản phẩm)")
                            continue
                        
                        # Tạo lại URL với locale mục tiêu (vn-vi)
                        parent_locale = path_parts[1] if len(path_parts) >= 2 and path_parts[1] in ["vn-vi", "sg-en", "en-us", "ja-jp"] else self.target_locale
                        final_url = f"{self.origin}/{parent_locale}{href_path_after_locale}"
                        final_url_clean = self.normalize_locale(final_url, parent_locale)

                        # Lấy tên danh mục từ <div class="data"><p>...</p></div> hoặc text của link
                        name = ""
                        data_div = li.select_one('div.data')
                        if data_div:
                            p_tag = data_div.select_one('p')
                            if p_tag:
                                name = p_tag.get_text(strip=True)
                        
                        # Fallback: lấy từ text của link
                        if not name or len(name) < 2:
                            name = a.get_text(strip=True)
                        
                        # Fallback cuối: lấy từ URL path
                        if not name or len(name) < 2:
                            path_part = href_path_after_locale.replace(base_path_after_locale + "/", "")
                            name = path_part.replace("-", " ").replace("_", " ").title()

                        if not name or name.lower() in ["xem thêm", "more", "chi tiết", "detail"]:
                            logger.debug(f"   Li {idx}: Tên không hợp lệ: {name}")
                            continue

                        if final_url_clean in seen_urls:
                            logger.debug(f"   Li {idx}: URL đã tồn tại")
                            continue
                        seen_urls.add(final_url_clean)

                        logger.info(f"   ✓ Thêm danh mục con: {name} -> {final_url_clean}")
                        subcategories.append({
                            "name": name,
                            "url": final_url_clean,
                        })
                    break  # Đã tìm thấy, không cần thử selector khác

            # ===== CÁCH 2: Tìm tất cả link có pattern URL đúng (nếu chưa tìm thấy) =====
            if not subcategories:
                logger.info("⚠️ Chưa tìm thấy bằng nav-index-02, thử tìm tất cả link có pattern đúng...")
                all_links = soup.select("a[href]")
                logger.info(f"✓ Tìm thấy {len(all_links)} link trong trang")
                
                for a in all_links:
                    href = (a.get("href") or "").strip()
                    if not href:
                        continue

                    full_url = urljoin(self.origin, href)
                    full_url_clean = full_url.split("?")[0].split("#")[0].rstrip("/")

                    # Kiểm tra: URL phải bắt đầu bằng parent_url + "/" và có thêm path segment
                    if not full_url_clean.startswith(parent_url_normalized + "/"):
                        continue

                    # Loại bỏ nếu là chính parent URL
                    if full_url_clean == parent_url_normalized:
                        continue

                    # Loại bỏ nếu có "id_" (đây là link sản phẩm)
                    if "/id_" in full_url_clean:
                        continue

                    # Kiểm tra: URL phải có thêm ít nhất 1 segment sau parent
                    remaining_path = full_url_clean.replace(parent_url_normalized + "/", "")
                    if not remaining_path or "/" in remaining_path:
                        # Có thể là sản phẩm hoặc danh mục con sâu hơn, bỏ qua
                        continue

                    # Lấy tên từ text của link
                    name = a.get_text(strip=True)
                    if not name or len(name) < 2:
                        # Fallback: lấy từ URL path
                        name = remaining_path.replace("-", " ").replace("_", " ").title()

                    if not name or name.lower() in ["xem thêm", "more", "chi tiết", "detail", "view all"]:
                        continue

                    if full_url_clean in seen_urls:
                        continue
                    seen_urls.add(full_url_clean)

                    subcategories.append({
                        "name": name,
                        "url": full_url_clean,
                    })

            # ===== CÁCH 3: Tìm bằng regex trong raw HTML (fallback cuối) =====
            if not subcategories:
                logger.info("⚠️ Chưa tìm thấy bằng cách trên, thử tìm bằng regex trong HTML...")
                import re
                # Pattern: href=".../data-acquisition/daq-testing" hoặc tương tự
                pattern = rf'href=["\']({re.escape(parent_url_normalized)}/[^"\'\s?]+)["\']'
                matches = re.findall(pattern, html, re.IGNORECASE)
                
                for match_url in matches:
                    full_url_clean = match_url.split("?")[0].split("#")[0].rstrip("/")
                    
                    if full_url_clean == parent_url_normalized:
                        continue
                    if "/id_" in full_url_clean:
                        continue
                    
                    remaining_path = full_url_clean.replace(parent_url_normalized + "/", "")
                    if not remaining_path or "/" in remaining_path:
                        continue
                    
                    if full_url_clean in seen_urls:
                        continue
                    seen_urls.add(full_url_clean)
                    
                    name = remaining_path.replace("-", " ").replace("_", " ").title()
                    
                    subcategories.append({
                        "name": name,
                        "url": full_url_clean,
                    })

            logger.info(f"✅ Tìm thấy {len(subcategories)} danh mục con")
            if subcategories:
                logger.info(f"   Danh sách: {[s['name'] for s in subcategories[:5]]}")
            return subcategories
        except Exception as e:
            logger.error(f"❌ Lỗi get_subcategories: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    # ============================================================
    # 2. Lấy URL sản phẩm từ danh mục con
    # ============================================================

    def get_products_from_subcategory(self, subcategory_url: str, max_pages: int = 5):
        """
        Lấy danh sách URL sản phẩm từ một danh mục con.
        - Hỗ trợ phân trang dạng ?page=...
        Trả về: list[product_url]
        """
        try:
            logger.info(f"📋 Lấy sản phẩm từ danh mục con: {subcategory_url}")
            product_urls = set()

            # Lấy locale mục tiêu từ URL danh mục con
            subcat_path = urlparse(subcategory_url).path
            subcat_parts = subcat_path.split("/")
            target_locale = subcat_parts[1] if len(subcat_parts) >= 2 and subcat_parts[1] in ["vn-vi", "sg-en", "en-us", "ja-jp"] else self.target_locale

            def collect_from_page(url):
                html = self._get_html(self.normalize_locale(url, target_locale))
                if not html:
                    return
                soup = BeautifulSoup(html, "html.parser")

                # Một URL sản phẩm Hioki thường dạng: /vn-vi/products/.../id_xxxx
                for a in soup.select("a[href]"):
                    href = (a.get("href") or "").strip()
                    if not href:
                        continue
                    full_url = urljoin(self.origin, href)
                    if "/products/" not in full_url or "id_" not in full_url:
                        continue
                    
                    # Normalize locale về target_locale (thường là vn-vi)
                    normalized_url = self.normalize_locale(full_url, target_locale)
                    product_urls.add(normalized_url.rstrip("/"))

            # Trang đầu
            collect_from_page(subcategory_url)

            # Tìm thêm các link phân trang ?page=
            html = self._get_html(self.normalize_locale(subcategory_url, target_locale))
            if html:
                soup = BeautifulSoup(html, "html.parser")
                page_links = set()
                for a in soup.select("a[href*='page=']"):
                    href = (a.get("href") or "").strip()
                    if not href:
                        continue
                    full_url = urljoin(subcategory_url, href)
                    page_links.add(full_url)

                for p_url in list(page_links)[:max_pages]:
                    collect_from_page(p_url)

            logger.info(f"✅ Tìm thấy {len(product_urls)} sản phẩm trong danh mục con")
            return list(product_urls)
        except Exception as e:
            logger.error(f"❌ Lỗi get_products_from_subcategory: {e}")
            return []

    # ============================================================
    # 3. Cào chi tiết 1 sản phẩm (một URL có thể nhiều mã)
    # ============================================================

    def crawl_product_info(self, url: str, index: int = 1):
        """
        Cào chi tiết 1 trang sản phẩm Hioki.
        Trả về record gốc:
        {
            'URL': url,
            'Product_Name': str,
            'Models': [ { 'code': str, 'description': str }, ... ],
            'Spec_Rows': [ { 'label': str, 'value': str }, ... ],
            'Image_URLs': [str, ...]
        }
        """
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return self._extract_product_info_hioki(url, index)
            except Exception as e:
                logger.warning(f"⚠️ Thử {attempt}/{max_retries} thất bại cho {url}: {e}")
                time.sleep(1)
        logger.error(f"❌ Không thể cào sản phẩm: {url}")
        return None

    def _extract_product_info_hioki(self, url: str, index: int = 1):
        url = self.normalize_locale(url)
        html = self._get_html(url)
        if not html:
            raise RuntimeError("Không tải được HTML")

        soup = BeautifulSoup(html, "html.parser")

        record = {
            "URL": url,
            "STT_raw": index,
        }

        # 3.1 Tên sản phẩm: <h1 class="hdg-01">
        name_el = soup.select_one("h1.hdg-01")
        product_name = name_el.get_text(strip=True) if name_el else ""
        if self.translate_to_vi and not self._is_vietnamese_text(product_name):
            product_name = self._translate_text(product_name)
        record["Product_Name"] = product_name

        # 3.2 Mã sản phẩm + mô tả: bảng sau h2.hdg2-01 "Số model (Mã đặt hàng)"
        models = self._parse_models_from_table(soup)
        record["Models"] = models

        # 3.3 Ảnh sản phẩm: <ul class="js-view-list view-list"> ...
        image_urls = self._extract_image_urls(soup)
        record["Image_URLs"] = image_urls

        # 3.4 Thông số kỹ thuật: bảng <table class="table-01">
        spec_rows = self._extract_spec_rows(soup)
        if self.translate_to_vi:
            # Ưu tiên dịch bằng Gemini nếu có
            if self.use_gemini:
                spec_rows = self._translate_spec_rows_with_gemini(spec_rows)
            else:
                spec_rows = self._translate_spec_rows(spec_rows)
        record["Spec_Rows"] = spec_rows

        logger.info(
            f"✓ Hioki product: {product_name[:60]}..., "
            f"models={len(models)}, images={len(image_urls)}, specs={len(spec_rows)}"
        )
        return record

    def _parse_models_from_table(self, soup: BeautifulSoup):
        """
        Từ cấu trúc:
        <h2 class="hdg2-01">Số model (Mã đặt hàng)</h2>
        <table class="unique-table-01">...</table>
        -> list[{code, description}]
        """
        models = []

        headers = soup.select("h2.hdg2-01")
        target_table = None
        for h2 in headers:
            title = h2.get_text(strip=True)
            if "Số model" in title or "Model No." in title:
                nxt = h2.find_next("table")
                if nxt and "unique-table-01" in (nxt.get("class") or []):
                    target_table = nxt
                    break

        if not target_table:
            return models

        tbody = target_table.find("tbody") or target_table
        for tr in tbody.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if not th or not td:
                continue
            code = th.get_text(strip=True)
            desc = td.get_text(" ", strip=True)
            if code:
                models.append({"code": code, "description": desc})

        return models

    def _extract_image_urls(self, soup: BeautifulSoup):
        """
        Lấy danh sách ảnh từ:
        <ul class="js-view-list view-list">
          <li class="js-view-item current"><img src="..."></li>
          ...
        </ul>
        """
        urls = []
        for img in soup.select("ul.js-view-list.view-list li.js-view-item img"):
            src = (img.get("src") or "").strip()
            if not src:
                continue
            full_url = urljoin(self.origin, src)
            if full_url not in urls:
                urls.append(full_url)
        return urls

    def _extract_spec_rows(self, soup: BeautifulSoup):
        """
        Lấy các dòng thông số từ:
        <table class="table-01"> ... </table>

        Trả về: list[{ 'label': str, 'value': str_html }]
        """
        spec_table = soup.select_one("table.table-01")
        if not spec_table:
            return []

        rows = []
        tbody = spec_table.find("tbody") or spec_table
        for tr in tbody.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if not th or not td:
                continue

            label = th.get_text(" ", strip=True)
            # giữ nguyên HTML trong <td> (br, text...)
            value_html = "".join(str(c) for c in td.contents).strip()
            if not value_html:
                value_html = td.get_text(" ", strip=True)

            if label:
                rows.append({"label": label, "value": value_html})

        return rows

    def _get_html(self, url: str, timeout: int = 30):
        """
        Lấy HTML từ URL, đảm bảo trang hiển thị bằng tiếng Việt.
        Nếu trang chưa ở locale Việt Nam, sẽ tìm và request tới link Việt Nam.
        """
        try:
            # Đảm bảo URL đã normalize về vn-vi
            url = self.normalize_locale(url, "vn-vi")
            
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            html = resp.text
            
            # Kiểm tra xem trang có đang ở tiếng Việt chưa
            # Nếu chưa, tìm link chuyển sang Việt Nam trong phần tử lang
            if not self._check_vietnamese_page(html):
                logger.info(f"🔄 Trang chưa ở tiếng Việt, đang tìm link chuyển đổi...")
                vietnam_url = self._find_vietnam_lang_link(html, url)
                if vietnam_url:
                    logger.info(f"✓ Tìm thấy link Việt Nam: {vietnam_url}")
                    resp = self.session.get(vietnam_url, timeout=timeout)
                    resp.raise_for_status()
                    html = resp.text
                else:
                    logger.warning(f"⚠️ Không tìm thấy link chuyển sang Việt Nam, tiếp tục với URL hiện tại")
            
            return html
        except Exception as e:
            logger.error(f"❌ Lỗi GET {url}: {e}")
            return None
    
    def _check_vietnamese_page(self, html: str) -> bool:
        """
        Kiểm tra xem trang có đang hiển thị bằng tiếng Việt không.
        Kiểm tra bằng cách tìm các từ khóa tiếng Việt trong HTML.
        """
        vietnamese_keywords = [
            "Thông số",
            "Số model",
            "Mã đặt hàng",
            "Tên sản phẩm",
            "Mã sản phẩm",
        ]
        
        html_lower = html.lower()
        for keyword in vietnamese_keywords:
            if keyword.lower() in html_lower:
                return True
        
        # Kiểm tra locale trong URL hoặc HTML
        if 'vn-vi' in html_lower or 'vietnam' in html_lower:
            return True
        
        return False
    
    def _find_vietnam_lang_link(self, html: str, current_url: str) -> str:
        """
        Tìm link chuyển sang Việt Nam từ phần tử <li class="lang">.
        Trả về URL đầy đủ của link Việt Nam nếu tìm thấy.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Tìm tất cả các phần tử <li class="lang">
            lang_items = soup.select('li.lang, li[class*="lang"]')
            
            for li in lang_items:
                # Tìm link bên trong
                link = li.select_one('a[href]')
                if not link:
                    continue
                
                # Lấy text và href
                link_text = link.get_text(strip=True).lower()
                href = link.get('href', '').strip()
                
                # Kiểm tra xem có phải link Việt Nam không
                if 'việt nam' in link_text or 'vietnam' in link_text or 'vn' in link_text:
                    # Tạo URL đầy đủ
                    if href.startswith('http'):
                        return href
                    elif href.startswith('/'):
                        return urljoin(self.origin, href)
                    else:
                        return urljoin(current_url, href)
                
                # Kiểm tra href có chứa vn-vi không
                if 'vn-vi' in href.lower():
                    if href.startswith('http'):
                        return href
                    elif href.startswith('/'):
                        return urljoin(self.origin, href)
                    else:
                        return urljoin(current_url, href)
            
            # Fallback: tìm link có chứa "vn-vi" trong toàn bộ HTML
            for link in soup.select('a[href*="vn-vi"], a[href*="vietnam"]'):
                href = link.get('href', '').strip()
                if href:
                    if href.startswith('http'):
                        return href
                    elif href.startswith('/'):
                        return urljoin(self.origin, href)
                    else:
                        return urljoin(current_url, href)
            
            return None
        except Exception as e:
            logger.error(f"❌ Lỗi khi tìm link Việt Nam: {e}")
            return None

    # ============================================================
    # 3.x Tiện ích dịch và nhận diện tiếng Việt
    # ============================================================

    def _is_vietnamese_text(self, text: str) -> bool:
        """Kiểm tra text có chứa ký tự/âm tiếng Việt."""
        if not text:
            return False
        vietnamese_pattern = r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
        return re.search(vietnamese_pattern, text, re.IGNORECASE) is not None

    def _translate_text(self, text: str) -> str:
        """Dịch text sang tiếng Việt (ưu tiên Gemini, fallback Google Translate)."""
        if not text:
            return text
        
        # Ưu tiên sử dụng Gemini
        if self.use_gemini:
            try:
                return translate_with_gemini(text, target_lang='vi')
            except Exception as e:
                logger.warning(f"⚠️ Gemini dịch lỗi '{text[:50]}...': {e}, thử Google Translate...")
        
        # Fallback: Google Translate
        if self.translator:
            try:
                return self.translator.translate(text, dest="vi").text
            except Exception as e:
                logger.warning(f"⚠️ Không thể dịch '{text[:50]}...': {e}")
        
        return text

    def _translate_spec_rows(self, spec_rows):
        """Dịch nhãn và giá trị thông số sang tiếng Việt nếu chưa phải tiếng Việt (Google Translate)."""
        translated = []
        for row in spec_rows:
            label = row.get("label", "")
            value_html = row.get("value", "")

            new_label = label
            if label and not self._is_vietnamese_text(label):
                new_label = self._translate_text(label)

            # Chuyển value_html sang text để dịch, giữ lại nếu không dịch được
            value_text = ""
            try:
                value_text = BeautifulSoup(value_html, "html.parser").get_text(" ", strip=True)
            except Exception:
                value_text = value_html

            new_value = value_html
            if value_text and not self._is_vietnamese_text(value_text):
                translated_text = self._translate_text(value_text)
                new_value = translated_text

            translated.append({"label": new_label, "value": new_value})
        return translated
    
    def _translate_spec_rows_with_gemini(self, spec_rows):
        """Dịch nhãn và giá trị thông số sang tiếng Việt sử dụng Gemini AI."""
        translated = []
        for row in spec_rows:
            label = row.get("label", "")
            value_html = row.get("value", "")

            new_label = label
            if label and not self._is_vietnamese_text(label):
                new_label = translate_with_gemini(label, target_lang='vi')

            # Chuyển value_html sang text để dịch
            value_text = ""
            try:
                value_text = BeautifulSoup(value_html, "html.parser").get_text(" ", strip=True)
            except Exception:
                value_text = value_html

            new_value = value_html
            if value_text and not self._is_vietnamese_text(value_text):
                translated_text = translate_with_gemini(value_text, target_lang='vi')
                new_value = translated_text

            translated.append({"label": new_label, "value": new_value})
        return translated

    # ============================================================
    # 4. Đa luồng cào nhiều sản phẩm
    # ============================================================

    def crawl_products_multithread(self, product_urls):
        """
        Đa luồng cào chi tiết các URL sản phẩm Hioki.
        Trả về: list[record_gốc] (chưa tách theo mã)
        """
        import threading

        results = []
        total = len(product_urls)
        completed = 0
        lock = threading.Lock()

        def _worker(args):
            nonlocal completed
            url, idx = args
            try:
                rec = self.crawl_product_info(url, idx)
                with lock:
                    completed += 1
                    logger.info(f"✓ [{completed}/{total}] {url}")
                return rec
            except Exception as e:
                with lock:
                    completed += 1
                    logger.error(f"❌ Lỗi crawl {url}: {e}")
                return None

        logger.info(f"🔄 Bắt đầu cào {total} sản phẩm với {self.max_workers} luồng...")
        args_list = [(u, i + 1) for i, u in enumerate(product_urls)]

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(_worker, a) for a in args_list]
            for f in as_completed(futures):
                r = f.result()
                if r:
                    results.append(r)

        logger.info(f"✅ Hoàn thành cào {len(results)}/{total} trang sản phẩm")
        return results

    # ============================================================
    # 5. Convert ảnh WebP theo từng mã sản phẩm
    # ============================================================

    def convert_images_to_webp_per_model(self, product_records, output_folder: str):
        """
        Từ danh sách record gốc (mỗi record có thể nhiều mã):
        - Mỗi mã (model code) sẽ gán danh sách ảnh
        - Tải và convert ảnh sang WebP
        - Đặt tên file: <Mã sản phẩm>_<index>.webp
        - Trả về: danh sách record theo từng mã sản phẩm
        """
        webp_folder = os.path.join(output_folder, "webp_images")
        os.makedirs(webp_folder, exist_ok=True)

        # Tạo danh sách bản ghi đã tách theo mã (chưa có ảnh WebP)
        expanded_records = self._expand_records_by_model(product_records)

        logger.info(f"🖼️ Bắt đầu tải & convert ảnh WebP (records theo mã: {len(expanded_records)})")

        def _img_worker(args):
            rec, _global_idx = args
            try:
                code = rec.get("Mã sản phẩm", "UNKNOWN")
                img_urls = rec.get("Image_URLs") or []
                webp_paths = []

                if not img_urls:
                    rec["Ảnh_WebP"] = json.dumps([], ensure_ascii=False)
                    return rec

                for i, img_url in enumerate(img_urls, start=1):
                    webp_path = self._download_and_convert_image(
                        img_url, code, i, webp_folder
                    )
                    if webp_path:
                        webp_paths.append(webp_path)

                rec["Ảnh_WebP"] = json.dumps(webp_paths, ensure_ascii=False)
                return rec
            except Exception as e:
                logger.error(f"❌ Lỗi xử lý ảnh cho mã {rec.get('Mã sản phẩm')}: {e}")
                rec["Ảnh_WebP"] = json.dumps([], ensure_ascii=False)
                return rec

        args_list = [(r, i + 1) for i, r in enumerate(expanded_records)]
        final_records = []

        with ThreadPoolExecutor(max_workers=min(5, self.max_workers)) as ex:
            futures = [ex.submit(_img_worker, a) for a in args_list]
            for f in as_completed(futures):
                rec = f.result()
                final_records.append(rec)

        logger.info(f"✅ Hoàn thành tải & convert ảnh WebP cho {len(final_records)} mã sản phẩm")
        return final_records

    def add_white_background(self, image):
        """
        Thêm nền trắng vào ảnh nếu cần, giữ nguyên kích thước gốc.
        Tương tự logic trong crawlerKeyence để tránh bị nhòe và vỡ ảnh.
        
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

    def _download_and_convert_image(self, img_url: str, product_code: str,
                                    index: int, webp_folder: str):
        """
        Tải 1 ảnh, thêm nền trắng, và convert trực tiếp sang WebP.
        Đặt tên file: <code>_<index>.webp
        Tương tự logic trong crawlerKeyence để tránh bị nhòe và vỡ ảnh.
        """
        try:
            headers = {
                "User-Agent": HEADERS.get("User-Agent", ""),
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": self.origin,
            }
            resp = self.session.get(img_url, headers=headers, timeout=30)
            resp.raise_for_status()

            content_type = (resp.headers.get("content-type") or "").lower()
            if not content_type.startswith("image/"):
                logger.warning(f"⚠️ URL không phải ảnh: {img_url} ({content_type})")
                return None

            if len(resp.content) < 1024:
                logger.warning(f"⚠️ Ảnh quá nhỏ: {img_url}")
                return None

            # Mở ảnh từ bytes
            original_image = Image.open(io.BytesIO(resp.content))
            if original_image.size[0] < 30 or original_image.size[1] < 30:
                logger.warning(f"⚠️ Ảnh kích thước nhỏ: {original_image.size}")
                return None

            # Thêm nền trắng nếu cần (giữ nguyên kích thước gốc)
            processed_image = self.add_white_background(original_image)

            safe_code = re.sub(r'[<>:"/\\|?*]', "_", product_code or "UNKNOWN")
            webp_name = f"{safe_code}_{index}.webp"
            webp_path = os.path.join(webp_folder, webp_name)

            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(webp_folder, exist_ok=True)

            # Lưu trực tiếp sang WebP để giữ nguyên chất lượng (tương tự Keyence)
            processed_image.save(webp_path, 'WebP', quality=95, method=6)
            
            logger.info(f"✓ Ảnh WebP: {webp_name}")
            return webp_path

        except Exception as e:
            logger.error(f"❌ Lỗi tải/convert ảnh {img_url}: {e}")
            return None

    # ============================================================
    # 6. Build bảng thông số HTML chuẩn cho từng mã
    # ============================================================

    def _expand_records_by_model(self, product_records):
        """
        Từ record gốc (mỗi record có nhiều model) -> tách thành record theo từng mã sản phẩm.

        Trả về list record:
        {
            'STT': ...,
            'Mã sản phẩm': code,
            'Tên sản phẩm': product_name (+ mã nếu cần),
            'Thông số_HTML': spec_html_table,
            'Image_URLs': [...],
            'URL': url
        }
        """
        expanded = []
        stt = 1

        for base in product_records:
            if not base:
                continue
            url = base.get("URL", "")
            product_name = base.get("Product_Name", "")
            image_urls = base.get("Image_URLs") or []
            spec_rows = base.get("Spec_Rows") or []
            models = base.get("Models") or []

            if not models:
                model_code = ""
                spec_html = self.build_spec_table_html(
                    model_code=model_code,
                    product_name=product_name,
                    spec_rows=spec_rows,
                )
                expanded.append({
                    "STT": stt,
                    "Mã sản phẩm": model_code,
                    "Tên sản phẩm": product_name,
                    "Thông số_HTML": spec_html,
                    "Image_URLs": image_urls,
                    "URL": url,
                })
                stt += 1
                continue

            for m in models:
                code = (m.get("code") or "").strip()
                full_name = product_name
                if code and code not in full_name:
                    full_name = f"{product_name} {code}"

                spec_html = self.build_spec_table_html(
                    model_code=code,
                    product_name=product_name,
                    spec_rows=spec_rows,
                )

                expanded.append({
                    "STT": stt,
                    "Mã sản phẩm": code,
                    "Tên sản phẩm": full_name,
                    "Thông số_HTML": spec_html,
                    "Image_URLs": image_urls,
                    "URL": url,
                })
                stt += 1

        return expanded

    def build_spec_table_html(self, model_code: str, product_name: str, spec_rows):
        """
        Tạo bảng HTML theo mẫu:
        <table id="specifications" ...>
          <tr><th>Thông số</th><th>Giá trị</th></tr>
          ...
          <tr><td style="font-weight: bold;">Mã sản phẩm</td><td>...</td></tr>
          <tr><td style="font-weight: bold;">Tên sản phẩm</td><td>...</td></tr>
          ...
          <tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>
        </table>
        """
        rows_html = []

        # Header
        rows_html.append(
            "<thead><tr style=\"background-color: #f2f2f2;\">"
            "<th>Thông số</th><th>Giá trị</th></tr></thead><tbody>"
        )

        # Mã sản phẩm
        rows_html.append(
            "<tr>"
            "<td style=\"font-weight: bold;\">Mã sản phẩm</td>"
            f"<td>{model_code}</td>"
            "</tr>"
        )

        # Tên sản phẩm
        rows_html.append(
            "<tr>"
            "<td style=\"font-weight: bold;\">Tên sản phẩm</td>"
            f"<td>{product_name}</td>"
            "</tr>"
        )

        # Các thông số lấy từ spec_rows
        for r in spec_rows:
            label = (r.get("label") or "").strip()
            value_html = (r.get("value") or "").strip()
            if not label or not value_html:
                continue
            rows_html.append(
                "<tr>"
                f"<td style=\"font-weight: bold;\">{label}</td>"
                f"<td>{value_html}</td>"
                "</tr>"
            )

        # HÀNG BẮT BUỘC CUỐI CÙNG
        rows_html.append(
            "<tr>"
            "<td style=\"font-weight: bold;\">Copyright</td>"
            "<td>Haiphongtech.vn</td>"
            "</tr>"
        )

        rows_html.append("</tbody>")
        table_html = (
            "<table id=\"specifications\" border=\"1\" cellpadding=\"8\" cellspacing=\"0\" "
            "style=\"border-collapse: collapse; font-family: Arial; width: 100%;\">"
            + "".join(rows_html) +
            "</table>"
        )
        return table_html

    # ============================================================
    # 7. Lưu Excel + ZIP (cấu trúc giống BAA Qlight, theo danh mục con)
    # ============================================================

    def create_excel(self, product_info_list, output_folder: str,
                     excel_name: str = "Hioki_Products.xlsx"):
        """
        Lưu dữ liệu ra 1 file Excel cho 1 danh mục con.
        Cột: STT, Mã sản phẩm, Tên sản phẩm, Thông số_HTML, Ảnh sản phẩm, Ảnh_WebP, URL
        """
        if not product_info_list:
            return None

        os.makedirs(output_folder, exist_ok=True)
        excel_path = os.path.join(output_folder, excel_name)

        df = pd.DataFrame(product_info_list)

        col_order = [
            "STT", "Mã sản phẩm", "Tên sản phẩm",
            "Thông số_HTML", "Image_URLs", "Ảnh_WebP", "URL",
        ]
        exist_cols = [c for c in col_order if c in df.columns]
        others = [c for c in df.columns if c not in exist_cols]
        df = df[exist_cols + others]

        df.to_excel(excel_path, index=False)
        logger.info(f"✅ Đã tạo file Excel: {excel_path}")
        return excel_path

    def create_download_zip(self, output_folder: str, zip_filename: str = None):
        """
        Tạo file ZIP chứa:
        - Các file Excel trong output_folder
        - Thư mục webp_images và ảnh
        - README.txt
        """
        try:
            if not os.path.isdir(output_folder):
                return {
                    "success": False,
                    "message": f"Thư mục không tồn tại: {output_folder}",
                }

            if not zip_filename:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                zip_filename = f"Hioki_Results_{ts}.zip"

            zip_path = os.path.join(output_folder, zip_filename)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(output_folder):
                    for f in files:
                        if f == zip_filename:
                            continue
                        # Nén Excel + ảnh
                        if not (
                            f.lower().endswith((".xlsx", ".xls", ".webp", ".jpg", ".jpeg", ".png"))
                        ):
                            continue
                        fpath = os.path.join(root, f)
                        arcname = os.path.relpath(fpath, output_folder)
                        zf.write(fpath, arcname)
                        logger.info(f"Thêm vào ZIP: {arcname}")

                readme = f"""Hioki Crawler - Kết quả cào dữ liệu
====================================

Ngày tạo: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Thư mục nguồn: {output_folder}

Nội dung:
- File Excel dữ liệu sản phẩm (mỗi bản ghi tương ứng 1 mã sản phẩm)
- Thư mục webp_images chứa ảnh sản phẩm (WebP)

Ghi chú:
- Ảnh được đặt tên theo Mã sản phẩm + index, ví dụ: SP7001-90_1.webp
- Thông số kỹ thuật đã được chuẩn hoá theo bảng HTML id=\\"specifications\\"
"""
                zf.writestr("README.txt", readme)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            logger.info(f"✅ ZIP: {zip_filename} ({size_mb:.2f} MB)")
            return {
                "success": True,
                "zip_path": zip_path,
                "zip_filename": zip_filename,
                "zip_size_mb": round(size_mb, 2),
            }
        except Exception as e:
            logger.error(f"❌ Lỗi tạo ZIP: {e}")
            return {"success": False, "message": str(e)}

    # ============================================================
    # 8. Cào 1 danh mục cha (theo yêu cầu)
    # ============================================================

    def crawl_hioki_category(self, category_url: str,
                             output_root: str = None,
                             max_pages_per_sub: int = 5):
        """
        Cào 1 URL danh mục cha:
        - Lấy danh mục con
        - Với mỗi danh mục con:
          + Lấy sản phẩm
          + Cào chi tiết đa luồng
          + Tách theo mã sản phẩm, xử lý ảnh WebP
          + Lưu Excel + ZIP trong thư mục con

        Cấu trúc:
        output_root/
          <CategorySlug>/
             <SubCategorySlug>/
                webp_images/
                Hioki_*.xlsx
                Hioki_Results_*.zip
        """
        start = time.time()

        if not output_root:
            output_root = os.path.join(os.getcwd(), "output_hioki")

        os.makedirs(output_root, exist_ok=True)

        parsed = urlparse(category_url)
        cat_slug = parsed.path.rstrip("/").split("/")[-1] or "category"
        cat_root = os.path.join(output_root, cat_slug)
        os.makedirs(cat_root, exist_ok=True)

        logger.info(f"🚀 Bắt đầu cào danh mục Hioki: {category_url}")
        subcats = self.get_subcategories(category_url)

        all_results = []
        for sc in subcats:
            name = (sc.get("name") or "").strip()
            url = (sc.get("url") or "").strip()
            if not url:
                continue

            sub_slug_src = urlparse(url).path.rstrip("/").split("/")[-1] or "sub"
            sub_slug = re.sub(r'[<>:"/\\|?*]+', "_", sub_slug_src)
            sub_folder = os.path.join(cat_root, sub_slug)
            os.makedirs(sub_folder, exist_ok=True)

            logger.info(f"📂 Danh mục con: {name} -> {url}")

            product_urls = self.get_products_from_subcategory(
                url, max_pages=max_pages_per_sub
            )
            if not product_urls:
                logger.warning(f"⚠️ Không có sản phẩm trong danh mục con {url}")
                continue

            base_records = self.crawl_products_multithread(product_urls)
            final_records = self.convert_images_to_webp_per_model(
                base_records, sub_folder
            )

            excel_name = f"Hioki_{sub_slug}.xlsx"
            excel_path = self.create_excel(final_records, sub_folder, excel_name)
            zip_result = self.create_download_zip(sub_folder)

            all_results.append({
                "subcategory_name": name,
                "subcategory_url": url,
                "output_folder": sub_folder,
                "excel_path": excel_path,
                "zip_result": zip_result,
                "total_records": len(final_records),
            })

        duration = time.time() - start
        logger.info(
            f"✅ Hoàn thành cào danh mục Hioki: {category_url} "
            f"trong {duration:.2f} giây, tổng {len(all_results)} danh mục con"
        )
        return {
            "success": True,
            "category_url": category_url,
            "output_root": output_root,
            "results": all_results,
            "duration": duration,
        }


# ================================================================
# 9. Hàm tiện ích mức module
# ================================================================

def crawl_hioki_category(category_url: str,
                         output_root: str = None,
                         max_workers: int = 10,
                         max_pages_per_sub: int = 5,
                         gemini_api_key: str = None):
    crawler = HIOKICrawler(max_workers=max_workers, gemini_api_key=gemini_api_key)
    return crawler.crawl_hioki_category(
        category_url=category_url,
        output_root=output_root,
        max_pages_per_sub=max_pages_per_sub,
    )


def test_single_product(gemini_api_key=None):
    """Test với một sản phẩm đơn lẻ để debug với Gemini translation"""
    print("🧪 Test với một sản phẩm đơn lẻ (có dịch bằng Gemini)...")
    
    # Test với 2 sản phẩm
    test_urls = [
        "https://www.hioki.com/vn-vi/products/data-acquisition/daq-testing/id_12345",  # Thay bằng URL thực tế
        "https://www.hioki.com/vn-vi/products/data-acquisition/daq-maintenance/id_12346"  # Thay bằng URL thực tế
    ]
    
    if gemini_api_key:
        print(f"🔑 Sử dụng Gemini API key: {gemini_api_key[:20]}...")
    
    c = HIOKICrawler(max_workers=2, translate_to_vi=True, gemini_api_key=gemini_api_key)
    
    for i, test_url in enumerate(test_urls[:2], 1):  # Chỉ test 2 sản phẩm đầu
        print(f"\n{'='*60}")
        print(f"📦 Test sản phẩm {i}/{len(test_urls[:2])}")
        print(f"🔗 Test URL: {test_url}")
        
        try:
            product_info = c.crawl_product_info(test_url, index=i)
            
            if product_info:
                print(f"\n✅ THÔNG TIN SẢN PHẨM:")
                print(f"   • Tên: {product_info.get('Product_Name', 'N/A')}")
                print(f"   • Số model: {len(product_info.get('Models', []))}")
                print(f"   • Số ảnh: {len(product_info.get('Image_URLs', []))}")
                print(f"   • Số thông số: {len(product_info.get('Spec_Rows', []))}")
                
                if product_info.get('Models'):
                    print(f"\n📦 MODELS (2 đầu tiên):")
                    for model in product_info['Models'][:2]:
                        print(f"   • {model.get('code', 'N/A')}: {model.get('description', 'N/A')[:50]}...")
                
                if product_info.get('Spec_Rows'):
                    print(f"\n📋 THÔNG SỐ KỸ THUẬT (đã dịch sang tiếng Việt - 10 đầu tiên):")
                    for spec in product_info['Spec_Rows'][:10]:
                        print(f"   • {spec.get('label', 'N/A')}: {spec.get('value', 'N/A')[:100] if len(str(spec.get('value', ''))) > 100 else spec.get('value', 'N/A')}")
            else:
                print(f"\n❌ Không thể lấy thông tin sản phẩm!")
        except Exception as e:
            print(f"\n❌ Lỗi khi test sản phẩm: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    # Test với Gemini API key
    GEMINI_API_KEY = "AIzaSyD0j8Mhf_1grariQdXB0OmCiZJ_WbyojQU"
    
    print("🚀 Bắt đầu test HIOKI crawler với Gemini translation...")
    test_single_product(gemini_api_key=GEMINI_API_KEY)
    
    # Hoặc test với danh mục (comment out nếu chỉ muốn test sản phẩm đơn lẻ)
    # test_category = "https://www.hioki.com/vn-vi/products/data-acquisition"
    # c = HIOKICrawler(max_workers=2, translate_to_vi=True, gemini_api_key=GEMINI_API_KEY)
    # res = c.crawl_hioki_category(test_category, max_pages_per_sub=1)
    # print(res)

