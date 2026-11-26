import os
import time
import cloudscraper
import pandas as pd
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class MisumiProductCrawler:
    def __init__(self, output_root=None):
        self.output_root = output_root or os.path.join(os.getcwd(), "output_misumi")
        os.makedirs(self.output_root, exist_ok=True)
        self.scraper = cloudscraper.create_scraper()
        
    def _init_driver(self):
        options = uc.ChromeOptions()
        # Removed headless mode to bypass Akamai WAF
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        # Pinning the Chrome major version to 146 due to the user's setup
        driver = uc.Chrome(options=options, use_subprocess=True, version_main=146)
        driver.implicitly_wait(10)
        return driver
        
    def _download_image_as_webp(self, img_url, part_number, output_dir):
        # Handle protocol-relative URL
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
            
        try:
            resp = self.scraper.get(img_url, timeout=15)
            if resp.status_code == 200:
                img_path = os.path.join(output_dir, f"{part_number}.webp")
                
                # We can save it as is and let other logic strictly convert to webp
                # or write directly if it's already a format that pillow can convert.
                # Here we use PIL to open the downloaded bytes and convert to WebP
                from PIL import Image
                from io import BytesIO
                
                img = Image.open(BytesIO(resp.content))
                # Convert to RGB (in case of RGBA/P to avoid errors when saving WebP without alpha)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                    
                img.save(img_path, "webp", quality=85)
                return True, img_path
            else:
                return False, None
        except Exception as e:
            print(f"Error downloading image for {part_number}: {e}")
            return False, None

    def crawl_product_detail(self, url):
        print(f"Crawling {url} using Selenium...")
        driver = None
        try:
            driver = self._init_driver()
            driver.get(url)
            
            # Explicit wait for JS to load the content
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CLASS_NAME, 'PartNumberDropDownList_partNumberOption__KnDz0'))
                )
                print("Found dynamically rendered Part Numbers!")
            except Exception as e:
                print("Wait timeout: 'PartNumberDropDownList' did not appear within 15s. May be anti-bot or invalid URL.")
                
            time.sleep(2)  # Extra buffer for DOM stability
            
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # --- 1. Product Part Numbers ---
            part_numbers = []
            options = soup.find_all('div', class_='PartNumberDropDownList_partNumberOption__KnDz0')
            for opt in options:
                pn = opt.get('title') or opt.text.strip()
                if pn and pn not in part_numbers:
                    part_numbers.append(pn)
            
            # Fallback if dropdown is not present or populated uniquely
            if not part_numbers:
                # Try finding active part number if options are not a list
                active_pn_container = soup.find('div', class_='ProductAttributes_partNumberSelection__20Ov1')
                if active_pn_container:
                    pn_text = active_pn_container.text.strip()
                    if pn_text:
                        part_numbers.append(pn_text)
            
            if not part_numbers:
                print("Warning: No part numbers found.")
                part_numbers = ["UNKNOWN-PN"]
                
            # --- 2. Base Product Name ---
            base_name = ""
            h1 = soup.find('h1', id='heading-description')
            if h1:
                base_name = h1.text.strip()
                
            # --- 3. Product Image URL ---
            img_url = ""
            img_elem = soup.find('img', class_='MainProductImage_mainImage__uMoSm')
            if img_elem and img_elem.has_attr('src'):
                img_url = img_elem['src']
            
            # --- 4. Specifications to HTML Table ---
            spec_containers = soup.find_all('div', class_='SpecTable_specContainer__Zd5hp')
            
            spec_rows = []
            for container in spec_containers:
                items = container.find_all('li')
                for item in items:
                    dt = item.find('dt', class_='SpecTable_specLabel__hvUFG')
                    dd = item.find('dd', class_='SpecTable_specValue__hPVpN')
                    if dt and dd:
                        label = dt.text.strip()
                        value = dd.text.strip()
                        spec_rows.append((label, value))
            
            # Build the specific HTML table
            # user requirement: <table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">
            html_table = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">'
            html_table += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead><tbody>'
            for label, value in spec_rows:
                html_table += f'<tr><td style="font-weight: bold;">{label}</td><td>{value}</td></tr>'
            
            # User requirement: add Haiphongtech.vn copyright
            html_table += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>'
            html_table += '</tbody></table>'
            
            results = []
            # Create a separate database entry for each part number
            for pn in part_numbers:
                full_name = f"{base_name} {pn}".strip()
                
                # The prompt asks for: Mã sản phẩm, Tên sản phẩm, Ảnh sản phẩm, Thông số kĩ thuật.
                # In Baa crawler, we usually use: 'Mã sản phẩm', 'Tên sản phẩm', 'Tổng quan', 'URL'
                result = {
                    'Mã sản phẩm': pn,
                    'Tên sản phẩm': full_name,
                    'Tổng quan': html_table,
                    'URL': url,
                    'Image_URL': img_url
                }
                results.append(result)
                
            return results
        except Exception as e:
            print(f"Error crawling {url}: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            if driver:
                driver.quit()

import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

if __name__ == "__main__":
    test_url = "https://vn.misumi-ec.com/vona2/detail/222005698835/"
    crawler = MisumiProductCrawler()
    
    # Save raw HTML to see what's actually there
    with open('test_misumi_raw.html', 'w', encoding='utf-8') as f:
        resp = crawler.scraper.get(test_url)
        f.write(resp.text)
    print("Saved test_misumi_raw.html for debugging.")

    # ensure output dir
    anh_dir = os.path.join(crawler.output_root, "Anh")
    os.makedirs(anh_dir, exist_ok=True)
    
    product_data = crawler.crawl_product_detail(test_url)
    if product_data:
        print(f"Found {len(product_data)} products.")
        for product in product_data:  # Process all products
            print("-" * 50)
            print(f"Mã sản phẩm: {product['Mã sản phẩm']}")
            print(f"Tên sản phẩm: {product['Tên sản phẩm']}")
            print(f"Image_URL: {product['Image_URL']}")
            print(f"Thông số kĩ thuật (bảng): {product['Tổng quan'][:200]}...")
            
            # Download image test
            if product['Image_URL']:
                success, path = crawler._download_image_as_webp(product['Image_URL'], product['Mã sản phẩm'], anh_dir)
                print(f"Hinh anh: {'Thanh cong ( ' + str(path) + ' )' if success else 'That bai'}")
        
        # Test creating excel
        import pandas as pd
        df = pd.DataFrame(product_data)
        excel_path = os.path.join(crawler.output_root, "Du_lieu_Misumi.xlsx")
        df.drop(columns=['Image_URL']).to_excel(excel_path, index=False)
        print(f"Da xua du lieu ra {excel_path}")
    else:
        print("Scrape return none.")
