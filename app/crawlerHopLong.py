"""
HopLong Product Crawler - CÃ o dá»¯ liá»‡u sáº£n pháº©m tá»« hoplongtech.com
YÃªu cáº§u: Láº¥y TÃªn sáº£n pháº©m, MÃ£ sáº£n pháº©m, ThÃ´ng sá»‘ ká»¹ thuáº­t (toÃ n bá»™), HÃ£ng. 
- DÃ¹ng Selenium Ä‘á»ƒ load danh má»¥c, Ã¡p dá»¥ng bá»™ lá»c hÃ£ng
- Äa luá»“ng khi cÃ o chi tiáº¿t sáº£n pháº©m báº±ng requests
- Xuáº¥t Excel theo Danh má»¥c + HÃ£ng: "<Danh má»¥c> <HÃ£ng>.xlsx"
- ThÃªm hÃ ng Copyright vÃ o cuá»‘i má»—i báº£ng thÃ´ng sá»‘ ká»¹ thuáº­t
"""

import os
import re
import time
import json
import asyncio
import random
import logging
import threading
import concurrent.futures
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import pandas as pd

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

from app import utils, socketio


logger = logging.getLogger(__name__)
if not logger.handlers:
    os.makedirs('logs', exist_ok=True)
    fh = logging.FileHandler('logs/hoplong.log', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)


