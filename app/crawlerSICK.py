import os
import re
import time
import zipfile
import asyncio
import logging
import threading
import random
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin, quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import json

from app.webp_converter import WebPConverter

try:
    from app import socketio
except Exception:
    socketio = None

logger = logging.getLogger(__name__)


class SICKCrawler:
    def __init__(self, max_workers=5, output_root=None, socketio=None):
        self.max_workers = max(1, int(max_workers))
        if output_root:
            self.output_root = output_root
        else:
            self.output_root = os.path.join(os.getcwd(), "app", "downloads", "output_sick")
        os.makedirs(self.output_root, exist_ok=True)
        self.socketio = socketio

        self.session = requests.Session()
        
        # Danh sách User-Agent để xoay vòng tránh bị chặn
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.2478.80",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]

        # Tăng kích thước connection pool và cấu hình retry
        retry_strategy = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[403, 429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(
            pool_connections=self.max_workers + 5, 
            pool_maxsize=self.max_workers * 2,
            max_retries=retry_strategy
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        self.session.headers.update(
            {
                "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
        )

    def emit_progress(self, percent, message):
        payload = {"percent": int(percent), "message": str(message)}
        sio = getattr(self, "socketio", None) or socketio
        if sio is None:
            try:
                from app import socketio as app_sio
                sio = app_sio
            except Exception:
                sio = None
        if sio:
            try:
                sio.emit("sick_progress", payload)
            except Exception:
                logger.info("SICK Progress fallback: %s", payload)
        else:
            logger.info("SICK Progress: %s%% - %s", payload["percent"], payload["message"])

    def _normalize_url(self, url):
        if not url:
            return ""
        url = url.strip()
        if not url:
            return ""
        parsed = urlparse(url)
        if not parsed.scheme:
            return ""
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean = f"{clean}?{parsed.query}"
        return clean

    def _to_abs_url(self, href, base_url):
        if not href:
            return ""
        href = href.strip()
        if href.startswith("javascript:") or href.startswith("mailto:"):
            return ""
        abs_url = urljoin(base_url, href)
        return self._normalize_url(abs_url)

    def _is_sick_domain(self, url):
        try:
            host = urlparse(url).netloc.lower()
            return host.endswith("sick.com")
        except Exception:
            return False

    def is_sick_product_url(self, url):
        if not self._is_sick_domain(url):
            return False
        path = urlparse(url).path.lower()
        return "/p/" in path and ("/products/" in path or "/catalog/" in path)

    def is_sick_category_url(self, url):
        if not self._is_sick_domain(url):
            return False
        path = urlparse(url).path.lower()
        return (
            ("/catalog/products/" in path and "/c/" in path)
            or ("/products/" in path and "/c/" in path)
        )

    def _fetch_html(self, url, timeout=35):
        # Nghỉ ngẫu nhiên để tránh bot detection
        time.sleep(random.uniform(0.5, 1.5))
        try:
            headers = {"User-Agent": random.choice(self.user_agents)}
            resp = self.session.get(url, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            logger.warning("Không tải được URL %s: %s", url, exc)
            return ""

    def _extract_links_from_html(self, html, base_url):
        soup = BeautifulSoup(html, "html.parser")
        links = set()
        for a in soup.select("a[href]"):
            abs_url = self._to_abs_url(a.get("href", ""), base_url)
            if abs_url:
                links.add(abs_url)
        return links

    def discover_sick_product_urls(self, start_urls):
        discovered_products = set()
        seed_codes = []

        for url in start_urls:
            # Nếu URL là link sản phẩm trực tiếp, thêm vào danh sách kết quả
            if self.is_sick_product_url(url):
                discovered_products.add(url)
                continue
                
            category_code = self._extract_category_code(url)
            if category_code:
                seed_codes.append(category_code)
        
        if not seed_codes and discovered_products:
            return sorted(discovered_products)

        # Tầng 1: lấy series/family codes từ danh mục gốc (song song)
        one_level_codes = set(seed_codes)
        if seed_codes:
            logger.info("Đang tìm MasterProduct cho codes: %s", seed_codes)
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(seed_codes))) as executor:
                future_to_code = {
                    executor.submit(self._search_fact_finder_hits, "MasterProduct", code): code 
                    for code in seed_codes
                }
                for future in as_completed(future_to_code):
                    try:
                        master_hits = future.result()
                        logger.info("Tìm thấy %d master hits cho 1 code", len(master_hits))
                        for hit in master_hits:
                            next_code = self._extract_category_code_from_hit(hit)
                            if next_code:
                                one_level_codes.add(next_code)
                    except Exception as exc:
                        logger.warning("Lỗi MasterProduct: %s", exc)

        # Tầng 2: chỉ lấy ProductVariant ở bộ code đã cố định (song song)
        if one_level_codes:
            logger.info("Đang tìm ProductVariant cho %d codes", len(one_level_codes))
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_code = {
                    executor.submit(self._search_fact_finder_hits, "ProductVariant", code): code 
                    for code in sorted(one_level_codes)
                }
                for future in as_completed(future_to_code):
                    try:
                        variant_hits = future.result()
                        logger.info("Tìm thấy %d variant hits cho 1 code", len(variant_hits))
                        for hit in variant_hits:
                            product_url = self._extract_product_url_from_hit(hit)
                            if product_url:
                                discovered_products.add(product_url)
                    except Exception as exc:
                        logger.warning("Lỗi ProductVariant: %s", exc)

        logger.info("Tổng cộng tìm thấy %d sản phẩm", len(discovered_products))
        return sorted(discovered_products)

    def _extract_category_code(self, url):
        path = urlparse(url).path
        match = re.search(r"/c/(g\d+)", path, re.IGNORECASE)
        return match.group(1).lower() if match else ""

    def _extract_product_number(self, url):
        path = urlparse(url).path
        match = re.search(r"/p/(p?\d+)", path, re.IGNORECASE)
        if match:
            num = match.group(1).lower()
            return num if num.startswith("p") else f"p{num}"
        return ""

    def _search_fact_finder_hits(self, def_type, category_code):
        def fetch_page(p):
            time.sleep(random.uniform(0.3, 1.2))
            endpoint = (
                "https://www.sick.com/api/fact-finder/search/enAG-sick"
                f"?query=%2A&filter=DefType%3A{def_type}&filter=CategoryCode%3A{category_code}"
                f"&page={p}&showPermutedSearchParams=false"
            )
            try:
                headers = {
                    "User-Agent": random.choice(self.user_agents), 
                    "Referer": "https://www.sick.com/",
                    "Accept": "application/json, text/plain, */*"
                }
                resp = self.session.get(endpoint, timeout=45, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning("API trả về status %d cho code %s", resp.status_code, category_code)
                return {}
            except Exception as e:
                logger.debug("Lỗi fetch_page: %s", e)
                return {}

        # Lấy trang đầu tiên để biết tổng số trang
        first_data = fetch_page(1)
        all_hits = first_data.get("hits", []) or []
        page_count = int(first_data.get("paging", {}).get("pageCount", 1) or 1)
        
        if page_count > 1:
            logger.info("Category %s có %d trang", category_code, page_count)

        # Lấy các trang còn lại song song
        if page_count > 1:
            with ThreadPoolExecutor(max_workers=min(5, page_count - 1)) as executor:
                futures = [executor.submit(fetch_page, p) for p in range(2, page_count + 1)]
                for future in as_completed(futures):
                    data = future.result()
                    hits = data.get("hits", []) or []
                    all_hits.extend(hits)
        
        return all_hits

    def _extract_category_code_from_hit(self, hit):
        master_values = hit.get("masterValues", {}) or {}
        code = str(master_values.get("DefArticleNo", "")).lower()
        if re.fullmatch(r"g\d+", code):
            return code
        code = str(hit.get("id", "")).lower()
        if re.fullmatch(r"g\d+", code):
            return code
        return ""

    def _extract_product_url_from_hit(self, hit):
        master_values = hit.get("masterValues", {}) or {}
        def_url = str(master_values.get("DefUrl", "")).strip()
        if not def_url:
            return ""
        if not def_url.startswith("/"):
            def_url = f"/{def_url}"
        # Chuẩn hóa như mẫu người dùng yêu cầu
        return f"https://www.sick.com/ag/en{def_url}?tab=detail"

    async def _discover_with_playwright_async(self, start_urls, max_depth=3):
        discovered_products = set()
        visited_categories = set()
        queue = deque()
        for url in start_urls:
            normalized = self._normalize_url(url)
            if normalized and self._is_sick_domain(normalized):
                queue.append((normalized, 0))

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            while queue:
                current_url, depth = queue.popleft()
                if depth > max_depth:
                    continue
                if current_url in visited_categories:
                    continue
                visited_categories.add(current_url)

                context = await browser.new_context()
                page = await context.new_page()
                try:
                    await page.goto(current_url, timeout=60000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2500)
                    links = await page.evaluate(
                        """() => {
                            const out = new Set();
                            document.querySelectorAll('a[href]').forEach(a => out.add(a.href));
                            document.querySelectorAll('[routerlink],[routerLink],[ng-reflect-router-link]').forEach(el => {
                              const v = el.getAttribute('routerlink') || el.getAttribute('routerLink') || el.getAttribute('ng-reflect-router-link');
                              if (v) {
                                const abs = v.startsWith('http') ? v : (window.location.origin + (v.startsWith('/') ? v : ('/' + v)));
                                out.add(abs);
                              }
                            });
                            return Array.from(out);
                        }"""
                    )
                    for link in links:
                        normalized = self._normalize_url(link)
                        if not normalized or not self._is_sick_domain(normalized):
                            continue
                        if self.is_sick_product_url(normalized):
                            discovered_products.add(normalized)
                        elif self.is_sick_category_url(normalized) and normalized not in visited_categories:
                            queue.append((normalized, depth + 1))
                except Exception:
                    pass
                finally:
                    await context.close()
            await browser.close()
        return sorted(discovered_products)

    def discover_sick_product_urls_fallback(self, start_urls):
        try:
            return asyncio.run(self._discover_with_playwright_async(start_urls))
        except Exception as exc:
            logger.warning("Playwright fallback discovery lỗi: %s", exc)
            return []

    def _format_sick_product_url(self, def_url):
        if not def_url:
            return ""
        def_url = str(def_url).strip()
        if def_url.lower().startswith("http://") or def_url.lower().startswith("https://"):
            url = def_url
        else:
            if not def_url.startswith("/"):
                def_url = f"/{def_url}"
            url = f"https://www.sick.com/ag/en{def_url}"
        if "?tab=" not in url:
            url = f"{url}?tab=detail"
        return url

    async def _search_product_playwright_async(self, code):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto("https://www.sick.com/ag/en/", timeout=45000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                search_input = page.locator('#search-input input, syn-input input, input[placeholder="Search"]').first
                if await search_input.count() > 0:
                    await search_input.fill(code)
                    await page.wait_for_timeout(1000)
                    await search_input.press("Enter")
                    await page.wait_for_timeout(4000)
                    links = await page.locator('a[href*="/p/"]').all()
                    for l in links:
                        href = await l.get_attribute("href")
                        if href and ("/products/" in href or "/catalog/" in href) and "/p/" in href:
                            return self._format_sick_product_url(href)
            except Exception as e:
                logger.debug("Lỗi Playwright async search cho %s: %s", code, e)
            finally:
                await context.close()
                await browser.close()
        return None

    def _search_product_playwright(self, code):
        try:
            return asyncio.run(self._search_product_playwright_async(code))
        except Exception as exc:
            logger.warning("Playwright search lỗi cho %s: %s", code, exc)
            return None

    def search_sick_product(self, product_code):
        """
        Tìm kiếm link sản phẩm SICK dựa trên mã sản phẩm (part number / model / type).
        Sử dụng Fact-Finder Search/Suggest API và fallback qua Playwright (#search-input).
        """
        if not product_code:
            return None
        code_clean = str(product_code).strip()
        if not code_clean:
            return None

        encoded = quote(code_clean)

        # 1. Fact-Finder search with ProductVariant filter
        url_variant = (
            f"https://www.sick.com/api/fact-finder/search/enAG-sick"
            f"?query={encoded}&filter=DefType%3AProductVariant&showPermutedSearchParams=false"
        )
        try:
            headers = {
                "User-Agent": random.choice(self.user_agents),
                "Referer": "https://www.sick.com/",
                "Accept": "application/json, text/plain, */*"
            }
            resp = self.session.get(url_variant, timeout=20, headers=headers)
            if resp.status_code == 200:
                hits = resp.json().get("hits", []) or []
                # Ưu tiên 1: Khớp chính xác DefName hoặc PartNumber
                for h in hits:
                    mv = h.get("masterValues", {}) or {}
                    dn = str(mv.get("DefName", "")).strip().lower()
                    pn = str(mv.get("PartNumber", "")).strip().lower()
                    if dn == code_clean.lower() or pn == code_clean.lower():
                        def_url = str(mv.get("DefUrl", "")).strip()
                        if def_url:
                            return self._format_sick_product_url(def_url)
                # Ưu tiên 2: Kết quả đầu tiên trong hits
                if hits:
                    def_url = str(hits[0].get("masterValues", {}).get("DefUrl", "")).strip()
                    if def_url:
                        return self._format_sick_product_url(def_url)
        except Exception as e:
            logger.debug("Lỗi search variant cho %s: %s", code_clean, e)

        # 2. Fact-Finder search không filter
        url_all = (
            f"https://www.sick.com/api/fact-finder/search/enAG-sick"
            f"?query={encoded}&showPermutedSearchParams=false"
        )
        try:
            headers = {
                "User-Agent": random.choice(self.user_agents),
                "Referer": "https://www.sick.com/",
                "Accept": "application/json, text/plain, */*"
            }
            resp = self.session.get(url_all, timeout=20, headers=headers)
            if resp.status_code == 200:
                hits = resp.json().get("hits", []) or []
                for h in hits:
                    mv = h.get("masterValues", {}) or {}
                    dn = str(mv.get("DefName", "")).strip().lower()
                    pn = str(mv.get("PartNumber", "")).strip().lower()
                    if dn == code_clean.lower() or pn == code_clean.lower():
                        def_url = str(mv.get("DefUrl", "")).strip()
                        if def_url and "/p/" in def_url:
                            return self._format_sick_product_url(def_url)
                for h in hits:
                    mv = h.get("masterValues", {}) or {}
                    def_url = str(mv.get("DefUrl", "")).strip()
                    if def_url and "/p/" in def_url:
                        return self._format_sick_product_url(def_url)
        except Exception as e:
            logger.debug("Lỗi search all cho %s: %s", code_clean, e)

        # 3. Fact-Finder suggest
        url_sug = f"https://www.sick.com/api/fact-finder/suggest/enAG-sick?query={encoded}"
        try:
            headers = {
                "User-Agent": random.choice(self.user_agents),
                "Referer": "https://www.sick.com/",
                "Accept": "application/json, text/plain, */*"
            }
            resp = self.session.get(url_sug, timeout=20, headers=headers)
            if resp.status_code == 200:
                sugs = resp.json().get("suggestions", []) or []
                for s in sugs:
                    name = str(s.get("name", "")).strip().lower()
                    attrs = s.get("attributes", {}) or {}
                    def_url = str(attrs.get("DefUrl", "")).strip()
                    pn = str(attrs.get("PartNumber", "")).strip().lower()
                    if (code_clean.lower() in name or pn == code_clean.lower()) and def_url and "/p/" in def_url:
                        return self._format_sick_product_url(def_url)
        except Exception as e:
            logger.debug("Lỗi suggest cho %s: %s", code_clean, e)

        # 4. Fallback Playwright qua ô tìm kiếm website (#search-input)
        try:
            pw_url = self._search_product_playwright(code_clean)
            if pw_url:
                return pw_url
        except Exception as e:
            logger.debug("Lỗi Playwright fallback cho %s: %s", code_clean, e)

        return None

    def is_sick_domain_url_or_path(self, url):
        return self._is_sick_domain(url)

    def _first_text(self, soup, selectors):
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                txt = node.get_text(" ", strip=True)
                if txt:
                    return txt
        return ""

    def _extract_largest_srcset_url(self, srcset):
        if not srcset:
            return ""
        best_url = ""
        best_w = -1
        for part in srcset.split(","):
            part = part.strip()
            if not part:
                continue
            if " " not in part:
                if not best_url:
                    best_url = part
                continue
            candidate, size_tag = part.rsplit(" ", 1)
            width = -1
            if size_tag.endswith("w"):
                try:
                    width = int(size_tag[:-1])
                except Exception:
                    width = -1
            if width > best_w:
                best_w = width
                best_url = candidate.strip()
        return best_url

    def _extract_product_images(self, soup, product_url):
        image_urls = []
        seen = set()

        image_nodes = soup.select(
            ".gallery__main img, .gallery__thumbnails img, ui-akamai-image img, picture img, img"
        )
        for node in image_nodes:
            candidates = [
                node.get("data-src", ""),
                node.get("src", ""),
                self._extract_largest_srcset_url(node.get("srcset", "")),
            ]
            for source in candidates:
                if not source:
                    continue
                abs_url = self._to_abs_url(source, product_url)
                if not abs_url:
                    continue
                lower = abs_url.lower()
                if "cdn.sick.com/media/" not in lower and "/media/" not in lower:
                    continue
                if abs_url in seen:
                    continue
                seen.add(abs_url)
                image_urls.append(abs_url)

        return image_urls

    def _extract_specifications(self, soup):
        specs = []
        seen_keys = set()

        # Tìm tất cả các bảng kỹ thuật trong các tab panel hoặc div tech-table
        tables = soup.select('.tech-table, ui-tab-panel table, syn-tab-panel table')
        
        for table in tables:
            for row in table.select("tr"):
                cols = row.select("td, th")
                if len(cols) < 2:
                    continue
                
                key = cols[0].get_text(" ", strip=True)
                # Xử lý trường hợp value có nhiều dòng (như thẻ <p> trong HTML người dùng gửi)
                value = cols[1].get_text(" | ", strip=True)
                
                if not key or not value:
                    continue
                
                # Bỏ qua các tiêu đề bảng không phải dữ liệu
                if key.lower() in {"thông số", "giá trị", "parameter", "value"}:
                    continue
                    
                norm_key = re.sub(r"\s+", " ", key.lower())
                # Tránh trùng lặp nếu cùng một thông số xuất hiện ở nhiều bảng
                if norm_key in seen_keys:
                    continue
                
                seen_keys.add(norm_key)
                specs.append((key, value))

        return specs

    def _extract_pdf_links(self, soup, product_url):
        links = []
        seen = set()

        for a in soup.select('a[href*=".pdf"], a[data-test-id*="split-button"]'):
            href = a.get("href", "")
            abs_url = self._to_abs_url(href, product_url)
            if not abs_url:
                continue
            if ".pdf" not in abs_url.lower():
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)
            links.append(abs_url)

        html_text = str(soup)
        for raw in re.findall(r"https://[^\"'\s]+?\.pdf(?:\?[^\"'\s]*)?", html_text, flags=re.I):
            if raw not in seen:
                seen.add(raw)
                links.append(raw)

        return links

    def _extract_pdf_links_from_download_json(self, download_json):
        links = []
        try:
            items = json.loads(download_json) if isinstance(download_json, str) else (download_json or [])
        except Exception:
            items = []
        # Ưu tiên English
        items_sorted = sorted(items, key=lambda x: 0 if str(x.get("Language", "")).lower() == "en" else 1)
        for item in items_sorted:
            path = str(item.get("Path", "")).strip()
            if path:
                links.append(urljoin("https://cdn.sick.com/media/", path.lstrip("/")))
        unique_links = list(dict.fromkeys(links))
        # Giữ 1 tài liệu ưu tiên English để giảm thời gian crawl
        return unique_links[:1]

    def _extract_images_from_gallery_json(self, gallery_json):
        try:
            items = json.loads(gallery_json) if isinstance(gallery_json, str) else (gallery_json or [])
        except Exception:
            items = []
        if isinstance(items, dict):
            items = [items]

        # main_image trước, sau đó further_image
        def image_rank(item):
            cls = str(item.get("Class", "")).lower()
            return 0 if cls == "main_image" else 1

        items_sorted = sorted(items, key=image_rank)
        image_urls = []
        for item in items_sorted:
            cls = str(item.get("Class", "")).lower()
            if cls in {"video", "document"}:
                continue
            url = str(item.get("URL", "")).strip()
            if not url:
                continue
            if url.lower().startswith("http"):
                image_urls.append(url)
            else:
                image_urls.append(urljoin("https://cdn.sick.com", url))
        return list(dict.fromkeys(image_urls))

    def _extract_specs_from_techdata_json(self, techdata_json):
        specs = []
        seen_keys = set()
        try:
            items = json.loads(techdata_json) if isinstance(techdata_json, str) else (techdata_json or [])
        except Exception:
            items = []
            
        for item in items:
            title = str(item.get("Title", "")).strip()
            value_html = str(item.get("Value", "")).strip()
            if not value_html:
                continue
                
            # Lấy toàn bộ thông số từ tất cả các tab (Features, Performance, Safety, etc.)
            soup = BeautifulSoup(value_html, "html.parser")
            for row in soup.select("tr"):
                tds = row.select("td, th")
                if len(tds) >= 2:
                    key = tds[0].get_text(" ", strip=True)
                    value = tds[1].get_text(" | ", strip=True)
                    
                    if not key or not value:
                        continue
                        
                    norm_key = re.sub(r"\s+", " ", key.lower())
                    if norm_key not in seen_keys:
                        seen_keys.add(norm_key)
                        specs.append((key, value))
        return specs

    def _fetch_product_record(self, product_number):
        time.sleep(random.uniform(0.2, 1.0))
        endpoint = f"https://www.sick.com/api/fact-finder/records/enAG-sick?productNumber={product_number}"
        try:
            headers = {
                "User-Agent": random.choice(self.user_agents), 
                "Referer": "https://www.sick.com/",
                "Accept": "application/json, text/plain, */*"
            }
            resp = self.session.get(endpoint, timeout=45, headers=headers)
            if resp.status_code != 200:
                logger.warning("Record API trả về status %d cho %s", resp.status_code, product_number)
                return {}
            data = resp.json()
            records = data.get("records", []) or []
            return records[0] if records else {}
        except Exception as e:
            logger.debug("Lỗi fetch_product_record: %s", e)
            return {}

    def _safe_product_code(self, code):
        safe = re.sub(r'[<>:"/\\|?*]+', "", (code or "").strip())
        return safe or f"SICK_{int(time.time() * 1000)}"

    def create_specifications_html_table(self, specs, product_code, product_name):
        html = (
            '<table id="specifications" border="1" cellpadding="8" cellspacing="0" '
            'style="border-collapse: collapse; font-family: Arial; width: 100%;">'
        )
        html += (
            '<thead><tr style="background-color: #f2f2f2;">'
            "<th>Thông số</th><th>Giá trị</th></tr></thead><tbody>"
        )
        html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product_code}</td></tr>'
        html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product_name}</td></tr>'
        for key, value in specs:
            html += f'<tr><td style="font-weight: bold;">{key}</td><td>{value}</td></tr>'
        html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
        html += "</tbody></table>"
        return html

    def download_sick_product_images(self, product_code, image_urls, image_dir):
        downloaded = [None] * len(image_urls)
        safe_code = self._safe_product_code(product_code)

        def download_one(idx, url):
            filename = f"{safe_code}.webp" if idx == 0 else f"{safe_code}-{idx}.webp"
            output_path = os.path.join(image_dir, filename)

            if os.path.exists(output_path):
                return filename

            for _ in range(3):
                try:
                    headers = {
                        "User-Agent": random.choice(self.user_agents),
                        "Referer": "https://www.sick.com/"
                    }
                    resp = self.session.get(url, timeout=40, headers=headers)
                    resp.raise_for_status()
                    success_path = WebPConverter.convert_bytes_to_webp(resp.content, output_path, quality=85, method=4)
                    if success_path and os.path.exists(output_path):
                        return filename
                    else:
                        with open(output_path, "wb") as f:
                            f.write(resp.content)
                        return filename
                except Exception as exc:
                    logger.debug("Lỗi tải ảnh %s: %s", url, exc)
                    continue
            return None

        # Tải nhiều ảnh cùng lúc
        if image_urls:
            with ThreadPoolExecutor(max_workers=min(10, len(image_urls))) as executor:
                future_to_idx = {executor.submit(download_one, i, url): i for i, url in enumerate(image_urls)}
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    res = future.result()
                    if res:
                        downloaded[idx] = res

        return [d for d in downloaded if d]

    def download_sick_pdfs(self, product_code, pdf_urls, pdf_dir):
        downloaded = [None] * len(pdf_urls)
        safe_code = self._safe_product_code(product_code)

        def download_one(idx, url):
            filename = f"{safe_code}_catalog.pdf" if idx == 0 else f"{safe_code}_catalog-{idx}.pdf"
            output_path = os.path.join(pdf_dir, filename)
            if os.path.exists(output_path):
                return filename
            try:
                resp = self.session.get(url, timeout=45)
                resp.raise_for_status()
                # Kiểm tra content-type hoặc đuôi file để đảm bảo là PDF
                if "application/pdf" not in resp.headers.get("content-type", "").lower() and not url.lower().endswith(".pdf"):
                    return None
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                return filename
            except Exception:
                return None

        # Tải nhiều PDF cùng lúc
        if pdf_urls:
            with ThreadPoolExecutor(max_workers=min(5, len(pdf_urls))) as executor:
                future_to_idx = {executor.submit(download_one, i, url): i for i, url in enumerate(pdf_urls)}
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    res = future.result()
                    if res:
                        downloaded[idx] = res

        return [d for d in downloaded if d]

    def extract_sick_product_info(self, product_url, img_dir, pdf_dir, download_media=True):
        product_number = self._extract_product_number(product_url)
        record = self._fetch_product_record(product_number) if product_number else {}

        product_code = str(record.get("DefName", "")).strip()
        if not product_code:
            product_code = str(record.get("PartNumber", "")).strip()
        if not product_code:
            html = self._fetch_html(product_url)
            if html:
                soup = BeautifulSoup(html, "html.parser")
                product_code = self._first_text(soup, ["span.title", "h1"])
        if not product_code:
            product_code = product_number.upper() if product_number else "UNKNOWN"

        family_group = str(record.get("DefNameAddon", "")).strip() or str(record.get("ProductFamilyGroup", "")).strip()
        family_name = str(record.get("ProductFamilyName", "")).strip()
        if family_group and family_name:
            category_name = f"{family_group}: {family_name}"
        else:
            category_name = family_name or family_group
        product_name = f"{category_name} {product_code}".strip() if category_name else product_code

        specs = self._extract_specs_from_techdata_json(record.get("TechData", ""))
        if not specs:
            html = self._fetch_html(product_url)
            if html:
                specs = self._extract_specifications(BeautifulSoup(html, "html.parser"))
        spec_html = self.create_specifications_html_table(specs, product_code, product_name)

        downloaded_images = []
        downloaded_pdfs = []

        if download_media:
            image_urls = self._extract_images_from_gallery_json(record.get("Gallery", ""))
            if not image_urls:
                pic_urls = record.get("DefPictureUrl", [])
                if isinstance(pic_urls, str):
                    try:
                        pic_urls = json.loads(pic_urls)
                    except Exception:
                        pic_urls = [pic_urls]
                if isinstance(pic_urls, list):
                    for p in pic_urls:
                        p_str = str(p).strip()
                        if p_str:
                            if p_str.lower().startswith("http"):
                                image_urls.append(p_str)
                            else:
                                image_urls.append(urljoin("https://cdn.sick.com", p_str))

            if not image_urls:
                html = self._fetch_html(product_url)
                if html:
                    image_urls = self._extract_product_images(BeautifulSoup(html, "html.parser"), product_url)
            downloaded_images = self.download_sick_product_images(product_code, image_urls, img_dir)

            pdf_urls = self._extract_pdf_links_from_download_json(record.get("DownloadJson", ""))
            if not pdf_urls:
                html = self._fetch_html(product_url)
                if html:
                    pdf_urls = self._extract_pdf_links(BeautifulSoup(html, "html.parser"), product_url)
            downloaded_pdfs = self.download_sick_pdfs(product_code, pdf_urls, pdf_dir)

        return {
            "Mã sản phẩm": product_code,
            "Tên sản phẩm": product_name,
            "Hình ảnh": ", ".join(downloaded_images) if downloaded_images else "N/A",
            "Tổng quan": spec_html,
            "Catalog_PDF": ", ".join(downloaded_pdfs) if downloaded_pdfs else "N/A",
            "URL": product_url,
        }

    def create_zip(self, src_dir, out_path):
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(src_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, src_dir)
                    zf.write(file_path, arcname)

    def run_crawling_sync(self, category_urls, download_media=True):
        start_time = time.time()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"SICK_Data_{timestamp}"
        result_dir = os.path.join(self.output_root, folder_name)
        image_dir = os.path.join(result_dir, "Anh")
        pdf_dir = os.path.join(result_dir, "Catalog")
        os.makedirs(image_dir, exist_ok=True)
        os.makedirs(pdf_dir, exist_ok=True)

        normalized_inputs = [self._normalize_url(url) for url in category_urls if self._normalize_url(url)]
        if not normalized_inputs:
            return {"success": False, "message": "Không có URL danh mục hợp lệ."}

        self.emit_progress(5, "Bắt đầu phân tích danh mục SICK...")
        product_urls = self.discover_sick_product_urls(normalized_inputs)
        if not product_urls:
            self.emit_progress(12, "Không thấy dữ liệu tĩnh, thử fallback trình duyệt ẩn...")
            product_urls = self.discover_sick_product_urls_fallback(normalized_inputs)

        if not product_urls:
            self.emit_progress(100, "Không tìm thấy sản phẩm nào.")
            return {"success": False, "message": "Không tìm thấy link sản phẩm từ danh mục đã nhập."}

        self.emit_progress(20, f"Tìm thấy {len(product_urls)} sản phẩm, bắt đầu cào chi tiết...")

        all_rows = []
        total = len(product_urls)
        completed = 0
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(self.extract_sick_product_info, url, image_dir, pdf_dir, download_media): url
                for url in product_urls
            }
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                row = None
                try:
                    row = future.result()
                except Exception as exc:
                    logger.warning("Lỗi xử lý sản phẩm %s: %s", url, exc)

                with lock:
                    completed += 1
                    if row:
                        all_rows.append(row)
                    progress = 20 + int((completed / total) * 70)
                    self.emit_progress(progress, f"Đã xử lý {completed}/{total} sản phẩm")

        if all_rows:
            for idx, row in enumerate(all_rows, start=1):
                row["STT"] = idx
                if "Trạng thái" not in row:
                    row["Trạng thái"] = "Thành công"
            df = pd.DataFrame(all_rows)
            columns = ["STT", "Mã sản phẩm", "Tên sản phẩm", "Hình ảnh", "Tổng quan", "Catalog_PDF", "URL", "Trạng thái"]
            available_cols = [col for col in columns if col in df.columns]
            excel_path = os.path.join(result_dir, "Du_lieu.xlsx")
            df[available_cols].to_excel(excel_path, index=False)
        else:
            self.emit_progress(100, "Không thu thập được sản phẩm nào.")
            return {
                "success": False,
                "message": "Không thu thập được dữ liệu sản phẩm nào từ các URL đã tìm thấy.",
            }

        zip_name = f"{folder_name}.zip"
        zip_path = os.path.join(self.output_root, zip_name)
        self.create_zip(result_dir, zip_path)

        duration = time.time() - start_time
        mode_str = "Lite (No Media)" if not download_media else "Full"
        self.emit_progress(100, f"Hoàn thành ({mode_str}): {len(all_rows)} sản phẩm trong {duration:.1f}s")
        return {
            "success": True,
            "total_products": len(all_rows),
            "zip_file": zip_name,
            "output_folder": result_dir.replace("\\", "/"),
            "message": f"Đã cào {len(all_rows)} sản phẩm.",
        }

    async def run_crawling(self, category_urls, download_media=True):
        return await asyncio.to_thread(self.run_crawling_sync, category_urls, download_media)

    def run_crawling_by_codes_sync(self, product_codes, download_media=True):
        """
        Cào dữ liệu SICK dựa trên danh sách mã sản phẩm (Part Number / Model).
        Tìm kiếm từng mã qua Fact-Finder API / Playwright search, sau đó trích xuất thông tin chi tiết.
        """
        start_time = time.time()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"SICK_Data_{timestamp}"
        result_dir = os.path.join(self.output_root, folder_name)
        image_dir = os.path.join(result_dir, "Anh")
        pdf_dir = os.path.join(result_dir, "Catalog")
        os.makedirs(image_dir, exist_ok=True)
        os.makedirs(pdf_dir, exist_ok=True)

        cleaned_codes = [str(c).strip() for c in product_codes if str(c).strip()]
        if not cleaned_codes:
            return {"success": False, "message": "Không có mã sản phẩm nào hợp lệ."}

        # Lọc danh sách mã duy nhất để tìm kiếm không bị lặp
        unique_codes = list(dict.fromkeys(cleaned_codes))
        total_unique = len(unique_codes)
        self.emit_progress(5, f"Bắt đầu tìm kiếm {total_unique} mã sản phẩm trên SICK...")

        code_to_url = {}
        search_completed = 0
        search_lock = threading.Lock()

        # Tìm kiếm URL sản phẩm theo mã song song
        search_workers = min(self.max_workers * 2, total_unique, 10)
        with ThreadPoolExecutor(max_workers=search_workers) as executor:
            future_to_code = {
                executor.submit(self.search_sick_product, code): code
                for code in unique_codes
            }
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                url = None
                try:
                    url = future.result()
                except Exception as exc:
                    logger.warning("Lỗi tìm kiếm mã %s: %s", code, exc)

                with search_lock:
                    search_completed += 1
                    if url:
                        code_to_url[code] = url
                    prog = 5 + int((search_completed / total_unique) * 20)
                    found_str = f"Đã tìm thấy: {len(code_to_url)}/{search_completed}"
                    self.emit_progress(prog, f"Tìm kiếm mã ({search_completed}/{total_unique}): {code} - {found_str}")

        found_codes = [c for c in unique_codes if c in code_to_url]
        not_found_codes = [c for c in unique_codes if c not in code_to_url]

        self.emit_progress(
            25,
            f"Tìm kiếm hoàn tất: {len(found_codes)}/{total_unique} mã tìm thấy link. Bắt đầu cào dữ liệu chi tiết...",
        )

        # Cào chi tiết các URL tìm được
        unique_urls = list(dict.fromkeys([code_to_url[c] for c in found_codes]))
        url_to_data = {}
        crawl_completed = 0
        crawl_lock = threading.Lock()

        if unique_urls:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_url = {
                    executor.submit(self.extract_sick_product_info, u, image_dir, pdf_dir, download_media): u
                    for u in unique_urls
                }
                for future in as_completed(future_to_url):
                    u = future_to_url[future]
                    pdata = None
                    try:
                        pdata = future.result()
                    except Exception as exc:
                        logger.warning("Lỗi cào sản phẩm %s: %s", u, exc)

                    with crawl_lock:
                        crawl_completed += 1
                        if pdata:
                            url_to_data[u] = pdata
                        prog = 25 + int((crawl_completed / len(unique_urls)) * 65)
                        self.emit_progress(prog, f"Đã cào dữ liệu {crawl_completed}/{len(unique_urls)} sản phẩm")

        # Ghép kết quả theo đúng thứ tự danh sách mã người dùng nhập ban đầu
        all_rows = []
        found_count = 0
        not_found_count = 0

        for idx, code in enumerate(cleaned_codes, start=1):
            url = code_to_url.get(code)
            pdata = url_to_data.get(url) if url else None

            if pdata:
                found_count += 1
                row = dict(pdata)
                row["STT"] = idx
                if not row.get("Mã sản phẩm") or row.get("Mã sản phẩm") == "UNKNOWN":
                    row["Mã sản phẩm"] = code
                row["Trạng thái"] = "Thành công"
                all_rows.append(row)
            else:
                not_found_count += 1
                all_rows.append({
                    "STT": idx,
                    "Mã sản phẩm": code,
                    "Tên sản phẩm": "Không tìm thấy sản phẩm trên SICK",
                    "Hình ảnh": "N/A",
                    "Tổng quan": f"<p>Không tìm thấy thông tin sản phẩm cho mã: {code}</p>",
                    "Catalog_PDF": "N/A",
                    "URL": "N/A",
                    "Trạng thái": "Không tìm thấy"
                })

        if all_rows:
            df = pd.DataFrame(all_rows)
            columns = ["STT", "Mã sản phẩm", "Tên sản phẩm", "Hình ảnh", "Tổng quan", "Catalog_PDF", "URL", "Trạng thái"]
            available_cols = [col for col in columns if col in df.columns]
            excel_path = os.path.join(result_dir, "Du_lieu.xlsx")
            df[available_cols].to_excel(excel_path, index=False)
        else:
            self.emit_progress(100, "Không thu thập được sản phẩm nào.")
            return {
                "success": False,
                "message": "Không thu thập được dữ liệu sản phẩm nào.",
            }

        zip_name = f"{folder_name}.zip"
        zip_path = os.path.join(self.output_root, zip_name)
        self.create_zip(result_dir, zip_path)

        duration = time.time() - start_time
        mode_str = "Lite (No Media)" if not download_media else "Full"
        self.emit_progress(
            100,
            f"Hoàn thành ({mode_str}): {found_count}/{len(cleaned_codes)} mã tìm thấy ({not_found_count} không tìm thấy) trong {duration:.1f}s",
        )
        return {
            "success": True,
            "total_products": len(all_rows),
            "found_count": found_count,
            "not_found_count": not_found_count,
            "zip_file": zip_name,
            "output_folder": result_dir.replace("\\", "/"),
            "message": f"Đã cào {found_count}/{len(cleaned_codes)} mã sản phẩm thành công.",
        }

    async def run_crawling_by_codes(self, product_codes, download_media=True):
        return await asyncio.to_thread(self.run_crawling_by_codes_sync, product_codes, download_media)
