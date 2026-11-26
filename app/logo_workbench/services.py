import os
import uuid
from typing import List, Dict, Tuple
from PIL import Image


BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'downloads', 'image_processor')
SHEETS_DIR = os.path.join(BASE_DIR, 'sheets')
CROPS_DIR = os.path.join(BASE_DIR, 'crops')
PREVIEWS_DIR = os.path.join(BASE_DIR, 'previews')
PRESETS_DIR = os.path.join(BASE_DIR, 'presets')
BATCH_DIR = os.path.join(BASE_DIR, 'batch')

for d in [SHEETS_DIR, CROPS_DIR, PREVIEWS_DIR, PRESETS_DIR, BATCH_DIR]:
    os.makedirs(d, exist_ok=True)


def new_session_id() -> str:
    return uuid.uuid4().hex


def save_sheet(file_storage) -> Dict:
    session_id = new_session_id()
    ext = os.path.splitext(file_storage.filename)[1].lower() or '.png'
    sheet_path = os.path.join(SHEETS_DIR, f'{session_id}{ext}')
    file_storage.save(sheet_path)
    with Image.open(sheet_path) as im:
        width, height = im.size
    return {
        'session_id': session_id,
        'sheet_path': sheet_path,
        'width': width,
        'height': height,
        'sheet_url': f'/static_proxy?path={sheet_path}'
    }


def _remove_white_bg(img: Image.Image, threshold: int = 240) -> Image.Image:
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    datas = img.getdata()
    new = []
    for r, g, b, a in datas:
        if r > threshold and g > threshold and b > threshold:
            new.append((255, 255, 255, 0))
        else:
            new.append((r, g, b, a))
    img.putdata(new)
    return img


def crop_regions(session_id: str, regions: List[Dict], remove_white_bg: bool = True) -> Dict:
    sheet_path = _get_sheet_path(session_id)
    if not os.path.exists(sheet_path):
        raise FileNotFoundError('sheet not found')
    out_dir = os.path.join(CROPS_DIR, session_id)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    with Image.open(sheet_path) as sheet:
        for i, reg in enumerate(regions):
            # Clamp and validate region
            x = int(round(reg.get('x', 0)))
            y = int(round(reg.get('y', 0)))
            w = int(round(reg.get('w', 0)))
            h = int(round(reg.get('h', 0)))
            if w <= 0 or h <= 0:
                continue
            left = max(0, min(x, sheet.width - 1))
            top = max(0, min(y, sheet.height - 1))
            right = min(sheet.width, max(left + 1, x + w))
            bottom = min(sheet.height, max(top + 1, y + h))
            if right <= left or bottom <= top:
                continue
            box = (left, top, right, bottom)
            crop = sheet.crop(box)
            if remove_white_bg:
                crop = _remove_white_bg(crop)
            out_path = os.path.join(out_dir, f'{i}.png')
            crop.save(out_path)
            results.append({'id': str(i), 'url': f'/static_proxy?path={out_path}', 'w': crop.width, 'h': crop.height})
    return {'logos': results}


def _get_sheet_path(session_id: str) -> str:
    for ext in ['.png', '.jpg', '.jpeg', '.webp']:
        p = os.path.join(SHEETS_DIR, f'{session_id}{ext}')
        if os.path.exists(p):
            return p
    return os.path.join(SHEETS_DIR, f'{session_id}.png')


def compose_preview(product_image, session_id: str, layers: List[Dict]) -> str:
    # Lưu ảnh sản phẩm tạm thời
    os.makedirs(PREVIEWS_DIR, exist_ok=True)
    temp_path = os.path.join(PREVIEWS_DIR, f'{uuid.uuid4().hex}.png')
    product_image.save(temp_path)
    with Image.open(temp_path) as base:
        base = base.convert('RGBA')
        for layer in sorted(layers, key=lambda x: x.get('z_index', 0)):
            logo_id = layer['logo_id']
            logo_path = os.path.join(CROPS_DIR, session_id, f'{logo_id}.png')
            if not os.path.exists(logo_path):
                continue
            with Image.open(logo_path) as lg:
                lg = lg.convert('RGBA')
                # không scale theo yêu cầu
                lw, lh = lg.size
                x, y = _calc_position(base.size, (lw, lh), layer)
                alpha = layer.get('opacity', 1.0)
                if 0 < alpha < 1:
                    a = lg.split()[-1].point(lambda v: int(v * alpha))
                    lg.putalpha(a)
                canvas = Image.new('RGBA', base.size, (0, 0, 0, 0))
                canvas.paste(lg, (x, y), mask=lg)
                base = Image.alpha_composite(base, canvas)
        out_path = os.path.join(PREVIEWS_DIR, f'preview_{uuid.uuid4().hex}.webp')
        base.save(out_path, 'WEBP', quality=95)
        return out_path


def _calc_position(base_size: Tuple[int, int], logo_size: Tuple[int, int], layer: Dict) -> Tuple[int, int]:
    bw, bh = base_size
    lw, lh = logo_size
    margin = layer.get('margin', 0.02)
    mx = int(bw * margin)
    my = int(bh * margin)
    pos = layer.get('position', 'top_right')
    if pos == 'top_left':
        x, y = mx, my
    elif pos == 'bottom_left':
        x, y = mx, bh - lh - my
    elif pos == 'bottom_right':
        x, y = bw - lw - mx, bh - lh - my
    elif pos == 'custom':
        x, y = int(layer.get('x', 0)), int(layer.get('y', 0))
    else:  # top_right
        x, y = bw - lw - mx, my
    x += int(layer.get('offset_x', 0))
    y += int(layer.get('offset_y', 0))
    x = max(0, min(bw - lw, x))
    y = max(0, min(bh - lh, y))
    return x, y


def compose_batch(products_paths: List[str], session_id: str, layers: List[Dict]) -> Dict:
    ts_dir = os.path.join(BATCH_DIR, uuid.uuid4().hex)
    os.makedirs(ts_dir, exist_ok=True)
    total = 0
    success = 0
    failed = []
    for p in products_paths:
        total += 1
        try:
            stem = os.path.splitext(os.path.basename(p))[0]
            out_path = os.path.join(ts_dir, f'{stem}_with_logo.webp')
            with Image.open(p) as base:
                base = base.convert('RGBA')
                for layer in sorted(layers, key=lambda x: x.get('z_index', 0)):
                    logo_id = layer['logo_id']
                    logo_path = os.path.join(CROPS_DIR, session_id, f'{logo_id}.png')
                    if not os.path.exists(logo_path):
                        continue
                    with Image.open(logo_path) as lg:
                        lg = lg.convert('RGBA')
                        lw, lh = lg.size
                        x, y = _calc_position(base.size, (lw, lh), layer)
                        alpha = layer.get('opacity', 1.0)
                        if 0 < alpha < 1:
                            a = lg.split()[-1].point(lambda v: int(v * alpha))
                            lg.putalpha(a)
                        canvas = Image.new('RGBA', base.size, (0, 0, 0, 0))
                        canvas.paste(lg, (x, y), mask=lg)
                        base = Image.alpha_composite(base, canvas)
                base.save(out_path, 'WEBP', quality=95)
                success += 1
        except Exception:
            failed.append(p)
    return {'total': total, 'success': success, 'failed': failed, 'output_dir': ts_dir}



