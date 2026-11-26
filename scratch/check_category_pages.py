import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
options.add_argument('--window-size=1920,1080')
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

driver = webdriver.Chrome(options=options)
try:
    url = "https://www.festo.com/vn/vi/p/cam-bien-quang-dien-id_SOEG_R"
    driver.get(url)
    time.sleep(5)
    
    # Dump HTML
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    print("=== Search for pagination elements ===")
    import re
    for el in soup.find_all(class_=re.compile(r'page|pagination', re.I)):
        print(f"[{el.name}] class={el.get('class')} text={repr(el.get_text(strip=True)[:100])}")
        
except Exception as e:
    print("Error:", e)
finally:
    driver.quit()
