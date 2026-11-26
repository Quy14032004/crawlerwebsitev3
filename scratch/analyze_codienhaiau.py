import requests
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from bs4 import BeautifulSoup

url = 'https://codienhaiau.com/category/bien-tan/?filter_brand=shihlin'
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36'}
r = requests.get(url, headers=headers, timeout=15)
soup = BeautifulSoup(r.text, 'html.parser')

print('Title:', soup.title.string.strip() if soup.title else 'None')

# Check links
all_a = soup.find_all('a', href=True)
product_links = [a['href'] for a in all_a if '/product/' in a['href'] or '/san-pham/' in a['href']]
print('Found product/san-pham links:', len(product_links))
unique_pl = list(set(product_links))
print('Unique product links:', len(unique_pl))
for l in unique_pl[:10]:
    print(' -', l)

# Check pagination
pag = soup.select('.pagination, .page-numbers, .wp-pagenavi, nav.woocommerce-pagination')
print('Pagination elements found:', len(pag))
for p in pag[:5]:
    print('  Tag:', p.name, 'Class:', p.get('class'), 'Text:', p.get_text(strip=True)[:50])

# Check count
count_elems = soup.select('mark.count, .woocommerce-result-count, [class*="count"]')
for c in count_elems[:5]:
    print('  Count elem:', c.name, c.get('class'), c.get_text(strip=True))
