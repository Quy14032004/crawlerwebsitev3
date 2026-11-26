import requests
from bs4 import BeautifulSoup
from app.crawlerFesto import get_html_with_selenium

url = "https://www.festo.com/vn/vi/a/8192260/"
html = get_html_with_selenium(url)
soup = BeautifulSoup(html, 'html.parser')

print("--- SELECTING ACTIVE GALLERY IMAGE ---")

# Try to find current active/current slick slide
active_imgs = []
for img in soup.select('[class*="gallery-image-wrapper"] img, [class*="gallery-image-preview-container"] img, img[class*="image--"]'):
    # Check if inside cloned slide
    is_cloned = False
    parent = img.parent
    while parent and parent.name != 'body':
        classes = parent.get('class', [])
        if any('slick-cloned' in c for c in classes):
            is_cloned = True
            break
        parent = parent.parent
    
    src = img.get('src') or img.get('data-src') or img.get('srcset')
    if src and '/media/catalog/' in src and not src.endswith('.svg'):
        if ',' in src:
            src = src.split(',')[0].strip().split(' ')[0]
        
        # We can also check if it is active/current
        is_active = False
        parent = img.parent
        while parent and parent.name != 'body':
            classes = parent.get('class', [])
            if any('slick-active' in c or 'slick-current' in c for c in classes):
                is_active = True
                break
            parent = parent.parent
            
        print(f"Candidate: {src} | Cloned={is_cloned} | Active={is_active}")
        active_imgs.append((src, is_cloned, is_active))

print("\nBest Selection Logic:")
# Rule: Prefer Active, then not Cloned, then anything
candidates = [img for img in active_imgs if img[2]] # Active
if not candidates:
    candidates = [img for img in active_imgs if not img[1]] # Not cloned
if not candidates:
    candidates = active_imgs

if candidates:
    print("Selected Image:", candidates[0][0])
else:
    print("No Image found")
