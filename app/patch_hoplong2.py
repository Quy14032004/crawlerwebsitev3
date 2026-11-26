import sys

file_path = r'c:\Users\khazi\OneDrive\Desktop\BackUp\crawlerwebsitev3\app\crawlerHopLong.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if i == 1634:
        new_lines.append("            product_code = \"\"\n")
        new_lines.append("            if sku_text:\n")
        new_lines.append("                import re\n")
        new_lines.append("                product_code = re.sub(r\"(?i)^\\s*mã\\s*sản\\s*phẩm\\s*:\\s*\", \"\", sku_text).strip()\n")
        new_lines.append("                # Fix encoding artifact\n")
        new_lines.append("                if \"M\" in product_code and \"s\" in product_code and \"ph\" in product_code:\n")
        new_lines.append("                    product_code = re.sub(r\"(?i)^.*?:\\s*\", \"\", product_code).strip()\n")
        new_lines.append("            if not product_code and product_url:\n")
        new_lines.append("                import re\n")
        new_lines.append("                match = re.search(r\"/products/([^/?]+)\", product_url)\n")
        new_lines.append("                if match:\n")
        new_lines.append("                    product_code = match.group(1).upper()\n")
    elif i == 1635:
        # Skip the original line 1635: product_code = re.sub(r'^\s*MÃ£\s*sáº£n\s*pháº©m\s*:\s*', '', sku_text, flags=re.IGNORECASE).strip()
        pass
    else:
        new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Patch applied successfully')
