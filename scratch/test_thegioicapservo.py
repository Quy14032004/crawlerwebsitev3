import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'vi,en;q=0.9',
}

# Use session
session = requests.Session()
# Test with custom headers that won't trigger 403
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
})

def fetch_page(url):
    # Try different user agents if needed
    uas = [
        'curl/8.4.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0'
    ]
    for ua in uas:
        try:
            r = requests.get(url, headers={'User-Agent': ua}, timeout=15)
            if r.status_code == 200:
                return r.text
        except Exception as e:
            pass
    return None

def test():
    cat_url = 'https://thegioicapservo.com/cap-dong-luc-mitsubishi'
    html = fetch_page(cat_url)
    if not html:
        print("Failed to fetch category page")
        return

    soup = BeautifulSoup(html, 'html.parser')
    
    # Category name
    title = soup.select_one('h1, .breadcrumb li.active, .breadcrumb-item.active')
    print("Category Title:", title.get_text(strip=True) if title else "N/A")

    # Products in category
    product_items = soup.select('.product-listing__collection .product')
    print(f"Total product elements found: {len(product_items)}")
    
    product_urls = []
    for item in product_items:
        a = item.select_one('.product_img a, .product_name a, a.btn-info')
        if a and a.get('href'):
            href = urljoin(cat_url, a['href'])
            if href not in product_urls:
                product_urls.append(href)
                
    print(f"Unique product URLs: {len(product_urls)}")
    for u in product_urls[:3]:
        print(" -", u)

    # Check pagination
    # OpenCart pagination usually looks like <div class="pagination">...</div> or <ul class="pagination">...
    pag = soup.select('.pagination, .paging, ul.pagination, .product-filter__pagination')
    print("Pagination found:", len(pag))
    if pag:
        print("Pagination text:", pag[0].get_text(strip=True))
        for a in pag[0].select('a'):
            print("  Link:", a.get('href'), "Text:", a.get_text(strip=True))
            
    # Now test a single product detail page
    prod_url = product_urls[0] if product_urls else 'https://thegioicapservo.com/cap-dong-luc-mitsubishi/MR-PWS1CBL1M-A1-L'
    print("\n--- Testing Product Detail ---")
    print("URL:", prod_url)
    p_html = fetch_page(prod_url)
    if not p_html:
        print("Failed to fetch product detail page")
        return
        
    p_soup = BeautifulSoup(p_html, 'html.parser')
    
    # 1. Product Code
    # <div class="product_vendor">Dòng sản phẩm:: <a title="MR-PWS1CBL1M-A1-L">MR-PWS1CBL1M-A1-L</a></div>
    code_el = p_soup.select_one('.product_vendor a, .product_vendor')
    code = ""
    if code_el:
        if code_el.name == 'a':
            code = code_el.get_text(strip=True)
        else:
            a_tag = code_el.select_one('a')
            if a_tag:
                code = a_tag.get_text(strip=True)
            else:
                code = code_el.get_text(strip=True).replace('Dòng sản phẩm::', '').replace('Dòng sản phẩm:', '').strip()
    print("Product Code:", code)
    
    # 2. Product Name
    name_el = p_soup.select_one('.product_name[itemprop="name"], h1.product_name, .product_info .product_name')
    name = name_el.get_text(strip=True) if name_el else ""
    print("Product Name:", name.encode('utf-8', 'ignore').decode('utf-8'))
    
    # 3. Product Price
    price_el = p_soup.select_one('.product_price .money, .price-new, .product-price')
    price = price_el.get_text(strip=True) if price_el else ""
    print("Product Price:", price.encode('utf-8', 'ignore').decode('utf-8'))
    
    # 4. Images
    # Main image element
    # User noted:
    # background-image: url("https://thegioicapservo.com/image/cache/catalog/cap-mitsubishi/z7270058379931_5a2863ded4aa6d66c25c410222818080-800x800.jpg")
    img_urls = []
    # Check zoomWindow or product image gallery
    for img in p_soup.select('#image, .product_image_main img, .thumbnails img, #default_gallery img, .image-additional img, .product-image img'):
        src = img.get('data-zoom-image') or img.get('src')
        if src:
            src = urljoin(prod_url, src)
            if src not in img_urls:
                img_urls.append(src)
                
    for a in p_soup.select('.thumbnails a, .image-additional a, a.thumbnail'):
        href = a.get('href')
        if href and ('/catalog/' in href or '/image/' in href):
            href = urljoin(prod_url, href)
            if href not in img_urls:
                img_urls.append(href)
                
    # Also check zoomWindow in HTML if present
    for el in p_soup.find_all(attrs={'data-zoom-image': True}):
        zi = urljoin(prod_url, el['data-zoom-image'])
        if zi not in img_urls:
            img_urls.append(zi)
            
    print(f"Product Images found ({len(img_urls)}):")
    for img in img_urls:
        print(" -", img)
        
    # 5. Description / Specs
    desc_el = p_soup.select_one('#tab-description, .tab-pane#tab-description')
    if desc_el:
        print("\n--- Description HTML sample ---")
        paragraphs = desc_el.find_all(['p', 'div', 'li', 'h2', 'h3', 'h4', 'table'])
        print(f"Total blocks in description: {len(paragraphs)}")
        for i, p in enumerate(paragraphs[:15]):
            txt = p.get_text(strip=True).encode('utf-8', 'ignore').decode('utf-8')
            if txt:
                print(f"[{p.name}] {txt}")

if __name__ == '__main__':
    test()
