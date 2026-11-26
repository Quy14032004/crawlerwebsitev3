import os
import requests
import concurrent.futures
from bs4 import BeautifulSoup
import pdfplumber
import pandas as pd
from PIL import Image
from io import BytesIO
import re
from datetime import datetime
import threading

GROQ_API_KEY = "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"

_translation_cache = {}

def translate_text(text, use_cache=True, **kwargs):
    if not text or not text.strip():
        return text
    
    clean_text = text.strip()
    
    if use_cache and clean_text in _translation_cache:
        return _translation_cache[clean_text]
        
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        prompt = f"Translate the following technical term or text to Vietnamese. Please use specialized terminology from the field of industrial electronic engineering (kỹ thuật công nghiệp điện tử). Only return the translated text, no explanations, no quotes:\n\n{clean_text}"
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1
        }
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        translated = data["choices"][0]["message"]["content"].strip()
        
        # Remove surrounding quotes if Llama adds them
        if translated.startswith('"') and translated.endswith('"'):
            translated = translated[1:-1]
            
        if use_cache:
            _translation_cache[clean_text] = translated
        return translated
    except Exception as e:
        print(f"Error translating with Groq: {e}")
        # Chỉ dùng Groq, nếu lỗi trả về text gốc
        return clean_text
# Lock để tránh crash pypdfium2 (libc++ out-of-bounds) trong môi trường đa luồng
pdf_render_lock = threading.Lock()

def download_pdf(url, save_path):
    print(f"Downloading PDF from {url}...")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"PDF downloaded successfully to {save_path}.")
        return save_path
    except Exception as e:
        print(f"Error downloading PDF: {e}")
        return None

