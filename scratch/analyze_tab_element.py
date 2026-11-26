from bs4 import BeautifulSoup

with open("scratch_final_specs.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')

print("All elements with text 'Dữ liệu kỹ thuật':")
for el in soup.find_all(text=lambda t: t and 'Dữ liệu kỹ thuật' in t):
    p = el.parent
    while p and p.name not in ['body', 'html']:
        print(f"  Tag: {p.name}, class: {p.get('class')}, id: {p.get('id')}, href: {p.get('href')}")
        p = p.parent
    print("---")
