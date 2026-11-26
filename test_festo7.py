from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
driver = webdriver.Chrome(options=options)
driver.get("https://www.festo.com/vn/vi/a/8192260/")
time.sleep(5)
try:
    tabs = driver.find_elements(By.XPATH, "//*[contains(text(), 'Dữ liệu kỹ thuật') or contains(text(), 'Technical Data')]")
    for tab in tabs:
        try:
            tab.click()
            print("Clicked tab:", tab.text)
            time.sleep(2)
        except Exception as e:
            pass
            
    table = driver.find_element(By.CSS_SELECTOR, ".technical-data-table-container--boX_I, [class*='technical-data-table']")
    print("Found table!")
    rows = table.find_elements(By.CSS_SELECTOR, "li")
    print(f"Table has {len(rows)} rows.")
except Exception as e:
    print("Error:", e)
driver.quit()
