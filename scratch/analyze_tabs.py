from bs4 import BeautifulSoup

with open("scratch_final_specs.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')

print("=== Analyze tab-navigation-list ===")
for nav in soup.select('[class*="tab-navigation-list"]'):
    print(f"Nav container tag={nav.name} class={nav.get('class')}")
    # Find all children tags (buttons, links)
    for i, child in enumerate(nav.find_all(['a', 'button', 'div', 'span'])):
        # only direct descendants or links
        txt = child.get_text(strip=True)
        if txt and len(txt) < 50:
            print(f"  {i}: tag={child.name} class={child.get('class')} text={repr(txt)}")
