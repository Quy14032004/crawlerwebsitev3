import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
options.add_argument('--window-size=1920,1080')
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 20)

try:
    url = "https://www.festo.com/vn/vi/a/8192260/"
    print("Navigating to:", url)
    driver.get(url)
    
    # 1. Click cookie consent if present
    print("Accepting cookies...")
    try:
        cookie_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[self::button or self::span or self::div][contains(text(), 'Accept all cookies') or contains(text(), 'Đồng ý tất cả') or contains(text(), 'Accept cookies')]")))
        driver.execute_script("arguments[0].click();", cookie_btn)
        time.sleep(2)
    except Exception as e:
        print("No cookie consent found or failed:", e)
        
    # 2. Click the Technical Data tab button
    print("Clicking Technical Data tab...")
    tab_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'tab-navigation-item-button')][contains(., 'Dữ liệu kỹ thuật') or contains(., 'Technical Data') or contains(., 'Technical data')]")))
    print("Found tab button:", tab_btn.text)
    driver.execute_script("arguments[0].click();", tab_btn)
    time.sleep(5) # Wait for specifications to load
    
    # Dump HTML
    html = driver.page_source
    with open("scratch_loaded_specs.html", "w", encoding="utf-8") as f:
        f.write(html)
        
    # Analyze the loaded specs
    soup = BeautifulSoup(html, 'html.parser')
    
    print("\n=== Specifications found in loaded HTML ===")
    specifications = {}
    
    # Find all elements matching class containing 'technical-data-table'
    containers = soup.select('[class*="technical-data-table"]')
    print("Specs containers matching [class*='technical-data-table']:", len(containers))
    for idx, container in enumerate(containers):
        print(f"Container {idx} tag={container.name} class={container.get('class')}")
        # Find rows
        rows = container.select('[class*="technical-data-table-row"], [class*="technical-data-row"], li')
        print(f"  Rows found: {len(rows)}")
        for r in rows:
            lbl_elem = r.select_one('[class*="technical-data-property"], [class*="technical-data-label"]')
            val_elem = r.select_one('[class*="technical-data-value"]')
            if lbl_elem and val_elem:
                lbl = lbl_elem.get_text(separator=" ", strip=True)
                val = val_elem.get_text(separator=" ", strip=True)
                specifications[lbl] = val
                
    if not specifications:
        # Let's search generally
        print("Fallback search...")
        for r in soup.select('[class*="technical-data-table-row"], [class*="technical-data-row"], li'):
            lbl_elem = r.select_one('[class*="property"], [class*="label"]')
            val_elem = r.select_one('[class*="value"]')
            if lbl_elem and val_elem:
                lbl = lbl_elem.get_text(separator=" ", strip=True)
                val = val_elem.get_text(separator=" ", strip=True)
                specifications[lbl] = val
                
    print(f"Total specifications parsed: {len(specifications)}")
    for k, v in list(specifications.items())[:20]:
        print(f"  - {k}: {v}")
        
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    driver.quit()
