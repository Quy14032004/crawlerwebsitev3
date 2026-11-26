import requests, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from bs4 import BeautifulSoup

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36'}

for page in [1, 2, 3, 4, 5]:
    url = f'https://codienhaiau.com/category/bien-tan/page/{page}/?filter_brand=shihlin' if page > 1 else 'https://codienhaiau.com/category/bien-tan/?filter_brand=shihlin'
    r = requests.get(url, headers=headers, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')
    # Use the same selector as extract_cdha_product_urls
    main_content = soup.select_one('.woocommerce-loop, .products, main .products')
    if main_content:
        links = [a['href'] for a in main_content.select('.woocommerce-loop-product__link, .product-item a, li.product a') if a.get('href')]
    else:
        links = [a['href'] for a in soup.select('a[href*="/product/"], a[href*="/san-pham/"]') if a.get('href')]
    
    unique_links = list(dict.fromkeys(links))
    print(f"Page {page}: status {r.status_code}, final_url: {r.url}, unique product links: {len(unique_links)}")
    if unique_links:
        print(f"   First: {unique_links[0]}")
        print(f"   Last:  {unique_links[-1]}")
    else:
        print("   No products!")
