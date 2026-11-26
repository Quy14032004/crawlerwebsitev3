from bs4 import BeautifulSoup
import re

with open("scratch_final_specs.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')

print("Searching for known specs words:")
keywords = ["Vị trí lắp", "Hành trình", "Tốc độ", "Gia tốc", "Lực", "Nhiệt độ", "ELGD", "60"]
for kw in keywords:
    matches = soup.find_all(text=re.compile(kw, re.I))
    print(f"Keyword '{kw}' matches: {len(matches)}")
    for m in matches[:3]:
        print(f"  - Parent: {m.parent.name}, class: {m.parent.get('class')}, text: {repr(m.strip())}")
