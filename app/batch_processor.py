"""
AI Agent xử lý ảnh - Module xử lý hàng loạt
Tác giả: AI Assistant
Ngày tạo: 2024
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Tuple
import time
from datetime import datetime
from app.image_processor import ImageProcessor

# Cấu hình logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BatchImageProcessor:
    """Class xử lý hàng loạt ảnh với logo"""
    
    def __init__(self):
        self.image_processor = ImageProcessor()
        self.supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
        
    def scan_folder_for_images(self, folder_path: str) -> List[str]:
        """Quét folder để tìm tất cả ảnh hợp lệ"""
        image_files = []
        
        try:
            folder = Path(folder_path)
            if not folder.exists():
                logger.error(f"Folder không tồn tại: {folder_path}")
                return image_files
                
            # Quét tất cả file trong folder và subfolder
            for file_path in folder.rglob('*'):
                if file_path.is_file():
                    ext = file_path.suffix.lower()
                    if ext in self.supported_formats:
                        image_files.append(str(file_path))
                        
            logger.info(f"Tìm thấy {len(image_files)} ảnh trong folder: {folder_path}")
            return image_files
            
        except Exception as e:
            logger.error(f"Lỗi quét folder {folder_path}: {str(e)}")
            return image_files
    
    def create_output_structure(self, input_folder: str, output_folder: str) -> str:
        """Tạo cấu trúc thư mục output"""
        try:
            # Tạo tên folder output với timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = Path(input_folder).name
            output_dir = Path(output_folder) / f"{base_name}_with_logo_{timestamp}"
            
            # Tạo thư mục
            output_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Đã tạo thư mục output: {output_dir}")
            return str(output_dir)
            
        except Exception as e:
            logger.error(f"Lỗi tạo thư mục output: {str(e)}")
            raise
    
    def preserve_folder_structure(self, input_path: str, output_dir: str, input_folder: str) -> str:
        """Bảo toàn cấu trúc thư mục con"""
        try:
            input_path_obj = Path(input_path)
            input_folder_obj = Path(input_folder)
            
            # Tính relative path
            relative_path = input_path_obj.relative_to(input_folder_obj)
            
            # Tạo thư mục con trong output
            output_subdir = Path(output_dir) / relative_path.parent
            output_subdir.mkdir(parents=True, exist_ok=True)
            
            # Tạo tên file output
            file_name = input_path_obj.stem + "_with_logo.webp"
            output_path = output_subdir / file_name
            
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Lỗi tạo cấu trúc thư mục: {str(e)}")
            # Fallback: lưu trực tiếp trong thư mục gốc
            file_name = Path(input_path).stem + "_with_logo.webp"
            return str(Path(output_dir) / file_name)
    
    def process_batch_multiple_logos(self, input_folder: str, logos_info: list, output_folder: str, 
                                   progress_callback=None, quality: int = 95) -> Dict:
        """Xử lý hàng loạt ảnh với nhiều logo"""
        start_time = time.time()
        results = {
            'total_images': 0,
            'processed_successfully': 0,
            'failed_images': [],
            'output_folder': '',
            'processing_time': 0,
            'details': []
        }
        
        try:
            # Kiểm tra logo
            for logo_info in logos_info:
                if not self.image_processor.validate_image(logo_info['path']):
                    raise ValueError(f"Logo không hợp lệ: {logo_info['path']}")
            
            # Quét ảnh trong folder
            image_files = self.scan_folder_for_images(input_folder)
            if not image_files:
                logger.warning("Không tìm thấy ảnh nào trong folder")
                return results
            
            results['total_images'] = len(image_files)
            
            # Tạo thư mục output
            output_dir = self.create_output_structure(input_folder, output_folder)
            results['output_folder'] = output_dir
            
            logger.info(f"Bắt đầu xử lý {len(image_files)} ảnh với {len(logos_info)} logo...")
            
            # Xử lý từng ảnh
            for i, image_path in enumerate(image_files):
                try:
                    # Tạo đường dẫn output
                    output_path = self.preserve_folder_structure(image_path, output_dir, input_folder)
                    
                    # Xử lý ảnh với nhiều logo
                    success = self.image_processor.process_single_image_multiple_logos(
                        image_path, logos_info, output_path, quality=quality
                    )
                    
                    if success:
                        results['processed_successfully'] += 1
                        results['details'].append({
                            'input': image_path,
                            'output': output_path,
                            'status': 'success'
                        })
                        logger.info(f"✓ Đã xử lý: {Path(image_path).name}")
                    else:
                        results['failed_images'].append(image_path)
                        results['details'].append({
                            'input': image_path,
                            'output': '',
                            'status': 'failed'
                        })
                        logger.error(f"✗ Lỗi xử lý: {Path(image_path).name}")
                    
                    # Gọi callback progress nếu có
                    if progress_callback:
                        progress = (i + 1) / len(image_files) * 100
                        progress_callback(progress, i + 1, len(image_files))
                        
                except Exception as e:
                    logger.error(f"Lỗi xử lý ảnh {image_path}: {str(e)}")
                    results['failed_images'].append(image_path)
                    results['details'].append({
                        'input': image_path,
                        'output': '',
                        'status': 'error',
                        'error': str(e)
                    })
            
            # Tính thời gian xử lý
            results['processing_time'] = time.time() - start_time
            
            # Log kết quả
            logger.info(f"""
            ===== KẾT QUẢ XỬ LÝ NHIỀU LOGO =====
            Tổng số ảnh: {results['total_images']}
            Thành công: {results['processed_successfully']}
            Thất bại: {len(results['failed_images'])}
            Thời gian: {results['processing_time']:.2f} giây
            Thư mục output: {results['output_folder']}
            =====================================
            """)
            
            return results
            
        except Exception as e:
            logger.error(f"Lỗi xử lý hàng loạt nhiều logo: {str(e)}")
            results['processing_time'] = time.time() - start_time
            raise

    def process_batch(self, input_folder: str, logo_path: str, output_folder: str, 
                     progress_callback=None, quality: int = 95) -> Dict:
        """Xử lý hàng loạt ảnh trong folder"""
        start_time = time.time()
        results = {
            'total_images': 0,
            'processed_successfully': 0,
            'failed_images': [],
            'output_folder': '',
            'processing_time': 0,
            'details': []
        }
        
        try:
            # Kiểm tra logo
            if not self.image_processor.validate_image(logo_path):
                raise ValueError("Logo không hợp lệ")
            
            # Quét ảnh trong folder
            image_files = self.scan_folder_for_images(input_folder)
            if not image_files:
                logger.warning("Không tìm thấy ảnh nào trong folder")
                return results
            
            results['total_images'] = len(image_files)
            
            # Tạo thư mục output
            output_dir = self.create_output_structure(input_folder, output_folder)
            results['output_folder'] = output_dir
            
            logger.info(f"Bắt đầu xử lý {len(image_files)} ảnh...")
            
            # Xử lý từng ảnh
            for i, image_path in enumerate(image_files):
                try:
                    # Tạo đường dẫn output
                    output_path = self.preserve_folder_structure(image_path, output_dir, input_folder)
                    
                    # Xử lý ảnh
                    success = self.image_processor.process_single_image(
                        image_path, logo_path, output_path, quality=quality
                    )
                    
                    if success:
                        results['processed_successfully'] += 1
                        results['details'].append({
                            'input': image_path,
                            'output': output_path,
                            'status': 'success'
                        })
                        logger.info(f"✓ Đã xử lý: {Path(image_path).name}")
                    else:
                        results['failed_images'].append(image_path)
                        results['details'].append({
                            'input': image_path,
                            'output': '',
                            'status': 'failed'
                        })
                        logger.error(f"✗ Lỗi xử lý: {Path(image_path).name}")
                    
                    # Gọi callback progress nếu có
                    if progress_callback:
                        progress = (i + 1) / len(image_files) * 100
                        progress_callback(progress, i + 1, len(image_files))
                        
                except Exception as e:
                    logger.error(f"Lỗi xử lý ảnh {image_path}: {str(e)}")
                    results['failed_images'].append(image_path)
                    results['details'].append({
                        'input': image_path,
                        'output': '',
                        'status': 'error',
                        'error': str(e)
                    })
            
            # Tính thời gian xử lý
            results['processing_time'] = time.time() - start_time
            
            # Log kết quả
            logger.info(f"""
            ===== KẾT QUẢ XỬ LÝ =====
            Tổng số ảnh: {results['total_images']}
            Thành công: {results['processed_successfully']}
            Thất bại: {len(results['failed_images'])}
            Thời gian: {results['processing_time']:.2f} giây
            Thư mục output: {results['output_folder']}
            =========================
            """)
            
            return results
            
        except Exception as e:
            logger.error(f"Lỗi xử lý hàng loạt: {str(e)}")
            results['processing_time'] = time.time() - start_time
            raise

    def process_batch_overlay(self, input_folder: str, overlay_path: str, output_folder: str,
                               position: str = 'top_center', scale_ratio: float = 0.6, opacity: float = 1.0,
                               progress_callback=None) -> Dict:
        """Xử lý hàng loạt: chèn ảnh overlay vào ảnh sản phẩm."""
        start_time = time.time()
        results = {
            'total_images': 0,
            'processed_successfully': 0,
            'failed_images': [],
            'output_folder': '',
            'processing_time': 0,
            'details': []
        }
        try:
            if not self.image_processor.validate_image(overlay_path):
                raise ValueError("Overlay không hợp lệ")

            image_files = self.scan_folder_for_images(input_folder)
            if not image_files:
                return results
            results['total_images'] = len(image_files)

            output_dir = self.create_output_structure(input_folder, output_folder)
            results['output_folder'] = output_dir

            logger.info(f"Bắt đầu overlay {len(image_files)} ảnh...")
            for i, image_path in enumerate(image_files):
                try:
                    output_path = self.preserve_folder_structure(image_path, output_dir, input_folder)
                    success = self.image_processor.process_single_image_overlay(
                        image_path, overlay_path, output_path,
                        position=position, scale_ratio=scale_ratio, opacity=opacity
                    )
                    if success:
                        results['processed_successfully'] += 1
                        results['details'].append({'input': image_path, 'output': output_path, 'status': 'success'})
                    else:
                        results['failed_images'].append(image_path)
                        results['details'].append({'input': image_path, 'output': '', 'status': 'failed'})

                    if progress_callback:
                        progress = (i + 1) / len(image_files) * 100
                        progress_callback(progress, i + 1, len(image_files))
                except Exception as e:
                    logger.error(f"Lỗi overlay {image_path}: {str(e)}")
                    results['failed_images'].append(image_path)
                    results['details'].append({'input': image_path, 'output': '', 'status': 'error', 'error': str(e)})

            results['processing_time'] = time.time() - start_time
            logger.info(f"Overlay xong: {results['processed_successfully']}/{results['total_images']} ảnh")
            return results
        except Exception as e:
            logger.error(f"Lỗi xử lý overlay: {str(e)}")
            results['processing_time'] = time.time() - start_time
            raise
    
    def get_processing_stats(self, results: Dict) -> Dict:
        """Lấy thống kê xử lý"""
        return {
            'success_rate': (results['processed_successfully'] / results['total_images'] * 100) if results['total_images'] > 0 else 0,
            'average_time_per_image': results['processing_time'] / results['total_images'] if results['total_images'] > 0 else 0,
            'total_size_processed': sum(os.path.getsize(detail['input']) for detail in results['details'] if detail['status'] == 'success'),
            'estimated_savings': self._calculate_size_savings(results)
        }
    
    def _calculate_size_savings(self, results: Dict) -> Dict:
        """Tính toán tiết kiệm dung lượng (WebP thường nhỏ hơn)"""
        try:
            total_original_size = 0
            total_webp_size = 0
            
            for detail in results['details']:
                if detail['status'] == 'success' and os.path.exists(detail['output']):
                    total_original_size += os.path.getsize(detail['input'])
                    total_webp_size += os.path.getsize(detail['output'])
            
            savings_percent = ((total_original_size - total_webp_size) / total_original_size * 100) if total_original_size > 0 else 0
            
            return {
                'original_size_mb': total_original_size / (1024 * 1024),
                'webp_size_mb': total_webp_size / (1024 * 1024),
                'savings_mb': (total_original_size - total_webp_size) / (1024 * 1024),
                'savings_percent': savings_percent
            }
        except Exception as e:
            logger.error(f"Lỗi tính toán tiết kiệm: {str(e)}")
            return {}

# Hàm tiện ích
def process_folder_with_logo(input_folder: str, logo_path: str, output_folder: str) -> Dict:
    """Hàm tiện ích để xử lý folder với logo"""
    processor = BatchImageProcessor()
    return processor.process_batch(input_folder, logo_path, output_folder)

if __name__ == "__main__":
    # Test function
    processor = BatchImageProcessor()
    print("AI Agent xử lý hàng loạt đã sẵn sàng!")
