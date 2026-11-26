from app.crawlerFesto import get_html_with_selenium
from bs4 import BeautifulSoup

def test_page(page):
    url = f"https://www.festo.com/vn/vi/p/ball-screw-axis-id_ELGD_BS/?page={page}"
    print(f"Testing URL: {url}")
    html = get_html_with_selenium(url)
    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.select('.single-article--cNEBP, [data-testid="suggested-article"]')
    print(f"Page {page} has {len(articles)} articles.")
    if articles:
        name_elem = articles[0].select_one('.triad-order-code--wX7MQ, [class*="triad-order-code"]')
        if name_elem:
            print(f"First item: {name_elem.get_text(strip=True)}")

test_page(1)
test_page(2)
test_page(3)
