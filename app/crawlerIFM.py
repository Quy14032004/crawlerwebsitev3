"""
IFM Crawler v2 - Selenium-based (Vue.js SPA requires JS rendering)
─────────────────────────────────────────────────────────────────────
Lý do viết lại: ifm.com là Single-Page Application dùng Vue.js.
aiohttp/requests chỉ nhận HTML tĩnh ban đầu, KHÔNG có dữ liệu sản phẩm.
Phải dùng Selenium để chờ Vue render xong rồi mới scrape DOM.

Selector đúng (đã xác nhận qua browser):
  - Series links  : a.tile__link-wrapper
  - Product links : a.ifm-result-item__product-link-wrapper
  - Product code  : h2[data-test="product-id"]  hoặc  div.ifm-product-id
  - Product name  : h1[data-test="product-category"]
  - Specs table   : div[data-test="data-sheet-section"]
  - Product image : img.viewer-transition  hoặc  div.ifm-product-thumbnail img
"""

import os
import asyncio
import zipfile
import shutil
import time
import logging
from datetime import datetime
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from PIL import Image
from io import BytesIO

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
# Không còn dùng ChromeDriverManager để tránh lỗi WinError 193

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: khởi Chrome headless chống phát hiện automation
# ──────────────────────────────────────────────────────────────────────────────
def _make_driver() -> webdriver.Chrome:
    opts = Options()
    # Chế độ headless cho các bản Chrome mới
    opts.add_argument('--headless') 
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1920,1080')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    )
    
    # Kể từ Selenium 4.6+, không cần ChromeDriverManager() nữa.
    # Selenium Manager sẽ tự động tải driver phù hợp nếu không tìm thấy.
    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as e:
        logger.error(f"Lỗi khi khởi tạo driver mặc định: {e}")
        # Nếu vẫn lỗi, thử dùng Service mặc định (có thể giúp với WinError 193)
        from selenium.webdriver.chrome.service import Service
        driver = webdriver.Chrome(service=Service(), options=opts)

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    driver.implicitly_wait(5)
    return driver


