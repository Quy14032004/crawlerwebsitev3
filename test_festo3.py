import requests
url = "https://www.festo.com/vn/vi/a/8192260/"
resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
print("Table container in HTML?", "technical-data-table" in resp.text)
