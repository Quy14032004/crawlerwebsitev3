import sys
import time
import threading
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, Callable, Any, Dict, List
import os
import re

# Fix for Windows terminal Unicode encoding
import os as _os

def _ensure_utf8():
    """Buộc stdout/stderr dùng UTF-8 trên Windows để tránh UnicodeEncodeError với emoji/tiếng Việt."""
    try:
        # Đặt environment variable trước (ảnh hưởng subprocesses)
        _os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
        _os.environ.setdefault('PYTHONUTF8', '1')

        # Reconfigure stdout/stderr nếu chưa phải UTF-8
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                enc = getattr(stream, 'encoding', None)
                if enc and enc.lower().replace('-', '') != 'utf8':
                    if hasattr(stream, 'reconfigure'):
                        try:
                            stream.reconfigure(encoding='utf-8', errors='replace')
                        except Exception:
                            pass
    except Exception:
        pass

_ensure_utf8()

def _remove_vietnamese_accents(text):
    """
    Remove Vietnamese accents and special characters for safe terminal output.
    """
    if not isinstance(text, str):
        return str(text)
    
    # Mapping for Vietnamese characters to ASCII
    accents = {
        'a': 'áàảãạăắằẳẵặâấầẩẫậ',
        'A': 'ÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬ',
        'd': 'đ',
        'D': 'Đ',
        'e': 'éèẻẽẹêếềểễệ',
        'E': 'ÉÈẺẼẸÊẾỀỂỄỆ',
        'i': 'íìỉĩị',
        'I': 'ÍÌỈĨỊ',
        'o': 'óòỏõọôốồổỗộơớờởỡợ',
        'O': 'ÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ',
        'u': 'úùủũụưứừửữự',
        'U': 'ÚÙỦŨỤƯỨỪỬỮỰ',
        'y': 'ýỳỷỹỵ',
        'Y': 'ÝỲỶỸỴ'
    }
    
    res = text
    for ascii_char, viet_chars in accents.items():
        for char in viet_chars:
            res = res.replace(char, ascii_char)
            
    # Also replace some other potential non-ASCII characters
    return res.encode('ascii', 'ignore').decode('ascii')

# ANSI color codes
class Colors:
    GREEN = '\033[92m'      # Thành công
    YELLOW = '\033[93m'     # Cảnh báo  
    RED = '\033[91m'        # Lỗi
    BLUE = '\033[94m'       # Thông tin
    CYAN = '\033[96m'       # Tiến trình
    WHITE = '\033[97m'      # Bình thường
    BOLD = '\033[1m'        # Đậm
    RESET = '\033[0m'       # Reset màu

