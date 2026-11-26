import re

with open("test_html.html", "r", encoding="utf-8") as f:
    html = f.read()

# Look for URLs starting with / or http containing api or modern
urls = re.findall(r'"(https?://[^"]+)"', html)
urls += re.findall(r'"(/[a-zA-Z0-9_\-\./]+)"', html)

distinct_urls = set(urls)
print(f"Total distinct URLs/paths: {len(distinct_urls)}")

print("\nInteresting API paths/URLs:")
for u in sorted(distinct_urls):
    if any(k in u.lower() for k in ['api', 'graphql', 'modern', 'catalog', 'product', 'detail', 'spec']):
        print(f"  - {u}")
