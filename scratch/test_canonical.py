import requests, sys
from bs4 import BeautifulSoup

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36'}

for p in [1, 3, 4]:
    url = f'https://codienhaiau.com/category/bien-tan/page/{p}/?filter_brand=shihlin' if p > 1 else 'https://codienhaiau.com/category/bien-tan/?filter_brand=shihlin'
    r = requests.get(url, headers=headers)
    soup = BeautifulSoup(r.text, 'html.parser')
    canon = soup.find('link', rel='canonical')
    body_classes = soup.body.get('class', []) if soup.body else []
    print(f'Page {p}: final_url={r.url}')
    print(f'   canonical: {canon.get("href") if canon else "None"}')
    print(f'   is home in body classes: {"home" in body_classes}')
