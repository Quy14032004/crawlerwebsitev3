import json
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
# Enable performance logging
options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

driver = webdriver.Chrome(options=options)
try:
    print("Navigating...")
    driver.get("https://www.festo.com/vn/vi/a/8192260/")
    time.sleep(5)
    
    # Click "Dữ liệu kỹ thuật"
    try:
        tab_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Dữ liệu kỹ thuật') or contains(text(), 'Technical Data') or contains(text(), 'Technical data')]")
        driver.execute_script("arguments[0].click();", tab_btn)
        print("Clicked technical data tab.")
        time.sleep(3)
    except Exception as e:
        print("Could not click tab:", e)
        
    logs = driver.get_log('performance')
    print(f"Captured {len(logs)} performance logs.")
    
    urls = []
    for entry in logs:
        log = json.loads(entry['message'])['message']
        if log['method'] == 'Network.requestWillBeSent':
            request = log['params']['request']
            url = request['url']
            urls.append(url)
            
    # Print distinct API-like URLs
    api_urls = [u for u in set(urls) if 'festo.com' in u and ('api' in u or 'json' in u or 'details' in u or 'ajax' in u or 'graphql' in u)]
    print("\nAPI-like URLs captured:")
    for u in sorted(api_urls):
        print(f"  - {u}")
        
    # Also save all captured URLs to a file
    with open("scratch/all_network_urls.txt", "w") as f:
        for u in sorted(set(urls)):
            f.write(u + "\n")
    print("\nSaved all URLs to scratch/all_network_urls.txt")
finally:
    driver.quit()