# ──────────────────────────────────────────────────────────────────────────────
class IFMCrawler:
    """
    Crawler cho ifm.com (Vue.js SPA) dùng Selenium.
    API công khai của crawl_products():  (all_products, result_dir)
    """

    BASE_URL = "https://www.ifm.com"

    def __init__(self, output_root: str = None, max_workers: int = 4):
        self.output_root = output_root or os.path.join(os.getcwd(), "output_ifm")
        self.images_dir = os.path.join(self.output_root, "images")
        os.makedirs(self.images_dir, exist_ok=True)
        # Giữ max_workers nhỏ - mỗi worker 1 Chrome instance tốn RAM
        self.max_workers = min(max_workers, 4)
        self._headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            )
        }

    # ── 1. Lấy series links từ category page ─────────────────────────────────
    def _fetch_series_links(self, category_url: str, driver: webdriver.Chrome) -> list[str]:
        """Trả về list URL series từ trang danh mục."""
        links = []
        try:
            logger.info(f"[IFM] Fetching category: {category_url}")
            driver.get(category_url)
            # Đợi Vue render tile links
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'a.tile__link-wrapper'))
                )
            except TimeoutException:
                logger.warning(f"[IFM] Timeout chờ tile links tại {category_url}")

            elements = driver.find_elements(By.CSS_SELECTOR, 'a.tile__link-wrapper')
            for el in elements:
                href = el.get_attribute('href')
                if href:
                    links.append(href if href.startswith('http') else urljoin(self.BASE_URL, href))
            logger.info(f"[IFM] Tìm thấy {len(links)} series links")
        except Exception as e:
            logger.error(f"[IFM] Lỗi fetch series links: {e}")
        return list(set(links))

    # ── 2. Lấy product links từ series page (có pagination) ──────────────────
    def _fetch_product_links(self, series_url: str, driver: webdriver.Chrome) -> list[str]:
        """Trả về list URL sản phẩm từ trang series (xử lý phân trang)."""
        product_links = []
        page = 1
        MAX_PAGES = 50  # Bảo vệ vòng lặp vô tận

        while page <= MAX_PAGES:
            url = series_url if page == 1 else f"{series_url}#/best/{(page-1)*50+1}/{page*50}"
            try:
                logger.debug(f"[IFM] Series page {page}: {url}")
                driver.get(url)
                time.sleep(2)  # Đợi Vue fetch dữ liệu từ API

                # Selector chính xác từ browser inspection
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, 'a.ifm-result-item__product-link-wrapper, a[href*="/product/"]')
                        )
                    )
                except TimeoutException:
                    logger.debug(f"[IFM] Không tìm thấy sản phẩm ở trang {page}")
                    break

                elements = driver.find_elements(
                    By.CSS_SELECTOR, 'a.ifm-result-item__product-link-wrapper'
                )
                # Fallback selector nếu class thay đổi
                if not elements:
                    elements = driver.find_elements(
                        By.CSS_SELECTOR, 'a[href*="/product/"]'
                    )

                if not elements:
                    logger.debug(f"[IFM] Hết sản phẩm ở trang {page}")
                    break

                before = len(product_links)
                for el in elements:
                    href = el.get_attribute('href')
                    if href and '/product/' in href:
                        full = href if href.startswith('http') else urljoin(self.BASE_URL, href)
                        if full not in product_links:
                            product_links.append(full)

                added = len(product_links) - before
                logger.debug(f"[IFM]   +{added} sản phẩm (tổng {len(product_links)})")

                # Nếu không có thêm sản phẩm mới → đã hết trang
                if added == 0:
                    break

                # Kiểm tra nút "Next page" có disabled không
                next_btns = driver.find_elements(
                    By.CSS_SELECTOR,
                    'button[aria-label*="next"], button[aria-label*="Next"], '
                    '.ifm-pagination__next, button[class*="next"]'
                )
                if not next_btns or all(
                    not btn.is_enabled() or btn.get_attribute('disabled')
                    for btn in next_btns
                ):
                    logger.debug("[IFM] Hết trang (nút Next disabled)")
                    break

                page += 1

            except Exception as e:
                logger.error(f"[IFM] Lỗi fetch product links trang {page}: {e}")
                break

        logger.info(f"[IFM] Tổng {len(product_links)} product links từ {series_url}")
        return product_links

    # ── 3. Lấy chi tiết 1 sản phẩm ──────────────────────────────────────────
    def _fetch_product_detail(self, product_url: str) -> dict:
        """Tạo driver mới, vào trang sản phẩm, trích dữ liệu, đóng driver."""
        info = {
            'URL': product_url,
            'Mã sản phẩm': '',
            'Tên sản phẩm': '',
            'Ảnh sản phẩm': '',
            'Thông số kỹ thuật': '',
        }
        driver = None
        try:
            driver = _make_driver()
            driver.get(product_url)

            # Đợi nội dung trang load
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, 'h2[data-test="product-id"], h1, .ifm-product-id')
                    )
                )
            except TimeoutException:
                pass

            time.sleep(1)

            # 1. Mã sản phẩm
            for sel in ['h2[data-test="product-id"]', '.ifm-product-id', 'span.product-id']:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    info['Mã sản phẩm'] = el.text.strip()
                    if info['Mã sản phẩm']:
                        break
                except NoSuchElementException:
                    pass

            # Fallback: lấy từ URL (vd: /us/en/product/IEP200 → IEP200)
            if not info['Mã sản phẩm']:
                parts = product_url.rstrip('/').split('/')
                if parts:
                    info['Mã sản phẩm'] = parts[-1].upper()

            # 2. Tên sản phẩm
            for sel in ['h1[data-test="product-category"]', 'h1.ifm-product-name', 'h1']:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    cat = el.text.strip()
                    if cat:
                        info['Tên sản phẩm'] = f"{cat} {info['Mã sản phẩm']}".strip()
                        break
                except NoSuchElementException:
                    pass

            # 3. Thông số kỹ thuật
            specs_parts = []
            try:
                sheet = driver.find_element(By.CSS_SELECTOR, 'div[data-test="data-sheet-section"]')
                rows = sheet.find_elements(By.CSS_SELECTOR, 'tr')
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, 'td')
                    if len(cells) >= 2:
                        key = cells[0].text.strip()
                        val = cells[1].text.strip().replace(';', ', ')
                        if key:
                            specs_parts.append(f"{key}: {val}")
                info['Thông số kỹ thuật'] = '\n'.join(specs_parts)
            except NoSuchElementException:
                pass

            # 4. Ảnh sản phẩm
            for img_sel in [
                'img.viewer-transition',
                'div.ifm-product-thumbnail img',
                'img[class*="product"]',
                '.ifm-product-image img',
            ]:
                try:
                    img = driver.find_element(By.CSS_SELECTOR, img_sel)
                    src = img.get_attribute('src') or img.get_attribute('data-src')
                    if src:
                        if src.startswith('/'):
                            src = urljoin(self.BASE_URL, src)
                        fname = f"{info['Mã sản phẩm']}.webp" if info['Mã sản phẩm'] else None
                        if fname:
                            saved = self._download_image(src, fname)
                            if saved:
                                info['Ảnh sản phẩm'] = fname
                        break
                except NoSuchElementException:
                    pass

        except Exception as e:
            logger.error(f"[IFM] Lỗi fetch product detail {product_url}: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        return info

    # ── 4. Download & convert ảnh sang WebP ─────────────────────────────────
    def _download_image(self, url: str, filename: str) -> str | None:
        save_path = os.path.join(self.images_dir, filename)
        if os.path.exists(save_path):
            return save_path
        try:
            resp = requests.get(url, headers=self._headers, timeout=20)
            if resp.status_code == 200:
                img = Image.open(BytesIO(resp.content))
                if img.mode not in ('RGB',):
                    img = img.convert('RGB')
                img.save(save_path, 'WEBP', quality=90)
                return save_path
        except Exception as e:
            logger.error(f"[IFM] Lỗi download ảnh {url}: {e}")
        return None

    # ── 5. Pipeline chính (sync wrapper) ─────────────────────────────────────
    def crawl_products(self, url_list: list) -> tuple[list, str]:
        """
        Crawl toàn bộ pipeline. Trả về (all_products, result_dir).
        Tương thích với routes.py hiện tại.
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        result_dir = os.path.join(self.output_root, f'ifm_result_{timestamp}')
        os.makedirs(result_dir, exist_ok=True)
        self.images_dir = os.path.join(result_dir, 'images')
        os.makedirs(self.images_dir, exist_ok=True)

        all_products: list[dict] = []

        for category_url in url_list:
            logger.info(f"IFM crawling: {category_url}")
            driver = None
            try:
                driver = _make_driver()

                # Bước 1: series links
                print("1. Fetching series links...")
                series_links = self._fetch_series_links(category_url, driver)
                print(f"Found {len(series_links)} series links.")

                # Bước 2: product links
                print("2. Fetching product links from all series...")
                product_links: list[str] = []
                for s_url in series_links:
                    links = self._fetch_product_links(s_url, driver)
                    product_links.extend(links)
                product_links = list(set(product_links))
                print(f"Found total {len(product_links)} product links.")

            except Exception as e:
                logger.error(f"[IFM] Lỗi giai đoạn 1-2 với {category_url}: {e}")
                product_links = []
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass

            if not product_links:
                print("No product links found for this category.")
                continue

            # Bước 3: chi tiết sản phẩm (song song, mỗi worker 1 driver)
            print(f"3. Fetching product details and images concurrent ({self.max_workers} workers)...")
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {pool.submit(self._fetch_product_detail, url): url for url in product_links}
                for i, future in enumerate(as_completed(futures), 1):
                    url = futures[future]
                    try:
                        data = future.result()
                        if data and data.get('Mã sản phẩm'):
                            all_products.append(data)
                            if i % 10 == 0 or i == len(product_links):
                                print(f"  [{i}/{len(product_links)}] done — {len(all_products)} products collected")
                    except Exception as e:
                        logger.error(f"[IFM] Lỗi xử lý {url}: {e}")

        # Lưu Excel
        if all_products:
            df = pd.DataFrame(all_products)
            excel_path = os.path.join(result_dir, 'ifm_products.xlsx')
            df.to_excel(excel_path, index=False)
            logger.info(f"[IFM] Saved {len(all_products)} products → {excel_path}")
        else:
            print("No data collected.")

        # Tạo ZIP
        zip_path = result_dir + '.zip'
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(result_dir):
                    for file in files:
                        fp = os.path.join(root, file)
                        arcname = os.path.relpath(fp, os.path.dirname(result_dir))
                        zf.write(fp, arcname)
        except Exception as e:
            logger.error(f"[IFM] Lỗi tạo ZIP: {e}")

        return all_products, result_dir

    # ── Giữ tương thích run_crawling (không dùng nữa nhưng import an toàn) ──
    async def run_crawling(self, category_url: str) -> list:
        """Async wrapper để code cũ không bị lỗi (delegate sang sync)."""
        products, _ = self.crawl_products([category_url])
        return products


if __name__ == "__main__":
    crawler = IFMCrawler()
    products, out = crawler.crawl_products(["https://www.ifm.com/us/en/category/200_010_010"])
    print(f"Done. {len(products)} products saved to {out}")
