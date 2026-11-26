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
    url = "https://www.festo.com/vn/vi/p/cam-bien-quang-dien-id_SOEG_R"
    driver.get(url)
    time.sleep(5)
    
    # Click accept cookies
    try:
        cookie_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[self::button or self::span or self::div][contains(text(), 'Accept all cookies') or contains(text(), 'Đồng ý tất cả')]")))
        driver.execute_script("arguments[0].click();", cookie_btn)
        time.sleep(2)
    except Exception:
        pass
        
    soup1 = BeautifulSoup(driver.page_source, 'html.parser')
    print("Page 1 products:")
    for idx, a in enumerate(soup1.select('.single-article--cNEBP, [data-testid="suggested-article"]')):
        print(f"  {idx+1}: {a.select_one('[class*=\"triad-part-number\"]').get_text(strip=True) if a.select_one('[class*=\"triad-part-number\"]') else 'no PN'}")

    # Inspect the pagination elements in page 1
    print("\nPagination elements info:")
    pagination_div = soup1.select_one('.product-list-pagination--qvZfL, [class*="pagination"]')
    if pagination_div:
        for i, child in enumerate(pagination_div.find_all(['a', 'button', 'span'])):
            print(f"  Child {i}: tag={child.name} class={child.get('class')} text={repr(child.get_text(strip=True))} href={child.get('href')}")
            
    # Try to find and click the Page 2 button (next arrow)
    print("\nClicking Page 2 button...")
    page2_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[class*='navigate-btn-up']")))
    driver.execute_script("arguments[0].click();", page2_btn)
    time.sleep(5)
    
    soup2 = BeautifulSoup(driver.page_source, 'html.parser')
    print("Page 2 products after click:")
    for idx, a in enumerate(soup2.select('.single-article--cNEBP, [data-testid="suggested-article"]')):
        print(f"  {idx+1}: {a.select_one('[class*=\"triad-part-number\"]').get_text(strip=True) if a.select_one('[class*=\"triad-part-number\"]') else 'no PN'}")
        
    print("Current URL after clicking page 2:", driver.current_url)

except Exception as e:
    print("Error:", e)
finally:
    driver.quit()
