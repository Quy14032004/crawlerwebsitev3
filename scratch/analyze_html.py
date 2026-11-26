from bs4 import BeautifulSoup

with open("scratch_initial.html", "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

print("Searching for buttons, links, etc:")
for tag in ['button', 'a', 'div', 'span']:
    elements = soup.find_all(tag)
    for el in elements:
        txt = el.get_text(strip=True)
        if txt and ("kỹ thuật" in txt or "Technical" in txt or "Dữ liệu" in txt):
            print(f"[{tag}] class={el.get('class')} text={txt[:50]}")
