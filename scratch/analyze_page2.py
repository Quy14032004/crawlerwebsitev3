from bs4 import BeautifulSoup

with open("scratch_page2.html", "r", encoding="utf-8") as f:
    html = f.read()
    
soup = BeautifulSoup(html, 'html.parser')
print("Length:", len(html))
print("Title:", soup.title.string if soup.title else "No title")
print("Body snippet:")
print(soup.body.get_text()[:1000] if soup.body else "No body")

# Search for any articles
articles = soup.select('.single-article--cNEBP, [data-testid="suggested-article"]')
print("Articles count:", len(articles))

# Let's see if there is any other selectors for products
import re
print("Matches for 'SOEG':", len(soup.find_all(text=re.compile('SOEG', re.I))))