class HopLongCrawler:
    """Crawler dÃ nh cho hoplongtech.com"""

    def __init__(self, output_root: str | None = None, max_workers: int = 10, socketio_instance=None):
        self.base_url = "https://hoplongtech.com"
        self.output_root = output_root or os.path.join(os.getcwd(), "output_hoplong")
        os.makedirs(self.output_root, exist_ok=True)

        self.max_workers = max_workers
        self.socketio = socketio_instance or socketio

        # requests session - enhanced vá»›i connection pooling vÃ  retry
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        })
        
        # Connection pooling vÃ  retry configuration
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=500, pool_maxsize=500)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # selenium options - enhanced for stability
        self.chrome_options = Options()
        self.chrome_options.add_argument('--headless')
        self.chrome_options.add_argument('--no-sandbox')
        self.chrome_options.add_argument('--disable-dev-shm-usage')
        self.chrome_options.add_argument('--window-size=1920,1080')
        self.chrome_options.add_argument('--disable-gpu')
        self.chrome_options.add_argument('--log-level=3')
        # Anti-detection vÃ  stability flags
        self.chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        self.chrome_options.add_argument('--disable-extensions')
        self.chrome_options.add_argument('--disable-plugins')
        # NOTE: Bá» --disable-javascript vÃ¬ cáº§n JavaScript cho Alpine.js
        # self.chrome_options.add_argument('--disable-images')  # Keep images for better interaction
        self.chrome_options.add_argument('--no-first-run')
        self.chrome_options.add_argument('--no-default-browser-check')
        self.chrome_options.add_argument('--disable-web-security')
        self.chrome_options.add_argument('--allow-running-insecure-content')
        self.chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        # Alpine.js support
        self.chrome_options.add_argument('--enable-javascript')
        self.chrome_options.add_argument('--allow-scripts')
        # Network optimization
        self.chrome_options.add_argument('--aggressive-cache-discard')
        self.chrome_options.add_argument('--memory-pressure-off')
        # Additional User-Agent Ä‘á»ƒ trÃ¡nh detection
        self.chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        self.chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        self.chrome_options.add_experimental_option('useAutomationExtension', False)

        # stats
        self.stats = {
            "categories_processed": 0,
            "brands_processed": 0,
            "products_found": 0,
            "products_processed": 0,
            "errors": 0,
        }

    def emit_progress(self, percent: int | float, message: str, detail: str = "") -> None:
        data = {'percent': percent, 'message': message, 'detail': detail}
        try:
            if self.socketio:
                self.socketio.emit('progress_update', data)
            else:
                print(f"[{percent}%] {message} - {detail}")
        except Exception:
            pass

    def get_driver(self, retries: int = 3):
        """Khá»Ÿi táº¡o WebDriver vá»›i retry logic vÃ  improved error handling"""
        def _create_driver():
            try:
                driver = webdriver.Chrome(options=self.chrome_options)
                driver.implicitly_wait(10)
                # Set timeouts
                driver.set_page_load_timeout(60)
                driver.set_script_timeout(30)
                
                # Anti-detection script
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                return driver
            except Exception as e:
                logger.error(f"Lá»—i khá»Ÿi táº¡o WebDriver: {e}")
                raise
        
        try:
            return self.retry_with_backoff(_create_driver, max_retries=retries, base_delay=2.0)
        except Exception as e:
            logger.error(f"KhÃ´ng thá»ƒ khá»Ÿi táº¡o WebDriver sau {retries} láº§n thá»­: {e}")
            raise

    def close_driver(self, driver):
        try:
            driver.quit()
        except Exception:
            pass
    
    def check_website_health(self, url: str, timeout: int = 15) -> bool:
        """
        Kiá»ƒm tra káº¿t ná»‘i Ä‘áº¿n website trÆ°á»›c khi thá»±c hiá»‡n operations
        """
        try:
            response = self.session.head(url, timeout=timeout)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Website health check tháº¥t báº¡i cho {url}: {e}")
            return False
    
    def retry_with_backoff(self, func, max_retries: int = 3, base_delay: float = 1.0):
        """
        Retry decorator vá»›i exponential backoff
        """
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                
                delay = base_delay * (2 ** attempt)
                error_type = type(e).__name__
                logger.warning(f"Attempt {attempt + 1} tháº¥t báº¡i ({error_type}): {e}. Thá»­ láº¡i sau {delay}s...")
                
                # Emit progress cho user biáº¿t Ä‘ang retry
                if hasattr(self, 'emit_progress'):
                    self.emit_progress(
                        0, 
                        f"Lá»—i káº¿t ná»‘i, Ä‘ang thá»­ láº¡i...", 
                        f"Láº§n thá»­ {attempt + 1}/{max_retries}"
                    )
                
                time.sleep(delay)
        
        raise Exception(f"Tháº¥t báº¡i sau {max_retries} láº§n thá»­")

    # ======= CATEGORY / BRAND HELPERS =======
    def fetch_categories_via_selenium(self) -> list[dict]:
        """Tráº£ vá» danh sÃ¡ch danh má»¥c á»Ÿ trang chá»§: [{name, url}]"""
        driver = self.get_driver()
        try:
            driver.set_page_load_timeout(40)
            driver.get(self.base_url)
            # Thá»­ selector chÃ­nh
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, '.category-navigation__list ul li a'))
                )
                candidates = driver.find_elements(By.CSS_SELECTOR, '.category-navigation__list ul li a')
            except Exception:
                # Thá»­ selector dá»± phÃ²ng: táº¥t cáº£ link category
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href^="/category/"]'))
                )
                candidates = driver.find_elements(By.CSS_SELECTOR, 'a[href^="/category/"]')

            results = []
            for a in candidates:
                try:
                    href = a.get_attribute('href') or ''
                    text = (a.text or '').strip()
                    if href and text:
                        if href.startswith('/'):
                            href = urljoin(self.base_url, href)
                        # Chá»‰ láº¥y Ä‘Æ°á»ng dáº«n category
                        if '/category/' in href:
                            results.append({"name": text, "url": href})
                except Exception:
                    continue
            # unique by url
            seen = set()
            unique = []
            for item in results:
                if item['url'] not in seen:
                    unique.append(item)
                    seen.add(item['url'])
            if unique:
                return unique
            # Fallback láº§n cuá»‘i báº±ng requests
            return self.fetch_categories_via_requests()
        except Exception:
            # Fallback requests náº¿u cÃ³ báº¥t ká»³ lá»—i nÃ o
            return self.fetch_categories_via_requests()
        finally:
            self.close_driver(driver)

    def fetch_categories_via_requests(self) -> list[dict]:
        """Fallback: láº¥y danh má»¥c tá»« trang /category báº±ng requests + BeautifulSoup."""
        try:
            url = urljoin(self.base_url, '/category')
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            results: list[dict] = []
            # Láº¥y táº¥t cáº£ link category há»£p lá»‡
            for a in soup.select('a[href^="/category/"]'):
                href = a.get('href') or ''
                text = (a.get_text() or '').strip()
                if not href or not text:
                    continue
                if href.startswith('/'):
                    href = urljoin(self.base_url, href)
                # trÃ¡nh cÃ¡c link rá»—ng, láº·p
                if '/category/' in href and len(text) >= 2:
                    results.append({"name": text, "url": href})
            # unique
            seen = set()
            unique = []
            for item in results:
                if item['url'] not in seen:
                    unique.append(item)
                    seen.add(item['url'])
            return unique
        except Exception as e:
            logger.error(f"Fallback categories via requests lá»—i: {e}")
            return []

    def fetch_brands_for_category(self, category_url: str) -> list[str]:
        """STRICT brand extraction - Chá»‰ láº¥y brands thá»±c sá»± cÃ³ trÃªn trang."""
        driver = None
        brands: list[str] = []
        
        logger.info(f"ðŸ” FETCHING BRANDS for category: {category_url}")
        
        # Kiá»ƒm tra website health trÆ°á»›c khi dÃ¹ng Selenium
        if not self.check_website_health(category_url):
            logger.warning(f"Website khÃ´ng sáºµn sÃ ng, chuyá»ƒn sang fallback method")
            return self.fetch_brands_via_requests(category_url)
        
        def _fetch_with_selenium():
            nonlocal driver, brands
            driver = self.get_driver()
            driver.get(category_url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            time.sleep(1.5)
            
            # Extract brands vÃ  validation
            extracted_brands = self._extract_brands_from_page(driver)
            
            # STRICT validation - check only valid brands for this category
            logger.info(f"ðŸ“‹ Raw extracted brands: {extracted_brands}")
            
            # Filter out invalid brands vÃ  chá»‰ giá»¯ brands cÃ³ trÃªn trang
            valid_brands = []
            for brand in extracted_brands:
                # Basic validation
                if (brand and len(brand.strip()) >= 2 and 
                    not any(word in brand.lower() for word in ['chá»n', 'tÃ¬m', 'filter', 'search', 'all'])):
                    valid_brands.append(brand.strip())
            
            logger.info(f"âœ… VALID brands after filtering: {valid_brands}")
            return valid_brands
        
        try:
            brands = self.retry_with_backoff(_fetch_with_selenium, max_retries=3, base_delay=2.0)
            
            # Final validation check
            if brands:
                logger.info(f"ðŸŽ¯ FINAL BRANDS for category: {brands}")
                # Äáº£m báº£o chá»‰ tráº£ vá» brands thá»±c sá»± cÃ³ trÃªn trang
                return [b for b in brands if b and len(b.strip()) >= 2]  # No limit â€” allow all brands
            else:
                logger.warning("âš ï¸ No brands found with Selenium, trying fallback...")
                return self.fetch_brands_via_requests(category_url)
                
        except WebDriverException as e:
            if "ERR_CONNECTION_RESET" in str(e) or "net::" in str(e):
                logger.error(f"Lá»—i káº¿t ná»‘i máº¡ng Selenium (ERR_CONNECTION_RESET): {e}")
                self.emit_progress(0, "Lá»—i káº¿t ná»‘i", "Chuyá»ƒn sang phÆ°Æ¡ng phÃ¡p backup...")
            else:
                logger.error(f"Lá»—i WebDriver: {e}")
            return self.fetch_brands_via_requests(category_url)
        except Exception as e:
            logger.error(f"Lá»—i khÃ´ng xÃ¡c Ä‘á»‹nh khi láº¥y brands báº±ng Selenium: {e}")
            return self.fetch_brands_via_requests(category_url)
        finally:
            if driver:
                self.close_driver(driver)
    
    def _extract_brands_from_page(self, driver) -> list[str]:
        """TrÃ­ch xuáº¥t brands tá»« trang Ä‘Ã£ load vá»›i Alpine.js support"""
        brands: list[str] = []

        # Step 1: Má»Ÿ filter panel 
        self._open_brand_filter_panel(driver)
        time.sleep(1.0)  # Wait for Alpine.js animations

        # Step 2: Wait for dropdown options to be visible
        try:
            # Wait for filter list options to appear vá»›i Alpine.js
            WebDriverWait(driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "ul.filter-list__options li") or
                         d.find_elements(By.XPATH, "//div[contains(@class,'filter-box__item')]//li")
            )
            time.sleep(0.5)
        except Exception:
            logger.warning("Timeout waiting for brand filter options")

        # Step 3: Parse brands tá»« new Alpine.js structure
        selectors_to_try = [
            # New Alpine.js selectors
            {
                "name": "Alpine.js filter-list__options",
                "xpath": "//ul[contains(@class,'filter-list__options')]//li[contains(@wire:click, 'brand')]"
            },
            {
                "name": "Alpine.js filter-box__item li",
                "xpath": "//div[contains(@class,'filter-box__item')]//li[text()]"
            },
            # Old selectors fallback
            {
                "name": "Old filter structure",
                "xpath": "//div[contains(@class,'filter-list__button')]//span[contains(.,'Chá»n hÃ£ng sáº£n xuáº¥t')]/ancestor::*[contains(@class,'filter-list__button')][1]/following-sibling::*//li"
            },
            {
                "name": "General filter list",
                "xpath": "//ul[contains(@class,'filter') or contains(@class,'list')]//li[text()]"
            }
        ]

        for selector_info in selectors_to_try:
            try:
                nodes = driver.find_elements(By.XPATH, selector_info["xpath"])
                logger.debug(f"TÃ¬m tháº¥y {len(nodes)} nodes vá»›i selector: {selector_info['name']}")
                
                for node in nodes:
                    brand_text = (node.text or '').strip()
                    if brand_text:
                        # Clean up brand text (remove extra content)
                        brand_text = brand_text.split('\n')[0].strip()  # Take first line only
                        
                        # Validate brand name
                        if (2 <= len(brand_text) <= 40 and 
                            'HÃ£ng' not in brand_text and 
                            'TÃ¬m kiáº¿m' not in brand_text and
                            'Chá»n' not in brand_text and
                            brand_text not in brands):
                            brands.append(brand_text)
                            logger.debug(f"Added brand: {brand_text}")
                
                if brands:
                    logger.info(f"Successfully extracted {len(brands)} brands using {selector_info['name']}")
                    break
                    
            except Exception as e:
                logger.debug(f"Selector {selector_info['name']} failed: {e}")
                continue

        # Step 4: Fallback - try to get any visible text elements
        if not brands:
            logger.warning("No brands found with primary methods, trying fallback...")
            try:
                # Look for any li elements with brand-like text
                all_li = driver.find_elements(By.TAG_NAME, "li")
                for li in all_li:
                    if li.is_displayed():
                        text = (li.text or '').strip()
                        # Check if it looks like a brand name
                        if (2 <= len(text) <= 40 and 
                            text.isalpha() and 
                            'HÃ£ng' not in text and 
                            'TÃ¬m kiáº¿m' not in text and
                            text not in brands):
                            brands.append(text)
                            if len(brands) >= 10:  # Limit fallback results
                                break
            except Exception as e:
                logger.debug(f"Fallback extraction failed: {e}")

        logger.info(f"Final extracted brands: {brands}")
        return brands

    def fetch_brands_via_requests(self, category_url: str) -> list[str]:
        """Fallback: cá»‘ gáº¯ng tÃ¬m danh sÃ¡ch hÃ£ng tá»« HTML (category hoáº·c /brands)."""
        brands: list[str] = []
        
        def _fetch_brands_from_category():
            resp = self.session.get(category_url, timeout=45, allow_redirects=True)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        
        try:
            # 1) thá»­ ngay trÃªn trang danh má»¥c vá»›i retry
            soup = self.retry_with_backoff(_fetch_brands_from_category, max_retries=3, base_delay=1.5)

            # tÃ¬m cá»¥m gáº§n chá»¯ 'Chá»n hÃ£ng sáº£n xuáº¥t'
            candidates = []
            for el in soup.find_all(text=re.compile(r'Chá»n\s*hÃ£ng\s*sáº£n\s*xuáº¥t', re.IGNORECASE)):
                parent = el.parent
                # láº¥y cÃ¡c label/span/li gáº§n Ä‘Ã³
                for lbl in parent.find_all_next(['label', 'span', 'li'], limit=200):
                    txt = lbl.get_text(strip=True)
                    if txt:
                        candidates.append(txt)
            # lá»c
            for t in candidates:
                if 2 <= len(t) <= 40 and 'HÃ£ng' not in t and 'TÃ¬m kiáº¿m' not in t:
                    if t not in brands:
                        brands.append(t)

            if brands:
                return brands

            # 2) fallback toÃ n site brands
            def _fetch_from_brands_page():
                brands_url = urljoin(self.base_url, '/brands')
                resp2 = self.session.get(brands_url, timeout=45, allow_redirects=True)
                resp2.raise_for_status()
                return BeautifulSoup(resp2.text, 'html.parser')
            
            try:
                soup2 = self.retry_with_backoff(_fetch_from_brands_page, max_retries=2, base_delay=1.0)
                names = []
                for a in soup2.select('a[href^="/brands/"], a[href*="/brands/"]'):
                    txt = (a.get_text() or '').strip()
                    if txt:
                        names.append(txt)
                for t in names:
                    if 2 <= len(t) <= 40 and t not in brands:
                        brands.append(t)
            except Exception as fallback_error:
                logger.warning(f"Fallback brands page cÅ©ng tháº¥t báº¡i: {fallback_error}")
            
            # KhÃ´ng giá»›i háº¡n: tráº£ vá» táº¥t cáº£ brands tÃ¬m Ä‘Æ°á»£c
            return brands
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Lá»—i káº¿t ná»‘i requests khi láº¥y brands: {e}")
            self.emit_progress(0, "Lá»—i káº¿t ná»‘i", "KhÃ´ng thá»ƒ káº¿t ná»‘i Ä‘áº¿n website")
            return []
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout khi láº¥y brands: {e}")
            self.emit_progress(0, "Háº¿t thá»i gian chá»", "Website pháº£n há»“i cháº­m")
            return []
        except Exception as e:
            logger.error(f"Fallback brands via requests lá»—i: {e}")
            return brands

    def fetch_subcategories_for_category(self, category_url: str) -> list[dict]:
        """
        Láº¥y danh sÃ¡ch danh má»¥c con tá»« trang danh má»¥c chÃ­nh
        Tráº£ vá» [{name, url, product_count}]
        """
        def _fetch_with_requests():
            resp = self.session.get(category_url, timeout=45, allow_redirects=True)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        
        subcategories: list[dict] = []
        try:
            # Sá»­ dá»¥ng requests Ä‘á»ƒ láº¥y HTML
            soup = self.retry_with_backoff(_fetch_with_requests, max_retries=3, base_delay=1.5)
            
            # TÃ¬m container chá»©a subcategories 
            cate_box = soup.select_one('div.cate-box.flex')
            if not cate_box:
                logger.warning(f"KhÃ´ng tÃ¬m tháº¥y cate-box container trong {category_url}")
                return []
            
            # Parse tá»«ng subcategory item
            for item in cate_box.select('div.cate-list__item.flex-center-left'):
                try:
                    # Láº¥y link vÃ  title tá»« tháº» a
                    link_el = item.select_one('div.cate-name a')
                    if not link_el:
                        continue
                        
                    href = link_el.get('href', '').strip()
                    title = link_el.get('title', '').strip()
                    
                    if not href or not title:
                        continue
                    
                    # Äáº£m báº£o URL Ä‘áº§y Ä‘á»§
                    if href.startswith('/'):
                        href = urljoin(self.base_url, href)
                    
                    # Láº¥y sá»‘ lÆ°á»£ng sáº£n pháº©m
                    count_el = item.select_one('div.cate-name span')
                    product_count = 0
                    if count_el:
                        count_text = count_el.get_text(strip=True)
                        # Parse sá»‘ tá»« text nhÆ° "(8 Sáº£n pháº©m)"
                        import re
                        match = re.search(r'\((\d+)', count_text)
                        if match:
                            product_count = int(match.group(1))
                    
                    subcategories.append({
                        'name': title,
                        'url': href,
                        'product_count': product_count
                    })
                    
                except Exception as e:
                    logger.debug(f"Lá»—i parse subcategory item: {e}")
                    continue
            
            logger.info(f"TÃ¬m tháº¥y {len(subcategories)} subcategories tá»« {category_url}")
            return subcategories
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Lá»—i káº¿t ná»‘i khi láº¥y subcategories: {e}")
            self.emit_progress(0, "Lá»—i káº¿t ná»‘i", "KhÃ´ng thá»ƒ káº¿t ná»‘i Ä‘á»ƒ láº¥y danh má»¥c con")
            return []
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout khi láº¥y subcategories: {e}")
            self.emit_progress(0, "Háº¿t thá»i gian chá»", "Website pháº£n há»“i cháº­m")
            return []
        except Exception as e:
            logger.error(f"Lá»—i khi láº¥y subcategories tá»« {category_url}: {e}")
            return []

    def _open_brand_filter_panel(self, driver) -> None:
        """Click Ä‘á»ƒ má»Ÿ pháº§n 'Chá»n hÃ£ng sáº£n xuáº¥t' trong filter vá»›i Alpine.js support."""
        try:
            # Step 1: Click vÃ o nÃºt "Bá»™ lá»c" chÃ­nh Ä‘á»ƒ má»Ÿ filter panel
            try:
                filter_btn = driver.find_element(By.CSS_SELECTOR, "div.filter-btn.responsive")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", filter_btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", filter_btn)
                logger.info("ÄÃ£ click vÃ o nÃºt Bá»™ lá»c chÃ­nh")
                time.sleep(1.0)  # Wait for Alpine.js to show panel
            except Exception as e:
                logger.debug(f"KhÃ´ng tÃ¬m tháº¥y filter button chÃ­nh: {e}")
                # Fallback: thá»­ old selector
                try:
                    filter_toggle = driver.find_element(By.XPATH, "//*[normalize-space()='Bá»™ lá»c' or contains(.,'Bá»™ lá»c')]")
                    driver.execute_script("arguments[0].click();", filter_toggle)
                    time.sleep(0.5)
                except Exception:
                    pass

            # Step 2: Click vÃ o "Chá»n hÃ£ng sáº£n xuáº¥t" trong filter panel
            selectors_to_try = [
                # New selectors cho Alpine.js structure
                "//div[contains(@class,'filter-box__item')]//h4[contains(.,'HÃ£ng sáº£n xuáº¥t')]/following-sibling::*//span[contains(.,'Chá»n hÃ£ng sáº£n xuáº¥t')]",
                "//div[contains(@class,'filter-box__item')]//span[contains(.,'Chá»n hÃ£ng sáº£n xuáº¥t')]",
                # Old selectors fallback
                "//div[contains(@class,'filter-list__button')]//span[contains(.,'Chá»n hÃ£ng sáº£n xuáº¥t')]",
                "//*[contains(.,'Chá»n hÃ£ng sáº£n xuáº¥t')]"
            ]
            
            for selector in selectors_to_try:
                try:
                    button = driver.find_element(By.XPATH, selector)
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", button)
                    logger.info(f"ÄÃ£ click vÃ o 'Chá»n hÃ£ng sáº£n xuáº¥t' vá»›i selector: {selector}")
                    time.sleep(0.8)  # Wait for Alpine.js dropdown - optimized
                    return
                except Exception:
                    continue
            
            logger.warning("KhÃ´ng thá»ƒ click vÃ o 'Chá»n hÃ£ng sáº£n xuáº¥t'")
            
        except Exception as e:
            logger.error(f"Lá»—i khi má»Ÿ brand filter panel: {e}")

    def _apply_brand_filter(self, driver, brand_name: str) -> bool:
        """STRICT brand filter - Ãp dá»¥ng filter theo brand vá»›i smart waits."""
        logger.info(f"ðŸŽ¯ APPLYING STRICT BRAND FILTER: {brand_name}")
        
        try:
            # Step 1: Clear existing filters trÆ°á»›c khi Ã¡p dá»¥ng
            try:
                clear_buttons = driver.find_elements(By.CSS_SELECTOR, '.filter-selected .button-selected .close, .filter-selected .remove, .clear-filter')
                for btn in clear_buttons:
                    try:
                        driver.execute_script("arguments[0].click();", btn)
                        # OPTIMIZED: Smart wait instead of fixed sleep
                        WebDriverWait(driver, 3).until(
                            lambda d: not d.find_elements(By.CSS_SELECTOR, '.filter-selected .button-selected')
                        )
                        logger.info("ðŸ§¹ Cleared existing filter")
                    except:
                        pass
            except:
                pass

            # Step 2: má»Ÿ panel chá»n hÃ£ng vá»›i smart wait
            self._open_brand_filter_panel(driver)
            # OPTIMIZED: Wait for panel to be actually visible instead of fixed time
            try:
                WebDriverWait(driver, 5).until(
                    EC.any_of(
                        EC.visibility_of_element_located((By.XPATH, "//div[contains(@class,'filter-box__item')]//li")),
                        EC.visibility_of_element_located((By.XPATH, "//ul[contains(@class,'filter-list__options')]//li"))
                    )
                )
            except TimeoutException:
                logger.warning("Panel might not be visible, continuing anyway")
                time.sleep(0.5)  # Minimal fallback wait
            
            # Step 3: TÃ¬m vÃ  click brand vá»›i EXACT match only
            selectors_to_try = [
                # EXACT match selectors only - NO partial match
                f"//div[contains(@class,'filter-box__item')]//li[normalize-space()='{brand_name}']",
                f"//ul[contains(@class,'filter-list__options')]//li[normalize-space()='{brand_name}']",
                f"//*[self::label or self::span][normalize-space()='{brand_name}']",
            ]
            
            item = None
            used_selector = None
            
            # Debug: Log available brands first
            try:
                all_brand_elements = driver.find_elements(By.XPATH, "//div[contains(@class,'filter-box__item')]//li | //ul[contains(@class,'filter-list__options')]//li")
                available_brands = [elem.text.strip() for elem in all_brand_elements if elem.is_displayed() and elem.text.strip()]
                logger.info(f"ðŸ“‹ Available brands on page: {available_brands}")
                
                if brand_name not in available_brands:
                    logger.error(f"âŒ BRAND '{brand_name}' NOT AVAILABLE on page! Available: {available_brands}")
                    return False
            except Exception as e:
                logger.warning(f"Cannot get available brands: {e}")
            
            for selector in selectors_to_try:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    logger.debug(f"Selector '{selector}' tÃ¬m tháº¥y {len(elements)} elements")
                    
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element_text = element.text.strip()
                            
                            # STRICT EXACT MATCH ONLY
                            if element_text.lower() == brand_name.lower():
                                item = element
                                used_selector = selector
                                logger.info(f"âœ… EXACT MATCH found for brand '{brand_name}' using selector: {selector}")
                                break
                            else:
                                logger.debug(f"âŒ REJECTED element text: '{element_text}' (not exact match)")
                    
                    if item:
                        break
                        
                except Exception as e:
                    logger.debug(f"Selector '{selector}' failed: {e}")
                    continue
            
            if not item:
                logger.error(f"âŒ CRITICAL: Cannot find EXACT MATCH element for brand '{brand_name}'")
                return False
            
            # Step 4: Click element vá»›i verification
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", item)
                # OPTIMIZED: Brief wait for scroll completion
                time.sleep(0.2)
                driver.execute_script("arguments[0].click();", item)
                logger.info(f"ðŸ–±ï¸ Clicked brand '{brand_name}' element")
            except Exception as e:
                logger.error(f"âŒ Failed to click brand element: {e}")
                return False
                
            # OPTIMIZED: Smart wait for filter application instead of fixed 2.0s
            try:
                WebDriverWait(driver, 8).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '.filter-selected .button-selected span')),
                        EC.presence_of_element_located((By.CSS_SELECTOR, '.filter-selected .chip span')),
                        EC.url_changes(driver.current_url)
                    )
                )
                logger.info("âœ… Filter application detected")
            except TimeoutException:
                logger.warning("Filter application timeout, continuing with verification")
                time.sleep(1.0)  # Minimal fallback

            # Step 5: STRICT verification - kiá»ƒm tra filter Ä‘Ã£ Ä‘Æ°á»£c Ã¡p dá»¥ng
            verification_passed = False
            try:
                # Check selected chips
                chips = driver.find_elements(By.CSS_SELECTOR, '.filter-selected .button-selected span, .filter-selected .chip span, .active-filter span')
                selected_chips = [c.text.strip() for c in chips if c.text.strip()]
                logger.info(f"ðŸ” Selected filter chips: {selected_chips}")
                
                # EXACT match verification
                verification_passed = any(brand_name.lower() == chip.lower() for chip in selected_chips)
                
                if verification_passed:
                    logger.info(f"âœ… BRAND FILTER VERIFIED: '{brand_name}' is active")
                else:
                    logger.error(f"âŒ BRAND FILTER VERIFICATION FAILED: '{brand_name}' not in active chips: {selected_chips}")
                    
            except Exception as e:
                logger.error(f"âŒ Brand filter verification failed: {e}")
                verification_passed = False

            # Step 6: Additional verification - check URL or page content
            if verification_passed:
                try:
                    current_url = driver.current_url
                    if 'filter' in current_url or '?' in current_url:
                        logger.info(f"ðŸ”— URL indicates filter applied: {current_url}")
                    
                    # OPTIMIZED: Smart wait for filtered results instead of fixed 1.5s
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.any_of(
                                EC.staleness_of(driver.find_element(By.TAG_NAME, "body")),
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".product-list__item"))
                            )
                        )
                        logger.info("ðŸ“¦ Filtered results loaded")
                    except TimeoutException:
                        logger.debug("Results loading timeout, using minimal wait")
                        time.sleep(0.5)
                    
                except Exception as e:
                    logger.debug(f"URL verification failed: {e}")
            
            return verification_passed
            
        except Exception as e:
            logger.error(f"âŒ CRITICAL ERROR in _apply_brand_filter for '{brand_name}': {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _collect_product_links(self, driver) -> list[str]:
        """Thu tháº­p toÃ n bá»™ link sáº£n pháº©m (cÃ³ thá»ƒ pháº£i scroll/pagination)."""
        links: list[str] = []

        def snapshot() -> set[str]:
            result = set()
            
            # Selectors dá»±a trÃªn HTML structure thá»±c táº¿
            selectors_to_try = [
                # ChÃ­nh xÃ¡c tá»« HTML user cung cáº¥p
                ".product-list__item .thumbnail a",     # Main selector tá»« HTML
                ".product-list__item .content h3 a",   # Alternative tá»« content
                ".product-list__item a[href*='/products/']",  # Direct products links
                "a[href^='/products/']",                # Links báº¯t Ä‘áº§u /products/
                "a[href*='/products/']",                # Links chá»©a /products/
                ".grid-list a[href*='/products/']",     # Trong grid-list container
                
                # Fallback patterns  
                "a[href^='/product/']",         # Original pattern
                "a[href*='/product/']",         # Contains /product/
                "a[href^='/san-pham/']",        # Vietnamese pattern
                "a[href*='/san-pham/']",        # Contains /san-pham/
                ".product-item a",              # Product item links
                ".product-card a",              # Product card links  
                ".product-list a",              # Product list links
                "a.product-link",               # Product link class
                ".col-product a",               # Column product links
                ".grid-item a",                 # Grid item links
                ".list-item a",                 # List item links
                "div[class*='product'] a",      # Divs with product class
            ]
            
            for selector in selectors_to_try:
                try:
                    items = driver.find_elements(By.CSS_SELECTOR, selector)
                    logger.debug(f"ðŸ” Selector '{selector}' tÃ¬m tháº¥y {len(items)} elements")
                    
                    for a in items:
                        href = a.get_attribute('href') or ''
                        title = a.get_attribute('title') or a.text or ''
                        
                        if href:
                            # Optimize logging - chá»‰ log khi debug mode
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug(f"  Found link: {href} | Title: {title[:50]}")
                            
                            # Validate product URL patterns - Æ°u tiÃªn /products/
                            is_valid = False
                            if '/products/' in href:  # Priority pattern
                                is_valid = True
                            elif any(pattern in href for pattern in ['/product/', '/san-pham/']):
                                is_valid = True
                            elif any(keyword in href.lower() for keyword in ['item', 'detail', 'view']) and 'hoplongtech.com' in href:
                                is_valid = True
                                
                            if is_valid:
                                # Ensure full URL vÃ  chÃ­nh xÃ¡c domain
                                if href.startswith('/'):
                                    href = urljoin(self.base_url, href)
                                elif not href.startswith('http'):
                                    continue
                                
                                # Chá»‰ láº¥y links tá»« hoplongtech.com
                                if 'hoplongtech.com' in href:
                                    result.add(href)
                                    if logger.isEnabledFor(logging.DEBUG):
                                        logger.debug(f"    âœ… Added to result set")
                                elif logger.isEnabledFor(logging.DEBUG):
                                    logger.debug(f"    âŒ Skipped - wrong domain: {href}")
                                    
                except Exception as e:
                    logger.debug(f"âŒ Selector '{selector}' failed: {e}")
                    continue
            
            # Enhanced fallback vá»›i detailed logging
            if not result:
                logger.warning("ðŸ”„ KhÃ´ng tÃ¬m tháº¥y product links vá»›i selectors, thá»­ comprehensive fallback...")
                try:
                    all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
                    logger.info(f"ðŸ” Analyzing {len(all_links)} total links...")
                    
                    sample_links = []
                    for i, a in enumerate(all_links):
                        href = a.get_attribute('href') or ''
                        if href:
                            if i < 10:  # Log first 10 for debugging
                                sample_links.append(href)
                            
                            if (any(pattern in href for pattern in ['/products/', '/product/', '/san-pham/']) and 
                                'hoplongtech.com' in href):
                                if href.startswith('/'):
                                    href = urljoin(self.base_url, href)
                                result.add(href)
                    
                    logger.debug(f"Sample links found: {sample_links}")
                            
                except Exception as e:
                    logger.error(f"âŒ Comprehensive fallback failed: {e}")
            
            logger.info(f"ðŸ“¦ Total unique product links found: {len(result)}")
            if result:
                logger.info(f"âœ… Sample valid product links: {list(result)[:5]}")
            else:
                logger.warning("âŒ No product links found - may need to debug page structure")
            
            return result

        seen = snapshot()
        links = list(seen)
        logger.info(f"ðŸ” Initial scan found {len(links)} product links")

        # OPTIMIZED: Smart scroll vá»›i dynamic detection
        if links:  # Only scroll if we found some links initially
            logger.info("ðŸ”„ Smart scrolling to load more products...")
            stagnant_rounds = 0
            last_scroll_position = 0
            
            for scroll_round in range(8):  # Reduced from 10 to 8
                # Get current scroll position
                current_position = driver.execute_script("return window.pageYOffset;")
                
                # Smart scroll with smooth scrolling
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                
                # OPTIMIZED: Adaptive wait based on content loading
                try:
                    # Wait for scroll completion or new content
                    WebDriverWait(driver, 3).until(
                        lambda d: d.execute_script("return window.pageYOffset;") > current_position
                    )
                    # Brief additional wait for dynamic content
                    time.sleep(0.4)  # Reduced from 0.8s
                except TimeoutException:
                    time.sleep(0.6)  # Fallback wait
                
                now = snapshot()
                if len(now) > len(seen):
                    seen = now
                    links = list(seen)
                    stagnant_rounds = 0
                    logger.debug(f"Scroll round {scroll_round + 1}: Found {len(links)} total links (+{len(now) - len(links)} new)")
                else:
                    stagnant_rounds += 1
                
                # Early exit if no progress for 2 rounds
                if stagnant_rounds >= 2:
                    logger.info("ðŸ›‘ Stopped scrolling - no new links detected")
                    break
                    
                # Check if we've reached the bottom
                new_position = driver.execute_script("return window.pageYOffset;")
                if new_position == last_scroll_position:
                    logger.info("ðŸ›‘ Reached page bottom")
                    break
                last_scroll_position = new_position

        # Duyá»‡t Ä‘áº§y Ä‘á»§ toÃ n bá»™ trang: click paginate-next Ä‘áº¿n háº¿t trang cuá»‘i
        logger.info("ðŸ“„ Full pagination traversal (Selenium)...")
        max_selenium_pages = 200  # Giá»›i háº¡n an toÃ n
        current_selenium_page = 1
        stagnant_pages = 0

        while current_selenium_page < max_selenium_pages:
            try:
                # TÃ¬m nÃºt "Xem thÃªm" / next pagination
                next_btn = None
                next_selectors = [
                    "button.paginate-next:not([disabled])",
                    "a.paginate-next:not([disabled])",
                    ".pagination-numbers__left button:not([disabled])",
                ]
                for sel in next_selectors:
                    try:
                        btn = driver.find_element(By.CSS_SELECTOR, sel)
                        if btn.is_displayed() and btn.is_enabled():
                            next_btn = btn
                            break
                    except Exception:
                        continue

                # Náº¿u khÃ´ng tÃ¬m tháº¥y báº±ng CSS, thá»­ XPATH
                if not next_btn:
                    for xpath in [
                        "//button[contains(@class,'paginate-next') and not(@disabled)]",
                        "//a[contains(.,'Tiáº¿p') or contains(.,'Sau') or contains(.,'Next')]",
                        "//button[contains(.,'Xem thÃªm') or contains(.,'Táº£i thÃªm')]",
                    ]:
                        try:
                            btn = driver.find_element(By.XPATH, xpath)
                            if btn.is_displayed() and btn.is_enabled():
                                next_btn = btn
                                break
                        except Exception:
                            continue

                if not next_btn:
                    logger.info(f"ðŸ›‘ KhÃ´ng tÃ¬m tháº¥y nÃºt next - Ä‘Ã£ cÃ o háº¿t {current_selenium_page} trang (Selenium)")
                    break

                initial_count = len(seen)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_btn)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", next_btn)

                # Chá» ná»™i dung má»›i load
                try:
                    WebDriverWait(driver, 6).until(
                        lambda d: len(snapshot()) > initial_count
                    )
                    time.sleep(0.3)
                except TimeoutException:
                    time.sleep(1.0)

                now = snapshot()
                if len(now) > len(seen):
                    seen = now
                    links = list(seen)
                    new_count = len(now) - initial_count
                    current_selenium_page += 1
                    stagnant_pages = 0
                    logger.info(f"ðŸ“„ Selenium trang {current_selenium_page}: +{new_count} links, tá»•ng {len(links)}")
                else:
                    stagnant_pages += 1
                    if stagnant_pages >= 2:
                        logger.info("ðŸ›‘ KhÃ´ng cÃ³ link má»›i sau 2 láº§n - dá»«ng phÃ¢n trang Selenium")
                        break

            except Exception as e:
                logger.debug(f"Pagination Selenium error: {e}")
                break

        final_links = sorted(links)
        logger.info(f"âœ… Final product links collected (Selenium): {len(final_links)} tá»« {current_selenium_page} trang")
        return final_links

    def _collect_product_links_bs4(self, url: str, session: requests.Session = None, soup: BeautifulSoup = None) -> list[str]:
        """Thu thập product links từ một trang sử dụng BeautifulSoup - version siêu tốc độ."""
        if soup is None:
            if session is None:
                session = self.session
            try:
                def _fetch_page():
                    resp = session.get(url, timeout=20)
                    resp.raise_for_status()
                    return resp.text
                html_content = self.retry_with_backoff(_fetch_page, max_retries=2, base_delay=1.0)
                if not html_content:
                    return []
                soup = BeautifulSoup(html_content, 'html.parser')
            except Exception as e:
                logger.debug(f"Lỗi tải trang {url}: {e}")
                return []

        links = set()
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            if '/products/' in href:
                clean_href = href.split('?')[0].split('#')[0].rstrip('/')
                if clean_href.startswith('/'):
                    clean_href = urljoin(self.base_url, clean_href)
                if 'hoplongtech.com' in clean_href:
                    links.add(clean_href)

        if not links:
            for a in soup.find_all('a', href=True):
                href = a.get('href', '').strip()
                if any(k in href for k in ['/product/', '/san-pham/']):
                    clean_href = href.split('?')[0].split('#')[0].rstrip('/')
                    if clean_href.startswith('/'):
                        clean_href = urljoin(self.base_url, clean_href)
                    if 'hoplongtech.com' in clean_href:
                        links.add(clean_href)

        result = sorted(list(links))
        return result

    def _extract_pagination_info(self, soup: BeautifulSoup) -> dict:
        """
        Extract pagination information tá»« HTML structure hoplongtech.com:
        <div class="pagination-wrap">
            <div class="pagination-numbers__right">
                <span class="text">Trang</span>
                <p class="paginate-item">1</p>
                <span class="text">cá»§a 5</span>
            </div>
        </div>
        """
        pagination_info = {
            'current_page': 1,
            'total_pages': 1,
            'has_next': False,
            'next_button_available': False
        }
        
        try:
            # TÃ¬m pagination container
            pagination_wrap = soup.select_one('div.pagination-wrap')
            if not pagination_wrap:
                logger.debug("âŒ No pagination-wrap found")
                return pagination_info
            
            # Extract current page vÃ  total pages tá»« "Trang X cá»§a Y"
            pagination_right = pagination_wrap.select_one('.pagination-numbers__right')
            if pagination_right:
                try:
                    # Láº¥y current page tá»« <p class="paginate-item">
                    current_page_elem = pagination_right.select_one('p.paginate-item')
                    if current_page_elem:
                        current_page_text = current_page_elem.get_text(strip=True)
                        pagination_info['current_page'] = int(current_page_text)
                        logger.debug(f"ðŸ“„ Current page: {pagination_info['current_page']}")
                    
                    # Láº¥y total pages tá»« text pattern "cá»§a X"
                    span_elements = pagination_right.find_all('span', class_='text')
                    for span in span_elements:
                        text = span.get_text(strip=True)
                        if 'cá»§a' in text:
                            # Handle both "cá»§a 5" vÃ  "cá»§a\xa05"
                            total_pages_text = re.sub(r'cá»§a\s*', '', text).strip()
                            try:
                                pagination_info['total_pages'] = int(total_pages_text)
                                logger.debug(f"ðŸ“„ Total pages: {pagination_info['total_pages']}")
                                break
                            except ValueError:
                                logger.debug(f"âš ï¸  Cannot parse total pages from: {total_pages_text}")
                                
                except Exception as e:
                    logger.debug(f"â Œ Error parsing pagination numbers: {e}")
            
            # Check if cÃ³ next button available
            pagination_left = pagination_wrap.select_one('.pagination-numbers__left')
            if pagination_left:
                next_button = pagination_left.select_one('button.paginate-next')
                if next_button:
                    is_disabled = (
                        next_button.get('disabled') is not None or
                        'disabled' in next_button.get('class', [])
                    )
                    pagination_info['next_button_available'] = not is_disabled
                    logger.debug(f"ðŸ”˜ Next button available: {pagination_info['next_button_available']}")
            
            # Determine if has next page
            pagination_info['has_next'] = (
                pagination_info['current_page'] < pagination_info['total_pages'] or 
                pagination_info['next_button_available']
            )
            
            logger.info(f"ðŸ“Š Pagination info: Page {pagination_info['current_page']}/{pagination_info['total_pages']}, Has next: {pagination_info['has_next']}")
            return pagination_info
            
        except Exception as e:
            logger.error(f"âŒ Error extracting pagination info: {e}")
            return pagination_info

    def _detect_pagination_hoplong(self, url: str) -> dict:
        """
        PhÃ¡t hiá»‡n tá»•ng sá»‘ trang vÃ  URL pattern phÃ¢n trang cho hoplongtech.com.
        Há»c theo CDHA's detect_pagination.
        Tráº£ vá»: {total_pages, url_pattern, products_per_page}
        """
        result = {'total_pages': 1, 'url_pattern': 'query', 'products_per_page': 0}
        try:
            def _fetch():
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                return resp.text

            html = self.retry_with_backoff(_fetch, max_retries=3, base_delay=1.5)
            if not html:
                logger.warning(f"HTML rỗng từ {url}!")
                return result

            try:
                with open('logs/debug_page1.html', 'w', encoding='utf-8', errors='ignore') as f_dbg:
                    f_dbg.write(html)
            except Exception:
                pass

            logger.info(f"HTML size from {url}: {len(html)} bytes")
            soup = BeautifulSoup(html, 'html.parser')

            all_a = soup.find_all('a', href=True)
            logger.info(f"Total a tags with href: {len(all_a)}")
            prod_a = [a['href'] for a in all_a if '/products/' in a['href']]
            logger.info(f"Total a tags with /products/: {len(prod_a)}")
            if prod_a:
                logger.info(f"Sample /products/ hrefs: {prod_a[:3]}")

            # Äáº¿m sáº£n pháº©m trÃªn trang Ä‘áº§u
            product_links_page1 = set()
            for a in soup.select('a[href*="/products/"], a[href^="/products/"]'):
                href = a.get('href', '')
                if '/products/' in href:
                    product_links_page1.add(href)
            result['products_per_page'] = len(product_links_page1)
            logger.info(f"ðŸ“Š Sáº£n pháº©m trang 1: {result['products_per_page']}")

            # Bước 1: Tìm pagination-wrap → parse "của X"
            pagination_wrap = soup.select_one('div.pagination-wrap')
            if pagination_wrap:
                pagination_right = pagination_wrap.select_one('.pagination-numbers__right')
                if pagination_right:
                    pr_text = pagination_right.get_text(strip=True)
                    m = re.search(r'(?:của|cá»§a|of|\/)\s*(\d+)', pr_text, re.IGNORECASE)
                    if m:
                        try:
                            result['total_pages'] = int(m.group(1))
                            logger.info(f"✅ Tổng số trang từ HTML: {result['total_pages']}")
                        except ValueError:
                            pass
                    else:
                        for span in pagination_right.find_all('span', class_='text'):
                            text = span.get_text(strip=True)
                            m_span = re.search(r'(\d+)', text)
                            if m_span:
                                try:
                                    result['total_pages'] = int(m_span.group(1))
                                    logger.info(f"✅ Tổng số trang từ span: {result['total_pages']}")
                                    break
                                except ValueError:
                                    pass

            # BÆ°á»›c 2: Náº¿u chÆ°a tÃ¬m Ä‘Æ°á»£c hoáº·c tá»•ng trang = 1, thá»­ kiá»ƒm tra trang 2
            # Ä‘á»ƒ phÃ¡t hiá»‡n URL pattern Ä‘Ãºng
            if result['total_pages'] <= 1:
                logger.info("ðŸ” Thá»­ phÃ¡t hiá»‡n URL pattern phÃ¢n trang...")
                test_patterns = []

                # Pattern 1: ?page=2 hoáº·c &page=2
                sep = '&' if '?' in url else '?'
                test_patterns.append((f"{url}{sep}page=2", 'query'))

                # Pattern 2: ?p=2
                test_patterns.append((f"{url}{sep}p=2", 'query_p'))

                for test_url, pattern_name in test_patterns:
                    try:
                        def _fetch_test(u=test_url):
                            r = self.session.get(u, timeout=20)
                            r.raise_for_status()
                            return r.text

                        test_html = self.retry_with_backoff(_fetch_test, max_retries=2, base_delay=1.0)
                        test_soup = BeautifulSoup(test_html, 'html.parser')

                        # Kiá»ƒm tra cÃ³ sáº£n pháº©m khÃ¡c khÃ´ng
                        test_links = set()
                        for a in test_soup.select('a[href*="/products/"]'):
                            href = a.get('href', '')
                            if '/products/' in href:
                                test_links.add(href)

                        if test_links and test_links != product_links_page1:
                            logger.info(f"âœ… Pattern '{pattern_name}' hoáº¡t Ä‘á»™ng: {len(test_links)} sáº£n pháº©m")
                            result['url_pattern'] = pattern_name

                            # Äá»“ng thá»i thá»­ parse tá»•ng sá»‘ trang tá»« test_soup
                            pw = test_soup.select_one('div.pagination-wrap')
                            if pw:
                                pr = pw.select_one('.pagination-numbers__right')
                                if pr:
                                    for span in pr.find_all('span', class_='text'):
                                        t = span.get_text(strip=True)
                                        if 'cá»§a' in t:
                                            try:
                                                result['total_pages'] = int(re.sub(r'cá»§a\s*', '', t).strip())
                                                logger.info(f"âœ… Tá»•ng trang tá»« trang 2: {result['total_pages']}")
                                            except ValueError:
                                                pass
                            break
                        else:
                            logger.debug(f"âŒ Pattern '{pattern_name}' khÃ´ng cho sáº£n pháº©m má»›i")
                    except Exception as e:
                        logger.debug(f"Pattern test {pattern_name} failed: {e}")
                        continue

            # BÆ°á»›c 3: Náº¿u váº«n chÆ°a biáº¿t tá»•ng trang, thÄƒm dÃ² Ä‘á»™ng (nhÆ° CDHA)
            if result['total_pages'] <= 1 and result['products_per_page'] > 0:
                logger.info("ðŸ” ThÄƒm dÃ² Ä‘á»™ng Ä‘á»ƒ tÃ¬m tá»•ng sá»‘ trang...")
                current = 1
                consecutive_empty = 0
                last_signature = None
                dup_count = 0

                while current < 500:
                    next_p = current + 1
                    test_url = self._make_hoplong_page_url(url, next_p, result['url_pattern'])
                    try:
                        def _fetch_page(u=test_url):
                            r = self.session.get(u, timeout=20)
                            r.raise_for_status()
                            return r.text

                        pg_html = self.retry_with_backoff(_fetch_page, max_retries=2, base_delay=1.0)
                        pg_soup = BeautifulSoup(pg_html, 'html.parser')
                        pg_links = [a.get('href', '') for a in pg_soup.select('a[href*="/products/"]') if '/products/' in a.get('href', '')]

                        if not pg_links:
                            consecutive_empty += 1
                            if consecutive_empty >= 2:
                                logger.info(f"âœ… Dá»«ng táº¡i trang {current}: 2 trang liÃªn tiáº¿p rá»—ng")
                                break
                            current += 1
                            continue

                        consecutive_empty = 0
                        sig = tuple(sorted(pg_links[:20]))
                        if sig == last_signature:
                            dup_count += 1
                            if dup_count >= 2:
                                logger.info(f"âœ… Dá»«ng táº¡i trang {current}: ná»™i dung láº·p")
                                break
                        else:
                            last_signature = sig
                            dup_count = 0

                        result['total_pages'] = next_p
                        current = next_p
                        logger.debug(f"   Trang {next_p}: {len(pg_links)} sáº£n pháº©m")

                    except Exception as e:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            break
                        current += 1

            logger.info(f"ðŸ“„ PhÃ¡t hiá»‡n: {result['total_pages']} trang, pattern='{result['url_pattern']}'")
            return result

        except Exception as e:
            logger.error(f"âŒ Lá»—i detect_pagination_hoplong: {e}")
            return result

    def _make_hoplong_page_url(self, base_url: str, page_number: int, pattern: str = 'query') -> str:
        """
        Táº¡o URL phÃ¢n trang cho hoplongtech.com.
        Há»c theo CDHA's make_pagination_url.
        pattern: 'query' â†’ ?page=N / &page=N
                 'query_p' â†’ ?p=N / &p=N
        """
        if page_number <= 1:
            return base_url
        try:
            # XÃ³a tham sá»‘ page/p cÅ© náº¿u cÃ³
            clean_url = re.sub(r'[&?]page=\d+', '', base_url)
            clean_url = re.sub(r'[&?]p=\d+', '', clean_url)
            clean_url = clean_url.rstrip('?&')

            sep = '&' if '?' in clean_url else '?'
            param = 'p' if pattern == 'query_p' else 'page'
            return f"{clean_url}{sep}{param}={page_number}"
        except Exception as e:
            logger.error(f"âŒ Lá»—i make_hoplong_page_url: {e}")
            sep = '&' if '?' in base_url else '?'
            return f"{base_url}{sep}page={page_number}"

    def _fetch_all_pages_bs4(self, base_url: str, session: requests.Session = None, max_pages: int = 200, shared_seen_links: set = None, seen_lock = None) -> list[str]:
        """
        Fetch tất cả product links song song theo batch và khử trùng lặp tức thời.
        Dừng khi hết sản phẩm thực sự hoặc đạt ngưỡng trang tối đa.
        """
        if session is None:
            session = self.session

        all_links = []
        all_links_set = set()

        try:
            logger.info(f"🔄 Bắt đầu cào đa trang từ: {base_url}")
            pagination_detect = self._detect_pagination_hoplong(base_url)
            detected_total = pagination_detect['total_pages']
            if max_pages <= 0:
                total_pages = detected_total
            else:
                total_pages = min(detected_total, max_pages)

            url_pattern = pagination_detect['url_pattern']

            logger.info(f"📄 Tổng số trang phát hiện: {detected_total} (sẽ quét: {total_pages}, pattern: {url_pattern})")

            # Quét song song theo lô (8 trang / đợt)
            batch_size = 8
            current_page = 1
            consecutive_empty = 0
            consecutive_duplicates = 0

            def fetch_page_worker(p_num):
                pu = self._make_hoplong_page_url(base_url, p_num, url_pattern)
                try:
                    def _fetch():
                        resp = session.get(pu, timeout=20)
                        resp.raise_for_status()
                        return resp.text
                    html = self.retry_with_backoff(_fetch, max_retries=2, base_delay=1.0)
                    if not html:
                        return p_num, []
                    soup = BeautifulSoup(html, 'html.parser')
                    p_links = self._collect_product_links_bs4(pu, session, soup=soup)
                    return p_num, p_links
                except Exception as e:
                    logger.debug(f"Lỗi tải trang {p_num}: {e}")
                    return p_num, []

            while current_page <= total_pages:
                chunk = list(range(current_page, min(current_page + batch_size, total_pages + 1)))

                with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunk)) as executor:
                    chunk_results = list(executor.map(fetch_page_worker, chunk))

                chunk_results.sort(key=lambda x: x[0])

                for p, p_links in chunk_results:
                    if not p_links:
                        consecutive_empty += 1
                        logger.debug(f"   Trang {p}: 0 sản phẩm")
                        if consecutive_empty >= 2:
                            logger.info(f"🛑 Dừng quét tại trang {p}: 2 trang liên tiếp không có sản phẩm (hết danh mục)")
                            break
                    else:
                        consecutive_empty = 0
                        new_links = []
                        for l in p_links:
                            if l not in all_links_set:
                                if shared_seen_links is not None and seen_lock is not None:
                                    with seen_lock:
                                        if l not in shared_seen_links:
                                            shared_seen_links.add(l)
                                            new_links.append(l)
                                else:
                                    new_links.append(l)

                        if new_links:
                            all_links.extend(new_links)
                            all_links_set.update(new_links)
                            consecutive_duplicates = 0
                            logger.info(f"   ✅ Trang {p}: +{len(new_links)} SP mới (Tổng: {len(all_links)})")
                        else:
                            consecutive_duplicates += 1
                            logger.debug(f"   Trang {p}: 0 SP mới ({consecutive_duplicates} trang trùng lặp)")

                        # Cơ chế dừng khi trùng lặp đã bị loại bỏ theo yêu cầu quét toàn bộ page
                        # if consecutive_duplicates >= 30:
                        #     logger.info(f"🛑 Dừng quét sớm tại trang {p}: 30 trang liên tiếp không có sản phẩm mới")
                        #     break

                if consecutive_empty >= 2:
                    break

                current_page += batch_size

            unique_links = sorted(list(all_links_set))
            logger.info(f"🎯 Hoàn thành quét danh mục {base_url}: {len(unique_links)} link duy nhất")
            return unique_links

        except Exception as e:
            logger.error(f"❌ Lỗi _fetch_all_pages_bs4 từ {base_url}: {e}")
            return sorted(list(all_links_set))

    def _parse_specs_from_technical_div(self, technical_div: BeautifulSoup) -> list[tuple[str, str]]:
        """
        TrÃ­ch xuáº¥t cáº·p (title, content) tá»« #technical theo structure:
        <ul>
            <li>
                <span class="title">TÃªn sáº£n pháº©m</span>
                <span class="content">Cáº£m biáº¿n tiá»‡m cáº­n E2B-M12KN05-WP-B2 2M OMI Omron</span>
            </li>
        </ul>
        """
        pairs: list[tuple[str, str]] = []
        if not technical_div:
            return pairs

        logger.debug("ðŸ” Parsing technical specifications...")
        
        # Extract tá»« ul/li/span structure theo HTML user cung cáº¥p
        ul_elements = technical_div.find_all('ul')
        logger.debug(f"Found {len(ul_elements)} ul elements")
        
        for ul_idx, ul in enumerate(ul_elements):
            li_elements = ul.find_all('li', recursive=False)
            logger.debug(f"UL {ul_idx + 1}: Found {len(li_elements)} li elements")
            
            for li_idx, li in enumerate(li_elements):
                # TÃ¬m span vá»›i class='title' vÃ  class='content'
                title_span = li.find('span', class_='title')
                content_span = li.find('span', class_='content')
                
                title = title_span.get_text(strip=True) if title_span else ''
                content = content_span.get_text(strip=True) if content_span else ''
                
                logger.debug(f"  LI {li_idx + 1}: '{title}' = '{content}'")
                
                # Chá»‰ thÃªm vÃ o náº¿u cÃ³ Ã­t nháº¥t title hoáº·c content
                if title or content:
                    pairs.append((title, content))
        
        # Fallback: náº¿u khÃ´ng tÃ¬m tháº¥y gÃ¬ báº±ng structure chÃ­nh xÃ¡c, thá»­ cÃ¡c pattern khÃ¡c
        if not pairs:
            logger.warning("ðŸ”„ No specs found with primary structure, trying fallback patterns...")
            
            # Thá»­ tÃ¬m táº¥t cáº£ li elements cÃ³ text
            all_li = technical_div.find_all('li')
            for li in all_li:
                text = li.get_text(strip=True)
                if ':' in text:
                    # Split theo dáº¥u : Ä‘áº§u tiÃªn
                    parts = text.split(':', 1)
                    if len(parts) == 2:
                        title, content = parts[0].strip(), parts[1].strip()
                        if title and content:
                            pairs.append((title, content))
                            logger.debug(f"  Fallback: '{title}' = '{content}'")
        
        logger.info(f"ðŸ“Š Total specifications extracted: {len(pairs)}")
        return pairs

    def _build_specs_html(self, technical_div: BeautifulSoup, pairs: list[tuple[str, str]], product_code: str = "", product_name: str = "") -> str:
        """Tạo HTML thông số kỹ thuật theo yêu cầu, thêm Copyright ở cuối."""
        rows = [
            '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">',
            '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>',
            '<tbody>'
        ]
        
        # Thêm Mã sản phẩm và Tên sản phẩm lên đầu
        if product_code:
            rows.append(f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{product_code}</td></tr>')
        if product_name:
            rows.append(f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{product_name}</td></tr>')
            
        exclude_keys = {"mã sản phẩm", "tên sản phẩm", "copyright"}
        has_added_pairs = False
        
        if technical_div:
            table = technical_div.find('table')
            if table:
                trs = table.find_all('tr')
                for tr in trs:
                    tds = tr.find_all(['td', 'th'])
                    if len(tds) >= 2:
                        k = tds[0].get_text(strip=True)
                        v = tds[1].get_text(strip=True)
                        if k.lower().strip() in exclude_keys:
                            continue
                        k_esc = k.replace('<', '&lt;').replace('>', '&gt;')
                        v_esc = v.replace('<', '&lt;').replace('>', '&gt;')
                        rows.append(f'<tr><td style="font-weight: bold;">{k_esc}</td><td>{v_esc}</td></tr>')
                        has_added_pairs = True
        
        if not has_added_pairs and pairs:
            for k, v in pairs:
                if k.lower().strip() in exclude_keys:
                    continue
                k_esc = (k or '').replace('<', '&lt;').replace('>', '&gt;')
                v_esc = (v or '').replace('<', '&lt;').replace('>', '&gt;')
                if k_esc or v_esc:
                    rows.append(f'<tr><td style="font-weight: bold;">{k_esc}</td><td>{v_esc}</td></tr>')
                    
        rows.append('<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>')
        rows.append('</tbody></table>')
        return '\n'.join(rows)

    def _download_image_webp(self, img_url: str, output_path: str) -> bool:
        """Download image and save exactly as .webp"""
        try:
            if not img_url:
                return False
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
            resp = self.session.get(img_url, timeout=20)
            resp.raise_for_status()
            
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(resp.content))
            # Convert to RGB if necessary before saving as WebP
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            image.save(output_path, 'WEBP')
            return True
        except Exception as e:
            logger.error(f"âŒ Error downloading image {img_url}: {e}")
            return False

    def extract_product_details(self, product_url: str, category_name: str, expected_brand: str = None) -> dict | None:
        """STRICT extraction - Láº¥y chi tiáº¿t sáº£n pháº©m vá»›i validation nghiÃªm ngáº·t theo brand."""
        try:
            logger.debug(f"ðŸ” Extracting product details from: {product_url}")
            if expected_brand:
                logger.info(f"ðŸŽ¯ Expected brand: {expected_brand}")
                
            resp = self.session.get(product_url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            # TÃªn sáº£n pháº©m: <h1 class="content-title">
            name_el = soup.select_one('h1.content-title')
            product_name = name_el.get_text(strip=True) if name_el else ''
            logger.debug(f"ðŸ“ Product name: {product_name}")

            # MÃ£ sáº£n pháº©m: <p class="content-meta__sku">MÃ£ sáº£n pháº©m: E2B-M12KN05-WP-B2 2M OMI</p>
            sku_el = soup.select_one('p.content-meta__sku')
            sku_text = sku_el.get_text(strip=True) if sku_el else ''
            # Láº¥y pháº§n sau "MÃ£ sáº£n pháº©m:" 
            product_code = ""
            if sku_text:
                import re
                product_code = re.sub(r"(?i)^\s*mã\s*sản\s*phẩm\s*:\s*", "", sku_text).strip()
                # Fix encoding artifact
                if "M" in product_code and "s" in product_code and "ph" in product_code:
                    product_code = re.sub(r"(?i)^.*?:\s*", "", product_code).strip()
            if not product_code and product_url:
                import re
                match = re.search(r"/products/([^/?]+)", product_url)
                if match:
                    product_code = match.group(1).upper()

            # GiÃ¡ sáº£n pháº©m: <div class="left"><p>45.540Ä‘</p>...</div>
            product_price = ""
            left_div = soup.select_one('div.left')
            if left_div:
                for p in left_div.find_all('p'):
                    text = p.get_text(strip=True)
                    if not text or 'VAT' in text.upper():
                        continue
                    if 'Ä‘' in text or 'â‚«' in text or any(char.isdigit() for char in text):
                        product_price = text
                        break
            logger.debug(f"ðŸ’° Product price: {product_price}")

            # HÃ£ng sáº£n pháº©m: <a class="content-meta__brand">
            brand_el = soup.select_one('a.content-meta__brand')
            actual_brand = brand_el.get_text(strip=True) if brand_el else ''
            logger.info(f"ðŸ­ Actual brand from product: {actual_brand}")

            # STRICT BRAND VALIDATION
            if expected_brand:
                if actual_brand.lower() != expected_brand.lower():
                    logger.error(f"âŒ BRAND MISMATCH! Expected: '{expected_brand}', Actual: '{actual_brand}' for product: {product_name}")
                    logger.error(f"âŒ REJECTING PRODUCT: {product_url}")
                    return None  # REJECT this product
                else:
                    logger.info(f"âœ… BRAND MATCH VERIFIED: {actual_brand}")

            # ThÃ´ng sá»‘ ká»¹ thuáº­t: <div class="content-tab__detail" id="technical">
            tech_div = soup.select_one('div.content-tab__detail#technical')
            if not tech_div:
                # Fallback selectors
                tech_div = soup.select_one('div#technical') or soup.select_one('.content-tab__detail')
                
            logger.debug(f"ðŸ”§ Technical div found: {tech_div is not None}")

            # Extract specifications tá»« ul/li/span structure
            pairs = self._parse_specs_from_technical_div(tech_div) if tech_div else []
            specs_html = self._build_specs_html(tech_div, pairs, product_code, product_name)
            
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"ðŸ“Š Specifications extracted: {len(pairs)} pairs")

            # áº¢nh sáº£n pháº©m
            img_el = soup.select_one('#slider-product img') or soup.select_one('.content-top__image img') or soup.select_one('img[src*="hoplongtech.com/hoplong/"]') or soup.find('img')
            img_url = img_el.get('src') if img_el else ''
            if img_url and not img_url.startswith('http'):
                img_url = urljoin(self.base_url, img_url)
            logger.debug(f"ðŸ–¼ï¸ Product image: {img_url}")

            # Validation - Ä‘áº£m báº£o cÃ³ Ã­t nháº¥t tÃªn sáº£n pháº©m
            if not product_name and not product_code:
                logger.warning(f"âŒ No product name or code found for {product_url}")
                return None

            # Use actual brand from product page (đã được verified)
            data = {
                'Tên sản phẩm': product_name,
                'Mã sản phẩm': product_code,
                'Giá': product_price,
                'Hãng': actual_brand,
                'Danh mục': category_name,
                'Link sản phẩm': product_url,
                'Thông số kỹ thuật HTML': specs_html,
                'Image URL': img_url
            }
            
            logger.info(f"âœ… Successfully extracted VERIFIED product: {product_name} (Brand: {actual_brand})")
            return data
            
        except Exception as e:
            logger.error(f"âŒ Lá»—i khi láº¥y chi tiáº¿t sáº£n pháº©m {product_url}: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            self.stats["errors"] += 1
            return None

    # ======= MAIN ORCHESTRATION =======
    def _category_name_from_html(self, driver) -> str:
        """Láº¥y tÃªn danh má»¥c tá»« HTML thá»±c táº¿ thay vÃ¬ URL"""
        try:
            # Láº¥y tá»« <h1 class="title"> trong .list-heading__left
            title_element = driver.find_element(By.CSS_SELECTOR, '.list-heading__left h1.title')
            category_name = title_element.text.strip()
            if category_name:
                logger.info(f"ðŸ“‚ Category name from HTML: {category_name}")
                return category_name
        except Exception as e:
            logger.debug(f"Cannot get category from HTML: {e}")
        
        # Fallback: tá»« page title
        try:
            page_title = driver.title
            if page_title:
                # Extract meaningful part from title
                if '|' in page_title:
                    category_name = page_title.split('|')[0].strip()
                elif '-' in page_title:
                    category_name = page_title.split('-')[0].strip()
                else:
                    category_name = page_title.strip()
                logger.info(f"ðŸ“‚ Category name from title: {category_name}")
                return category_name
        except Exception:
            pass
        
        # Last fallback: tá»« URL
        try:
            current_url = driver.current_url
            path = urlparse(current_url).path.strip('/')
            last = path.split('/')[-1]
            category_name = last.replace('-', ' ').title()
            logger.info(f"ðŸ“‚ Category name from URL: {category_name}")
            return category_name
        except Exception:
            logger.warning("Cannot extract category name, using default")
            return 'Danh má»¥c'

    def crawl_multiple_categories(self, category_urls: list[str], max_pages: int = 200) -> str:
        """Tối ưu hóa: cào nhiều danh mục đồng thời, khử trùng lặp tức thời và dừng khi hết trang thực tế."""
        start = time.time()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        batch_folder = os.path.join(self.output_root, f"HopLong_Multi_{timestamp}")
        os.makedirs(batch_folder, exist_ok=True)
        
        logger.info(f"=== BẮT ĐẦU CÀO {len(category_urls)} DANH MỤC HOPLONG (SIÊU TỐC ĐỘ, MAX PAGES: {max_pages if max_pages > 0 else 'ALL'}) ===")
        
        all_product_links = []
        links_lock = threading.Lock()
        shared_seen_links = set()
        seen_lock = threading.Lock()
        
        self.emit_progress(5, f"Đang quét {len(category_urls)} danh mục với bộ lọc khử trùng lặp...")
        
        def process_cat(url):
            try:
                cat_links = self._fetch_all_pages_bs4(
                    url, max_pages=max_pages, shared_seen_links=shared_seen_links, seen_lock=seen_lock
                )
                with links_lock:
                    for l in cat_links:
                        if l not in all_product_links:
                            all_product_links.append(l)
                return cat_links
            except Exception as e:
                logger.error(f"Lỗi khi quét danh mục {url}: {e}")
                return []

        # Quét các danh mục song song (tối đa 6 danh mục cùng lúc)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(category_urls), 6)) as executor:
            list(executor.map(process_cat, category_urls))
                            
        # Khử trùng danh sách link lần cuối
        all_product_links = list(dict.fromkeys(all_product_links))
        logger.info(f"Đã tìm thấy tổng cộng {len(all_product_links)} link sản phẩm duy nhất.")
        self.emit_progress(20, f"Tìm thấy {len(all_product_links)} sản phẩm duy nhất (đã lọc trùng). Bắt đầu cào chi tiết...")
        
        results_by_brand = {}
        processed_count = 0
        seen_skus = set()
        sku_lock = threading.Lock()
        
        def process_product(url):
            try:
                return self.extract_product_details(url, "HopLong_Category")
            except Exception as e:
                logger.error(f"Error extracting product {url}: {e}")
                return None

        optimal_workers = min(self.max_workers * 4, 60)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=optimal_workers) as executor:
            futures = {executor.submit(process_product, url): url for url in all_product_links}
            for fut in concurrent.futures.as_completed(futures):
                item = fut.result()
                processed_count += 1
                if item:
                    # Khử trùng theo SKU (Mã sản phẩm)
                    sku = item.get('Mã sản phẩm') or item.get('MÃ£ sáº£n pháº©m') or ''
                    if sku:
                        with sku_lock:
                            if sku in seen_skus:
                                logger.debug(f"Trùng SKU {sku}, bỏ qua không thêm trùng lặp")
                                continue
                            seen_skus.add(sku)

                    brand = (item.get('Hãng') or item.get('HÃ£ng') or 'Khac').strip()
                    if not brand:
                        brand = 'Khac'
                    brand_safe = re.sub(r'[\\/*?:"<>|]', "", brand).strip()
                    if not brand_safe:
                        brand_safe = 'Khac'
                        
                    if brand_safe not in results_by_brand:
                        results_by_brand[brand_safe] = []
                    
                    img_url = item.pop('Image URL', '')
                    item['Ảnh sản phẩm'] = img_url
                    
                    brand_folder = os.path.join(batch_folder, brand_safe)
                    os.makedirs(brand_folder, exist_ok=True)
                    images_dir = os.path.join(brand_folder, 'images')
                    os.makedirs(images_dir, exist_ok=True)
                    
                    if img_url:
                        product_code = sku or f"product_{len(results_by_brand[brand_safe])}"
                        product_code = re.sub(r'[\\/*?:"<>|]', "_", product_code)
                        img_path = os.path.join(images_dir, f"{product_code}.webp")
                        self._download_image_webp(img_url, img_path)
                    
                    results_by_brand[brand_safe].append(item)
                    
                if processed_count % 5 == 0 or processed_count == len(all_product_links):
                    self.emit_progress(
                        20 + (processed_count / max(1, len(all_product_links))) * 70,
                        f"Đã xử lý {processed_count}/{len(all_product_links)} sản phẩm"
                    )

        # Lưu Excel theo từng hãng
        self.emit_progress(95, "Đang xuất file Excel theo từng hãng...")
        for brand, items in results_by_brand.items():
            if items:
                brand_folder = os.path.join(batch_folder, brand)
                excel_path = os.path.join(brand_folder, f"{brand}.xlsx")
                self._export_excel(items, excel_path)
                
        dur = time.time() - start
        logger.info(f"=== HOÀN THÀNH CÀO NHIỀU DANH MỤC ===")
        logger.info(f"Thời gian: {dur:.1f}s, Sản phẩm: {processed_count}")
        
        total_images = sum(
            len([x for x in os.listdir(os.path.join(batch_folder, b, 'images')) if x.lower().endswith('.webp')])
            for b in results_by_brand if os.path.exists(os.path.join(batch_folder, b, 'images'))
        )
        self.stats = {
            'products_ok': sum(len(items) for items in results_by_brand.values()),
            'images_ok': total_images,
            'categories': len(results_by_brand),
            'brands': len(results_by_brand),
            'products_found': len(all_product_links),
        }
        self.emit_progress(100, "Hoàn thành cào dữ liệu", f"{dur:.1f}s - {self.stats['products_ok']} sản phẩm | {total_images} ảnh")
        
        return batch_folder

    def crawl_category_by_brands(self, category_url: str, brands: list[str]) -> str:
        """OPTIMIZED: CÃ o 1 danh má»¥c cho nhiá»u hÃ£ng vá»›i parallel processing."""
        start = time.time()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        batch_folder = os.path.join(self.output_root, f"HopLong_{timestamp}")
        os.makedirs(batch_folder, exist_ok=True)

        # Sáº½ láº¥y category name sau khi driver Ä‘Ã£ load trang
        category_name = "HopLong_Products"  # Temporary name
        category_dir = os.path.join(batch_folder, category_name)
        os.makedirs(category_dir, exist_ok=True)

        logger.info(f"=== OPTIMIZED CRAWL HopLong category: {category_name} ===")
        logger.info(f"URL: {category_url}")
        logger.info(f"Sá»‘ lÆ°á»£ng brands: {len(brands)}")
        logger.info(f"Output folder: {batch_folder}")
        
        self.emit_progress(0, f"Báº¯t Ä‘áº§u cÃ o HopLong: {category_name}", category_url)

        # OPTIMIZATION DECISION: LuÃ´n dÃ¹ng parallel mode khi cÃ³ >= 2 brands.
        # Vá»›i danh má»¥c lá»›n nhÆ° nut-nhan (nhiá»u brands), parallel mode nhanh hÆ¡n nhiá»u.
        if len(brands) >= 2:
            logger.info(f"ðŸš€ PARALLEL MODE: Processing {len(brands)} brands concurrently")
            return self._crawl_brands_parallel_mode(category_url, brands, batch_folder, category_name, category_dir, start)
        else:
            logger.info(f"ðŸ”„ SEQUENTIAL MODE: {len(brands)} brand (chá»‰ 1 brand)")
            return self._crawl_brands_sequential_mode(category_url, brands, batch_folder, category_name, category_dir, start)

    def _crawl_brands_parallel_mode(self, category_url: str, brands: list[str], batch_folder: str, category_name: str, category_dir: str, start_time: float) -> str:
        """OPTIMIZED: Process multiple brands in parallel for major speed boost."""
        import threading
        
        # Shared data between threads
        shared_data = {
            "category_name": category_name,
            "category_dir": category_dir,
            "results_lock": threading.Lock()
        }
        
        def process_brand_parallel(brand_data):
            """Process single brand in parallel thread."""
            idx, brand = brand_data
            brand_display = brand.strip()
            if not brand_display:
                return None
                
            thread_id = threading.current_thread().name
            logger.info(f"[{thread_id}] ðŸ”„ Processing brand {idx+1}/{len(brands)}: {brand_display}")
            
            try:
                # Update progress
                with shared_data["results_lock"]:
                    self.emit_progress(
                        5 + idx * (80/max(1, len(brands))), 
                        f"[Parallel] Äang xá»­ lÃ½: {brand_display}",
                        f"Thread: {thread_id[-8:]}"
                    )
                
                # Get product links for this brand
                product_links = self._get_brand_links_threadsafe(category_url, brand_display, idx, shared_data)
                
                # Process products for this brand
                results = self._process_brand_products_threadsafe(product_links, brand_display, idx, shared_data)
                
                # Export Excel for this brand
                if results:
                    excel_name = f"{shared_data['category_name']} {brand_display}.xlsx"
                    excel_path = os.path.join(shared_data['category_dir'], excel_name)
                    self._export_excel(results, excel_path)
                    
                    with shared_data["results_lock"]:
                        self.stats['brands_processed'] += 1
                        
                    logger.info(f"[{thread_id}] âœ… Completed {brand_display}: {len(results)} products")
                
                return {"brand": brand_display, "products": len(results), "success": True}
                
            except Exception as e:
                logger.error(f"[{thread_id}] âŒ Error processing {brand_display}: {e}")
                with shared_data["results_lock"]:
                    self.stats['errors'] += 1
                return {"brand": brand_display, "products": 0, "success": False, "error": str(e)}
        
        # PARALLEL EXECUTION
        # Vá»›i nhiá»u brands (nut-nhan cÃ³ thá»ƒ > 50 brands): tÄƒng workers lÃªn nhÆ°ng khÃ´ng quÃ¡ 8
        # Ä‘á»ƒ trÃ¡nh bá»‹ rate-limit. Má»—i brand worker sáº½ tá»± xá»­ lÃ½ toÃ n bá»™ pages + products cá»§a brand Ä‘Ã³.
        max_parallel_brands = min(len(brands), 20)  # Tá»‘i Ä‘a 20 brand workers song song
        logger.info(f"ðŸ”§ Using {max_parallel_brands} parallel brand workers (total {len(brands)} brands)")
        
        brand_data = [(idx, brand) for idx, brand in enumerate(brands)]
        brand_results = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel_brands, thread_name_prefix="Brand") as executor:
            futures = {executor.submit(process_brand_parallel, data): data for data in brand_data}
            
            for future in concurrent.futures.as_completed(futures):
                brand_data_item = futures[future]
                try:
                    result = future.result()
                    if result:
                        brand_results.append(result)
                except Exception as e:
                    logger.error(f"âŒ Future exception for brand {brand_data_item}: {e}")
        
        # COMPLETION SUMMARY
        successful_brands = sum(1 for r in brand_results if r.get("success"))
        total_products = sum(r.get("products", 0) for r in brand_results)
        
        self.stats['categories_processed'] += 1
        duration = time.time() - start_time
        
        logger.info(f"=== PARALLEL CRAWL COMPLETED ===")
        logger.info(f"Category: {shared_data['category_name']}")
        logger.info(f"Duration: {duration:.1f}s")
        logger.info(f"Brands: {successful_brands}/{len(brands)} successful")
        logger.info(f"Products: {total_products} total")
        
        self.emit_progress(100, f"Parallel crawl hoÃ n thÃ nh: {shared_data['category_name']}", f"{duration:.1f}s - {total_products} sáº£n pháº©m")
        return batch_folder

    def _get_brand_links_threadsafe(self, category_url: str, brand_display: str, idx: int, shared_data: dict) -> list[str]:
        """OPTIMIZED: Thread-safe method to get product links for a brand."""
        def _crawl_brand():
            driver = self.get_driver()
            product_links: list[str] = []
            try:
                driver.get(category_url)
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
                time.sleep(0.8)  # Reduced wait time
                
                # Update category name from HTML (thread-safe, only first brand)
                if idx == 0:
                    with shared_data["results_lock"]:
                        try:
                            real_category_name = self._category_name_from_html(driver)
                            if real_category_name and real_category_name != "Danh má»¥c":
                                old_category_dir = shared_data["category_dir"]
                                shared_data["category_name"] = real_category_name.replace('/', '-').replace('\\', '-')
                                shared_data["category_dir"] = os.path.join(os.path.dirname(old_category_dir), shared_data["category_name"])
                                
                                if old_category_dir != shared_data["category_dir"] and os.path.exists(old_category_dir):
                                    try:
                                        os.rename(old_category_dir, shared_data["category_dir"])
                                    except:
                                        os.makedirs(shared_data["category_dir"], exist_ok=True)
                                else:
                                    os.makedirs(shared_data["category_dir"], exist_ok=True)
                                    
                                logger.info(f"ðŸ“‚ Updated category name: {shared_data['category_name']}")
                        except Exception as e:
                            logger.debug(f"Cannot update category name: {e}")

                # Apply brand filter
                filter_success = self._apply_brand_filter(driver, brand_display)
                if not filter_success:
                    logger.error(f"âŒ Brand filter failed for '{brand_display}'")
                    return []
                
                # HYBRID APPROACH: Get filtered URL tá»« Selenium, sau Ä‘Ã³ dÃ¹ng BeautifulSoup
                filtered_url = driver.current_url
                logger.info(f"ðŸ”— Filtered URL obtained: {filtered_url}")
                
                # ÄÃ³ng driver sá»›m Ä‘á»ƒ tiáº¿t kiá»‡m resource
                self.close_driver(driver)
                driver = None  # Mark as closed
                
                # Chuyá»ƒn sang BeautifulSoup Ä‘á»ƒ thu tháº­p product links vá»›i pagination
                logger.info(f"ðŸ”„ Switching to BeautifulSoup for multi-page collection: {brand_display}")
                
                # Sá»­ dá»¥ng requests session Ä‘á»ƒ fetch táº¥t cáº£ pages
                product_links = self._fetch_all_pages_bs4(filtered_url, self.session)
                logger.info(f"ðŸ“¦ HYBRID: Found {len(product_links)} products for {brand_display}")
                
                with shared_data["results_lock"]:
                    self.stats['products_found'] += len(product_links)
                
                return product_links
                
            except Exception as e:
                logger.error(f"âŒ Error getting links for {brand_display}: {e}")
                with shared_data["results_lock"]:
                    self.stats['errors'] += 1
                return []
            finally:
                if driver is not None:
                    self.close_driver(driver)
        
        try:
            return self.retry_with_backoff(_crawl_brand, max_retries=2, base_delay=2.0)
        except Exception as e:
            logger.error(f"âŒ Failed to get links for {brand_display}: {e}")
            return []

    def _process_brand_products_threadsafe(self, product_links: list[str], brand_display: str, idx: int, shared_data: dict) -> list[dict]:
        """OPTIMIZED: Thread-safe method to process products for a brand."""
        results = []
        
        if not product_links:
            return results
            
        def task(url: str):
            return self.extract_product_details(url, shared_data["category_name"], expected_brand=brand_display)

        # OPTIMIZED: Dynamic worker calculation for thread safety
        optimal_workers = min(
            self.max_workers,  # Update to more workers per brand
            len(product_links) // 2 + 1,
            25  # Max 25 workers per brand in parallel mode
        )
        
        logger.info(f"ðŸ”§ Using {optimal_workers} workers for {len(product_links)} products ({brand_display})")

        with concurrent.futures.ThreadPoolExecutor(max_workers=optimal_workers) as executor:
            futures = {executor.submit(task, url): url for url in product_links}
            
            completed_count = 0
            for fut in concurrent.futures.as_completed(futures):
                url = futures[fut]
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                        with shared_data["results_lock"]:
                            self.stats['products_processed'] += 1
                        
                    completed_count += 1
                    
                    # Thread-safe progress update
                    if completed_count % max(1, len(product_links) // 10) == 0 or completed_count == len(product_links):
                        with shared_data["results_lock"]:
                            self.emit_progress(
                                20 + idx * (60/4),  # Approximate progress for parallel mode
                                f"[{brand_display}] {completed_count}/{len(product_links)}",
                                f"{len(results)} thÃ nh cÃ´ng"
                            )
                except Exception as e:
                    logger.error(f"âŒ Error processing {url}: {e}")
                    completed_count += 1
                    
        return results

    def _crawl_brands_sequential_mode(self, category_url: str, brands: list[str], batch_folder: str, category_name: str, category_dir: str, start_time: float) -> str:
        """OPTIMIZED: Sequential processing with enhanced performance."""
        # vÃ²ng theo tá»«ng brand vá»›i enhanced performance
        for idx, brand in enumerate(brands):
            brand_display = brand.strip()
            if not brand_display:
                continue

            self.emit_progress(5 + idx * (80/max(1, len(brands))), f"Äang Ã¡p dá»¥ng bá»™ lá»c hÃ£ng: {brand_display}")

            # Má»—i brand má»Ÿ má»™t driver má»›i Ä‘á»ƒ tráº¡ng thÃ¡i filter sáº¡ch
            logger.info(f"--- Báº¯t Ä‘áº§u crawl brand: {brand_display} ---")
            
            def _crawl_brand():
                driver = self.get_driver()
                product_links: list[str] = []
                try:
                    driver.get(category_url)
                    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))  # Giáº£m tá»« 30 xuá»‘ng 20
                    time.sleep(1.0)  # Giáº£m tá»« 1.5s xuá»‘ng 1.0s

                    # Láº¥y category name tá»« HTML thá»±c táº¿ (chá»‰ láº§n Ä‘áº§u)
                    if idx == 0:  # Chá»‰ láº¥y category name tá»« brand Ä‘áº§u tiÃªn
                        nonlocal category_name, category_dir
                        try:
                            real_category_name = self._category_name_from_html(driver)
                            if real_category_name and real_category_name != "Danh má»¥c":
                                # Update folder structure vá»›i tÃªn tháº­t
                                old_category_dir = category_dir
                                category_name = real_category_name.replace('/', '-').replace('\\', '-')  # Safe filename
                                category_dir = os.path.join(batch_folder, category_name)
                                
                                # Rename directory náº¿u cáº§n
                                if old_category_dir != category_dir and os.path.exists(old_category_dir):
                                    try:
                                        os.rename(old_category_dir, category_dir)
                                    except:
                                        os.makedirs(category_dir, exist_ok=True)
                                else:
                                    os.makedirs(category_dir, exist_ok=True)
                                    
                                logger.info(f"ðŸ“‚ Updated category name: {category_name}")
                        except Exception as e:
                            logger.debug(f"Cannot update category name: {e}")

                    # STRICT brand filter application
                    filter_success = self._apply_brand_filter(driver, brand_display)
                    if not filter_success:
                        logger.error(f"âŒ CRITICAL: Cannot apply brand filter for '{brand_display}' - SKIPPING this brand!")
                        self.emit_progress(
                            5 + idx * (80/max(1, len(brands))), 
                            f"âŒ Brand filter failed: {brand_display}", 
                            "Skipping this brand - filter not working"
                        )
                        return []  # Return empty list - don't process this brand
                    
                    # HYBRID APPROACH: Get filtered URL tá»« Selenium, sau Ä‘Ã³ dÃ¹ng BeautifulSoup
                    try:
                        filtered_url = driver.current_url
                        logger.info(f"ðŸ”— Filtered URL obtained: {filtered_url}")
                    except:
                        filtered_url = category_url  # Fallback to original URL
                        logger.warning("âš ï¸ Cannot get filtered URL, using original category URL")

                    # ÄÃ³ng driver sá»›m Ä‘á»ƒ tiáº¿t kiá»‡m resource
                    self.close_driver(driver) 
                    driver = None  # Mark as closed
                    
                    # Chuyá»ƒn sang BeautifulSoup Ä‘á»ƒ thu tháº­p product links vá»›i pagination
                    logger.info(f"ðŸ”„ SEQUENTIAL: Switching to BeautifulSoup for multi-page collection: {brand_display}")
                    
                    # Sá»­ dá»¥ng requests session Ä‘á»ƒ fetch táº¥t cáº£ pages
                    product_links = self._fetch_all_pages_bs4(filtered_url, self.session)
                    logger.info(f"ðŸ“¦ SEQUENTIAL HYBRID: Found {len(product_links)} products for {brand_display}")
                    
                    # Additional verification - make sure we have filtered results
                    if not product_links:
                        logger.warning(f"âš ï¸ No products found for brand '{brand_display}' - this may be correct or filter issue")
                    
                    return product_links
                except WebDriverException as e:
                    if "ERR_CONNECTION_RESET" in str(e) or "net::" in str(e):
                        logger.error(f"Lá»—i káº¿t ná»‘i máº¡ng cho brand {brand_display}: {e}")
                        self.emit_progress(
                            5 + idx * (80/max(1, len(brands))), 
                            f"Lá»—i káº¿t ná»‘i cho {brand_display}", 
                            "Thá»­ phÆ°Æ¡ng phÃ¡p backup..."
                        )
                        raise
                    else:
                        logger.error(f"Lá»—i WebDriver cho brand {brand_display}: {e}")
                        raise
                finally:
                    if driver is not None:
                        self.close_driver(driver)
            
            try:
                product_links = self.retry_with_backoff(_crawl_brand, max_retries=2, base_delay=3.0)
                self.stats['products_found'] += len(product_links)
            except Exception as e:
                logger.error(f"Tháº¥t báº¡i hoÃ n toÃ n khi crawl brand {brand_display}: {e}")
                self.stats['errors'] += 1
                self.emit_progress(
                    5 + idx * (80/max(1, len(brands))), 
                    f"Bá» qua brand {brand_display}", 
                    f"Lá»—i: {str(e)[:100]}..."
                )
                continue

            # OPTIMIZED: Enhanced multithreading vá»›i adaptive workers
            results: list[dict] = []
            if product_links:
                self.emit_progress(20 + idx * (60/max(1, len(brands))), f"Äang láº¥y chi tiáº¿t {len(product_links)} sáº£n pháº©m", brand_display)

                def task(url: str):
                    # Pass expected brand to extraction for validation
                    return self.extract_product_details(url, category_name, expected_brand=brand_display)

                # OPTIMIZED: Dynamic worker calculation for better performance
                optimal_workers = min(
                    self.max_workers,
                    len(product_links) // 2 + 1,  # At least 2 URLs per worker
                    20  # Max 20 workers to avoid overwhelming server
                )
                
                logger.info(f"ðŸ”§ Using {optimal_workers} workers for {len(product_links)} products")

                with concurrent.futures.ThreadPoolExecutor(max_workers=optimal_workers) as executor:
                    # OPTIMIZED: Submit all tasks at once for better scheduling
                    futures = {executor.submit(task, url): url for url in product_links}
                    
                    # Process results as they complete
                    completed_count = 0
                    for fut in concurrent.futures.as_completed(futures):
                        url = futures[fut]
                        try:
                            item = fut.result()
                            if item:
                                results.append(item)
                                self.stats['products_processed'] += 1
                                
                            completed_count += 1
                            
                            # OPTIMIZED: Less frequent progress updates for better performance  
                            if completed_count % max(1, len(product_links) // 20) == 0 or completed_count == len(product_links):
                                progress_pct = 20 + idx * (60/max(1, len(brands))) + (completed_count/max(1, len(product_links))) * (60/max(1, len(brands)))
                                self.emit_progress(
                                    progress_pct, 
                                    f"ÄÃ£ xá»­ lÃ½ {completed_count}/{len(product_links)} sáº£n pháº©m",
                                    f"{brand_display} - {len(results)} thÃ nh cÃ´ng"
                                )
                        except Exception as e:
                            logger.error(f"âŒ Error processing {url}: {e}")
                            completed_count += 1

            # xuáº¥t excel cho brand nÃ y
            if results:
                excel_name = f"{category_name} {brand_display}.xlsx"
                excel_path = os.path.join(category_dir, excel_name)
                self._export_excel(results, excel_path)
                self.stats['brands_processed'] += 1

        self.stats['categories_processed'] += 1
        dur = time.time() - start_time
        
        # Log káº¿t quáº£ tá»•ng káº¿t
        logger.info(f"=== HoÃ n thÃ nh crawl category: {category_name} ===")
        logger.info(f"Thá»i gian: {dur:.1f}s")
        logger.info(f"Brands processed: {self.stats['brands_processed']}")
        logger.info(f"Products found: {self.stats['products_found']}")
        logger.info(f"Products processed: {self.stats['products_processed']}")
        logger.info(f"Errors: {self.stats['errors']}")
        
        self.emit_progress(100, f"HoÃ n thÃ nh danh má»¥c: {category_name}", f"{dur:.1f}s - {self.stats['products_processed']} sáº£n pháº©m")
        return batch_folder

    def _export_excel(self, rows: list[dict], excel_path: str) -> None:
        df = pd.DataFrame(rows)
        os.makedirs(os.path.dirname(excel_path), exist_ok=True)
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='HopLong')
            ws = writer.sheets['HopLong']
            for column in ws.columns:
                max_len = 0
                col_letter = column[0].column_letter
                for cell in column:
                    try:
                        max_len = max(max_len, len(str(cell.value)))
                    except Exception:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 2, 60)