def extract_general_name(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            text = page.extract_text()
            if text:
                lines = text.split('\n')
                for line in lines:
                    if "Series" in line or "Supply" in line or "Power" in line:
                        # Clean up " series" or " Series"
                        return re.sub(r'(?i)\s*series\s*', '', line).strip()
                # fallback
                if len(lines) > 1:
                    return re.sub(r'(?i)\s*series\s*', '', lines[1]).strip()
    except Exception as e:
        print(f"Error extracting general name: {e}")
    return "Switching Power Supply"

def process_image(pdf_path, output_dir, model_name):
    # Extract the largest image from the first page
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            images = page.images
            
            if not images:
                print("No images found in PDF.")
                return ""
            
            # Find the largest image by area
            largest_image = max(images, key=lambda img: img['width'] * img['height'])
            
            # Extract the image
            # pdfplumber doesn't directly give the image bytes easily without PyMuPDF
            # We can use pdf.pages[0].to_image() to render the page and crop it
            bbox = [largest_image['x0'], largest_image['top'], largest_image['x1'], largest_image['bottom']]
            
            # Adjust bbox to avoid text (like GTIN CODE) overlaying the bottom of the image
            for c in page.chars:
                if (c['x0'] < bbox[2] and c['x1'] > bbox[0] and 
                    c['top'] < bbox[3] and c['bottom'] > bbox[1]):
                    if c['top'] > (bbox[1] + bbox[3]) / 2:
                        bbox[3] = min(bbox[3], c['top'])
            
            crop_bbox = (
                max(0, bbox[0]),
                max(0, bbox[1]),
                min(page.width, bbox[2]),
                min(page.height, bbox[3])
            )
            
            if crop_bbox[0] >= crop_bbox[2] or crop_bbox[1] >= crop_bbox[3]:
                print(f"Invalid crop_bbox {crop_bbox} for {model_name}, skipping image extraction.")
                return ""
            
            cropped_page = page.within_bbox(crop_bbox)
            with pdf_render_lock:
                img = cropped_page.to_image(resolution=300).original
            
            # Now we have a PIL image `img`. We need to pad it to 800x800 white background.
            # Convert to RGB just in case
            img = img.convert("RGB")
            
            # Calculate resize ratio to fit within 800x800
            target_size = 800
            ratio = min(target_size / img.width, target_size / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            
            resized_img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Create white canvas 800x800
            canvas = Image.new("RGB", (target_size, target_size), "white")
            
            # Paste resized image into center
            paste_x = (target_size - new_size[0]) // 2
            paste_y = (target_size - new_size[1]) // 2
            canvas.paste(resized_img, (paste_x, paste_y))
            
            # Save as webp
            os.makedirs(output_dir, exist_ok=True)
            safe_model_name = "".join(c for c in model_name if c.isalnum() or c in ('-', '_'))
            img_filename = f"{safe_model_name}.webp"
            img_path = os.path.join(output_dir, img_filename)
            
            canvas.save(img_path, "WEBP", quality=90)
            print(f"Saved image for {model_name} at {img_path}")
            return img_path
            
    except Exception as e:
        print(f"Error processing image: {e}")
        return ""

def _find_col_idx(h_suffix, models, voltage_to_col):
    """Tìm col_idx cho model dựa trên suffix (voltage), hỗ trợ ô bị gộp (merged cells).
    
    Args:
        h_suffix: suffix của model HTML, vd '36', '48', '3.3'
        models: danh sách model từ PDF table (col headers)
        voltage_to_col: dict ánh xạ voltage string -> col_idx (0-based trong data columns)
    """
    import re
    # Cách 1: Dùng voltage_to_col map (xử lý merged cells như '36V 48V')
    h_suffix_clean = h_suffix.replace('V', '').strip()
    if h_suffix_clean in voltage_to_col:
        return voltage_to_col[h_suffix_clean]
    
    # Cách 2: Match suffix với danh sách models từ PDF
    for i, m in enumerate(models):
        m_clean = re.sub(r'[^\w\.\-]', '', m)
        m_suffix = m_clean.rsplit('-', 1)[-1].strip()
        if m_suffix == h_suffix:
            return i
    
    return 0  # fallback về cột đầu tiên

def extract_specs_from_pdf(pdf_path, general_name, image_output_dir, prod_id, models_from_html=None):
    results = []
    
    models = []
    main_table = []
    encoding_table = []
    is_vertical_table = False
    vertical_model_data = {}

    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            text = page.extract_text() or ""
            
            # Find models from text on this page (if it missed the table boundary)
            page_models = []
            for line in text.split('\n'):
                if line.strip().upper().startswith('MODEL') and prod_id in line:
                    parts = line.split()
                    page_models = [p.strip() for p in parts if p.upper() != 'MODEL' and '-' in p]
                    break
                    
            if not tables:
                continue
                
            for t in tables:
                # Check if MODEL is explicitly in this table
                found_in_table = False
                found_models_this_table = False
                
                # Check if it's a vertical table (models in rows)
                header_row = [str(x).upper().strip() for x in t[0] if x] if len(t) > 0 else []
                model_col_idx = -1
                if header_row and ('ORDER NO.' in header_row or 'MODEL' in header_row or 'PART NO.' in header_row) and len(t) > 1:
                    for idx, cell in enumerate(t[0]):
                        cell_str = str(cell).upper().strip()
                        if cell and ('ORDER NO.' in cell_str or 'MODEL' in cell_str or 'PART NO.' in cell_str):
                            model_col_idx = idx
                            break
                            
                    if model_col_idx != -1:
                        # extract models from that column
                        table_models = []
                        for row_idx in range(1, len(t)):
                            if len(t[row_idx]) > model_col_idx and t[row_idx][model_col_idx]:
                                cell_val = str(t[row_idx][model_col_idx]).strip().replace('\n', '')
                                if '-' in cell_val: # crude check if it's a model
                                    table_models.append(cell_val)
                                    
                        if table_models:
                            models = table_models
                            found_in_table = True
                            found_models_this_table = True
                            is_vertical_table = True
                            
                if not is_vertical_table:
                    for row in t:
                        if row[0] and 'MODEL' in row[0].upper():
                            m_row = [m.replace('\n', '').strip() for m in row[1:] if m]
                            if m_row:
                                models = m_row
                                found_in_table = True
                                found_models_this_table = True
                                break
                                
                # If not found in table, but we have page_models and table is wide enough
                if not found_in_table and page_models and len(t) > 3 and not is_vertical_table:
                    max_cols = max(len(r) for r in t)
                    # Must have enough columns to contain model data
                    # Relaxed: at least len(page_models)/2 + 1 to handle merged cells case
                    min_required = max(3, len(page_models) // 2 + 1)
                    if max_cols >= min_required:
                        models = page_models
                        found_models_this_table = True
                        
                # Only update main_table if:
                # 1. We have models AND this table is where models were found
                # 2. AND the new table is larger than what we already have (avoid overwrite by small tables)
                if found_models_this_table and len(t) > 3:
                    if len(t) > len(main_table):
                        main_table = t

                    
                # Look for Model Encoding table
                if len(t) > 1 and len(t[0]) >= 2:
                    header_row = [str(x).lower().strip() for x in t[0] if x]
                    if any('type' in h for h in header_row) and any('description' in h for h in header_row):
                        encoding_table = t
                    
            if models and main_table and encoding_table:
                break
                
        # Check if any model contains a special character (like the square box □ or spaces)
        has_special_box = False
        import re
        for m in models:
            if re.search(r'[^\w\.\-]', m):
                has_special_box = True
                break
                
        # Continue searching other pages if we need encoding_table but found main_table
        if models and main_table and not encoding_table and has_special_box:
            for page in pdf.pages[pdf.pages.index(page)+1:]:
                for t in page.extract_tables():
                    if len(t) > 1 and len(t[0]) >= 2:
                        header_row = [str(x).lower().strip() for x in t[0] if x]
                        if any('type' in h for h in header_row) and any('description' in h for h in header_row):
                            encoding_table = t
                            break
                if encoding_table:
                    break
                    
        # If no special box is found, ignore encoding_table even if we accidentally found it earlier
        if not has_special_box:
            encoding_table = []
                
        if not models:
            print("Could not find MODEL row in table or text.")
            if models_from_html:
                models = models_from_html
            else:
                # Try to extract models from first page text using regex
                first_page_text = pdf.pages[0].extract_text()
                if first_page_text:
                    pattern = rf'{prod_id}-\d+(?:\.\d+)?(?![\w])'
                    found_models = list(set(re.findall(pattern, first_page_text)))
                    if found_models:
                        models = sorted(found_models)
                        
            if not models:
                # Fallback to the general prod_id if still not found
                models = [prod_id]
            
        print(f"Found PDF models (columns): {models}")
        
        # Build mapping from actual models (HTML) to PDF table columns
        final_models_mapping = []
        
        # Build a voltage-to-column-index map from the first data row of main_table
        # This handles merged cells like '36V 48V' where PDF packs 2 voltages into 1 cell
        voltage_to_col = {}  # maps voltage suffix string -> (col_idx_in_table, is_merged_twin)
        
        if is_vertical_table and main_table:
            # Apply fill-down logic for vertically merged cells
            filled_table = []
            last_values = [None] * len(main_table[0]) if main_table else []
            for row in main_table:
                filled_row = list(row)
                for col_idx, cell in enumerate(filled_row):
                    if cell is None or str(cell).strip() == "":
                        if col_idx < len(last_values) and last_values[col_idx] is not None:
                            filled_row[col_idx] = last_values[col_idx]
                    else:
                        while len(last_values) <= col_idx:
                            last_values.append(None)
                        last_values[col_idx] = cell
                filled_table.append(filled_row)
            main_table = filled_table
            
            # Find the first row that contains a model
            first_model_row_idx = -1
            model_col_idx = -1
            for row_idx, row in enumerate(main_table):
                if model_col_idx == -1:
                    for col_idx, cell in enumerate(row):
                        cell_str = str(cell).upper().strip()
                        if cell and ('ORDER NO.' in cell_str or 'MODEL' in cell_str or 'PART NO.' in cell_str):
                            model_col_idx = col_idx
                            break
                if model_col_idx != -1 and row_idx > 0:
                    if len(row) > model_col_idx and row[model_col_idx]:
                        cell_val = str(row[model_col_idx]).strip()
                        if '-' in cell_val:
                            first_model_row_idx = row_idx
                            break
                            
            if first_model_row_idx != -1:
                # Extract headers
                headers = []
                for col_idx in range(len(main_table[0])):
                    col_header_parts = []
                    for r in range(first_model_row_idx):
                        if col_idx < len(main_table[r]):
                            val = str(main_table[r][col_idx]).strip().replace('\n', ' ')
                            if val and val not in col_header_parts and val != "None":
                                col_header_parts.append(val)
                    headers.append(" ".join(col_header_parts).strip())
                    
                # Build mapping for each model
                for r in range(first_model_row_idx, len(main_table)):
                    row = main_table[r]
                    if len(row) > model_col_idx and row[model_col_idx]:
                        m_name = str(row[model_col_idx]).strip().replace('\n', '')
                        if '-' in m_name:
                            data = {}
                            for col_idx in range(len(row)):
                                if col_idx != model_col_idx and col_idx < len(headers):
                                    spec_name = headers[col_idx]
                                    val = str(row[col_idx]).strip().replace('\n', '<br>')
                                    if spec_name and val and val != "None":
                                        data[spec_name] = val
                            vertical_model_data[m_name] = data
                            
        elif main_table:
            # Horizontal table parsing logic (existing)
            # First row of main_table is a data row (no MODEL header in MSP-style tables)
            # Try to find a header row that looks like voltages
            first_row = main_table[0]
            for col_i, cell in enumerate(first_row[1:], start=1):  # skip spec name col
                if cell:
                    cell_str = str(cell).strip()
                    # Find all voltage values in this cell (handles merged '36V 48V')
                    voltages_in_cell = re.findall(r'([\d]+(?:\.[\d]+)?(?:V)?)', cell_str)
                    for v in voltages_in_cell:
                        v_clean = v.replace('V', '').strip()
                        if v_clean:
                            voltage_to_col[v_clean] = col_i - 1  # 0-based index into data columns
        
        types = []
        if encoding_table:
            # Tạo các tùy chọn dựa trên bảng Model Encoding
            header = [str(x).lower().strip() if x else "" for x in encoding_table[0]]
            type_idx = next((i for i, h in enumerate(header) if 'type' in h), 0)
            for r_idx in range(1, len(encoding_table)):
                if len(encoding_table[r_idx]) > type_idx:
                    t_val = str(encoding_table[r_idx][type_idx]).strip()
                    if t_val:
                        types.append(t_val)
        else:
            # Dự phòng: Bảng Model Encoding bị ẩn/lỗi font (wingdings) không trích xuất được bằng extract_tables
            def ungarble(text):
                if not text: return text
                return ''.join(chr(ord(c)-0xf000) if 0xf000<=ord(c)<=0xf0ff else c for c in text)
            
            full_text = ' '.join([ungarble(p.extract_text()) for p in pdf.pages])
            import re
            match = re.search(r'Type\s+Description.*?\n(.*?)(?:File Name|SPECIFICATION|$)', full_text, re.DOTALL | re.IGNORECASE)
            if match:
                encoding_table = [['Type', 'Description']]
                for line in match.group(1).strip().split('\n'):
                    parts = line.strip().split(' ', 1)
                    t_val = parts[0].strip()
                    if t_val and t_val not in types:
                        types.append(t_val)
                        desc = parts[1].strip() if len(parts) > 1 else ""
                        encoding_table.append([t_val, desc])
                        
        if types:
                        
            base_models = models_from_html if models_from_html else models
            if not base_models:
                base_models = [prod_id]
                
            generated = set()
            for h_m in base_models:
                h_suffix = h_m.rsplit('-', 1)[-1].strip()
                if is_vertical_table:
                    # Find the matching model in models
                    base_table_model = None
                    for m in models:
                        m_clean = re.sub(r'[^\w\.\-]', '', m)
                        if m_clean.rsplit('-', 1)[-1].strip() == h_suffix:
                            base_table_model = m
                            break
                    if not base_table_model and models:
                        base_table_model = models[0]
                    mapped_val = base_table_model
                else:
                    # Find original column index
                    mapped_val = _find_col_idx(h_suffix, models, voltage_to_col)
                
                # Multiply by Model Encoding options
                if h_m.startswith(prod_id):
                    rem = h_m[len(prod_id):]
                    if '-' in rem:
                        suffix = '-' + rem.split('-', 1)[1]
                    else:
                        suffix = ''
                        
                    for t in types:
                        t_clean = t.replace('\n', ' ').strip()
                        repl = '' if t_clean.lower() in ['blank', 'option'] else t_clean
                        new_m = f"{prod_id}{repl}{suffix}"
                        generated.add((new_m, mapped_val))
                else:
                    generated.add((h_m, mapped_val))
                    
            # Sort models properly
            final_models_mapping = sorted(list(generated), key=lambda x: x[0])
            
        elif models_from_html and models:
            for h_m in models_from_html:
                # Extract suffix, e.g. UHP-500-12 -> 12, UHP-500R-12 -> 12
                h_suffix = h_m.rsplit('-', 1)[-1].strip()
                if is_vertical_table:
                    base_table_model = None
                    for m in models:
                        m_clean = re.sub(r'[^\w\.\-]', '', m)
                        if m_clean.rsplit('-', 1)[-1].strip() == h_suffix:
                            base_table_model = m
                            break
                    if not base_table_model and models:
                        base_table_model = models[0]
                    mapped_val = base_table_model
                else:
                    mapped_val = _find_col_idx(h_suffix, models, voltage_to_col)
                final_models_mapping.append((h_m, mapped_val))
        else:
            actual_models = models_from_html if models_from_html else models
            for i, m in enumerate(actual_models):
                if is_vertical_table:
                    mapped_val = m
                else:
                    mapped_val = i if i < len(models) else 0
                final_models_mapping.append((m, mapped_val))
        
        # Process image for the series
        base_img_path = process_image(pdf_path, image_output_dir, final_models_mapping[0][0] if final_models_mapping else "Product")
        
        for model_name, mapped_val in final_models_mapping:
            full_product_name = f"{model_name} {general_name}"
            
            html = '<table id="specifications" border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; font-family: Arial; width: 100%;">\n'
            html += '<thead><tr style="background-color: #f2f2f2;"><th>Thông số</th><th>Giá trị</th></tr></thead>\n'
            html += '<tbody>\n'
            html += f'<tr><td style="font-weight: bold;">Mã sản phẩm</td><td>{model_name}</td></tr>\n'
            html += f'<tr><td style="font-weight: bold;">Tên sản phẩm</td><td>{translate_text(full_product_name, use_cache=True)}</td></tr>\n'
            
            if is_vertical_table:
                # In vertical mode, mapped_val is the base_table_model string
                base_model = mapped_val
                if base_model in vertical_model_data:
                    for spec_name, val in vertical_model_data[base_model].items():
                        # Skip if spec_name contains "ORDER NO." or "MODEL"
                        if 'ORDER NO.' in spec_name.upper() or 'MODEL' in spec_name.upper() or 'PART NO.' in spec_name.upper():
                            continue
                        html += f'<tr><td style="font-weight: bold;">{translate_text(spec_name, use_cache=True)}</td><td>{translate_text(str(val), use_cache=True)}</td></tr>\n'
            elif main_table:
                # In horizontal mode, mapped_val is col_idx
                col_idx = mapped_val
                for row in main_table:
                    spec_name = row[0]
                    # ✅ FIX: indent đúng - toàn bộ xử lý phải nằm trong vòng lặp for
                    if not spec_name or 'MODEL' in str(spec_name).upper():
                        continue
                        
                    spec_name = str(spec_name).replace('\n', ' ').strip()
                    if not spec_name:
                        continue
                    
                    # Find the value for this model
                    val = ""
                    num_data_cols = len(row) - 1  # exclude spec name column
                    
                    if spec_name == "OVER VOLTAGE" and row[1]:
                        # Handled specifically because pdfplumber merges it into a single column
                        text = str(row[1])
                        ranges = re.findall(r'\d+(?:\.\d+)?\s*~\s*\d+(?:\.\d+)?V', text)
                        
                        protection_text = ""
                        if "Protection type" in text:
                            protection_text = text[text.find("Protection type"):].strip()
                        
                        if len(ranges) == len(models) and len(models) > 0:
                            # Tìm index của model_name trong danh sách models
                            h_suffix = model_name.rsplit('-', 1)[-1].strip()
                            model_index = 0
                            for mi, m in enumerate(models):
                                m_clean = re.sub(r'[^\w\.\-]', '', m)
                                m_suffix = m_clean.rsplit('-', 1)[-1].strip()
                                if m_suffix == h_suffix:
                                    model_index = mi
                                    break
                                    
                            try:
                                val = ranges[model_index] + "<br>" + protection_text
                            except IndexError:
                                val = text.replace('\n', '<br>').strip()
                        else:
                            val = text.replace('\n', '<br>').strip()
                    elif len(row) > 1:
                        # Determine effective col index, clamped to actual row length
                        eff_col = min(col_idx + 1, len(row) - 1)
                        
                        if num_data_cols == 1:
                            # Only 1 value provided for all models (shared spec)
                            if row[1] is not None:
                                val = str(row[1]).replace('\n', ' ').strip()
                        elif row[eff_col] is not None:
                            val = str(row[eff_col]).replace('\n', ' ').strip()
                            # Remove artefacts from merged cells (e.g. '36V 48V' leaking extra voltage)
                            # If val starts with a voltage label that doesn't match this model, skip it
                        else:
                            # Fallback: check if row[1] contains the unified value and others are None
                            if row[1] is not None and all(r is None for r in row[2:]):
                                val = str(row[1]).replace('\n', ' ').strip()
                    
                    if val:
                        html += f'<tr><td style="font-weight: bold;">{translate_text(spec_name, use_cache=True)}</td><td>{translate_text(str(val), use_cache=True)}</td></tr>\n'
                    
            if encoding_table:
                # Xác định ký tự tùy chọn (option) của mã sản phẩm hiện tại
                current_opt = ""
                if model_name.startswith(prod_id):
                    rem = model_name[len(prod_id):]
                    if '-' in rem:
                        current_opt = rem.split('-')[0].strip().lower()
                        
                header = [str(x).lower().strip() if x else "" for x in encoding_table[0]]
                type_idx = next((i for i, h in enumerate(header) if 'type' in h), 0)
                desc_idx = next((i for i, h in enumerate(header) if 'description' in h), 1)
                note_idx = next((i for i, h in enumerate(header) if 'note' in h), -1)
                
                matched_rows = []
                all_rows = []
                
                for r_idx in range(1, len(encoding_table)):
                    r = encoding_table[r_idx]
                    if len(r) > max(type_idx, desc_idx):
                        t_val = str(r[type_idx]).replace('\n', ' ').strip() if r[type_idx] else ""
                        d_val = str(r[desc_idx]).replace('\n', ' ').strip() if r[desc_idx] else ""
                        n_val = ""
                        if note_idx != -1 and len(r) > note_idx and r[note_idx]:
                            n_val = f" ({str(r[note_idx]).replace(chr(10), ' ').strip()})"
                            
                        if t_val or d_val:
                            if not t_val: t_val = "Option"
                            all_rows.append((t_val, d_val + n_val))
                            
                            t_val_lower = t_val.lower()
                            if current_opt == "" and t_val_lower == "blank":
                                matched_rows.append((t_val, d_val + n_val))
                            elif current_opt != "" and t_val_lower == current_opt:
                                matched_rows.append((t_val, d_val + n_val))
                                
                if matched_rows:
                    html += '<tr><th colspan="2" style="background-color: #e0e0e0; text-align: center;">Tùy chọn đặc biệt của mã này</th></tr>\n'
                    for t, d in matched_rows:
                        html += f'<tr><td style="font-weight: bold;">{translate_text(t, use_cache=True)}</td><td>{translate_text(d, use_cache=True)}</td></tr>\n'
                elif all_rows:
                    html += '<tr><th colspan="2" style="background-color: #e0e0e0; text-align: center;">Model Encoding / Các tùy chọn khác</th></tr>\n'
                    for t, d in all_rows:
                        html += f'<tr><td style="font-weight: bold;">{translate_text(t, use_cache=True)}</td><td>{translate_text(d, use_cache=True)}</td></tr>\n'

            html += '<tr><td style="font-weight: bold;">Copyright</td><td>Haiphongtech.vn</td></tr>\n'
            html += '</tbody>\n</table>'
            
            # Since all models share the same image from the catalog, copy or reuse the path
            img_path = base_img_path
            
            results.append({
                "Mã sản phẩm": model_name,
                "Tên sản phẩm": full_product_name,
                "Thông số kỹ thuật (HTML)": html,
                "Đường dẫn ảnh": img_path
            })
            
    return results

def scrape_meanwell_category(url, output_dir="meanwell_exports", progress_callback=None, parent_series=""):
    if progress_callback: progress_callback(10, f"Bắt đầu cào dữ liệu từ: {url}")
    print(f"\n--- Bắt đầu cào dữ liệu từ: {url} ---")
    
    # In a real scenario, we would parse the page to find the PDF link.
    # The user provided search.aspx?prod=RS-15. The PDF is at Upload/PDF/RS-15/RS-15-SPEC.PDF
    from urllib.parse import urlparse, parse_qs
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    
    prod_id = query_params.get('prod', [''])[0]
    if not prod_id:
        print("Không tìm thấy mã 'prod' trong URL.")
        return []
    
    import re
    pdf_prod_id = re.sub(r'\(.*?\)', '', prod_id).strip()
    
    # Cào HTML trước để lấy chuẩn danh sách mã sản phẩm và URL PDF chính xác nhất
    try:
        html_content = requests.get(url, timeout=60).text
        # Lấy danh sách models
        pattern = rf'{pdf_prod_id}[a-zA-Z]*-\d+(?:\.\d+)?(?![\w])'
        models_from_html = sorted(list(set(re.findall(pattern, html_content))))
        
        # Tìm chính xác link PDF trong HTML để tránh lỗi 404
        pdf_matches = re.findall(r'[\'\"](\.\./\.\./upload/pdf/[^\'\"]+\.pdf)[\'\"]', html_content, re.IGNORECASE)
        if pdf_matches:
            pdf_url = "https://www.meanwell.com" + pdf_matches[0].replace("../../", "/")
        else:
            pdf_url = f"https://www.meanwell.com/Upload/PDF/{prod_id}/{pdf_prod_id}-SPEC.PDF"
    except:
        models_from_html = []
        pdf_url = f"https://www.meanwell.com/Upload/PDF/{prod_id}/{pdf_prod_id}-SPEC.PDF"
        
    print(f"URL PDF trích xuất/dự đoán: {pdf_url}")
    
    catalog_dir = os.path.join(output_dir, "catalog")
    # Lấy tên file gốc từ URL
    pdf_filename = pdf_url.split('/')[-1]
    # Thêm pdf_prod_id vào tên file để tránh file bị ghi đè/lỗi khi chạy đa luồng
    save_path = os.path.join(catalog_dir, f"{pdf_prod_id}_{pdf_filename}")
    
    pdf_path = download_pdf(pdf_url, save_path)
    if not pdf_path:
        print("Không tải được PDF.")
        return []
        
    general_name = extract_general_name(pdf_path)
    print(f"Tên chung sản phẩm: {general_name}")
        
    image_output_dir = os.path.join(output_dir, "images")
    
    # Pass pdf_prod_id instead of prod_id because prod_id might contain (R) which messes up text matching
    results = extract_specs_from_pdf(pdf_path, general_name, image_output_dir, pdf_prod_id, models_from_html)
    
    # Add Series, Original URL and Catalog URL to results
    updated_results = []
    for r in results:
        final_series = parent_series if parent_series else pdf_prod_id
        new_r = {"Series": final_series}
        new_r.update(r)
        new_r["Link gốc"] = url
        new_r["Link catalog"] = pdf_url
        updated_results.append(new_r)
        
    return updated_results

import sys

def extract_urls_from_category(category_url):
    print(f"Đang lấy danh sách sản phẩm từ danh mục: {category_url}")
    try:
        html = requests.get(category_url, timeout=60).text
        soup = BeautifulSoup(html, 'html.parser')
        
        full_links = []
        list_items = soup.find_all('div', class_='list-item')
        
        if list_items:
            for item in list_items:
                h4 = item.find('h4')
                series_name = h4.get_text(strip=True) if h4 else ""
                
                links = item.find_all('a', href=re.compile(r'search\.aspx\?prod=', re.IGNORECASE))
                for a in links:
                    link = a['href']
                    if link.startswith('http'):
                        full_link = link
                    elif link.startswith('/'):
                        full_link = "https://www.meanwell.com" + link
                    else:
                        clean_link = re.sub(r'^(\.\./)+', '/', link)
                        if not clean_link.startswith('/'):
                            clean_link = '/' + clean_link
                        full_link = "https://www.meanwell.com" + clean_link
                    full_links.append((full_link, series_name))
        else:
            links = set(re.findall(r'href=[\'\"]([^\"\']*search\.aspx\?prod=[^\"\']+)[\'\"]', html, re.IGNORECASE))
            for link in links:
                if link.startswith('http'):
                    full_link = link
                elif link.startswith('/'):
                    full_link = "https://www.meanwell.com" + link
                else:
                    clean_link = re.sub(r'^(\.\./)+', '/', link)
                    if not clean_link.startswith('/'):
                        clean_link = '/' + clean_link
                    full_link = "https://www.meanwell.com" + clean_link
                full_links.append((full_link, ""))
                
        unique_links = {}
        for link, series in full_links:
            if link not in unique_links or not unique_links[link]:
                unique_links[link] = series
                
        print(f"Đã tìm thấy {len(unique_links)} sản phẩm/series trong danh mục.")
        return list(unique_links.items())
    except Exception as e:
        print(f"Lỗi khi lấy danh mục: {e}")
        return []

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass
        
    print("=== MEANWELL CRAWLER ===")
    
    if len(sys.argv) > 1:
        urls_input = sys.argv[1]
        urls = [u.strip() for u in urls_input.split(',')]
    else:
        urls = ["https://www.meanwell.com/webapp/product/search.aspx?prod=RS-15"]
        
    # Expand category URLs
    expanded_urls = {}
    category_urls = [url for url in urls if url and ("productSeries.aspx" in url or "search.aspx" not in url)]
    direct_urls = [url for url in urls if url and ("productSeries.aspx" not in url and "search.aspx" in url)]
    
    for url in direct_urls:
        expanded_urls[url] = ""
    
    if category_urls:
        print(f"Đang xử lý {len(category_urls)} danh mục song song...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_url = {executor.submit(extract_urls_from_category, url): url for url in category_urls}
            for future in concurrent.futures.as_completed(future_to_url):
                try:
                    res = future.result()
                    if res:
                        for link, series_name in res:
                            if link not in expanded_urls or not expanded_urls[link]:
                                expanded_urls[link] = series_name
                except Exception as e:
                    print(f"Lỗi khi lấy danh mục: {e}")
            
    print(f"Tổng cộng có {len(expanded_urls)} link series/sản phẩm cần cào.")
        
    all_data = []
    output_dir = "meanwell_exports"
    os.makedirs(output_dir, exist_ok=True)
    
    if expanded_urls:
        print("Bắt đầu cào dữ liệu song song...")
        # Sử dụng ThreadPoolExecutor để chạy song song việc cào dữ liệu
        # max_workers=5 là con số an toàn để không bị website block và tối ưu CPU khi parse PDF
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_url = {executor.submit(scrape_meanwell_category, url, output_dir, None, series_name): url for url, series_name in expanded_urls.items() if url}
            for future in concurrent.futures.as_completed(future_to_url):
                try:
                    data = future.result()
                    if data:
                        all_data.extend(data)
                except Exception as e:
                    url = future_to_url[future]
                    print(f"Lỗi khi cào dữ liệu từ {url}: {e}")
            
    if all_data:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        excel_path = os.path.join(output_dir, f"MeanWell_Data_{timestamp}.xlsx")
        
        df = pd.DataFrame(all_data)
        
        # Sắp xếp theo Series và Mã sản phẩm
        if "Series" in df.columns:
            df = df.sort_values(by=["Series", "Mã sản phẩm"])
            
        # Xóa bỏ các ký tự ẩn bất hợp lệ trong Excel để tránh lỗi "cannot be used in worksheets"
        df = df.replace(r'[\000-\010]|[\013-\014]|[\016-\037]', '', regex=True)
        
        # Lặp lại logic format cột giống Baa.vn
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Dữ liệu Sản phẩm')
            sheet = writer.sheets['Dữ liệu Sản phẩm']
            
            for idx, col in enumerate(df.columns, 1):
                import openpyxl
                column_letter = openpyxl.utils.get_column_letter(idx)
                if col in ['Link gốc', 'Link catalog', 'Đường dẫn ảnh']:
                    sheet.column_dimensions[column_letter].width = 50
                elif col == 'Thông số kỹ thuật (HTML)':
                    sheet.column_dimensions[column_letter].width = 100
                else:
                    sheet.column_dimensions[column_letter].width = 25
                    
        print(f"\n✅ Đã xuất {len(all_data)} sản phẩm ra file {excel_path}")
    else:
        print("\n❌ Không trích xuất được dữ liệu nào.")

if __name__ == "__main__":
    main()
