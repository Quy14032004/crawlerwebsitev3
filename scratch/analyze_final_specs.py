from bs4 import BeautifulSoup
import re

with open("scratch_final_specs.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')

print("=== Search for class containing 'technical-data' or 'spec' ===")
for el in soup.find_all(class_=re.compile(r'technical-data|spec', re.I)):
    print(f"Element tag={el.name} class={el.get('class')} children={len(el.contents)}")
    # Print the element text (first 100 chars)
    print("  Text:", repr(el.get_text(strip=True)[:100]))
    # If it is a list or has children, let's print some child tags
    child_tags = [c.name for c in el.contents if c.name]
    if child_tags:
        print("  Child tags:", child_tags[:10])
