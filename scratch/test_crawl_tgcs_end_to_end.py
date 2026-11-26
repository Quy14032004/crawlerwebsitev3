import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import shutil
import openpyxl
from PIL import Image
from app.crawlerTheGioiCapServo import TheGioiCapServoCrawler

def test_crawl_end_to_end():
    test_output = os.path.abspath("scratch/test_tgcs_output")
    if os.path.exists(test_output):
        shutil.rmtree(test_output)
        
    crawler = TheGioiCapServoCrawler(output_root=test_output, max_workers=2)
    test_urls = [
        "https://thegioicapservo.com/cap-dong-luc-mitsubishi/MR-PWS1CBL1M-A1-L"
    ]
    
    print("[1] Chạy crawler...")
    products, result_dir = crawler.crawl_products(test_urls)
    print(f"[OK] Đã xử lý {len(products)} sản phẩm.")
    print(f"Folder kết quả: {result_dir}")
    
    # Kiểm tra file ZIP
    zip_files = [f for f in os.listdir(result_dir) if f.endswith('.zip')]
    print(f"[2] File ZIP: {zip_files}")
    assert len(zip_files) > 0, "Không tìm thấy file ZIP!"
    
    # Kiểm tra thư mục Anh
    single_dir = os.path.join(result_dir, "san_pham_le")
    img_dir = os.path.join(single_dir, "Anh")
    assert os.path.exists(img_dir), "Thư mục ảnh không tồn tại!"
    
    webp_files = [f for f in os.listdir(img_dir) if f.endswith('.webp')]
    print(f"[3] File ảnh WebP: {webp_files}")
    assert len(webp_files) > 0, "Không có file ảnh WebP nào!"
    
    # Kiểm tra kích thước ảnh
    for wf in webp_files:
        img_path = os.path.join(img_dir, wf)
        with Image.open(img_path) as im:
            print(f"   Ảnh: {wf} - Size: {im.size} - Format: {im.format}")
            assert im.size == (800, 800), f"Kích thước ảnh {im.size} không phải 800x800!"
            assert im.format == 'WEBP', f"Format {im.format} không phải WEBP!"
            
    # Kiểm tra file Excel
    excel_file = os.path.join(single_dir, "san_pham_le.xlsx")
    assert os.path.exists(excel_file), "File Excel không tồn tại!"
    
    wb = openpyxl.load_workbook(excel_file)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    print(f"[4] Các cột trong Excel: {headers}")
    
    assert "Tên ảnh" in headers, "Cột 'Tên ảnh' không có trong Excel!"
    assert "Thông số kỹ thuật" in headers, "Cột 'Thông số kỹ thuật' không có trong Excel!"
    
    # Kiểm tra dòng dữ liệu
    row2 = [cell.value for cell in ws[2]]
    col_map = {h: v for h, v in zip(headers, row2)}
    print(f"[5] Dữ liệu sản phẩm:")
    print(f"   - Mã sản phẩm: {col_map.get('Mã sản phẩm')}")
    print(f"   - Tên sản phẩm: {col_map.get('Tên sản phẩm')}")
    print(f"   - Tên ảnh: {col_map.get('Tên ảnh')}")
    
    specs = col_map.get('Thông số kỹ thuật', '')
    assert 'Haiphongtech.vn' in specs, "Bảng thông số không có Copyright Haiphongtech.vn!"
    assert 'id="specifications"' in specs, "Bảng thông số không có id='specifications'!"
    print("   - Copyright Haiphongtech.vn: ĐÃ CÓ")
    print("   - Table specifications: ĐÃ CÓ")
    
    print("\n===> TẤT CẢ KIỂM THỬ THÀNH CÔNG RỰC RỠ! <===")

if __name__ == '__main__':
    test_crawl_end_to_end()
