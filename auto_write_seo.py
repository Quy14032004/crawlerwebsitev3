import os
import json
import time
from pathlib import Path
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import ollama

# Cấu hình Google Sheets
SCOPES = ['https://www.googleapis.com/auth/drive']
SPREADSHEET_ID = '1tXNGCpgObf0X2R5XVjJjaH5lSZJdv_XUKV8Ri1cXdg8'
SHEET_NAME = 'Sheet1' # Tên sheet của bạn
START_ROW = 2 # Bắt đầu chạy từ dòng 2
END_ROW = 10 # Chạy đến dòng 10 (Sửa lại theo nhu cầu của bạn)

# Thư mục gốc
BASE_DIR = Path(__file__).parent
CREDENTIALS_FILE = BASE_DIR / 'app' / 'credentials.json'
TOKEN_FILE = BASE_DIR / 'token.json'

def get_sheets_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token cũ đã hỏng ({e}), tiến hành đăng nhập lại...")
                creds = None
                
        if not creds:
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            flow.redirect_uri = 'http://localhost:8009/oauth_setup.php'
            
            auth_url, _ = flow.authorization_url(prompt='consent')
            print("\n" + "="*60)
            print("VUI LÒNG ĐĂNG NHẬP ĐỂ CẤP QUYỀN GOOGLE SHEETS:")
            print("1. Hãy click (hoặc copy) đường link dưới đây và mở bằng trình duyệt:")
            print(f"\n{auth_url}\n")
            print("2. Đăng nhập và bấm 'Cho phép' (Allow).")
            print("3. Sau đó, trình duyệt sẽ chuyển hướng đến một trang trắng (hoặc báo lỗi 404).")
            print("4. HÃY COPY TOÀN BỘ ĐƯỜNG LINK (URL) ở thanh địa chỉ của trang đó.")
            print("5. Dán toàn bộ đường link đó vào dòng bên dưới rồi nhấn Enter.")
            print("="*60 + "\n")
            
            auth_response = input("👉 Dán link vừa copy vào đây: ").strip()
            flow.fetch_token(authorization_response=auth_response)
            creds = flow.credentials
            
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
            
    return build('sheets', 'v4', credentials=creds)

def call_ollama(ten_san_pham, thuong_hieu):
    prompt = f"""
Viết một bài giới thiệu sản phẩm chuẩn SEO, độ dài khoảng 500 từ, định dạng HTML.

⚠️ Yêu cầu:
✅ Chỉ xuất ra đúng mã HTML nằm trong cặp thẻ <body>...</body>, không thêm bất kỳ đoạn mã nào khác.
✅ Nếu tên sản phẩm chứa kích thước (ví dụ: 11mm, 20mm...), hãy loại bỏ thông số khỏi tiêu đề tổng quan, chỉ ghi trong mô tả với cách diễn đạt như: "khoảng cách đo ..." hoặc "khoảng cách phát hiện ...".
✅ Không sử dụng CSS, không chèn bất kỳ đoạn CSS nào.
✅ Không chèn icon dạng hình ảnh base64 hoặc HTML. Nếu cần biểu tượng, chỉ dùng emoji đơn giản như: 🔧, ⚙️, 📦, 🚀, ✅...
✅ Không lặp lại các đoạn văn mẫu. Viết tự nhiên, đúng chuyên ngành thiết bị công nghiệp.
✅ Tối đa 3 lần nhắc tên sản phẩm "{ten_san_pham}" trong toàn bài, phân bổ hợp lý vào tiêu đề, mở đầu, đoạn kết và 1–2 chỗ trong thân bài.
✅ Không chèn bảng thông số kỹ thuật.
✅ Không bịa đặt thông tin.

💡 Sản phẩm: {ten_san_pham} – một thiết bị công nghiệp đến từ thương hiệu {thuong_hieu}.

💡 Cấu trúc bài viết:
<body>
<h2>Tổng quan về {ten_san_pham}</h2>

<h3>1. Giới Thiệu về sản phẩm</h3>
<ul>
  <li>Giới thiệu dòng thiết bị, ứng dụng trong lĩnh vực tự động hóa công nghiệp</li>
  <li>Nguyên lý hoạt động cơ bản của loại cảm biến sợi quang</li>
  <li>Nêu điểm mạnh như độ nhạy, độ bền, thiết kế tối ưu</li>
  <li>So sánh nhẹ với một số dòng cảm biến thông dụng khác</li>
</ul>

<h3>2. Ứng Dụng Thực Tế</h3>
<ul>
  <li>Phát hiện vật thể trong dây chuyền sản xuất</li>
  <li>Kiểm tra vị trí, giám sát lỗi hoặc theo dõi trạng thái</li>
  <li>Hệ thống đóng gói tự động, máy gia công CNC, và băng tải thông minh</li>
</ul>

<h3>3. Ưu Điểm Nổi Bật</h3>
<ul>
  <li>Độ chính xác cao trong phát hiện vật thể nhỏ</li>
  <li>Khả năng chống nhiễu điện từ tốt</li>
  <li>Dễ dàng lắp đặt và kết nối với bộ khuếch đại</li>
  <li>Tuổi thọ dài, hoạt động ổn định trong môi trường khắc nghiệt</li>
</ul>

<h3>4. Mua {ten_san_pham} Chính Hãng Ở Đâu?</h3>
<p>🔹 <a href='https://haiphongtech.vn'><strong>Hải Phòng Tech</strong></a> – Nhà cung cấp thiết bị công nghiệp uy tín, cung cấp chính hãng {thuong_hieu} với đầy đủ chứng từ.</p>
<p>🌐 <strong>Website:</strong> haiphongtech.vn<br />
📧 <strong>Email:</strong> kinhdoanhhpt@haiphongtech.vn<br />
📞 <strong>Hotline:</strong> 0866 555 212 </p>
<p>🎯 <strong>Lý do chọn Hải Phòng Tech?</strong><br />
✔ Sản phẩm chính hãng 100%<br />
✔ Kỹ thuật viên hỗ trợ tận nơi<br />
✔ Giao hàng toàn quốc nhanh chóng<br />
✔ Giá cả cạnh tranh – báo giá rõ ràng</p>
<p>👉 <strong>Liên hệ ngay để nhận báo giá ưu đãi và tài liệu kỹ thuật!</strong> 🚀</p>
</body>
"""
    system_prompt = "You are an expert SEO article writer. Output ONLY the raw HTML text requested by the user, starting immediately with <body>. DO NOT use markdown code blocks like ```html ... ```."
    
    print(f"🔄 Đang nhờ Ollama viết bài cho {ten_san_pham}...")
    try:
        # Bạn có thể đổi 'llama3' thành model khác nhanh hơn như 'gemma:2b' hoặc 'qwen:0.5b'
        response = ollama.chat(
            model='llama3',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': prompt}
            ]
        )
        content = response['message']['content'].strip()
        
        # Dọn dẹp markdown nếu có
        if content.startswith('```html'):
            content = content[7:]
        if content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]
            
        return content.strip()
    except Exception as e:
        print(f"❌ Lỗi khi gọi Ollama: {e}")
        return None

