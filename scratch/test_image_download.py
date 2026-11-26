import requests
import cloudscraper

image_url = "https://www.festo.com/media/catalog/157441_1056x1024-j.jpg"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'https://www.festo.com/'
}

# Try standard requests
try:
    r = requests.get(image_url, headers=headers, timeout=10)
    print(f"Requests status: {r.status_code}, Length: {len(r.content)}")
except Exception as e:
    print(f"Requests failed: {e}")

# Try cloudscraper
try:
    scraper = cloudscraper.create_scraper()
    r = scraper.get(image_url, headers=headers, timeout=10)
    print(f"Cloudscraper status: {r.status_code}, Length: {len(r.content)}")
except Exception as e:
    print(f"Cloudscraper failed: {e}")
