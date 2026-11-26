import pdfplumber
import pandas as pd
import re
import math
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

class PdfSpecExtractor:
    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        self.models_info = [] # [{"base_model": "DDRH-15-05[]", "output_voltage": 5.0, "variants": ["DDRH-15-05P", ...]}]
        self.raw_specs = [] # [{"category": "INPUT", "spec_name": "VOLTAGE RANGE", "value": "..."}]
        
    def extract_all(self):
        with pdfplumber.open(self.pdf_path) as pdf:
            tables = []
            for page in pdf.pages:
                page_tables = page.extract_tables()
                if page_tables:
                    tables.extend(page_tables)
            
            if not tables:
                print("No tables found in PDF")
                return []
                
            self._parse_model_selection_table(tables, pdf)
            self._parse_specification_table(tables)
            
            return self._generate_html_outputs()

    def _parse_model_selection_table(self, tables, pdf):
        """
        Tìm và phân tích bảng Model Selection. Trích xuất mã cơ sở, điện áp đầu ra và các biến thể.
        """
        model_table = None
        for table in tables:
            # Tìm bảng có cột ORDER NO. hoặc MODEL
            if not table or not table[0]: continue
            header = [str(cell).upper() if cell else "" for cell in table[0]]
            if any("ORDER NO" in h or "MODEL" in h for h in header):
                model_table = table
                break
                
        if not model_table:
            print("Could not find Model Selection Table.")
            return

        header = [str(cell).upper().replace('\n', ' ') if cell else "" for cell in model_table[0]]
        
        # Tìm cột Output Voltage
        out_volt_idx = -1
        order_no_idx = -1
        
        for i, h in enumerate(header):
            if "ORDER NO" in h or "MODEL" in h:
                order_no_idx = i
            if "OUTPUT" in h and "VOLTAGE" in h:
                out_volt_idx = i
                
        # Nếu dòng header bị merged 2 dòng, check dòng thứ 2
        if out_volt_idx == -1 and len(model_table) > 1:
            header2 = [str(cell).upper().replace('\n', ' ') if cell else "" for cell in model_table[1]]
            for i, h in enumerate(header2):
                if "OUTPUT" in h and "VOLTAGE" in h:
                    out_volt_idx = i
                elif h == "VOLTAGE": # Có thể phần trên là OUTPUT, phần dưới là VOLTAGE
                    out_volt_idx = i
                    
        if order_no_idx == -1: order_no_idx = 0
        if out_volt_idx == -1: out_volt_idx = 4 # Default fallback based on typical MW format

        # Extract variants suffix from all pages text
        suffixes = [""] # Default empty suffix
        all_text = " ".join([p.extract_text() or "" for p in pdf.pages])
        # Look for pattern like: [] = P, ST, DR or []= P, ST, DR
        suffix_match = re.search(r'\[\s*\]\s*=\s*([A-Za-z0-9,\s]+)', all_text)
        if suffix_match:
            suffix_str = suffix_match.group(1)
            suffixes = [s.strip() for s in suffix_str.split(',')]

        for row in model_table[1:]: # Skip header
            if not row or len(row) <= max(order_no_idx, out_volt_idx): continue
            
            order_no = str(row[order_no_idx] or "").strip()
            if not order_no or "ORDER NO" in order_no.upper() or "MODEL" in order_no.upper():
                continue
                
            out_volt_raw = str(row[out_volt_idx] or "").strip()
            
            # Chỉ lấy các row có mã hợp lệ
            if len(order_no) > 3:
                # Phân tích Output Voltage thành dạng float
                out_volt = 0.0
                volt_match = re.search(r'(\d+(?:\.\d+)?)', out_volt_raw)
                if volt_match:
                    out_volt = float(volt_match.group(1))
                    
                # Tạo các biến thể chính xác
                variants = []
                if "[]" in order_no:
                    for s in suffixes:
                        variants.append(order_no.replace("[]", s))
                else:
                    variants.append(order_no)
                    
                self.models_info.append({
                    "base_model": order_no,
                    "output_voltage": out_volt,
                    "variants": variants
                })
                
        print(f"Extracted {len(self.models_info)} base models with their variants.")

    def _parse_specification_table(self, tables):
        """
        Tìm và phân tích bảng Specification, tổ chức dữ liệu theo Category và Spec Name
        """
        spec_table = None
        for table in tables:
            if not table or not table[0]: continue
            header = [str(cell).upper() if cell else "" for cell in table[0]]
            if any("SPECIFICATION" in h for h in header) or \
               (len(table) > 2 and "INPUT" in str(table[1][0] or "").upper()):
                spec_table = table
                break
                
        if not spec_table:
            # Fallback to the largest table if we can't find explicitly
            tables.sort(key=len, reverse=True)
            if tables: spec_table = tables[0]
            
        if not spec_table: return
        
        current_category = ""
        
        for row in spec_table:
            # Ignore headers
            if not row or "SPECIFICATION" in str(row[0] or "").upper(): continue
            
            cat_col = str(row[0] or "").strip() if len(row) > 0 else ""
            spec_name = str(row[1] or "").strip() if len(row) > 1 else ""
            
            if cat_col and cat_col.isupper() and not spec_name:
                # Có thể là merged category row
                current_category = cat_col
                continue
                
            if cat_col:
                current_category = cat_col
                
            if not spec_name and cat_col:
                # Nếu cột 1 rỗng nhưng cột 0 có data, có thể là do pdfplumber đọc lệch
                spec_name = cat_col
                
            # Values column can be row[2] or row[1] depending on table structure
            value = ""
            if len(row) > 2 and row[2]:
                value = str(row[2]).strip()
            elif len(row) == 2 and not current_category:
                value = str(row[1]).strip()
                spec_name = str(row[0]).strip()
            
            # Handle multiple columns (for older extract_specs logic where value is spread across cols)
            if not value and len(row) > 2:
                values = [str(v).strip() for v in row[2:] if v is not None]
                if values:
                    value = " | ".join(values)
            
            if spec_name and value:
                value = value.replace('\n', ' ')
                self.raw_specs.append({
                    "category": current_category,
                    "spec_name": spec_name,
                    "value": value
                })

    def _resolve_value_for_model(self, raw_value, model_volt):
        """
        Ánh xạ giá trị theo dải điện áp (ví dụ 5Vo: 10W, 12Vo~24Vo: 15W)
        """
        # Nếu chuỗi không chứa Vo: thì dùng chung cho tất cả
        if "Vo:" not in raw_value and "Vo :" not in raw_value:
            # Handle notes like "Note.2" which usually prefixes
            clean_val = re.sub(r'Note\.\d+\s*', '', raw_value).strip()
            return clean_val
            
        # Tách chuỗi bằng regex
        # Pattern: (dải_voltage)Vo: (giá_trị)
        # Ví dụ: 5Vo: 10W  12Vo ~ 24Vo: 15W
        pattern = r'(?:Note\.\d+\s*)?([\d\.~]+)\s*Vo\s*:\s*(.*?)(?=(?:[\d\.~]+\s*Vo\s*:)|$)'
        matches = re.finditer(pattern, raw_value)
        
        for match in matches:
            volt_range_str = match.group(1).strip()
            val = match.group(2).strip()
            
            # Parse voltage range
            if '~' in volt_range_str:
                parts = volt_range_str.split('~')
                try:
                    v_min = float(parts[0].strip())
                    v_max = float(parts[1].strip())
                    if v_min <= model_volt <= v_max:
                        return val
                except ValueError:
                    pass
            else:
                try:
                    v_exact = float(volt_range_str)
                    if math.isclose(v_exact, model_volt, rel_tol=1e-5):
                        return val
                except ValueError:
                    pass
                    
        return raw_value # Trả về giá trị gốc nếu không khớp được regex

    def _generate_html_outputs(self):
        results = []
        for model_group in self.models_info:
            volt = model_group["output_voltage"]
            for exact_model in model_group["variants"]:
                html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">\n'
                html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>\n'
                html += '<tbody>\n'
                html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{exact_model}</td></tr>\n'
                html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>Nguồn xung {exact_model}</td></tr>\n'
                
                current_cat = None
                for spec in self.raw_specs:
                    if spec["category"] != current_cat and spec["category"]:
                        current_cat = spec["category"]
                        # Optional: Add category header row
                        # html += f'<tr style="background-color: #e9ecef;"><td colspan="2" style="font-weight: bold; text-align: center;">{current_cat}</td></tr>\n'
                        
                    spec_name = spec["spec_name"]
                    raw_val = spec["value"]
                    
                    # Resolve voltage-dependent value
                    resolved_val = self._resolve_value_for_model(raw_val, volt)
                    
                    if resolved_val:
                        html += f'<tr><td style="font-weight: bold;">{spec_name}</td><td>{resolved_val}</td></tr>\n'
                
                html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>\n'
                html += '</tbody>\n</table>'
                
                results.append({
                    "Mã sản phẩm": exact_model,
                    "HTML Table": html
                })
        return results

def extract_and_export():
    pdf_path = r"app\downloads\output_meanwell_20260704094306\catalog\DDRH-15DR_DDRH-15-spec.pdf" # Mặc định để test
    
    # Có thể nhận tham số truyền vào từ arguments hoặc môi trường
    import sys
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        
    print(f"Bắt đầu trích xuất dữ liệu từ: {pdf_path}")
    
    import os
    if not os.path.exists(pdf_path):
        print(f"Lỗi: Không tìm thấy file {pdf_path}")
        return

    extractor = PdfSpecExtractor(pdf_path)
    results = extractor.extract_all()
    
    if results:
        df = pd.DataFrame(results)
        excel_path = "Specs_Export_Dynamic.xlsx"
        df.to_excel(excel_path, index=False)
        print(f"Đã trích xuất thành công {len(results)} mã sản phẩm (biến thể) và xuất ra {excel_path}")
    else:
        print("Trích xuất không thành công hoặc file PDF không có dữ liệu phù hợp.")

if __name__ == "__main__":
    extract_and_export()
