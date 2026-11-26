import hashlib
import os
import re
import sqlite3
import requests
from typing import Dict, List, Optional, Tuple
import threading

# Cấu hình LibreTranslate
LIBRETRANSLATE_URL = os.getenv("LIBRETRANSLATE_URL", "http://localhost:5000")
LIBRETRANSLATE_API_KEY = os.getenv("LIBRETRANSLATE_API_KEY", "")

# Đường dẫn DB cache
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "translation_cache.db")

MAX_CHARS = 5000

# Khóa thread-safe cho SQLite khi dùng chung nhiều crawler
db_lock = threading.Lock()

# Từ điển kỹ thuật chuyên ngành (Công nghiệp, Nguồn điện, Cảm biến)
TECHNICAL_DICT = {
    "RIPPLE & NOISE": "Độ gợn & Nhiễu",
    "VOLTAGE TOLERANCE": "Dung sai điện áp",
    "LINE REGULATION": "Ổn định điện áp lưới",
    "LOAD REGULATION": "Ổn định tải",
    "SETUP, RISE TIME": "Thời gian khởi động, tăng tốc",
    "HOLD UP TIME": "Thời gian lưu điện",
    "VOLTAGE RANGE": "Dải điện áp",
    "FREQUENCY RANGE": "Dải tần số",
    "EFFICIENCY": "Hiệu suất",
    "AC CURRENT": "Dòng điện xoay chiều",
    "INRUSH CURRENT": "Dòng khởi động (Inrush)",
    "LEAKAGE CURRENT": "Dòng rò",
    "OVERLOAD": "Quá tải",
    "OVER VOLTAGE": "Quá áp",
    "OVER TEMPERATURE": "Quá nhiệt",
    "WORKING TEMP.": "Nhiệt độ hoạt động",
    "WORKING HUMIDITY": "Độ ẩm hoạt động",
    "STORAGE TEMP., HUMIDITY": "Nhiệt độ, độ ẩm lưu trữ",
    "TEMP. COEFFICIENT": "Hệ số nhiệt độ",
    "VIBRATION": "Độ rung",
    "SAFETY STANDARDS": "Tiêu chuẩn an toàn",
    "WITHSTAND VOLTAGE": "Điện áp chịu đựng",
    "ISOLATION RESISTANCE": "Điện trở cách điện",
    "EMC EMISSION": "Phát xạ EMC",
    "EMC IMMUNITY": "Miễn nhiễm EMC",
    "MTBF": "Tuổi thọ trung bình (MTBF)",
    "DIMENSION": "Kích thước",
    "PACKING": "Đóng gói",
    "DC VOLTAGE": "Điện áp DC",
    "RATED CURRENT": "Dòng điện định mức",
    "CURRENT RANGE": "Dải dòng điện",
    "RATED POWER": "Công suất định mức",
    "VOLTAGE ADJ. RANGE": "Dải điều chỉnh điện áp",
    "VOLTAGE ADJ": "Dải điều chỉnh điện áp",
    "PROTECTION": "Bảo vệ",
    "ENVIRONMENT": "Môi trường",
    "SAFETY & EMC": "An toàn & EMC",
    "OTHERS": "Khác",
    "OUTPUT": "Đầu ra",
    "INPUT": "Đầu vào"
}

def init_db() -> None:
    """Khởi tạo database cache SQLite nếu chưa có."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with db_lock:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_lang, target_lang, source_hash)
            )
            """
        )
        conn.commit()
        conn.close()

# Tự động khởi tạo DB khi import module
init_db()


def _make_hash(source_lang: str, target_lang: str, text: str) -> str:
    raw = f"{source_lang}:{target_lang}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_from_cache(source_lang: str, target_lang: str, text: str) -> Optional[str]:
    source_hash = _make_hash(source_lang, target_lang, text)
    with db_lock:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT translated_text
            FROM translation_cache
            WHERE source_lang = ? AND target_lang = ? AND source_hash = ?
            LIMIT 1
            """,
            (source_lang, target_lang, source_hash),
        )
        row = cursor.fetchone()
        conn.close()

    if row:
        return row[0]
    return None


def _save_to_cache(source_lang: str, target_lang: str, text: str, translated_text: str) -> None:
    source_hash = _make_hash(source_lang, target_lang, text)
    with db_lock:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO translation_cache (
                source_lang,
                target_lang,
                source_hash,
                source_text,
                translated_text
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_lang, target_lang, source_hash, text, translated_text),
        )
        conn.commit()
        conn.close()


def protect_technical_terms(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Che mã kỹ thuật để tránh bị LibreTranslate dịch sai (hoặc làm hỏng định dạng).
    """
    patterns = [
        # 1. Các dải số / công thức có khoảng trắng (VD: 10 ~ 500 Hz, -20 ~ + 70 °C, 62.5 * 51 * 28)
        r"(?:-|\+)?\d+(?:\.\d+)?\s*(?:-|~|\*|/)\s*(?:-|\+)?\d+(?:\.\d+)?\s*(?:\*\s*\d+(?:\.\d+)?)?\s*[a-zA-Z/%°]*",
        # 2. Bất kỳ từ liền mạch nào CÓ CHỨA CHỮ SỐ (VD: 30m/s, 120mVp-p, RS-15-15, 24VDC, IP67, 10min./1cycle)
        r"\S*\d\S*",
        # 3. Các cụm từ chứa ký tự toán học, công thức và ký tự đặc biệt (VD: L*W*H, <50ms, >100, ON/OFF, A/D, +, -)
        r"\S*[~*<>/\+\-\\_&]\S*",
        # 4. Các ký hiệu I/O đặc thù của nguồn
        r"\b(?:I/P|O/P|F/G|I/P-O/P|I/P-FG|O/P-FG)\S*",
        # 5. Các thuật ngữ chuyên ngành cố định (để nguyên chữ hoa)
        r"\b(?:NPN|PNP|NO|NC|NO/NC|DC|AC|IP[0-9]{2}|M8|M12|M18|M30|PLC|HMI|SSR|TUV|BS|EN|IEC|EAC|CB|CCC|BSMI|BIS)\b",
    ]

    token_map: Dict[str, str] = {}
    protected_text = text
    index = 0

    for pattern in patterns:
        matches = list(re.finditer(pattern, protected_text, flags=re.IGNORECASE))
        for match in matches:
            value = match.group(0)
            if value in token_map.values():
                continue
            
            # Sử dụng token chữ và số để LibreTranslate không dịch
            token = f'HPTTKN{index}END'
            token_map[token] = value
            # Thay thế chính xác (chỉ thế một lần cho mỗi vị trí tìm thấy hoặc thế toàn bộ)
            protected_text = protected_text.replace(value, token, 1)
            index += 1

    return protected_text, token_map


