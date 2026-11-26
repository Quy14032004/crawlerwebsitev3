from bs4 import BeautifulSoup
import re

with open("scratch_after_click.html", "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

print("=== Search for text matching 'kỹ thuật' or 'Technical' ===")
for tag in ['button', 'a', 'div', 'span', 'li']:
    for el in soup.find_all(tag):
        # elements with no children (leaves) or direct text
        if el.string or (len(el.contents) == 1 and isinstance(el.contents[0], str)):
            text = el.get_text(strip=True)
            if any(term in text for term in ["kỹ thuật", "Technical", "Dữ liệu"]):
                print(f"Leaf [{tag}] class={el.get('class')} id={el.get('id')} text={text}")
        else:
            # check if the text matches but has children
            text = el.get_text(strip=True)
            if any(term in text for term in ["kỹ thuật", "Technical", "Dữ liệu"]) and len(text) < 100:
                print(f"Parent [{tag}] class={el.get('class')} id={el.get('id')} text={text}")

print("=== Search for any class containing 'technical-data' or 'tab' ===")
for el in soup.find_all(class_=re.compile(r'technical-data|tab', re.I)):
    print(f"Element class={el.get('class')} tag={el.name} text={el.get_text(strip=True)[:100]}")
