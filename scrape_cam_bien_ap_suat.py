import requests
from bs4 import BeautifulSoup
import pandas as pd
import concurrent.futures
import sys

sys.stdout.reconfigure(encoding='utf-8')

base_url = "https://hoplongtech.com/category/cam-bien-ap-suat"
brand_filter = "filterBrandIds[0]=1129"
total_pages = 381

all_products = []

def scrape_page(page):
    url = f"{base_url}?{brand_filter}&page={page}"
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        items = soup.select('.product-list__item, .product-item, .item-product, .grid-list .item, .col-span-1 > div, .bg-white.rounded')
        page_products = []
        for item in items:
            name_el = item.select_one('.content h3, .product-name, .title, .name a, h3 a, h4 a')
            price_el = item.select_one('.price, .product-price, .text-red-500')
            link_el = item.select_one('a')
            
            name = name_el.get_text(strip=True) if name_el else ""
            price = price_el.get_text(strip=True) if price_el else ""
            link = link_el.get('href') if link_el else ""
            
            if link and not link.startswith('http'):
                link = "https://hoplongtech.com" + link
                
            page_products.append({'Tên sản phẩm': name, 'Giá': price, 'Link': link})
        print(f"Page {page} done: {len(page_products)} products")
        return page_products
    except Exception as e:
        print(f"Page {page} error: {e}")
        return []

if __name__ == "__main__":
    print(f"Starting scraping {total_pages} pages...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(scrape_page, p) for p in range(1, total_pages + 1)]
        for future in concurrent.futures.as_completed(futures):
            all_products.extend(future.result())

    print(f"Total extracted: {len(all_products)} products.")

    df = pd.DataFrame(all_products)
    # Remove duplicates if any
    df.drop_duplicates(subset=['Link'], inplace=True)
    print(f"After removing duplicates: {len(df)} products.")
    
    df.to_excel("cam_bien_ap_suat_sensys.xlsx", index=False)
    df.to_csv("cam_bien_ap_suat_sensys.csv", index=False, encoding='utf-8-sig')
    print("Data saved to cam_bien_ap_suat_sensys.xlsx and .csv")
