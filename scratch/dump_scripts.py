from bs4 import BeautifulSoup
import re

with open("test_html.html", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')
scripts = soup.find_all('script')

with open("scratch/scripts_extracted.txt", "w", encoding="utf-8") as out:
    for i, s in enumerate(scripts):
        src = s.get('src', '')
        id_attr = s.get('id', '')
        klass = s.get('class', '')
        content = s.string or ""
        out.write(f"=== Script {i} (id='{id_attr}', class='{klass}', src='{src}') ===\n")
        if content:
            # format content slightly
            if len(content) > 1000:
                out.write(content[:1000] + f"\n... [TRUNCATED {len(content)} chars] ...\n")
            else:
                out.write(content + "\n")
        out.write("\n\n")

print("Done! Extracted scripts to scratch/scripts_extracted.txt")
