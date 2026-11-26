from bs4 import BeautifulSoup
import re

with open("scratch_final_specs.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')

print("All classes containing table, list, or row:")
classes = set()
for tag in ['div', 'ul', 'ol', 'table', 'li']:
    for el in soup.find_all(tag):
        cls = el.get('class')
        if cls:
            for c in cls:
                if any(x in c.lower() for x in ['table', 'row', 'list', 'spec', 'tech']):
                    classes.add(c)
                    
for c in sorted(classes):
    print("  -", c)
