import re
import pandas as pd
import os
from urllib.parse import urlparse
import zipfile
import traceback
import unicodedata
import logging
import time

logger = logging.getLogger(__name__)

# Import Gemini AI
GEMINI_AVAILABLE = False
try:
    # Ẩn cảnh báo FutureWarning từ google.generativeai trước khi import
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=FutureWarning)
        import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Global Gemini model instance / trạng thái
_gemini_model = None
_gemini_api_key = None
_gemini_disabled_reason = None
_gemini_disabled_at = None

# Trạng thái Google Translate fallback
_googletrans_disabled = False
_googletrans_disabled_reason = None

def is_gemini_disabled() -> bool:
    """Kiểm tra Gemini đã bị disable do lỗi nghiêm trọng chưa."""
    return _gemini_disabled_reason is not None

def is_valid_url(url):
    """
    Kiểm tra URL có hợp lệ không
    """
    pattern = r'^https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)$'
    return bool(re.match(pattern, url))
def save_to_excel(data_list, file_path):
    """
    Lưu danh sách dữ liệu vào file Excel
    """
    # Tạo thư mục chứa file nếu chưa tồn tại
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # Tạo DataFrame từ danh sách dữ liệu
    df = pd.DataFrame(data_list)
    
    # Lưu vào file Excel
    df.to_excel(file_path, index=False)
    
    return file_path 

