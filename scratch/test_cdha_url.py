import sys
import os
sys.path.insert(0, os.path.abspath('.'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.crawler import get_html_content
from app.crawlerCDHA import CDHACrawler, detect_pagination, extract_cdha_product_urls, is_cdha_category_url, is_cdha_product_url
from bs4 import BeautifulSoup

url = "https://codienhaiau.com/category/bien-tan/?filter_brand=shihlin"

print("is_cdha_category_url:", is_cdha_category_url(url))
print("is_cdha_product_url:", is_cdha_product_url(url))

print("Fetching HTML...")
html = get_html_content(url)
print("HTML length:", len(html) if html else 0)

if html:
    soup = BeautifulSoup(html, 'html.parser')
    print("Page title:", soup.title.string.strip() if soup.title and soup.title.string else "No title")
    
    # Check pagination detection
    pages = detect_pagination(url)
    print("Detected pages:", pages)
    
    # Check product urls extraction
    products = extract_cdha_product_urls(url)
    print(f"Extracted products ({len(products)}):")
    for p in products[:10]:
        print(" -", p)