class TerminalProgressBar:
    """
    Thanh tiến trình terminal chi tiết với:
    - Hiển thị % hoàn thành (0-100%)
    - Thời gian đã trôi qua và ước tính còn lại
    - Tên function đang thực thi
    - Hỗ trợ nested operations với indentation
    - Color coding theo trạng thái
    - Tóm tắt kết quả cuối cùng
    - Chế độ verbose/minimal
    """
    
    _instances: Dict[str, 'TerminalProgressBar'] = {}
    _lock = threading.Lock()
    _global_stats = {
        'total_operations': 0,
        'completed_operations': 0,
        'failed_operations': 0,
        'start_time': None
    }
    
    def __init__(self, 
                 name: str, 
                 total_steps: int = 100,
                 verbose: bool = True,
                 parent: Optional['TerminalProgressBar'] = None,
                 show_eta: bool = True):
        self.name = name
        self.total_steps = total_steps
        self.current_step = 0
        self.verbose = verbose
        self.parent = parent
        self.show_eta = show_eta
        self.start_time = time.time()
        self.last_update_time = self.start_time
        self.status = "running"  # running, success, warning, error
        self.message = ""
        self.details = ""
        # Ensure name is ASCII-safe for terminal
        self.name = _remove_vietnamese_accents(name)
        self.indent_level = 0 if parent is None else parent.indent_level + 1
        self.children: List['TerminalProgressBar'] = []
        self.completed = False
        self.result_summary = {}
        
        # Thống kê global
        with self._lock:
            if self._global_stats['start_time'] is None:
                self._global_stats['start_time'] = time.time()
            self._global_stats['total_operations'] += 1
            self._instances[name] = self
            
        if parent:
            parent.children.append(self)
            
        self._print_start()
    
    def _print_start(self):
        """In thông báo bắt đầu"""
        indent = "  " * self.indent_level
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if self.verbose:
            # Re-ensure message is clean of Vietnamese accents for terminal display
            safe_name = self.name
            start_msg = _remove_vietnamese_accents("Bat dau...")
            print(f"{Colors.BLUE}[{timestamp}]{Colors.RESET} {indent}>> {Colors.BOLD}{safe_name}{Colors.RESET} - {start_msg}")
        else:
            print(f"{indent}> {self.name}")
            
        sys.stdout.flush()
    
    def update(self, 
               step: int, 
               message: str = "", 
               details: str = "",
               force_print: bool = False):
        """Cập nhật tiến trình"""
        self.current_step = min(step, self.total_steps)
        self.message = message
        self.details = details
        current_time = time.time()
        
        # Chỉ in nếu đã qua ít nhất 0.5 giây hoặc force_print
        if force_print or (current_time - self.last_update_time) >= 0.5:
            self._print_progress()
            self.last_update_time = current_time
    
    def _print_progress(self):
        """In thanh tiến trình hiện tại"""
        if not self.verbose and self.current_step < self.total_steps:
            return
            
        indent = "  " * self.indent_level
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Tính toán phần trăm
        percent = (self.current_step / self.total_steps) * 100
        
        # Tính thời gian
        elapsed = time.time() - self.start_time
        elapsed_str = self._format_time(elapsed)
        
        # Ước tính thời gian còn lại
        eta_str = ""
        if self.show_eta and self.current_step > 0 and self.current_step < self.total_steps:
            eta = (elapsed / self.current_step) * (self.total_steps - self.current_step)
            eta_str = f" | ETA: {self._format_time(eta)}"
        
        # Thanh tiến trình
        bar_length = 20
        filled_length = int(bar_length * percent // 100)
        bar = "#" * filled_length + "-" * (bar_length - filled_length)
        
        # Màu sắc theo trạng thái
        color = Colors.CYAN
        if self.status == "success":
            color = Colors.GREEN
        elif self.status == "warning":
            color = Colors.YELLOW
        elif self.status == "error":
            color = Colors.RED
        
        # Tạo dòng hiển thị
        progress_line = f"{color}[{timestamp}]{Colors.RESET} {indent}"
        progress_line += f"{color}[{bar}]{Colors.RESET} "
        progress_line += f"{color}{percent:5.1f}%{Colors.RESET} "
        progress_line += f"({self.current_step}/{self.total_steps}) "
        progress_line += f"T: {elapsed_str}{eta_str}"
        
        if self.message:
            safe_msg = _remove_vietnamese_accents(self.message)
            progress_line += f" | {Colors.WHITE}{safe_msg}{Colors.RESET}"
            
        if self.details and self.verbose:
            safe_details = _remove_vietnamese_accents(self.details)
            progress_line += f"\n{indent}  +- {Colors.CYAN}{safe_details}{Colors.RESET}"
        
        # Clear line và in lại
        print(f"\r\033[K{progress_line}", end="")
        if self.details and self.verbose:
            print()  # Xuống dòng nếu có details
        
        sys.stdout.flush()
    
    def complete(self, 
                 status: str = "success", 
                 message: str = "", 
                 summary: Dict[str, Any] = None):
        """Hoàn thành tiến trình"""
        self.current_step = self.total_steps
        self.status = status
        self.message = message or f"Hoan thanh {self.name}"
        self.completed = True
        
        if summary:
            self.result_summary = summary
        
        # Cập nhật thống kê global
        with self._lock:
            if status == "success":
                self._global_stats['completed_operations'] += 1
            else:
                self._global_stats['failed_operations'] += 1
        
        self._print_completion()
        
        # Xóa khỏi instances
        if self.name in self._instances:
            del self._instances[self.name]
    
    def _print_completion(self):
        """In thông báo hoàn thành"""
        indent = "  " * self.indent_level
        timestamp = datetime.now().strftime("%H:%M:%S")
        elapsed = time.time() - self.start_time
        elapsed_str = self._format_time(elapsed)
        
        # Icon và màu theo trạng thái
        if self.status == "success":
            icon = "[OK] "
            color = Colors.GREEN
        elif self.status == "warning":
            icon = "[WARN]"
            color = Colors.YELLOW
        elif self.status == "error":
            icon = "[ERR] "
            color = Colors.RED
        else:
            icon = "[INFO]"
            color = Colors.BLUE
        
        print(f"\r\033[K", end="")  # Clear current line
        completion_line = f"{color}[{timestamp}]{Colors.RESET} {indent}{icon} "
        completion_line += f"{Colors.BOLD}{self.name}{Colors.RESET} - "
        safe_msg = _remove_vietnamese_accents(self.message)
        completion_line += f"{color}{safe_msg}{Colors.RESET} "
        completion_line += f"(T: {elapsed_str})"
        
        print(completion_line)
        
        # In tóm tắt nếu có
        if self.result_summary and self.verbose:
            for key, value in self.result_summary.items():
                print(f"{indent}  * {key}: {Colors.CYAN}{value}{Colors.RESET}")
        
        sys.stdout.flush()
    
    def error(self, message: str, details: str = ""):
        """Đánh dấu lỗi"""
        self.complete("error", message)
        if details:
            indent = "  " * self.indent_level
            print(f"{indent}  ? {Colors.RED}{details}{Colors.RESET}")
    
    def warning(self, message: str):
        """Đánh dấu cảnh báo"""
        self.complete("warning", message)
    
    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format thời gian thành chuỗi dễ đọc"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m{secs:02d}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h{minutes:02d}m"
    
    @staticmethod
    def print_global_summary():
        """In tóm tắt toàn bộ hệ thống"""
        with TerminalProgressBar._lock:
            stats = TerminalProgressBar._global_stats.copy()
        
        if stats['start_time'] is None:
            return
            
        total_time = time.time() - stats['start_time']
        total_ops = stats['total_operations']
        completed = stats['completed_operations']
        failed = stats['failed_operations']
        
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}SUMMARY - TOM TAT TONG QUAN HE THONG{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"Time - Tong thoi gian: {Colors.CYAN}{TerminalProgressBar._format_time(total_time)}{Colors.RESET}")
        print(f"Ops  - Tong so operations: {Colors.WHITE}{total_ops}{Colors.RESET}")
        print(f"OK   - Thanh cong: {Colors.GREEN}{completed}{Colors.RESET}")
        print(f"ERR  - That bai: {Colors.RED}{failed}{Colors.RESET}")
        
        if total_ops > 0:
            success_rate = (completed / total_ops) * 100
            color = Colors.GREEN if success_rate >= 80 else Colors.YELLOW if success_rate >= 60 else Colors.RED
            print(f"Rate - Ty le thanh cong: {color}{success_rate:.1f}%{Colors.RESET}")
        
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")
        
        # Reset stats
        TerminalProgressBar._global_stats = {
            'total_operations': 0,
            'completed_operations': 0,
            'failed_operations': 0,
            'start_time': None
        }

