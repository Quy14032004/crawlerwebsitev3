from app.crawlerFesto import extract_festo_single_product_info
import requests

url = "https://www.festo.com/vn/vi/a/8192260/"
session = requests.Session()
info = extract_festo_single_product_info(url, session=session)

print("Name:", info.get('name'))
print("Code:", info.get('code'))
print("Part Number:", info.get('part_number'))
print("Price:", info.get('price'))
print("Image URL:", info.get('image_url'))
print(f"Specs count: {len(info.get('specifications', {}))}")
for k, v in list(info.get('specifications', {}).items())[:10]:
    print(f"  {k}: {v}")
