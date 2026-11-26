import time
from selenium import webdriver
from selenium.webdriver.common.by import By

options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
options.add_argument('--window-size=1920,1080')
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

driver = webdriver.Chrome(options=options)
try:
    url = "https://www.festo.com/vn/vi/a/8192260/"
    driver.get(url)
    time.sleep(5)
    
    # Dump active page HTML before clicking anything
    with open("scratch_initial.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
        
    # Attempt to close cookie overlay if present
    try:
        # Search for buttons containing "Accept all cookies" or "Đồng ý tất cả"
        cookies_btn = driver.execute_script("""
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var txt = buttons[i].innerText || buttons[i].textContent;
                if (txt && (txt.includes('Accept all') || txt.includes('Đồng ý') || txt.includes('nhận tất cả') || txt.includes('Accept cookies'))) {
                    return buttons[i];
                }
            }
            return null;
        """)
        if cookies_btn:
            driver.execute_script("arguments[0].click();", cookies_btn)
            print("Accepted cookies!")
            time.sleep(2)
    except Exception as e:
        print("Error accepting cookies:", e)
        
    # Try to find and click the "Dữ liệu kỹ thuật" / "Technical data" tab
    try:
        clicked = driver.execute_script("""
            var elements = document.querySelectorAll('button, a, div, span');
            for(var i = 0; i < elements.length; i++) {
                var text = elements[i].innerText || elements[i].textContent;
                if(text && (text.trim() === 'Dữ liệu kỹ thuật' || text.trim() === 'Technical data' || text.trim() === 'Technical Data')) {
                    elements[i].click();
                    return text;
                }
            }
            return null;
        """)
        print("Clicked tab:", clicked)
        time.sleep(3)
    except Exception as e:
        print("Error clicking tab:", e)

    # Let's check the DOM structure around technical-data
    with open("scratch_after_click.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
        
    print("Done!")
finally:
    driver.quit()
