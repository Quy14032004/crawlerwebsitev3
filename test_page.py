import requests
from bs4 import BeautifulSoup

all_links = set()
for page in range(1, 20):
    url = f'https://hoplongtech.com/category/chuyen-mach-2-3-vi-tri?filterBrandIds[0]=1126&page={page}'
    r = requests.get(url, headers={'Accept-Encoding': 'gzip, deflate', 'User-Agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(r.text, 'html.parser')
    links = set([a['href'] for a in soup.select('a[href*="/products/"]') if '/products/' in a['href']])
    if not links:
        print(f"Page {page} empty")
    else:
        new_links = links - all_links
        print(f"Page {page}: {len(links)} links, {len(new_links)} new")
        all_links.update(links)
        
print(f"Total unique links: {len(all_links)}")