def main():
    if SPREADSHEET_ID == 'ĐIỀN_ID_GOOGLE_SHEETS_CỦA_BẠN_VÀO_ĐÂY':
        print("❌ LỖI: Bạn chưa điền SPREADSHEET_ID trong file auto_write_seo.py")
        return
        
    print("🔌 Đang kết nối tới Google Sheets...")
    try:
        service = get_sheets_service()
        sheet = service.spreadsheets()
    except Exception as e:
        print(f"❌ Lỗi kết nối Google Sheets: {e}")
        return
        
    # Đọc dữ liệu từ cột A (Tên SP) và cột R (Thương hiệu)
    range_name = f"{SHEET_NAME}!A{START_ROW}:R{END_ROW}"
    print(f"📖 Đang đọc dữ liệu từ {range_name}...")
    
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=range_name).execute()
    rows = result.get('values', [])
    
    if not rows:
        print("⚠️ Không tìm thấy dữ liệu.")
        return

    # Mặc định lấy Thương hiệu ở R4 làm fallback
    thuong_hieu_fallback = ""
    try:
        # Đọc trực tiếp R4 (Dòng 4, Cột R có index 17)
        fallback_result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!R4").execute()
        thuong_hieu_fallback = fallback_result.get('values', [['']])[0][0]
    except:
        pass

    for i, row in enumerate(rows):
        current_row_number = START_ROW + i
        
        if not row or len(row) == 0:
            continue
            
        ten_san_pham = row[0]
        if not ten_san_pham:
            continue
            
        thuong_hieu = ""
        if len(row) > 17: # Cột R
            thuong_hieu = row[17]
            
        if not thuong_hieu:
            thuong_hieu = thuong_hieu_fallback
            
        print(f"\n--- Bắt đầu xử lý dòng {current_row_number}: {ten_san_pham} ---")
        
        article_html = call_ollama(ten_san_pham, thuong_hieu)
        
        if article_html:
            # Ghi vào Cột B
            write_range = f"{SHEET_NAME}!B{current_row_number}"
            body = {
                'values': [[article_html]]
            }
            try:
                sheet.values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=write_range,
                    valueInputOption='RAW',
                    body=body
                ).execute()
                print(f"✅ Đã ghi thành công bài viết vào {write_range}!")
            except Exception as e:
                print(f"❌ Lỗi khi ghi vào Google Sheets: {e}")
        else:
            print("⚠️ Không có bài viết nào được tạo.")
            
        # Nghỉ 2 giây để tránh bị spam API Google
        time.sleep(2)

if __name__ == '__main__':
    main()
