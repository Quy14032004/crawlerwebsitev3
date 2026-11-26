@echo off
echo ====================================================
echo Khởi động máy chủ LibreTranslate cục bộ (Không Docker)
echo Đang tải mô hình ngôn ngữ (Anh - Viet)...
echo Server se chay tai http://localhost:5000
echo ====================================================

libretranslate --load-only en,vi --disable-files-translation

pause
