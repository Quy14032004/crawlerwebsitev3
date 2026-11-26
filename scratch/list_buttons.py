from bs4 import BeautifulSoup

with open("scratch_after_click.html", "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

print("All buttons:")
for btn in soup.find_all('button'):
    print("  - Text:", repr(btn.get_text(strip=True)), "class:", btn.get('class'))

print("\nAll anchors (a):")
for a in soup.find_all('a'):
    txt = a.get_text(strip=True)
    if txt:
        print("  - Text:", repr(txt), "class:", a.get('class'), "href:", a.get('href'))
