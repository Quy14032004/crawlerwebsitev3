"""
AI Agent xử lý ảnh - Module ghép logo vào ảnh sản phẩm
Tác giả: AI Assistant
Ngày tạo: 2024
"""

import os
import logging
from PIL import Image, ImageOps, ImageDraw, ImageFont
from typing import Tuple, Optional
import math

# Cấu hình logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ImageProcessor:
    """Class chính để xử lý ảnh và ghép logo"""
    
    def __init__(self):
        self.supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
        self.logo_position = 'top_right'  # Vị trí logo mặc định
        self.logo_scale_ratio = 0.18  # Mặc định 18% chiều rộng ảnh
        self.logo_margin = 0.02  # Khoảng cách từ mép (2%)
        self.default_size = (600, 600)  # Kích thước mặc định 600x600px (không dùng trừ khi bật)
        self.multiple_logos = False  # Hỗ trợ nhiều logo
        self.overlay_scale_ratio = 0.6  # Tỷ lệ chiều rộng ảnh overlay so với ảnh sản phẩm
        self.keep_original_size = True  # Giữ nguyên kích thước ảnh sản phẩm
        self.use_original_logo_size = True  # Dùng nguyên kích thước logo (100%) mặc định
        
    def validate_image(self, image_path: str) -> bool:
        """Kiểm tra tính hợp lệ của file ảnh"""
        try:
            if not os.path.exists(image_path):
                logger.error(f"File không tồn tại: {image_path}")
                return False
                
            # Kiểm tra extension
            ext = os.path.splitext(image_path)[1].lower()
            if ext not in self.supported_formats:
                logger.error(f"Định dạng không được hỗ trợ: {ext}")
                return False
                
            # Kiểm tra file có phải ảnh không
            with Image.open(image_path) as img:
                img.verify()
                
            return True
            
        except Exception as e:
            logger.error(f"Lỗi kiểm tra ảnh {image_path}: {str(e)}")
            return False
    
    def calculate_logo_size(self, product_image: Image.Image, logo_image: Image.Image) -> Tuple[int, int]:
        """Tính toán kích thước logo phù hợp"""
        product_width, product_height = product_image.size
        
        if self.use_original_logo_size:
            # Dùng nguyên size logo, nhưng không vượt quá 90% bề ngang/dọc của ảnh nền
            logo_w, logo_h = logo_image.size
            max_w = int(product_width * 0.9)
            max_h = int(product_height * 0.9)
            if logo_w <= max_w and logo_h <= max_h:
                return logo_w, logo_h
            # Nếu lớn quá thì thu nhỏ theo tỷ lệ để vừa khung
            ratio = min(max_w / logo_w, max_h / logo_h)
            return max(1, int(logo_w * ratio)), max(1, int(logo_h * ratio))
        
        # Logo dựa theo chiều rộng ảnh sản phẩm
        max_logo_width = int(product_width * self.logo_scale_ratio)
        # Chiều cao dựa theo tỷ lệ logo
        max_logo_height = int(product_height)  # trần, sẽ tính theo ratio
        
        # Giữ tỷ lệ gốc của logo
        logo_ratio = logo_image.width / logo_image.height
        
        if logo_ratio > 1:  # Logo ngang
            logo_width = max_logo_width
            logo_height = int(logo_width / logo_ratio)
        else:  # Logo dọc hoặc vuông
            logo_height = max_logo_height
            logo_width = int(logo_height * logo_ratio)
            
        # Đảm bảo không vượt quá giới hạn
        if logo_width > max_logo_width:
            logo_width = max_logo_width
            logo_height = int(logo_width / logo_ratio)
        if logo_height > max_logo_height:
            logo_height = max_logo_height
            logo_width = int(logo_height * logo_ratio)
            
        return logo_width, logo_height

    def remove_white_background(self, logo_image: Image.Image, threshold: int = 240) -> Image.Image:
        """Loại bỏ nền trắng (biến trắng thành trong suốt). Giữ alpha nếu đã có."""
        try:
            if logo_image.mode != 'RGBA':
                logo_image = logo_image.convert('RGBA')
            datas = logo_image.getdata()
            new_data = []
            for item in datas:
                r, g, b, a = item
                if r > threshold and g > threshold and b > threshold:
                    new_data.append((255, 255, 255, 0))  # trong suốt
                else:
                    new_data.append((r, g, b, a))
            logo_image.putdata(new_data)
            return logo_image
        except Exception as e:
            logger.warning(f"Không thể loại nền trắng logo: {str(e)}")
            return logo_image
    
    def calculate_logo_position(self, product_image: Image.Image, logo_size: Tuple[int, int], position: str = None) -> Tuple[int, int]:
        """Tính toán vị trí đặt logo"""
        if position is None:
            position = self.logo_position
            
        product_width, product_height = product_image.size
        logo_width, logo_height = logo_size
        
        # Tính margin
        margin_x = int(product_width * self.logo_margin)
        margin_y = int(product_height * self.logo_margin)
        
        # Tính vị trí dựa trên position
        if position == 'top_left':
            x = margin_x
            y = margin_y
        elif position == 'top_right':
            x = product_width - logo_width - margin_x
            y = margin_y
        elif position == 'bottom_left':
            x = margin_x
            y = product_height - logo_height - margin_y
        elif position == 'bottom_right':
            x = product_width - logo_width - margin_x
            y = product_height - logo_height - margin_y
        else:  # Mặc định top_right
            x = product_width - logo_width - margin_x
            y = margin_y
        
        # Đảm bảo không vượt ra ngoài ảnh
        x = max(0, x)
        y = max(0, y)
        
        return x, y
    
    def resize_logo(self, logo_image: Image.Image, target_size: Tuple[int, int]) -> Image.Image:
        """Resize logo với chất lượng cao"""
        try:
            # Sử dụng LANCZOS để có chất lượng tốt nhất
            resized_logo = logo_image.resize(target_size, Image.Resampling.LANCZOS)
            return resized_logo
        except Exception as e:
            logger.error(f"Lỗi resize logo: {str(e)}")
            raise
    
    def resize_to_default(self, image: Image.Image) -> Image.Image:
        """Resize ảnh về kích thước mặc định 600x600px"""
        try:
            # Resize với giữ tỷ lệ và crop center
            resized_image = ImageOps.fit(image, self.default_size, Image.Resampling.LANCZOS)
            logger.info(f"Đã resize ảnh về kích thước: {self.default_size}")
            return resized_image
        except Exception as e:
            logger.error(f"Lỗi resize ảnh về kích thước mặc định: {str(e)}")
            raise
    
    def add_logo_to_image(self, product_image: Image.Image, logo_image: Image.Image, position: str = None) -> Image.Image:
        """Ghép logo vào ảnh sản phẩm"""
        try:
            # Giữ kích thước gốc nếu được cấu hình
            result_image = product_image.copy() if self.keep_original_size else self.resize_to_default(product_image)
            
            # Tính toán kích thước và vị trí logo
            prepared_logo = self.remove_white_background(logo_image)
            # Kích thước logo: dùng nguyên size nếu được yêu cầu
            if self.use_original_logo_size:
                logo_size = (prepared_logo.width, prepared_logo.height)
            else:
                logo_size = self.calculate_logo_size(result_image, prepared_logo)
            logo_position = self.calculate_logo_position(result_image, logo_size, position)
            
            # Resize logo
            resized_logo = prepared_logo if self.use_original_logo_size else self.resize_logo(prepared_logo, logo_size)
            
            # Chuyển đổi sang RGBA nếu cần
            if result_image.mode != 'RGBA':
                result_image = result_image.convert('RGBA')
            if resized_logo.mode != 'RGBA':
                resized_logo = resized_logo.convert('RGBA')
            
            # Tạo layer trong suốt cho logo
            logo_layer = Image.new('RGBA', result_image.size, (0, 0, 0, 0))
            # đảm bảo dùng alpha như mask để không có nền trắng
            logo_layer.paste(resized_logo, logo_position, mask=resized_logo)

            # Tránh che khuất: nếu vùng đặt logo không phải nền sáng, đẩy logo xuống thêm 10% chiều cao
            try:
                px, py = logo_position
                lw, lh = resized_logo.size
                crop_region = result_image.convert('L').crop((px, py, min(px + lw, result_image.width), min(py + lh, result_image.height)))
                # Tính tỷ lệ pixel sáng
                white_pixels = sum(1 for p in crop_region.getdata() if p > 230)
                ratio_white = white_pixels / max(1, lw * lh)
                if ratio_white < 0.7:
                    py2 = min(result_image.height - lh - int(result_image.height * self.logo_margin), py + int(result_image.height * 0.1))
                    logo_layer = Image.new('RGBA', result_image.size, (0, 0, 0, 0))
                    logo_layer.paste(resized_logo, (px, py2))
                    logo_position = (px, py2)
            except Exception:
                pass
            
            # Ghép logo vào ảnh
            result_image = Image.alpha_composite(result_image, logo_layer)
            
            logger.info(f"Đã ghép logo thành công. Kích thước logo: {logo_size}, Vị trí: {logo_position}")
            return result_image
            
        except Exception as e:
            logger.error(f"Lỗi ghép logo: {str(e)}")
            raise
    
    def add_multiple_logos(self, product_image: Image.Image, logos: list) -> Image.Image:
        """Ghép nhiều logo vào ảnh sản phẩm"""
        try:
            # Resize ảnh sản phẩm về kích thước mặc định 600x600px
            result_image = self.resize_to_default(product_image)
            
            # Chuyển đổi sang RGBA nếu cần
            if result_image.mode != 'RGBA':
                result_image = result_image.convert('RGBA')
            
            # Tạo layer trong suốt cho tất cả logo
            logo_layer = Image.new('RGBA', result_image.size, (0, 0, 0, 0))
            
            # Xử lý từng logo
            for logo_info in logos:
                logo_image = logo_info['image']
                position = logo_info.get('position', 'top_right')
                
                # Tính toán kích thước và vị trí logo
                logo_size = self.calculate_logo_size(result_image, logo_image)
                logo_position = self.calculate_logo_position(result_image, logo_size, position)
                
                # Resize logo
                resized_logo = self.resize_logo(logo_image, logo_size)
                
                # Chuyển đổi sang RGBA nếu cần
                if resized_logo.mode != 'RGBA':
                    resized_logo = resized_logo.convert('RGBA')
                
                # Paste logo vào layer
                logo_layer.paste(resized_logo, logo_position)
                
                logger.info(f"Đã thêm logo ở vị trí {position}. Kích thước: {logo_size}, Vị trí: {logo_position}")
            
            # Ghép tất cả logo vào ảnh
            result_image = Image.alpha_composite(result_image, logo_layer)
            
            logger.info(f"Đã ghép {len(logos)} logo thành công")
            return result_image
            
        except Exception as e:
            logger.error(f"Lỗi ghép nhiều logo: {str(e)}")
            raise

    def add_overlay_image(self, product_image: Image.Image, overlay_image: Image.Image,
                           scale_ratio: Optional[float] = None,
                           position: str = 'top_center',
                           opacity: float = 1.0) -> Image.Image:
        """Chèn toàn bộ ảnh overlay (ảnh chứa logo) vào ảnh sản phẩm.

        - scale_ratio: tỷ lệ chiều rộng ảnh overlay so với ảnh sản phẩm (mặc định 60%).
        - position: vị trí đặt overlay: 'top_center' | 'center' | 'bottom_center'.
        """
        try:
            result_image = self.resize_to_default(product_image)

            if scale_ratio is None:
                scale_ratio = self.overlay_scale_ratio

            # Tính kích thước overlay theo tỉ lệ chiều rộng ảnh sản phẩm
            product_width, product_height = result_image.size
            target_width = max(1, int(product_width * scale_ratio))
            overlay_ratio = overlay_image.width / overlay_image.height
            target_height = max(1, int(target_width / overlay_ratio))

            # Giới hạn chiều cao overlay để không che sản phẩm (≤ 30% chiều cao)
            max_overlay_height = int(product_height * 0.3)
            if target_height > max_overlay_height:
                target_height = max_overlay_height
                target_width = max(1, int(target_height * overlay_ratio))

            # Resize overlay
            overlay_resized = overlay_image.resize((target_width, target_height), Image.Resampling.LANCZOS)

            # Đảm bảo kênh alpha
            if overlay_resized.mode != 'RGBA':
                overlay_resized = overlay_resized.convert('RGBA')
            if result_image.mode != 'RGBA':
                result_image = result_image.convert('RGBA')

            # Tính vị trí đặt
            margin_x = int(product_width * self.logo_margin)
            margin_y = int(product_height * self.logo_margin)
            if position == 'center':
                x = (product_width - target_width) // 2
                y = (product_height - target_height) // 2
            elif position == 'bottom_center':
                x = (product_width - target_width) // 2
                y = product_height - target_height - margin_y
            else:  # top_center
                x = (product_width - target_width) // 2
                y = margin_y

            # Chuẩn bị overlay (loại nền trắng nếu có)
            overlay_resized = self.remove_white_background(overlay_resized)

            # Tạo layer và ghép
            layer = Image.new('RGBA', result_image.size, (0, 0, 0, 0))
            # Áp dụng độ mờ nếu cần
            if 0 < opacity < 1.0:
                alpha = overlay_resized.split()[-1] if 'A' in overlay_resized.getbands() else None
                if alpha is None:
                    # Tạo alpha mặc định nếu ảnh không có alpha
                    alpha = Image.new('L', overlay_resized.size, int(255 * opacity))
                else:
                    # Giảm alpha theo opacity
                    alpha = alpha.point(lambda a: int(a * opacity))
                overlay_resized.putalpha(alpha)

            layer.paste(overlay_resized, (x, y), mask=overlay_resized)
            result_image = Image.alpha_composite(result_image, layer)

            logger.info(
                f"Đã chèn ảnh overlay. Size: {(target_width, target_height)}, Pos: {(x, y)}, Ratio: {scale_ratio}"
            )
            return result_image
        except Exception as e:
            logger.error(f"Lỗi chèn ảnh overlay: {str(e)}")
            raise
    
    def save_as_webp(self, image: Image.Image, output_path: str, quality: int = 95) -> bool:
        """Lưu ảnh dưới định dạng WebP với chất lượng cao"""
        try:
            # Giữ nguyên nền: nếu RGBA thì lưu kèm alpha, nếu RGB giữ như cũ
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
            
            # Lưu với chất lượng cao
            image.save(output_path, 'WEBP', quality=quality, method=6)
            logger.info(f"Đã lưu ảnh WebP: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Lỗi lưu WebP: {str(e)}")
            return False
    
    def process_single_image(self, product_image_path: str, logo_image_path: str, output_path: str, position: str = None, quality: int = 95) -> bool:
        """Xử lý một ảnh sản phẩm"""
        try:
            # Kiểm tra tính hợp lệ
            if not self.validate_image(product_image_path):
                return False
            if not self.validate_image(logo_image_path):
                return False
            
            # Mở ảnh
            with Image.open(product_image_path) as product_img:
                with Image.open(logo_image_path) as logo_img:
                    # Ghép logo
                    result_image = self.add_logo_to_image(product_img, logo_img, position)
                    # Thêm watermark
                    result_image = self.add_watermark_text(result_image)
                    # Lưu kết quả
                    return self.save_as_webp(result_image, output_path, quality=quality)
                    
        except Exception as e:
            logger.error(f"Lỗi xử lý ảnh {product_image_path}: {str(e)}")
            return False
    
    def process_single_image_multiple_logos(self, product_image_path: str, logos_info: list, output_path: str, quality: int = 95) -> bool:
        """Xử lý một ảnh sản phẩm với nhiều logo"""
        try:
            # Kiểm tra tính hợp lệ
            if not self.validate_image(product_image_path):
                return False
            
            # Mở ảnh sản phẩm
            with Image.open(product_image_path) as product_img:
                # Chuẩn bị danh sách logo
                logos = []
                for logo_info in logos_info:
                    logo_path = logo_info['path']
                    position = logo_info.get('position', 'top_right')
                    
                    if not self.validate_image(logo_path):
                        logger.error(f"Logo không hợp lệ: {logo_path}")
                        continue
                    
                    with Image.open(logo_path) as logo_img:
                        logos.append({
                            'image': logo_img.copy(),
                            'position': position
                        })
                
                if not logos:
                    logger.error("Không có logo hợp lệ nào")
                    return False
                
                # Ghép nhiều logo
                result_image = self.add_multiple_logos(product_img, logos)
                result_image = self.add_watermark_text(result_image)
                # Lưu kết quả
                return self.save_as_webp(result_image, output_path, quality=quality)
                    
        except Exception as e:
            logger.error(f"Lỗi xử lý ảnh {product_image_path}: {str(e)}")
            return False

    def process_single_image_overlay(self, product_image_path: str, overlay_path: str, output_path: str,
                                     position: str = 'top_center', scale_ratio: Optional[float] = None,
                                     opacity: float = 1.0, quality: int = 95) -> bool:
        """Xử lý chèn ảnh overlay (ảnh chứa logo) vào ảnh sản phẩm."""
        try:
            if not self.validate_image(product_image_path):
                return False
            if not self.validate_image(overlay_path):
                return False
            with Image.open(product_image_path) as product_img:
                with Image.open(overlay_path) as overlay_img:
                    result = self.add_overlay_image(product_img, overlay_img, scale_ratio=scale_ratio,
                                                    position=position, opacity=opacity)
                    result = self.add_watermark_text(result)
                    return self.save_as_webp(result, output_path, quality=quality)
        except Exception as e:
            logger.error(f"Lỗi xử lý overlay cho {product_image_path}: {str(e)}")
            return False

    def add_watermark_text(self, image: Image.Image, text: str = 'HAIPHONGTECH.VN', color: Tuple[int, int, int] = (58, 141, 222)) -> Image.Image:
        """Thêm watermark chữ ở dưới, căn giữa, cỡ ~5% chiều rộng ảnh."""
        try:
            result = image.copy()
            draw = ImageDraw.Draw(result)
            width, height = result.size
            font_size = max(12, int(width * 0.05))
            font = None
            try:
                # Windows thường có Arial
                font = ImageFont.truetype('arial.ttf', font_size)
            except Exception:
                try:
                    font = ImageFont.truetype('DejaVuSans.ttf', font_size)
                except Exception:
                    font = ImageFont.load_default()
            # Tính bounding box
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (width - text_w) // 2
            # Cách đáy 2% chiều cao
            y = height - text_h - int(height * 0.02)
            draw.text((x, y), text, fill=color, font=font)
            return result
        except Exception as e:
            logger.error(f"Lỗi thêm watermark: {str(e)}")
            return image
    
    def get_image_info(self, image_path: str) -> dict:
        """Lấy thông tin ảnh"""
        try:
            with Image.open(image_path) as img:
                return {
                    'width': img.width,
                    'height': img.height,
                    'mode': img.mode,
                    'format': img.format,
                    'size_mb': os.path.getsize(image_path) / (1024 * 1024)
                }
        except Exception as e:
            logger.error(f"Lỗi lấy thông tin ảnh {image_path}: {str(e)}")
            return {}

# Hàm tiện ích để sử dụng
def process_image_with_logo(product_path: str, logo_path: str, output_path: str) -> bool:
    """Hàm tiện ích để xử lý ảnh với logo"""
    processor = ImageProcessor()
    return processor.process_single_image(product_path, logo_path, output_path)

if __name__ == "__main__":
    # Test function
    processor = ImageProcessor()
    print("AI Agent xử lý ảnh đã sẵn sàng!")
