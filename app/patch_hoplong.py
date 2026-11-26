import sys
import re

file_path = r'c:\Users\khazi\OneDrive\Desktop\BackUp\crawlerwebsitev3\app\crawlerHopLong.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace block 1 (extract_product_details)
pattern1 = re.compile(
    r"(\s*# MÃ£ sáº£n pháº©m.*?<p class=\"content-meta__sku\">.*?\n"
    r"\s*sku_el = soup\.select_one\('p\.content-meta__sku'\)\n"
    r"\s*sku_text = sku_el\.get_text\(strip=True\) if sku_el else ''\n"
    r"\s*# Láº¥y pháº§n sau \"MÃ£ sáº£n pháº©m:\" \n"
    r"\s*product_code = re\.sub\(r'\^\\s\*MÃ£\\s\*sáº£n\\s\*pháº©m\\s\*:\\s\*', '', sku_text, flags=re\.IGNORECASE\)\.strip\(\))"
)

replacement1 = """            # MÃ£ sáº£n pháº©m: <p class=\"content-meta__sku\">MÃ£ sáº£n pháº©m: E2B-M12KN05-WP-B2 2M OMI</p>
            sku_el = soup.select_one('p.content-meta__sku')
            product_code = \"\"
            if sku_el:
                sku_text = sku_el.get_text(strip=True)
                product_code = re.sub(r'(?i)^\\s*mã\\s*sản\\s*phẩm\\s*:\\s*', '', sku_text).strip()
                # If still contains text due to encoding issues
                product_code = re.sub(r'(?i)^\\s*m\\u00e3\\s*s\\u1ea3n\\s*ph\\u1ea9m\\s*:\\s*', '', product_code).strip()
                product_code = re.sub(r'(?i)^\\s*mÃ£\\s*sáº£n\\s*pháº©m\\s*:\\s*', '', product_code).strip()

            # Fallback URL slug as product code
            if not product_code and product_url:
                match = re.search(r'/products/([^/?]+)', product_url)
                if match:
                    product_code = match.group(1).upper()"""

# We'll use a string replacement to be safer instead of complex regex, but encoding might bite us.
# Let's find the index of "sku_el = soup.select_one('p.content-meta__sku')" at line ~1632
target1_str = "sku_text = sku_el.get_text(strip=True) if sku_el else ''"
idx = content.find(target1_str)
if idx != -1:
    end_idx = content.find("logger.debug(f\"ðŸ ·ï¸  Product code: {product_code}\")", idx)
    start_idx = content.rfind("sku_el = soup.select_one('p.content-meta__sku')", 0, idx)
    
    if start_idx != -1 and end_idx != -1:
        content = content[:start_idx] + replacement1 + "\n            " + content[end_idx:]


# Replace block 2 (_scrape_product)
target2_str = "sku_el = soup.select_one(\"p.content-meta__sku\")"
idx2 = content.find(target2_str, end_idx if idx != -1 else 0)
if idx2 != -1:
    end_idx2 = content.find("price = \"\"", idx2)
    
    replacement2 = """sku_el = soup.select_one(\"p.content-meta__sku\")
        sku = \"\"
        if sku_el:
            sku_text = sku_el.get_text(strip=True)
            sku = re.sub(r'(?i)^\\s*mã\\s*sản\\s*phẩm\\s*:\\s*', '', sku_text).strip()
            sku = re.sub(r'(?i)^\\s*m\\u00e3\\s*s\\u1ea3n\\s*ph\\u1ea9m\\s*:\\s*', '', sku).strip()
            sku = re.sub(r'(?i)^\\s*mÃ£\\s*sáº£n\\s*pháº©m\\s*:\\s*', '', sku).strip()
            
        if not sku and url:
            match = re.search(r'/products/([^/?]+)', url)
            if match:
                sku = match.group(1).upper()
                
        """
    
    if end_idx2 != -1:
        content = content[:idx2] + replacement2 + content[end_idx2:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("done")