def progress_tracker(name: str = None, 
                    total_steps: int = 100, 
                    verbose: bool = True,
                    show_summary: bool = True):
    """
    Decorator để tự động thêm progress bar cho function
    
    Args:
        name: Tên hiển thị (mặc định sẽ dùng tên function)
        total_steps: Tổng số bước (mặc định 100)
        verbose: Hiển thị chi tiết hay không
        show_summary: Hiển thị tóm tắt global khi kết thúc
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = name or func.__name__
            progress = TerminalProgressBar(
                name=func_name,
                total_steps=total_steps,
                verbose=verbose
            )
            
            try:
                # Inject progress bar vào kwargs nếu function có parameter 'progress'
                import inspect
                sig = inspect.signature(func)
                if 'progress' in sig.parameters:
                    kwargs['progress'] = progress
                
                # Thực thi function
                result = func(*args, **kwargs)
                
                # Tự động complete nếu chưa complete
                if not progress.completed:
                    progress.complete("success", f"Hoan thanh {func_name}")
                
                return result
                
            except Exception as e:
                progress.error(f"Loi: {str(e)}")
                if show_summary and len(TerminalProgressBar._instances) == 0:
                    TerminalProgressBar.print_global_summary()
                raise
            
            finally:
                # In tóm tắt global nếu đây là operation cuối cùng
                if show_summary and len(TerminalProgressBar._instances) == 0:
                    TerminalProgressBar.print_global_summary()
        
        return wrapper
    return decorator

def create_child_progress(parent: TerminalProgressBar, 
                         name: str, 
                         total_steps: int = 100) -> TerminalProgressBar:
    """Tạo progress bar con cho nested operations"""
    return TerminalProgressBar(
        name=name,
        total_steps=total_steps,
        verbose=parent.verbose,
        parent=parent,
        show_eta=parent.show_eta
    )

# Utility functions để sử dụng trong code
def simple_progress(name: str, steps: int = 100, verbose: bool = True) -> TerminalProgressBar:
    """Tạo progress bar đơn giản"""
    return TerminalProgressBar(name, steps, verbose)

def batch_progress(items: list, 
                   name: str, 
                   process_func: Callable,
                   verbose: bool = True) -> list:
    """Xử lý batch với progress bar tự động"""
    progress = TerminalProgressBar(name, len(items), verbose)
    results = []
    
    try:
        for i, item in enumerate(items):
            progress.update(i, f"Đang xử lý item {i+1}/{len(items)}")
            result = process_func(item)
            results.append(result)
        
        progress.complete("success", f"Đã xử lý {len(items)} items")
        return results
        
    except Exception as e:
        progress.error(f"Lỗi khi xử lý: {str(e)}")
        raise 