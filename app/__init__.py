from flask import Flask
import os

"""
Khởi tạo SocketIO theo cách an toàn với các phiên bản Python mới (3.12/3.13):
- Nếu socketio/engineio không hỗ trợ async_mode tương ứng -> fallback về None
- Khi đó ứng dụng vẫn chạy được với Flask thuần, chỉ mất realtime progress bar.
"""
try:
    from flask_socketio import SocketIO  # type: ignore
    _SocketIOClass = SocketIO
except Exception:  # Nếu không import được flask_socketio
    _SocketIOClass = None

# Nếu có SocketIO, tạo instance; nếu không thì để None và fallback về Flask thuần
socketio = _SocketIOClass(async_mode="threading") if _SocketIOClass else None

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'crawlbot_secret_key'
    
    # Tạo thư mục uploads/downloads nếu chưa tồn tại
    downloads_dir = os.path.join(app.root_path, 'downloads')
    os.makedirs(downloads_dir, exist_ok=True)
    app.config['UPLOAD_FOLDER'] = downloads_dir
    
    # Tạo thư mục categorized trong downloads
    categorized_dir = os.path.join(downloads_dir, 'categorized')
    os.makedirs(categorized_dir, exist_ok=True)
    
    # Tạo thư mục upscaled_images trong downloads để lưu ảnh đã xử lý bằng AI
    upscaled_images_dir = os.path.join(downloads_dir, 'upscaled_images')
    os.makedirs(upscaled_images_dir, exist_ok=True)
    
    # Tạo thư mục logs nếu chưa tồn tại
    logs_dir = os.path.join(os.path.dirname(app.root_path), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # Đăng ký blueprint
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    # Khởi tạo SocketIO với ứng dụng Flask (nếu khả dụng)
    if socketio is not None:
        try:
            socketio.init_app(app, cors_allowed_origins="*")
        except Exception as e:
            # Nếu SocketIO không khởi tạo được (thường do async_mode / engineio không tương thích),
            # ghi log và fallback dùng Flask thuần.
            print(f"[WARN] Không thể khởi tạo SocketIO, sẽ chạy Flask thuần. Lý do: {e}")
            globals()["socketio"] = None

    # (Đã gỡ bỏ) Đăng ký HoplongCrawler routes
    return app 