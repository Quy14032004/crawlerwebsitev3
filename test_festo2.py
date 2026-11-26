from app.crawlerFesto import get_html_with_selenium
from bs4 import BeautifulSoup

def test():
    url = "https://www.festo.com/vn/vi/p/ball-screw-axis-id_ELGD_BS/"
    html = get_html_with_selenium(url)
    soup = BeautifulSoup(html, 'html.parser')
    max_page = soup.select_one('.fwe-page-max')
    print("Max page:", max_page.get_text(strip=True) if max_page else None)
    
    # Try to find a total count element
    total = soup.select('.number-of-results, .total-results, .result-count, span[class*="count"]')
    for t in total:
        print("Count elem:", t.get_text(strip=True))
        
    print("HTML length:", len(html))
test()
