from app.crawlerFesto import get_html_with_selenium
html = get_html_with_selenium("https://www.festo.com/vn/vi/a/8192260/")
with open("test_html.html", "w", encoding="utf-8") as f:
    f.write(html)
