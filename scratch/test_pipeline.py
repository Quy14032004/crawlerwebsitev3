import sys, os
sys.path.insert(0, os.path.abspath('.'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

print("1. Importing CDHACrawler...")
from app.crawlerCDHA import CDHACrawler

print("2. Initializing CDHACrawler...")
crawler = CDHACrawler(output_root='data/test_output_cdha', max_workers=1, max_retries=2)

print("3. Calling crawl_products...")
urls = ['https://codienhaiau.com/product/bien-tan-shihlin-sl3-043-1-5k-1-5kw-3-pha-380v/']
products, result_dir = crawler.crawl_products(urls)

print("4. Done!")
print('Products processed:', len(products))
print('Result dir:', result_dir)
