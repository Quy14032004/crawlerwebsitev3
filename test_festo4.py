import requests
from bs4 import BeautifulSoup
url = "https://www.festo.com/vn/vi/a/8192260/"
resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
soup = BeautifulSoup(resp.text, 'html.parser')
scripts = soup.find_all('script')
for s in scripts:
    if s.string and ('technical' in s.string.lower() or '1550' in s.string):
        print("Found data in script:", s.get('id') or s.get('class') or 'inline script')
        print(s.string[:200])