def create_zip_from_folder(folder_path, zip_path):
    """
    Tạo file ZIP từ thư mục
    
    Args:
        folder_path (str): Đường dẫn tới thư mục cần nén
        zip_path (str): Đường dẫn đầu ra cho file ZIP
        
    Returns:
        bool: True nếu thành công, False nếu thất bại
    """
    try:
        print(f"Bắt đầu tạo file ZIP từ thư mục: {folder_path}")
        print(f"File ZIP sẽ được lưu tại: {zip_path}")
        
        # Đảm bảo thư mục cha của file ZIP tồn tại
        zip_dir = os.path.dirname(zip_path)
        if not os.path.exists(zip_dir):
            os.makedirs(zip_dir)
            print(f"Đã tạo thư mục: {zip_dir}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Duyệt qua tất cả các file trong thư mục
            for root, dirs, files in os.walk(folder_path):
                print(f"Đang quét thư mục: {root}")
                print(f"Tìm thấy {len(files)} files")
                
                for file in files:
                    # Tạo đường dẫn đầy đủ đến file
                    file_path = os.path.join(root, file)
                    # Tạo tên cho file trong ZIP (đường dẫn tương đối)
                    arcname = os.path.relpath(file_path, folder_path)
                    print(f"Thêm file vào ZIP: {arcname}")
                    # Thêm file vào ZIP
                    zipf.write(file_path, arcname)
        
        # Kiểm tra file ZIP đã được tạo
        if os.path.exists(zip_path):
            zip_size = os.path.getsize(zip_path)
            print(f"File ZIP đã được tạo thành công: {zip_path} (Kích thước: {zip_size} bytes)")
            return True
        else:
            print(f"Không tìm thấy file ZIP sau khi tạo: {zip_path}")
            return False
            
    except Exception as e:
        print(f"Lỗi khi tạo file ZIP: {str(e)}")
        print(f"Chi tiết lỗi: {traceback.format_exc()}")
        return False 

def slug(title: str, separator: str = '-', language: str = 'en', dictionary: dict = {'@': 'at'}) -> str:
    def to_ascii(text: str, lang: str = 'en') -> str:
        # Chuyển đổi chuỗi unicode sang ASCII (loại bỏ dấu tiếng Việt...)
        text = unicodedata.normalize('NFKD', text)
        return text.encode('ascii', 'ignore').decode('ascii')

    def to_lower(text: str) -> str:
        return text.lower()

    if language:
        title = to_ascii(title, language)

    # Convert all dashes/underscores into separator
    flip = '_' if separator == '-' else '-'
    title = re.sub(f"[{re.escape(flip)}]+", separator, title)

    # Replace dictionary words like '@' => 'at'
    for key, val in dictionary.items():
        title = title.replace(key, f"{separator}{val}{separator}")

    # Remove all characters except letters, numbers, separators, and whitespace
    title = to_lower(title)
    title = re.sub(rf"[^{re.escape(separator)}\w\s]", '', title, flags=re.UNICODE)

    # Replace all whitespace or repeated separators with single separator
    title = re.sub(rf"[{re.escape(separator)}\s]+", separator, title)

    return title.strip(separator)

def standardize_filename(product_code):
    """
    Chuẩn hóa tên file từ mã sản phẩm.
    Sử dụng hàm slug để chuẩn hóa tên file, loại bỏ dấu tiếng Việt và các ký tự đặc biệt.
    Mặc định viết hoa tất cả các chữ cái.
    
    Args:
        product_code (str): Mã sản phẩm cần chuẩn hóa
        
    Returns:
        str: Tên file đã được chuẩn hóa và viết hoa
    """
    # Sử dụng hàm slug để chuẩn hóa tên file
    normalized = slug(product_code, separator='-', language='vi')
    
    # Viết hoa tất cả các chữ cái
    normalized = normalized.upper()
    
    # Đảm bảo tên file không quá dài (giới hạn 255 ký tự)
    if len(normalized) > 255:
        normalized = normalized[:255]
    
    return normalized 

def standardize_for_image_filename(code: str) -> str:
    """
    Chuẩn hóa mã sản phẩm để dùng làm tên file ảnh theo quy tắc chung:
    - Loại bỏ các chú thích phổ biến: (with special coating), [with special coating], add-on kit
    - Thay mọi ký tự đặc biệt bằng '-'
    - Gom nhiều '-' liên tiếp lại thành một
    - Loại bỏ '-' ở đầu/cuối
    - Chuyển kết quả thành UPPERCASE
    """
    if code is None:
        return ""
    s = str(code)
    s = re.sub(r"\(with\s+special\s+coating\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[with\s+special\s+coating\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"add[\s\-]*on[\s\-]*kit", "", s, flags=re.IGNORECASE)
    s = s.strip()
    s = re.sub(r"[^a-zA-Z0-9\-_]", "-", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s.upper()

def init_gemini_translator(api_key=None):
    """
    Khởi tạo Gemini translator với API key
    
    Args:
        api_key: Gemini API key (nếu None, sẽ lấy từ environment variable GEMINI_API_KEY)
    
    Returns:
        bool: True nếu khởi tạo thành công, False nếu thất bại
    """
    global _gemini_model, _gemini_api_key, _gemini_disabled_reason, _gemini_disabled_at
    
    if not GEMINI_AVAILABLE:
        logger.warning("Gemini API không khả dụng. Vui lòng cài đặt: pip install google-generativeai")
        return False

    # Nếu model đã được khởi tạo thành công rồi thì không cần init lại
    if _gemini_model is not None:
        return True

    # Nếu Gemini đã bị disable do lỗi nghiêm trọng thì không thử init lại (tránh spam log)
    if _gemini_disabled_reason:
        return False
    
    try:
        # Lấy API key từ tham số hoặc environment variable
        if not api_key:
            api_key = os.getenv('GEMINI_API_KEY')
        
        if not api_key:
            logger.warning("Chưa cung cấp Gemini API key. Chức năng dịch sẽ không hoạt động.")
            return False
        
        genai.configure(api_key=api_key)

        # Sử dụng list_models() để lấy danh sách model available và hỗ trợ generateContent
        try:
            available_models = genai.list_models()
            # Lọc các model có hỗ trợ generateContent
            candidate_models = []
            for model in available_models:
                if 'generateContent' in model.supported_generation_methods:
                    candidate_models.append(model.name)
            
            # Nếu không tìm thấy model nào qua list_models, thử các model phổ biến
            if not candidate_models:
                logger.warning("Không tìm thấy model nào qua list_models(), thử các model phổ biến...")
                candidate_models = [
                    "models/gemini-1.5-flash-latest",
                    "models/gemini-1.5-flash",
                    "models/gemini-1.5-pro-latest",
                    "models/gemini-1.5-pro",
                    "models/gemini-pro",
                    "gemini-pro",
                ]
            else:
                logger.info(f"Tìm thấy {len(candidate_models)} model(s) hỗ trợ generateContent")
        except Exception as e_list:
            # Nếu list_models() fail, thử các model phổ biến
            logger.warning(f"Không thể list models: {e_list}. Thử các model phổ biến...")
            candidate_models = [
                "models/gemini-1.5-flash-latest",
                "models/gemini-1.5-flash",
                "models/gemini-1.5-pro-latest",
                "models/gemini-1.5-pro",
                "models/gemini-pro",
                "gemini-pro",
            ]

        last_error = None
        for model_name in candidate_models:
            try:
                model = genai.GenerativeModel(model_name)
                # Gọi 1 request rất nhỏ để xác nhận model hoạt động với generateContent
                _ = model.generate_content("ping")
                _gemini_model = model
                _gemini_api_key = api_key
                logger.info(f"✅ Đã khởi tạo Gemini translator với model: {model_name}")
                return True
            except Exception as e_model:
                last_error = e_model
                msg = str(e_model)
                # Chỉ log debug, không log error để tránh spam
                logger.debug(f"Model '{model_name}' không khả dụng: {msg[:100]}")
                continue

        # Nếu tới đây là không model nào hoạt động - DISABLE Gemini để tránh spam log
        error_msg = str(last_error) if last_error else "Không có model nào khả dụng"
        _gemini_model = None
        _gemini_disabled_reason = error_msg[:200]
        _gemini_disabled_at = time.time()
        logger.error(f"❌ Không thể khởi tạo Gemini. Đã DISABLE Gemini. Lỗi: {error_msg[:200]}")
        logger.warning("Gemini sẽ không được sử dụng cho các lần dịch tiếp theo. Sẽ fallback sang Google Translate (nếu có).")
        return False
    except Exception as e:
        error_msg = str(e)
        _gemini_model = None
        _gemini_disabled_reason = error_msg[:200]
        _gemini_disabled_at = time.time()
        logger.error(f"❌ Lỗi khi khởi tạo Gemini translator: {error_msg[:200]}")
        logger.warning("Gemini đã bị DISABLE. Sẽ fallback sang Google Translate (nếu có).")
        return False

def translate_with_gemini(text, target_lang='vi', source_lang='auto', fallback_to_googletrans=True):
    """
    Dịch text sang tiếng Việt sử dụng Gemini AI, có fallback sang Google Translate
    
    Args:
        text: Text cần dịch
        target_lang: Ngôn ngữ đích (mặc định: 'vi' - tiếng Việt)
        source_lang: Ngôn ngữ nguồn (mặc định: 'auto' - tự động phát hiện)
        fallback_to_googletrans: Có fallback sang Google Translate không (mặc định: True)
    
    Returns:
        str: Text đã dịch, hoặc text gốc nếu dịch thất bại
    """
    global _gemini_model, _gemini_disabled_reason, _gemini_disabled_at
    
    if not text or not text.strip():
        return text

    # Nếu Gemini đã bị disable (suspended/permission) thì bỏ qua luôn
    if _gemini_disabled_reason:
        if fallback_to_googletrans:
            return _translate_with_googletrans(text, target_lang, source_lang)
        return text
    
    # Nếu chưa khởi tạo, thử khởi tạo với API key từ environment
    if not _gemini_model:
        if not init_gemini_translator():
            # Fallback sang Google Translate nếu Gemini không khả dụng
            if fallback_to_googletrans:
                return _translate_with_googletrans(text, target_lang, source_lang)
            return text
    
    if not _gemini_model:
        logger.warning("Gemini model chưa được khởi tạo. Fallback sang Google Translate.")
        if fallback_to_googletrans:
            return _translate_with_googletrans(text, target_lang, source_lang)
        return text
    
    try:
        # Tạo prompt cho Gemini
        if source_lang == 'auto':
            prompt = f"Translate the following text to Vietnamese. Only return the translated text, no explanations:\n\n{text}"
        else:
            prompt = f"Translate the following text from {source_lang} to {target_lang}. Only return the translated text, no explanations:\n\n{text}"
        
        # Gọi Gemini API
        response = _gemini_model.generate_content(prompt)
        
        if response and response.text:
            translated_text = response.text.strip()
            logger.debug(f"  Gemini dịch: '{text[:50]}...' -> '{translated_text[:50]}...'")
            return translated_text
        else:
            logger.warning(f"Gemini không trả về kết quả cho: '{text[:50]}...'")
            # Fallback sang Google Translate
            if fallback_to_googletrans:
                return _translate_with_googletrans(text, target_lang, source_lang)
            return text
    except Exception as e:
        error_str = str(e)
        # Kiểm tra các lỗi nghiêm trọng (suspended, permission denied, model not found, etc.)
        if any(keyword in error_str.lower() for keyword in [
            'suspended',
            'permission denied',
            '403',
            '401',
            'invalid api key',
            'is not found for api version',
        ]):
            _gemini_model = None
            _gemini_disabled_reason = error_str[:200]
            _gemini_disabled_at = time.time()
            logger.error(
                "Gemini bị lỗi nghiêm trọng (403/401/suspended). "
                "Đã DISABLE Gemini và sẽ fallback (nếu có). "
                f"reason={_gemini_disabled_reason}"
            )
            if fallback_to_googletrans:
                return _translate_with_googletrans(text, target_lang, source_lang)
            return text
        else:
            logger.warning(f"Lỗi khi dịch bằng Gemini '{text[:50]}...': {error_str[:100]}")
            # Fallback sang Google Translate cho lỗi tạm thời
            if fallback_to_googletrans:
                return _translate_with_googletrans(text, target_lang, source_lang)
            return text

def _translate_with_googletrans(text, target_lang='vi', source_lang='auto'):
    """Fallback function sử dụng Google Translate (nếu khả dụng)."""
    global _googletrans_disabled, _googletrans_disabled_reason

    # Nếu trước đó đã disable googletrans thì không thử lại nữa để tránh spam log
    if _googletrans_disabled:
        return text

    try:
        from googletrans import Translator  # type: ignore
        translator = Translator()
        if source_lang == 'auto':
            result = translator.translate(text, dest=target_lang)
        else:
            result = translator.translate(text, src=source_lang, dest=target_lang)
        translated_text = result.text
        logger.debug(f"  Google Translate dịch: '{text[:50]}...' -> '{translated_text[:50]}...'")
        return translated_text
    except ModuleNotFoundError as e:
        # Python 3.13 bỏ module cgi; googletrans/deps có thể import cgi -> lỗi
        msg = str(e)
        if "No module named 'cgi'" in msg:
            _googletrans_disabled = True
            _googletrans_disabled_reason = msg
            logger.warning(
                "Google Translate fallback không dùng được trên Python hiện tại (thiếu module 'cgi'). "
                "Đã DISABLE googletrans, các lần dịch sau sẽ bỏ qua fallback này."
            )
            return text
        logger.warning(f"Google Translate fallback lỗi: {msg}")
        _googletrans_disabled = True
        _googletrans_disabled_reason = msg
        return text
    except Exception as e:
        msg = str(e)
        logger.warning(f"Google Translate cũng lỗi: {msg}")
        # Nếu lỗi lặp lại nhiều lần thì cũng disable luôn
        _googletrans_disabled = True
        _googletrans_disabled_reason = msg
        return text

def translate_specifications_with_gemini(specifications, translate_keys=True, translate_values=True, fallback_to_googletrans=True):
    """
    Dịch các thông số kỹ thuật sang tiếng Việt sử dụng Gemini AI, có fallback sang Google Translate
    
    Args:
        specifications: Dictionary chứa thông số kỹ thuật
        translate_keys: Có dịch tên thông số (key) không
        translate_values: Có dịch giá trị (value) không
        fallback_to_googletrans: Có fallback sang Google Translate không (mặc định: True)
    
    Returns:
        dict: Dictionary chứa thông số kỹ thuật đã dịch
    """
    if not specifications:
        return specifications
    
    translated_specs = {}
    
    # Các key không cần dịch
    skip_keys = ['Mã sản phẩm', 'Tên sản phẩm', 'Copyright', 'Product Code', 'Product Name']
    
    # Đếm số lần lỗi để quyết định có nên tắt Gemini không
    error_count = 0
    max_errors = 3  # Sau 3 lỗi liên tiếp, tắt Gemini
    
    for key, value in specifications.items():
        # Không dịch các key đặc biệt
        if key in skip_keys:
            translated_specs[key] = value
            continue
        
        # Dịch key nếu cần
        try:
            translated_key = translate_with_gemini(key, target_lang='vi', fallback_to_googletrans=fallback_to_googletrans) if translate_keys else key
        except Exception as e:
            error_count += 1
            if error_count >= max_errors:
                logger.error(f"Quá nhiều lỗi Gemini ({error_count}), tắt Gemini và chỉ dùng Google Translate")
                _gemini_model = None
            translated_key = key if not translate_keys else _translate_with_googletrans(key, 'vi') if fallback_to_googletrans else key
        
        # Dịch value nếu cần
        if translate_values and value:
            try:
                translated_value = translate_with_gemini(str(value), target_lang='vi', fallback_to_googletrans=fallback_to_googletrans)
            except Exception as e:
                error_count += 1
                if error_count >= max_errors:
                    logger.error(f"Quá nhiều lỗi Gemini ({error_count}), tắt Gemini và chỉ dùng Google Translate")
                    _gemini_model = None
                translated_value = _translate_with_googletrans(str(value), 'vi') if fallback_to_googletrans else value
        else:
            translated_value = value
        
        translated_specs[translated_key] = translated_value
    
    return translated_specs