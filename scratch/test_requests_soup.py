import requests
from bs4 import BeautifulSoup

url = "https://www.festo.com/vn/vi/a/8192260/"
resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'})
print("Status code:", resp.status_code)
print("Response text length:", len(resp.text))
if resp.status_code != 200:
    print(resp.text[:500])
soup = BeautifulSoup(resp.text, 'html.parser')

print("=== Search for technical data in requests HTML ===")
# Find elements with class containing technical-data
import re
for el in soup.find_all(class_=re.compile(r'technical-data|product-detail', re.I)):
    print(f"[{el.name}] class={el.get('class')} text={el.get_text(strip=True)[:100]}")
    
# Let's check if there are scripts containing JSON data with specs
print("=== Search for scripts with specs ===")
for s in soup.find_all('script'):
    if s.string and ('technical' in s.string.lower() or '1550' in s.string or 'ELGD' in s.string):
        print("Found script containing keyword, length:", len(s.string))
        # Save first 500 chars
        print(s.string[:500])
        # Search for specifications in script string using regex
        specs_matches = re.findall(r'"specifications":\s*\[{0,1}\{[^\]]+\}\]{0,1}', s.string)
        print("Specs matches in script:", len(specs_matches))
        if specs_matches:
            print(specs_matches[0][:500])
