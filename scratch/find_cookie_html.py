from bs4 import BeautifulSoup

with open("scratch_after_click.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')
print("Searching for 'Accept all cookies':")
for el in soup.find_all(text=lambda t: t and 'Accept all cookies' in t):
    parent = el.parent
    print(f"Parent tag: {parent.name}, class: {parent.get('class')}, id: {parent.get('id')}")
    # Print outer HTML of parent
    print(str(parent)[:500])
