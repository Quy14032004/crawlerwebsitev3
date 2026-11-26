import re

with open("test_html.html", "r", encoding="utf-8") as f:
    html = f.read()

# Find all script tags using regex
pattern = re.compile(r'<script([^>]*)>(.*?)</script>', re.DOTALL)
matches = pattern.findall(html)

print(f"Total script tags found: {len(matches)}")

with open("scratch/scripts_extracted.txt", "w", encoding="utf-8") as out:
    for i, (attrs, content) in enumerate(matches):
        out.write(f"=== Script {i} (attrs: {attrs.strip()}) ===\n")
        content = content.strip()
        if content:
            if len(content) > 1500:
                out.write(content[:1500] + f"\n... [TRUNCATED {len(content)} chars] ...\n")
            else:
                out.write(content + "\n")
        out.write("\n\n")

print("Finished extracting scripts!")
