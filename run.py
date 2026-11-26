import os
import warnings
try:
    from requests.exceptions import RequestsDependencyWarning
    warnings.filterwarnings("ignore", category=RequestsDependencyWarning)
except ImportError:
    pass

from app import create_app
import app as app_module

# Đảm bảo thư mục logs tồn tại
logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Khởi tạo Flask app
app = create_app()

# Sau khi create_app() chạy, giá trị app_module.socketio có thể đã bị set lại (hoặc None)
socketio = getattr(app_module, "socketio", None)

if __name__ == '__main__':
    # Nếu SocketIO khởi tạo được thì dùng socketio.run để giữ realtime progress.
    # Nếu không, fallback sang app.run() để đảm bảo server vẫn chạy được.
    if socketio is not None:
        socketio.run(app, host='127.0.0.1', port=8009, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
    else:
        app.run(host='127.0.0.1', port=8009, debug=True, use_reloader=False)