import time
from selenium import webdriver
from bs4 import BeautifulSoup

options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
options.add_argument('--window-size=1920,1080')
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

driver = webdriver.Chrome(options=options)
try:
    url = "https://www.festo.com/vn/vi/p/cam-bien-quang-dien-id_SOEG_R?page=2"
    print("Navigating to page 2...")
    driver.get(url)
    time.sleep(5)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    articles = soup.select('.single-article--cNEBP, [data-testid="suggested-article"]')
    print(f"Page 2 articles found: {len(articles)}")
    for a in articles:
        print("  - Product:", a.get_text(strip=True)[:100])
        
    with open("scratch_page2.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
        
except Exception as e:
    print("Error:", e)
finally:
    driver.quit()
