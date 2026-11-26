import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
    
    # 1. Wait for cookie consent button and click it
    print("Waiting for cookie consent button...")
    cookie_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[self::button or self::span or self::div][contains(text(), 'Accept all cookies') or contains(text(), 'Đồng ý tất cả') or contains(text(), 'Accept cookies')]")))
    print("Found cookie button. Clicking it...")
    driver.execute_script("arguments[0].click();", cookie_btn)
    time.sleep(2) # wait for overlay to disappear
    
    # 2. Wait for the "Dữ liệu kỹ thuật" / "Technical Data" tab to be clickable
    print("Waiting for technical data tab...")
    tab_element = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[self::button or self::a or self::div or self::span][text()='Dữ liệu kỹ thuật' or text()='Technical Data' or text()='Technical data']")))
    print("Found tab element:", tab_element.text)
    driver.execute_script("arguments[0].click();", tab_element)
    print("Clicked technical data tab.")
    
    # 3. Wait for the technical data table/container to load
    print("Waiting for specifications container...")
    # Let's find any element containing class *technical-data-table*
    specs_container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='technical-data-table'], div[class*='technical-data-container'], [class*='technical-data']")))
    print("Found specifications container!")
    
    # Scroll down to ensure everything loads (lazy loading)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    
    # Let's fetch all rows
    # Rows are typically list items (li) or divs with class matching technical-data-row
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    # Find specifications
    specifications = {}
    
    # Try different structures
    # Structure 1: class-based matching
    tech_data_container = soup.select_one('[class*="technical-data-table"]')
    if tech_data_container:
        print("Using Structure 1 (class-based matching)")
        rows = tech_data_container.select('[class*="technical-data-table-row"], [class*="technical-data-row"], li')
        print(f"Found {len(rows)} potential rows")
        for row in rows:
            lbl_elem = row.select_one('[class*="technical-data-property"], [class*="technical-data-label"]')
            val_elem = row.select_one('[class*="technical-data-value"]')
            if lbl_elem and val_elem:
                lbl = lbl_elem.get_text(separator=" ", strip=True)
                val = val_elem.get_text(separator=" ", strip=True)
                if lbl and val:
                    specifications[lbl] = val
                    
    # Structure 2: Fallback if empty
    if not specifications:
        print("Using Structure 2 (general elements inside the container)")
        # Look for labels and values under the specs container
        for row in soup.select('[class*="technical-data-row"], [class*="technical-data-table-row"], li'):
            lbl_elem = row.select_one('[class*="property"], [class*="label"]')
            val_elem = row.select_one('[class*="value"]')
            if lbl_elem and val_elem:
                lbl = lbl_elem.get_text(separator=" ", strip=True)
                val = val_elem.get_text(separator=" ", strip=True)
                if lbl and val:
                    specifications[lbl] = val

    print(f"Total specifications found: {len(specifications)}")
    for k, v in list(specifications.items())[:20]:
        print(f"  - {k}: {v}")
        
    with open("scratch_final_specs.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
        
except Exception as e:
    print("An error occurred:")
    import traceback
    traceback.print_exc()
finally:
    driver.quit()