def restore_technical_terms(text: str, token_map: Dict[str, str]) -> str:
    """Khôi phục lại các mã kỹ thuật từ token."""
    restored = text
    for token, value in token_map.items():
        restored = restored.replace(token, value)
        # Dự phòng: đôi khi công cụ dịch tự tách khoảng trắng (VD: HPTTKN 0 END)
        import re
        escaped_token = re.escape(token).replace('END', r'\s*END').replace('HPTTKN', r'HPTTKN\s*')
        restored = re.sub(escaped_token, lambda m: value, restored, flags=re.IGNORECASE)
    return restored


def call_libretranslate(text: str, source_lang: str, target_lang: str, is_html: bool = False) -> str:
    """Gọi API trực tiếp đến LibreTranslate."""
    payload = {
        "q": text,
        "source": source_lang,
        "target": target_lang,
        "format": "html" if is_html else "text",
    }
    if LIBRETRANSLATE_API_KEY:
        payload["api_key"] = LIBRETRANSLATE_API_KEY

    url = f"{LIBRETRANSLATE_URL.rstrip('/')}/translate"
    try:
        response = requests.post(url, json=payload, timeout=90)
        response.raise_for_status()
        data = response.json()
        translated_text = data.get("translatedText")
        if not translated_text:
            print("Lỗi từ LibreTranslate: Không có trường translatedText")
            return text
        return translated_text
    except Exception as e:
        print(f"Lỗi khi kết nối LibreTranslate ({url}): {e}")
        # Trả về text gốc nếu API bị lỗi để tránh gián đoạn hệ thống
        return text


def translate_text(text: str, source_lang: str = "en", target_lang: str = "vi", use_cache: bool = True) -> str:
    """
    Hàm dịch chính để các file crawler import và sử dụng.
    
    Cách dùng:
        from app.libre_translator import translate_text
        vietnamese_text = translate_text("This is a photo sensor E3Z-D61 24VDC.", "en", "vi")
    """
    clean_text = text.strip()
    if not clean_text:
        return ""

    # 0. Tra từ điển chuyên ngành trước khi gọi dịch thuật (chỉ áp dụng nếu đoạn text khớp nguyên vẹn)
    cleaned_upper = re.sub(r'[\.\:\s]+$', '', clean_text.upper())
    for k, v in TECHNICAL_DICT.items():
        if cleaned_upper == k:
            return v
        # Xử lý trường hợp text có chứa hậu tố "(Typ.)"
        if cleaned_upper == f"{k} (TYP.)" or cleaned_upper == f"{k} (TYP)":
            return f"{v} (Typ.)"

    # Tự động nhận diện HTML
    is_html = bool(re.search(r'<[a-z]+[^>]*>', clean_text, re.IGNORECASE))

    if len(clean_text) > MAX_CHARS:
        print(f"Cảnh báo: Đoạn text quá dài ({len(clean_text)} ký tự). Đang bỏ qua dịch hoặc bạn cần chia nhỏ.")
        return clean_text

    if use_cache:
        cached_text = _get_from_cache(source_lang, target_lang, clean_text)
        if cached_text:
            return cached_text

    # 1. Che mã kỹ thuật
    protected_text, token_map = protect_technical_terms(clean_text)

    # Ép LibreTranslate phân tích dưới dạng HTML nếu có mã được che
    if token_map:
        is_html = False # Không dùng dạng HTML nữa để tránh LibreTranslate dịch bậy thẻ

    # 2. Gọi dịch thuật
    raw_translated = call_libretranslate(protected_text, source_lang, target_lang, is_html)

    # Nếu API lỗi và trả về nguyên gốc, ta khôi phục lại và trả về
    if raw_translated == protected_text:
        restored_fail = restore_technical_terms(raw_translated, token_map)
        return restored_fail

    # 3. Khôi phục mã kỹ thuật
    translated_text = restore_technical_terms(raw_translated, token_map)

    # 4. Lưu cache
    if use_cache:
        _save_to_cache(source_lang, target_lang, clean_text, translated_text)

    return translated_text


def translate_batch(texts: List[str], source_lang: str = "en", target_lang: str = "vi", use_cache: bool = True) -> List[str]:
    """Hàm tiện ích dịch nhiều câu."""
    return [translate_text(t, source_lang, target_lang, use_cache) for t in texts]

if __name__ == "__main__":
    # Test script cục bộ
    test_text = "E3Z-D61 Photoelectric Sensor, NPN output, 12-24VDC power supply, IP67 protection."
    print("Bản gốc:", test_text)
    print("Đang dịch...")
    result = translate_text(test_text)
    print("Bản dịch:", result)
