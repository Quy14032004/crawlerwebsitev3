from app.crawlerFesto import get_html_with_selenium
from bs4 import BeautifulSoup

def test_page_7():
    url = "https://www.festo.com/vn/vi/p/ball-screw-axis-id_ELGD_BS/?page=7"
    print(f"Testing URL: {url}")
    html = get_html_with_selenium(url)
    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.select('.single-article--cNEBP, [data-testid="suggested-article"]')
    print(f"Page 7 has {len(articles)} articles.")
    for i, a in enumerate(articles):
        name_elem = a.select_one('.triad-order-code--wX7MQ, [class*="triad-order-code"]')
        name = name_elem.get_text(strip=True) if name_elem else "suggested"
        print(f"  {i+1}: {name}")

test_page_7()