# ======================================================================
# HopLongProductCrawler - Async HTTP crawler (aiohttp + queue + checkpoint)
# ======================================================================

class HopLongProductCrawler:
    """
    Async crawler Hop Long:
      - aiohttp: HTTP bat dong bo, nhanh hon requests nhieu lan
      - asyncio.Queue: pipeline producer-consumer
      - asyncio.Semaphore: gioi han concurrent (pages / products / images)
      - Checkpoint: luu tien do -> resume neu bi gian doan
      - Tai anh song song co gioi han (img_concurrency)
    """

    HOPLONGTECH_DOMAIN = "hoplongtech.com"
    BASE_URL = "https://hoplongtech.com"
    BASE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    def __init__(
        self,
        output_root: str = None,
        max_workers: int = 20,
        socketio_instance=None,
        page_concurrency: int = 30,
        product_concurrency: int = 25,
        img_concurrency: int = 15,
    ):
        self.output_root = output_root or os.path.join(os.getcwd(), "output_hoplong")
        self.max_workers = max_workers
        self.page_concurrency = page_concurrency
        self.product_concurrency = product_concurrency
        self.img_concurrency = img_concurrency
        self.socketio = socketio_instance
        self.stats = {
            "categories": 0, "products_found": 0,
            "products_ok": 0, "products_failed": 0,
            "images_ok": 0, "images_failed": 0,
        }
        self._failed_img_urls: set = set()

    # ------------------------------------------------------------------
    # Emit & helpers
    # ------------------------------------------------------------------
    def _emit(self, percent: float, message: str, detail: str = "") -> None:
        if self.socketio:
            try:
                self.socketio.emit("progress_update", {
                    "percent": round(percent, 1),
                    "message": message,
                    "detail": str(detail),
                })
            except Exception:
                pass
        logger.info(f"[{percent:.1f}%] {message} {detail}")

    @staticmethod
    def _safe_folder(name: str) -> str:
        name = (name or "Khac").strip()
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        name = re.sub(r"\s+", "_", name)
        return name.strip("_") or "Khac"

    def _make_page_url(self, base_url: str, page: int) -> str:
        if page <= 1:
            return base_url
        clean = re.sub(r'[&?]page=\d+', '', base_url).rstrip('?&')
        sep = '&' if '?' in clean else '?'
        return f"{clean}{sep}page={page}"

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------
    def _load_checkpoint(self, path: str) -> set:
        if path and os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return set(json.load(f))
            except Exception:
                pass
        return set()

    def _save_checkpoint(self, path: str, done: set) -> None:
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(sorted(done), f)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Async HTTP helpers
    # ------------------------------------------------------------------
    async def _fetch(self, session, url: str, sem) -> str | None:
        """Async fetch HTML voi semaphore + retry."""
        async with sem:
            for attempt in range(5):
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.text(encoding='utf-8', errors='replace')
                        if resp.status == 429:
                            await asyncio.sleep(5 + attempt * 2)
                        elif attempt < 4:
                            await asyncio.sleep(2.0)
                except Exception as e:
                    if attempt < 4:
                        await asyncio.sleep(2.0)
                    else:
                        logger.debug(f"[fetch] {url}: {e}")
        return None

    async def _fetch_bytes(self, session, url: str, sem) -> bytes | None:
        """Async fetch binary (anh) voi semaphore + retry."""
        async with sem:
            for attempt in range(3):
                try:
                    img_headers = {
                        "User-Agent": self.BASE_HEADERS["User-Agent"],
                        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                        "Referer": self.BASE_URL + "/",
                    }
                    async with session.get(url, headers=img_headers) as resp:
                        if resp.status == 200:
                            return await resp.read()
                        if attempt < 2:
                            await asyncio.sleep(1.0)
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(1.0)
                    else:
                        logger.debug(f"[img] {url}: {e}")
        return None

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------
    def _extract_links(self, html: str) -> set:
        try:
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            soup = BeautifulSoup(html, 'html.parser')
        links = set()
        for a in soup.select('a[href]'):
            href = a.get('href', '').strip().split('?')[0]
            if not href or href == '#' or href == '/':
                continue
            
            # Dieu kien 1: link chua cac tu khoa san pham
            is_product = ('/products/' in href or '/product/' in href or '/san-pham/' in href)
            
            # Dieu kien 2: link nam trong the hien thi san pham (ke ca khi URL la /p12345)
            if not is_product:
                parent = a.find_parent(class_=re.compile(r'product-item|box-product|item-product|product-list|product-box', re.I))
                if parent:
                    is_product = True
                    
            if is_product:
                if href.startswith('/'):
                    href = f"https://{self.HOPLONGTECH_DOMAIN}{href}"
                elif href.startswith('http') and self.HOPLONGTECH_DOMAIN not in href:
                    continue
                links.add(href)
        return links

    def _parse_total_pages(self, html: str) -> int | None:
        """
        Da bo co che tinh so trang theo yeu cau de bat buoc quet dong tung trang 1 den khi het hang.
        """
        return None

    # ------------------------------------------------------------------
    # Async category link collector
    # ------------------------------------------------------------------
    async def _collect_links_async(self, session, category_url: str, page_sem) -> list:
        """Thu thap tat ca /products/ links tu 1 category URL - async, quet tung trang 1 de loc san pham chinh xac."""
        cat_name = category_url.rstrip('/').split('/')[-1]
        all_links: set = set()

        html1 = await self._fetch(session, category_url, page_sem)
        if not html1:
            logger.warning(f"[HopLong] Khong fetch duoc: {category_url}")
            return []

        links_page1 = self._extract_links(html1)
        all_links |= links_page1
        logger.info(f"[HopLong] {cat_name}: Bo qua tinh toan so trang, tien hanh quet tuan tu tung trang cho den khi khong con san pham...")

        max_page = 1000
        consecutive_empty = 0

        # Quet tuan tu tung trang 1 thay vi batch de dam bao lay day du va khong bi loi
        for p in range(2, max_page + 1):
            page_url = self._make_page_url(category_url, p)
            html = await self._fetch(session, page_url, page_sem)
            
            # Neu html rong, thu lai
            if not html:
                await asyncio.sleep(2.0)
                html = await self._fetch(session, page_url, page_sem)

            if not html:
                logger.warning(f"[HopLong] Loi tai trang {p}: {page_url}")
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                continue

            links = self._extract_links(html)
            if links:
                new_links = links - all_links
                if not new_links:
                    logger.debug(f"[HopLong] Trang {p} khong co san pham moi")
                    consecutive_empty += 1
                else:
                    all_links |= new_links
                    consecutive_empty = 0
            else:
                logger.debug(f"[HopLong] Trang {p} khong co san pham")
                consecutive_empty += 1

            if consecutive_empty >= 3:
                logger.info(f"[HopLong] Dung quet tai trang {p} do 3 trang lien tiep khong co SP moi")
                break
                
            await asyncio.sleep(0.3)

        logger.info(f"[HopLong] {cat_name}: {len(all_links)} links duy nhat")
        return sorted(all_links)

    # ------------------------------------------------------------------
    # Subcategory detection (sync)
    # ------------------------------------------------------------------
    def _get_subcategory_urls(self, category_url: str) -> list:
        try:
            resp = requests.get(category_url, headers=self.BASE_HEADERS, timeout=20)
            if not resp.ok:
                return []
            soup = BeautifulSoup(resp.text, 'html.parser')
            subcats = set()
            cat_path = category_url.rstrip('/').split('/')[-1]
            for a in soup.select('a[href*="/category/"]'):
                href = a.get('href', '').strip()
                if not href:
                    continue
                if href.startswith('/'):
                    href = f"https://{self.HOPLONGTECH_DOMAIN}{href}"
                href_path = href.rstrip('/').split('/')[-1]
                if href_path != cat_path and href_path.startswith(cat_path):
                    subcats.add(href.split('?')[0])
            return sorted(subcats)
        except Exception as e:
            logger.debug(f"[subcats] {category_url}: {e}")
            return []

    # ------------------------------------------------------------------
    # Async product scraper
    # ------------------------------------------------------------------
    async def _scrape_product(self, session, url: str, product_sem, img_sem, run_dir: str, prod_url_to_cat: dict) -> dict | None:
        """Scrape 1 san pham + tai anh bat dong bo."""
        html = await self._fetch(session, url, product_sem)
        if not html:
            return None
        try:
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            soup = BeautifulSoup(html, 'html.parser')

        h1 = soup.select_one("h1.content-title")
        name = h1.get_text(strip=True) if h1 else ""

        sku_el = soup.select_one("p.content-meta__sku")
        sku = ""
        if sku_el:
            sku = re.sub(r"(?i)^\s*mã\s*sản\s*phẩm\s*:\s*", "", sku_el.get_text(strip=True)).strip()
        
        if not sku:
            match = re.search(r'/products/([^/?]+)', url)
            if match:
                sku = match.group(1).upper()

        price = ""
        for sel in ["div.left", "div.product-price", ".content-price"]:
            el = soup.select_one(sel)
            if el:
                for p_tag in el.find_all("p"):
                    txt = p_tag.get_text(strip=True)
                    if txt and "VAT" not in txt and "Li\u00ean h\u1ec7" not in txt:
                        price = txt
                        break
                if price:
                    break

        brand_el = soup.select_one("a.content-meta__brand")
        brand = (brand_el.get("aria-label", "") or brand_el.get_text(strip=True)).strip() if brand_el else ""

        specs_html = self._build_specs_table(soup, name, sku)

        img_url = ""
        for sel in ["div#slider-product img", "div.content-top__image img", "img[src*='hoplongtech']"]:
            img_tag = soup.select_one(sel)
            if img_tag and img_tag.get("src"):
                src = img_tag["src"].strip()
                if src and "default_image" not in src:
                    img_url = src
                    break

        if not name and not sku:
            return None

        cat_folder = prod_url_to_cat.get(url, "Khac")
        cat_dir = os.path.join(run_dir, cat_folder)
        local_img = ""
        if img_url and sku:
            local_img = await self._download_image_async(session, img_url, sku, cat_dir, img_sem)

        return {
            "T\u00ean s\u1ea3n ph\u1ea9m": name,
            "M\u00e3 s\u1ea3n ph\u1ea9m": sku,
            "Gi\u00e1": price,
            "H\u00e3ng": brand or "Khac",
            "Th\u00f4ng s\u1ed1 k\u1ef9 thu\u1eadt": specs_html,
            "URL \u1ea3nh": img_url,
            "\u1ea2nh (local)": local_img,
            "URL s\u1ea3n ph\u1ea9m": url,
        }

    # ------------------------------------------------------------------
    # Async image downloader
    # ------------------------------------------------------------------
    async def _download_image_async(self, session, img_url: str, sku: str, cat_dir: str, img_sem) -> str:
        """Tai anh bat dong bo + convert WebP, co gioi han concurrency."""
        if img_url in self._failed_img_urls:
            return ""
        images_dir = os.path.join(cat_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        safe_sku = re.sub(r'[\\/:*?"<>|]', "_", sku)
        out_path = os.path.join(images_dir, f"{safe_sku}.webp")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path

        content = await self._fetch_bytes(session, img_url, img_sem)
        if not content:
            self._failed_img_urls.add(img_url)
            self.stats["images_failed"] += 1
            return ""

        try:
            from PIL import Image
            import io as _io
            loop = asyncio.get_event_loop()

            def _save():
                img = Image.open(_io.BytesIO(content))
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                img.save(out_path, "WEBP", quality=90)
                return out_path

            result = await loop.run_in_executor(None, _save)
            self.stats["images_ok"] += 1
            return result
        except Exception:
            try:
                with open(out_path, "wb") as f:
                    f.write(content)
                self.stats["images_ok"] += 1
                return out_path
            except Exception:
                self._failed_img_urls.add(img_url)
                self.stats["images_failed"] += 1
                return ""

    # ------------------------------------------------------------------
    # Specs table builder
    # ------------------------------------------------------------------
    def _build_specs_table(self, soup, name: str, sku: str) -> str:
        rows = []
        technical_div = soup.select_one("div.content-tab__detail#technical")
        if technical_div:
            for li in technical_div.select("ul li"):
                t_el = li.select_one("span.title")
                c_el = li.select_one("span.content")
                if t_el and c_el:
                    t = t_el.get_text(strip=True)
                    c = c_el.get_text(strip=True)
                    if t and c:
                        rows.append((t, c))
                        
        exclude_keys = {"mã sản phẩm", "tên sản phẩm", "copyright"}
        tbody_rows = []
        
        # 1. Mã sản phẩm
        if sku:
            tbody_rows.append(f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{sku}</td></tr>')
        # 2. Tên sản phẩm
        if name:
            tbody_rows.append(f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{name}</td></tr>')
            
        # 3. Other specifications
        for t, c in rows:
            if t.lower().strip() in exclude_keys:
                continue
            tbody_rows.append(f'<tr><td style="font-weight: bold;">{t}</td><td>{c}</td></tr>')
            
        # 4. Copyright
        tbody_rows.append('<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>')
        
        tbody = "".join(tbody_rows)
        return (
            '<table id="specifications" border="1" cellpadding="8" cellspacing="0" '
            'style="border-collapse: collapse; font-family: Arial; width: 100%;">'
            '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>'
            f'<tbody>{tbody}</tbody></table>'
        )

    # ------------------------------------------------------------------
    # Excel saver
    # ------------------------------------------------------------------
    def _save_excel(self, products: list, excel_path: str) -> None:
        if not products:
            return
        df = pd.DataFrame(products)
        os.makedirs(os.path.dirname(excel_path), exist_ok=True)
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="HopLong")
            ws = writer.sheets["HopLong"]
            for col in ws.columns:
                max_len = max((len(str(c.value or "")) for c in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 80)

    # ------------------------------------------------------------------
    # Main async orchestrator
    # ------------------------------------------------------------------
    async def _async_crawl(self, category_urls: list, run_dir: str, checkpoint_path: str) -> None:
        import aiohttp

        page_sem = asyncio.Semaphore(self.page_concurrency)
        product_sem = asyncio.Semaphore(self.product_concurrency)
        img_sem = asyncio.Semaphore(self.img_concurrency)

        done_urls = self._load_checkpoint(checkpoint_path)
        if done_urls:
            self._emit(0, f"Resume checkpoint: {len(done_urls)} SP da xong truoc do", "")

        connector = aiohttp.TCPConnector(
            limit=max(self.page_concurrency, self.product_concurrency) + 30,
            ttl_dns_cache=300,
            ssl=False,
        )
        timeout = aiohttp.ClientTimeout(total=25, connect=8)

        async with aiohttp.ClientSession(
            headers=self.BASE_HEADERS,
            connector=connector,
            timeout=timeout,
        ) as session:

            # Step 1: Thu thap links
            self._emit(0, f"Quet {len(category_urls)} danh muc (async aiohttp)...", "")
            loop = asyncio.get_event_loop()

            seen_urls: set = set()
            prod_url_to_cat: dict = {}
            all_product_urls: list = []

            for cat_url in category_urls:
                subcats = await loop.run_in_executor(None, self._get_subcategory_urls, cat_url)
                urls_to_scan = [cat_url] + subcats
                if subcats:
                    logger.info(f"[HopLong] {cat_url.split('/')[-1]}: +{len(subcats)} subcats")

                link_tasks = [self._collect_links_async(session, u, page_sem) for u in urls_to_scan]
                link_results = await asyncio.gather(*link_tasks, return_exceptions=True)

                for u, links in zip(urls_to_scan, link_results):
                    sub_name = self._safe_folder(u.rstrip('/').split('/')[-1])
                    if isinstance(links, list):
                        for lnk in links:
                            if lnk not in seen_urls:
                                seen_urls.add(lnk)
                                prod_url_to_cat[lnk] = sub_name
                                all_product_urls.append(lnk)

            self.stats["products_found"] = len(all_product_urls)
            self._emit(20, f"Tim thay {len(all_product_urls)} SP (dedup xong)", "")

            pending = [u for u in all_product_urls if u not in done_urls]
            logger.info(f"[HopLong] Can scrape: {len(pending)}/{len(all_product_urls)}")

            if not pending:
                self._emit(100, "Tat ca da scrape (checkpoint)!", "")
                return

            with open(os.path.join(run_dir, "product_urls.txt"), "w", encoding="utf-8") as f:
                f.writelines(u + "\n" for u in all_product_urls)

            # Step 2: Scrape qua asyncio.Queue
            queue: asyncio.Queue = asyncio.Queue()
            for u in pending:
                await queue.put(u)

            scraped: list = []
            seen_skus: set = set()
            done_count = 0
            total = len(pending)
            lock = asyncio.Lock()

            async def _worker():
                nonlocal done_count
                while True:
                    try:
                        url = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    try:
                        result = await self._scrape_product(
                            session, url, product_sem, img_sem, run_dir, prod_url_to_cat
                        )
                    except Exception as e:
                        logger.error(f"Scrape error {url}: {e}")
                        result = None

                    async with lock:
                        done_count += 1
                        done_urls.add(url)
                        if result:
                            sku = result.get("M\u00e3 s\u1ea3n ph\u1ea9m", "").strip()
                            if sku and sku in seen_skus:
                                logger.debug(f"Bo qua san pham trung ma: {sku}")
                            else:
                                if sku:
                                    seen_skus.add(sku)
                                scraped.append(result)
                                self.stats["products_ok"] += 1
                        else:
                            self.stats["products_failed"] += 1

                        if done_count % 100 == 0 or done_count == total:
                            pct = 20 + (done_count / total) * 65
                            self._emit(
                                pct,
                                f"Scrape {done_count}/{total} SP | OK:{self.stats['products_ok']} | Anh:{self.stats['images_ok']}",
                                f"Loi:{self.stats['products_failed']}"
                            )
                            self._save_checkpoint(checkpoint_path, done_urls)
                    queue.task_done()

            workers = [asyncio.create_task(_worker()) for _ in range(self.product_concurrency)]
            await asyncio.gather(*workers)
            self._save_checkpoint(checkpoint_path, done_urls)

        # Step 3: Luu Excel
        self._emit(85, f"Scrape xong {len(scraped)} SP. Dang luu Excel...", "")
        cat_map: dict = {}
        for prod in scraped:
            cf = prod_url_to_cat.get(prod.get("URL s\u1ea3n ph\u1ea9m", ""), "Khac")
            cat_map.setdefault(cf, []).append(prod)

        for cf, products in cat_map.items():
            cat_dir = os.path.join(run_dir, cf)
            os.makedirs(cat_dir, exist_ok=True)
            self._save_excel(products, os.path.join(cat_dir, f"{cf}.xlsx"))

        if scraped:
            self._save_excel(scraped, os.path.join(run_dir, "Bao_cao_tong_hop.xlsx"))

        self._emit(
            100,
            f"Hoan thanh! {len(scraped)} SP | {self.stats['images_ok']} anh | {len(cat_map)} danh muc",
            run_dir
        )
        logger.info(f"HopLongProductCrawler async hoan thanh: {run_dir} | {self.stats}")

    # ------------------------------------------------------------------
    # Public sync entry point (tuong thich voi routes.py)
    # ------------------------------------------------------------------
    def crawl(self, category_urls: list, max_workers: int = None) -> str:
        """Sync wrapper: chay async event loop trong thread hien tai."""
        max_workers = max_workers or self.max_workers
        self.product_concurrency = max(self.product_concurrency, max_workers)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(self.output_root, f"HopLong_{timestamp}")
        os.makedirs(run_dir, exist_ok=True)

        checkpoint_path = os.path.join(run_dir, ".checkpoint.json")
        self.stats["categories"] = len(category_urls)
        self._emit(0, f"Bat dau cao {len(category_urls)} danh muc (async aiohttp)", "")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                self._async_crawl(category_urls, run_dir, checkpoint_path)
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        return run_dir
