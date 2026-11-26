import json
from bs4 import BeautifulSoup

with open("test_html.html", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')
scripts = soup.find_all('script')

print(f"Total script tags: {len(scripts)}")
for i, s in enumerate(scripts):
    src = s.get('src')
    if src:
        continue
    content = s.string or ""
    if not content:
        continue
    print(f"Script {i}: length {len(content)}, starts with: {content[:100].strip()}")
    # Search for keywords like technicalData, specifications, or key values
    if "technicalData" in content or "technical-data" in content or "8192260" in content or "ELGD" in content:
        print(f"  -> Matches keyword! Snippet: {content[:300]}")
