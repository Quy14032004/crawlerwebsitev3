from bs4 import BeautifulSoup

with open("scratch_initial.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')
print("Title:", soup.title.string if soup.title else "No title")
print("HTML length:", len(html))
print("Body text snippet (first 1000 chars):")
print(soup.body.get_text()[:1000] if soup.body else "No body")
