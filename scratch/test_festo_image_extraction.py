import os
import sys
import base64
from bs4 import BeautifulSoup
from app.crawlerFesto import extract_festo_single_product_info, get_thread_driver, close_thread_drivers, re

os.makedirs("test_festo_out", exist_ok=True)
url = "https://www.festo.com/vn/vi/a/8192260/"

print("Extracting product info...")
prod = extract_festo_single_product_info(url)
print("Product Info:", prod)

if prod and prod.get("image_url"):
    driver = get_thread_driver()
    image_url = prod["image_url"]
    high_res_url = re.sub(r'_\d+x\d+', '_1056x1024', image_url)
    print(f"Original URL: {image_url}")
    print(f"High Res URL: {high_res_url}")
    
    script = """
    var callback = arguments[arguments.length - 1];
    var targetUrl = arguments[0];
    var fallbackUrl = arguments[1];
    
    function tryFetch(url, isFallback) {
        fetch(url)
          .then(response => {
            if (!response.ok) {
                throw new Error("HTTP error " + response.status);
            }
            return response.arrayBuffer();
          })
          .then(buffer => {
            var binary = '';
            var bytes = new Uint8Array(buffer);
            for (var i = 0; i < bytes.byteLength; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            callback(JSON.stringify({url: url, size: bytes.byteLength, data: window.btoa(binary)}));
          })
          .catch(err => {
            if (!isFallback && fallbackUrl && fallbackUrl !== url) {
                tryFetch(fallbackUrl, true);
            } else {
                callback(JSON.stringify({error: err.toString()}));
            }
          });
    }
    tryFetch(targetUrl, false);
    """
    
    driver.set_script_timeout(30)
    import json
    res_str = driver.execute_async_script(script, high_res_url, image_url)
    res = json.loads(res_str)
    
    if "error" in res:
        print("Fetch failed:", res["error"])
    else:
        print(f"Success! URL fetched: {res['url']}, Size in bytes: {res['size']}")
        # Save to file
        img_data = base64.b64decode(res["data"])
        with open("test_festo_out/raw_image.jpg", "wb") as f:
            f.write(img_data)
        from PIL import Image
        img = Image.open("test_festo_out/raw_image.jpg")
        print(f"Saved JPEG image size: {img.size}, format: {img.format}")

close_thread_drivers()
