import pandas as pd
import openpyxl
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
import os
import zipfile
import tempfile
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

class ExcelMerger:
    """
    Class xử lý gộp nhiều file Excel thành một file tổng hợp
    """
    
    def __init__(self):
        self.supported_extensions = ['.xlsx', '.xls']
        
    def detect_columns(self, df: pd.DataFrame) -> Dict[str, str]:
        """
        Tự động nhận diện và mapping các cột trong DataFrame
        
        Args:
            df: DataFrame cần phân tích
            
        Returns:
            Dict mapping từ tên cột gốc sang tên cột chuẩn
        """
        column_mapping = {}
        
        # Các pattern thường gặp cho các cột
        common_patterns = {
            'mã sản phẩm': ['mã', 'code', 'id', 'sản phẩm', 'product_code', 'item_code'],
            'tên sản phẩm': ['tên', 'name', 'product_name', 'item_name', 'title'],
            'giá': ['giá', 'price', 'cost', 'amount', 'value'],
            'số lượng': ['số lượng', 'quantity', 'qty', 'count', 'amount'],
            'mô tả': ['mô tả', 'description', 'desc', 'note', 'remark'],
            'danh mục': ['danh mục', 'category', 'type', 'group', 'class'],
            'nhà cung cấp': ['nhà cung cấp', 'supplier', 'vendor', 'brand', 'manufacturer']
        }
        
        for col in df.columns:
            col_lower = str(col).lower().strip()
            
            # Tìm mapping phù hợp nhất
            best_match = None
            max_score = 0
            
            for standard_col, patterns in common_patterns.items():
                for pattern in patterns:
                    if pattern in col_lower:
                        score = len(pattern) / len(col_lower)
                        if score > max_score:
                            max_score = score
                            best_match = standard_col
            
            # Nếu không tìm thấy mapping, giữ nguyên tên cột
            column_mapping[col] = best_match if best_match else col
            
        return column_mapping
        
    def standardize_dataframe(self, df: pd.DataFrame, file_name: str) -> pd.DataFrame:
        """
        Chuẩn hóa DataFrame với mapping cột và thêm thông tin nguồn
        
        Args:
            df: DataFrame cần chuẩn hóa
            file_name: Tên file nguồn
            
        Returns:
            DataFrame đã được chuẩn hóa
        """
        # Tạo bản copy để không ảnh hưởng đến dữ liệu gốc
        standardized_df = df.copy()
        
        # Thêm cột nguồn file
        standardized_df['Nguồn File'] = file_name
        standardized_df['Thời Gian Xử Lý'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Detect và rename columns
        column_mapping = self.detect_columns(df)
        
        # Rename columns dựa trên mapping
        rename_dict = {}
        for old_col, new_col in column_mapping.items():
            if new_col != old_col:  # Chỉ rename nếu khác tên gốc
                rename_dict[old_col] = new_col
                
        if rename_dict:
            standardized_df = standardized_df.rename(columns=rename_dict)
        
        # Xử lý các giá trị NaN
        standardized_df = standardized_df.fillna('')
        
        # Chuyển đổi tất cả cột object thành string
        for col in standardized_df.select_dtypes(include=['object']).columns:
            standardized_df[col] = standardized_df[col].astype(str)
            
        return standardized_df
        
    def add_source_tracking(self, df: pd.DataFrame, file_info: Dict) -> pd.DataFrame:
        """
        Thêm thông tin tracking cho DataFrame
        
        Args:
            df: DataFrame cần thêm tracking
            file_info: Thông tin về file (name, size, sheet_name, etc.)
            
        Returns:
            DataFrame với thông tin tracking
        """
        result_df = df.copy()
        
        # Thêm các cột tracking
        result_df['File Nguồn'] = file_info.get('file_name', 'Unknown')
        result_df['Sheet Nguồn'] = file_info.get('sheet_name', 'Sheet1')
        result_df['Kích Thước File'] = file_info.get('file_size', 0)
        result_df['Số Dòng Gốc'] = len(df)
        
        return result_df
        
    def create_summary_sheet(self, all_dataframes: List[pd.DataFrame]) -> Tuple[pd.DataFrame, Dict]:
        """
        Tạo sheet tổng hợp từ tất cả các DataFrame với phân tích mã sản phẩm
        
        Args:
            all_dataframes: List các DataFrame đã được chuẩn hóa
            
        Returns:
            Tuple (DataFrame tổng hợp, Dict phân tích mã sản phẩm)
        """
        if not all_dataframes:
            return pd.DataFrame(), {}
            
        # Phân tích mã sản phẩm trước
        analysis = self.analyze_product_codes(all_dataframes)
        
        # Gộp tất cả DataFrame
        summary_df = pd.concat(all_dataframes, ignore_index=True, sort=False)
        
        # Thêm STT
        summary_df.insert(0, 'STT', range(1, len(summary_df) + 1))
        
        # Thêm cột phân tích so sánh mã sản phẩm
        summary_df = self.add_comparison_columns(summary_df, analysis)
        
        # Sắp xếp theo File Nguồn sau đó theo STT gốc nếu có
        if 'File Nguồn' in summary_df.columns:
            summary_df = summary_df.sort_values(['File Nguồn', 'STT'])
            # Reset lại STT sau khi sắp xếp
            summary_df['STT'] = range(1, len(summary_df) + 1)
        
        return summary_df, analysis
    
    def analyze_product_codes(self, all_dataframes: List[pd.DataFrame]) -> Dict:
        """
        Phân tích mã sản phẩm trùng lặp và unique giữa các file
        
        Args:
            all_dataframes: List các DataFrame đã được chuẩn hóa
            
        Returns:
            Dict chứa thông tin phân tích mã sản phẩm
        """
        analysis_result = {
            'duplicate_codes': {},  # Mã trùng lặp và nguồn
            'unique_codes': {},     # Mã unique theo từng file
            'total_duplicates': 0,
            'total_unique': 0,
            'files_comparison': {}
        }
        
        if not all_dataframes:
            return analysis_result
        
        # Tìm cột mã sản phẩm
        product_code_column = None
        common_product_columns = ['mã sản phẩm', 'product_code', 'item_code', 'mã', 'code', 'id']
        
        for df in all_dataframes:
            for col in df.columns:
                col_lower = str(col).lower().strip()
                if any(pattern in col_lower for pattern in common_product_columns):
                    product_code_column = col
                    break
            if product_code_column:
                break
        
        if not product_code_column:
            # Nếu không tìm thấy cột mã sản phẩm, thử cột đầu tiên
            if all_dataframes and len(all_dataframes[0].columns) > 0:
                product_code_column = all_dataframes[0].columns[1] if len(all_dataframes[0].columns) > 1 else all_dataframes[0].columns[0]
        
        if not product_code_column:
            return analysis_result
        
        # Collect tất cả mã sản phẩm từ mỗi file
        file_codes = {}
        all_codes = {}
        
        for i, df in enumerate(all_dataframes):
            if product_code_column not in df.columns:
                continue
                
            file_name = df['File Nguồn'].iloc[0] if 'File Nguồn' in df.columns else f'File_{i+1}'
            codes = set(df[product_code_column].astype(str).str.strip())
            codes.discard('')  # Bỏ mã rỗng
            codes.discard('nan')  # Bỏ NaN
            
            file_codes[file_name] = codes
            
            # Track tất cả codes và nguồn
            for code in codes:
                if code not in all_codes:
                    all_codes[code] = []
                all_codes[code].append(file_name)
        
        # Phân tích duplicate và unique
        for code, sources in all_codes.items():
            if len(sources) > 1:
                # Mã trùng lặp
                analysis_result['duplicate_codes'][code] = sources
                analysis_result['total_duplicates'] += 1
            else:
                # Mã unique
                source_file = sources[0]
                if source_file not in analysis_result['unique_codes']:
                    analysis_result['unique_codes'][source_file] = []
                analysis_result['unique_codes'][source_file].append(code)
                analysis_result['total_unique'] += 1
        
        # So sánh giữa các files
        file_names = list(file_codes.keys())
        for i, file1 in enumerate(file_names):
            for j, file2 in enumerate(file_names[i+1:], i+1):
                common_codes = file_codes[file1].intersection(file_codes[file2])
                unique_file1 = file_codes[file1] - file_codes[file2]
                unique_file2 = file_codes[file2] - file_codes[file1]
                
                comparison_key = f"{file1} vs {file2}"
                analysis_result['files_comparison'][comparison_key] = {
                    'common_codes': list(common_codes),
                    'file1_unique': list(unique_file1),
                    'file2_unique': list(unique_file2),
                    'common_count': len(common_codes),
                    'file1_unique_count': len(unique_file1),
                    'file2_unique_count': len(unique_file2)
                }
        
        analysis_result['product_code_column'] = product_code_column
        return analysis_result
    
    def add_comparison_columns(self, summary_df: pd.DataFrame, analysis: Dict) -> pd.DataFrame:
        """
        Thêm cột phân tích so sánh vào DataFrame
        
        Args:
            summary_df: DataFrame tổng hợp
            analysis: Kết quả phân tích từ analyze_product_codes
            
        Returns:
            DataFrame với các cột phân tích thêm vào
        """
        if analysis['product_code_column'] not in summary_df.columns:
            return summary_df
        
        # Thêm cột phân loại mã sản phẩm
        def classify_product_code(row):
            code = str(row[analysis['product_code_column']]).strip()
            if code in analysis['duplicate_codes']:
                return f"Trùng lặp ({len(analysis['duplicate_codes'][code])} files)"
            else:
                return "Unique"
        
        summary_df['Phân Loại Mã SP'] = summary_df.apply(classify_product_code, axis=1)
        
        # Thêm cột danh sách files chứa mã trùng
        def get_duplicate_sources(row):
            code = str(row[analysis['product_code_column']]).strip()
            if code in analysis['duplicate_codes']:
                return ", ".join(analysis['duplicate_codes'][code])
            else:
                return row.get('File Nguồn', '')
        
        summary_df['Nguồn Trùng Lặp'] = summary_df.apply(get_duplicate_sources, axis=1)
        
        return summary_df
        
    def format_excel_file(self, file_path: str, summary_sheet_name: str = "Dữ Liệu Tổng Hợp"):
        """
        Format file Excel với style đẹp
        
        Args:
            file_path: Đường dẫn file Excel
            summary_sheet_name: Tên sheet tổng hợp
        """
        try:
            workbook = openpyxl.load_workbook(file_path)
            
            for sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
                
                # Style cho header
                header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
                header_font = Font(color="FFFFFF", bold=True)
                
                # Áp dụng style cho dòng đầu tiên (header)
                for cell in sheet[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                
                # Auto-fit columns
                for column in sheet.columns:
                    max_length = 0
                    column_letter = get_column_letter(column[0].column)
                    
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    
                    # Đặt độ rộng cột (tối đa 50 chars)
                    adjusted_width = min(max_length + 2, 50)
                    sheet.column_dimensions[column_letter].width = adjusted_width
                
                # Thêm border cho tất cả cells có dữ liệu
                from openpyxl.styles import Border, Side
                thin_border = Border(
                    left=Side(style='thin'),
                    right=Side(style='thin'),
                    top=Side(style='thin'),
                    bottom=Side(style='thin')
                )
                
                for row in sheet.iter_rows():
                    for cell in row:
                        if cell.value is not None:
                            cell.border = thin_border
            
            workbook.save(file_path)
            logger.info(f"Đã format file Excel: {file_path}")
            
        except Exception as e:
            logger.error(f"Lỗi khi format Excel file: {str(e)}")
            
    def merge_excel_files(self, file_paths: List[str], output_path: str, 
                         progress_callback=None) -> Dict:
        """
        Gộp nhiều file Excel thành một file tổng hợp
        
        Args:
            file_paths: List đường dẫn các file Excel cần gộp
            output_path: Đường dẫn file Excel output
            progress_callback: Function callback để cập nhật progress
            
        Returns:
            Dict chứa thông tin kết quả
        """
        try:
            all_dataframes = []
            file_info_list = []
            total_files = len(file_paths)
            
            if progress_callback:
                progress_callback(5, f"Bắt đầu xử lý {total_files} files...")
            
            # Xử lý từng file
            for i, file_path in enumerate(file_paths):
                try:
                    file_name = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    
                    if progress_callback:
                        progress_callback(10 + (i * 60 // total_files), 
                                        f"Đang xử lý file: {file_name}")
                    
                    # Đọc file Excel
                    excel_file = pd.ExcelFile(file_path)
                    sheet_names = excel_file.sheet_names
                    
                    # Xử lý từng sheet trong file
                    for sheet_name in sheet_names:
                        df = pd.read_excel(file_path, sheet_name=sheet_name)
                        
                        if df.empty:
                            continue
                            
                        # Chuẩn hóa DataFrame
                        file_info = {
                            'file_name': file_name,
                            'sheet_name': sheet_name,
                            'file_size': file_size
                        }
                        
                        standardized_df = self.standardize_dataframe(df, file_name)
                        tracked_df = self.add_source_tracking(standardized_df, file_info)
                        
                        all_dataframes.append(tracked_df)
                        file_info_list.append({
                            'file_name': file_name,
                            'sheet_name': sheet_name,
                            'rows': len(df),
                            'columns': len(df.columns)
                        })
                        
                except Exception as e:
                    logger.error(f"Lỗi khi xử lý file {file_path}: {str(e)}")
                    continue
            
            if not all_dataframes:
                raise Exception("Không có dữ liệu nào được đọc từ các file")
            
            if progress_callback:
                progress_callback(70, "Đang tạo file tổng hợp...")
            
            # Tạo sheet tổng hợp với phân tích mã sản phẩm
            summary_df, product_analysis = self.create_summary_sheet(all_dataframes)
            
            # Tạo file Excel output với chỉ 1 sheet
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                # Chỉ ghi 1 sheet duy nhất chứa toàn bộ dữ liệu
                summary_df.to_excel(writer, sheet_name='Dữ Liệu Tổng Hợp', index=False)
            
            if progress_callback:
                progress_callback(90, "Đang format file Excel...")
            
            # Format file Excel
            self.format_excel_file(output_path)
            
            if progress_callback:
                progress_callback(100, "Hoàn thành!")
            
            # Tính toán thống kê
            total_rows = sum([info['rows'] for info in file_info_list])
            result = {
                'success': True,
                'output_file': output_path,
                'total_files': total_files,
                'total_rows': total_rows,
                'total_sheets': 1,  # Chỉ có 1 sheet duy nhất
                'file_info': file_info_list,
                'summary_rows': len(summary_df),
                'product_analysis': product_analysis
            }
            
            logger.info(f"Gộp file Excel thành công: {result}")
            return result
            
        except Exception as e:
            error_msg = f"Lỗi khi gộp file Excel: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg
            }
    
    def find_excel_files_recursive(self, root_path: str) -> List[Dict]:
        """
        Tìm tất cả file Excel trong folder structure một cách đệ quy
        
        Args:
            root_path: Đường dẫn thư mục gốc
            
        Returns:
            List các dict chứa thông tin file Excel
        """
        excel_files = []
        
        try:
            for root, dirs, files in os.walk(root_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_ext = os.path.splitext(file)[1].lower()
                    
                    if file_ext in self.supported_extensions:
                        # Tính relative path từ root
                        relative_path = os.path.relpath(file_path, root_path)
                        folder_path = os.path.dirname(relative_path)
                        
                        excel_files.append({
                            'file_path': file_path,
                            'file_name': file,
                            'relative_path': relative_path,
                            'folder_path': folder_path if folder_path != '.' else 'Root',
                            'file_size': os.path.getsize(file_path)
                        })
                        
            logger.info(f"Tìm thấy {len(excel_files)} file Excel trong {root_path}")
            return excel_files
            
        except Exception as e:
            logger.error(f"Lỗi khi tìm file Excel: {str(e)}")
            return []
    
    def extract_zip_and_find_excel(self, zip_path: str, extract_to: str = None) -> Tuple[str, List[Dict]]:
        """
        Extract ZIP file và tìm tất cả file Excel bên trong
        
        Args:
            zip_path: Đường dẫn file ZIP
            extract_to: Thư mục extract (tạo temp nếu None)
            
        Returns:
            Tuple (extract_path, list_excel_files)
        """
        if extract_to is None:
            extract_to = tempfile.mkdtemp(prefix="excel_merger_")
        
        try:
            # Validate ZIP file
            if not zipfile.is_zipfile(zip_path):
                raise Exception("File không phải là ZIP hợp lệ")
            
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
                
            logger.info(f"Đã extract ZIP đến: {extract_to}")
            
            # Tìm file Excel
            excel_files = self.find_excel_files_recursive(extract_to)
            
            return extract_to, excel_files
            
        except Exception as e:
            logger.error(f"Lỗi khi extract ZIP: {str(e)}")
            return extract_to, []
    
    def merge_from_folder_structure(self, folder_path: str = None, zip_path: str = None, 
                                   output_path: str = None, progress_callback=None) -> Dict:
        """
        Gộp file Excel từ folder structure hoặc ZIP file
        
        Args:
            folder_path: Đường dẫn folder chứa Excel files
            zip_path: Đường dẫn ZIP file chứa folder structure
            output_path: Đường dẫn file Excel output
            progress_callback: Function callback để cập nhật progress
            
        Returns:
            Dict chứa thông tin kết quả
        """
        try:
            temp_extract_path = None
            
            if progress_callback:
                progress_callback(5, "Khởi tạo xử lý folder...")
            
            # Xác định source: folder hoặc ZIP
            if zip_path and os.path.exists(zip_path):
                if progress_callback:
                    progress_callback(10, "Đang extract ZIP file...")
                
                temp_extract_path, excel_files = self.extract_zip_and_find_excel(zip_path)
                source_type = "ZIP"
                source_path = zip_path
                
            elif folder_path and os.path.exists(folder_path):
                if progress_callback:
                    progress_callback(10, "Đang quét folder structure...")
                
                excel_files = self.find_excel_files_recursive(folder_path)
                source_type = "Folder"
                source_path = folder_path
                
            else:
                raise Exception("Không tìm thấy folder hoặc ZIP file hợp lệ")
            
            if not excel_files:
                raise Exception("Không tìm thấy file Excel nào trong source")
            
            if progress_callback:
                progress_callback(20, f"Tìm thấy {len(excel_files)} file Excel")
            
            # Lấy danh sách đường dẫn file
            file_paths = [file_info['file_path'] for file_info in excel_files]
            
            # Extend merge_excel_files để xử lý folder info
            def enhanced_progress_callback(percent, message):
                # Chuyển đổi percent từ merge (0-100) sang tổng (20-90)
                total_percent = 20 + (percent * 70 // 100)
                if progress_callback:
                    progress_callback(total_percent, message)
            
            # Gọi merge_excel_files với enhanced callback
            result = self.merge_excel_files(
                file_paths=file_paths,
                output_path=output_path,
                progress_callback=enhanced_progress_callback
            )
            
            if result.get('success', False):
                # Thêm thông tin folder structure vào result
                result.update({
                    'source_type': source_type,
                    'source_path': source_path,
                    'folder_structure': self._analyze_folder_structure(excel_files),
                    'excel_files_found': excel_files
                })
                
                # product_analysis đã được thêm trong merge_excel_files
                
                if progress_callback:
                    progress_callback(100, "Hoàn thành gộp file từ folder!")
            
            # Cleanup temp folder nếu có
            if temp_extract_path and temp_extract_path != folder_path:
                try:
                    import shutil
                    shutil.rmtree(temp_extract_path)
                    logger.info(f"Đã dọn dẹp thư mục tạm: {temp_extract_path}")
                except Exception as e:
                    logger.warning(f"Không thể xóa thư mục tạm: {str(e)}")
            
            return result
            
        except Exception as e:
            error_msg = f"Lỗi khi gộp file từ folder: {str(e)}"
            logger.error(error_msg)
            
            # Cleanup temp folder nếu có lỗi
            if 'temp_extract_path' in locals() and temp_extract_path:
                try:
                    import shutil
                    shutil.rmtree(temp_extract_path)
                except:
                    pass
            
            return {
                'success': False,
                'error': error_msg
            }
    
    def _analyze_folder_structure(self, excel_files: List[Dict]) -> Dict:
        """
        Phân tích cấu trúc folder của các file Excel
        
        Args:
            excel_files: List thông tin file Excel
            
        Returns:
            Dict thống kê folder structure
        """
        folder_stats = {}
        
        for file_info in excel_files:
            folder_path = file_info['folder_path']
            
            if folder_path not in folder_stats:
                folder_stats[folder_path] = {
                    'file_count': 0,
                    'total_size': 0,
                    'files': []
                }
            
            folder_stats[folder_path]['file_count'] += 1
            folder_stats[folder_path]['total_size'] += file_info['file_size']
            folder_stats[folder_path]['files'].append(file_info['file_name'])
        
        return {
            'total_folders': len(folder_stats),
            'folder_details': folder_stats,
            'total_files': len(excel_files)
        }
