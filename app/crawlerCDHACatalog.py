"""
Cơ Điện Hải Âu Catalog Crawler (Async)
========================================
Cào catalog PDF từ codienhaiau.com.
Catalog được nhúng bằng plugin pdfemb-viewer → URL PDF nằm trong thuộc tính
data-src hoặc thẻ script embed của plugin.

Kiến trúc luồng:
  Category URL
    ↓ thu thập link sản phẩm
  Lưu SQLite: product_url, status = pending
    ↓
  Worker async đọc pending URLs
    ↓  tải HTML sản phẩm
  Parse dữ liệu (tên, mã, catalog PDF URL)
    ↓  tải PDF song song có giới hạn
  Lưu JSONL / DB
    ↓
  Upload lên Google Drive
    ↓
  Update status = done
"""

import asyncio
import aiohttp
import aiofiles
import sqlite3
import json
from dotenv import load_dotenv
load_dotenv()  # Đọc .flaskenv / .env
import os
import re
import time
import logging
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional, List, Dict
from bs4 import BeautifulSoup
from app import socketio

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Cấu hình Google Drive (đọc từ .flaskenv / .env)
# ─────────────────────────────────────────────
import os as _os
GDRIVE_FOLDER_ID = _os.environ.get('GDRIVE_FOLDER_ID', '1y-0_7EkVn9urUk09YADtQuOQppwAXd7V')
GDRIVE_CREDENTIALS_PATH = _os.environ.get('GDRIVE_CREDENTIALS_PATH', 'app/credentials.json')

def _get_gdrive_service():
    """
    Trả về Google Drive service object.
    Ưu tiên:
      1. token.json  — OAuth user credentials (hoạt động với My Drive)
      2. credentials.json — Service Account (chỉ dùng được với Shared Drive)
    Nếu token.json chưa tồn tại, tự động mở trình duyệt để xác thực lần đầu.
    """
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = ['https://www.googleapis.com/auth/drive']
    base_dir = Path(__file__).parent.parent
    token_file  = base_dir / 'token.json'

    # Tìm file credentials theo thứ tự ưu tiên
    candidate_paths = [
        Path(GDRIVE_CREDENTIALS_PATH),
        base_dir / 'app' / 'credentials.json',
        base_dir / 'credentials.json',
    ]
    creds_file = None
    for p in candidate_paths:
        if p.exists():
            creds_file = p
            break

    if creds_file is None:
        logger.warning("[GDRIVE] Khong tim thay file credentials – bo qua upload.")
        return None

    creds = None

    # Load token đã lưu nếu có
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    # Refresh hoặc chạy OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("[GDRIVE] Token da duoc refresh thanh cong")
            except Exception as e:
                logger.warning(f"[GDRIVE] Refresh that bai: {e} – chay lai OAuth flow")
                creds = None

        if not creds:
            # Kiểm tra loại credentials
            with open(str(creds_file), 'r') as f:
                creds_data = json.load(f)

            creds_type = creds_data.get('type', '')

            if creds_type == 'service_account':
                # Service Account — chỉ hoạt động với Shared Drive
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(
                    str(creds_file), scopes=SCOPES)
                logger.info("[GDRIVE] Dung Service Account (chi ho tro Shared Drive)")
            elif 'installed' in creds_data or 'web' in creds_data:
                # OAuth Client ID — mở trình duyệt để xác thực
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
                    creds = flow.run_local_server(port=0)
                    # Lưu token để dùng lần sau
                    with open(str(token_file), 'w') as f:
                        f.write(creds.to_json())
                    logger.info(f"[GDRIVE] OAuth thanh cong, token luu tai: {token_file}")
                except Exception as e:
                    logger.error(f"[GDRIVE] OAuth flow that bai: {e}")
                    return None
            else:
                logger.warning(f"[GDRIVE] Khong nhan biet loai credentials: {creds_type}")
                return None

        # Lưu lại token đã refresh (chỉ cho OAuth user credentials)
        if hasattr(creds, 'token') and hasattr(creds, 'refresh_token') and token_file.exists():
            with open(str(token_file), 'w') as f:
                f.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


def upload_bytes_to_gdrive(
    file_bytes: bytes,
    filename: str,
    gdrive_folder_id: str = GDRIVE_FOLDER_ID,
    mime_type: str = 'application/pdf'
) -> Optional[str]:
    """
    Upload bytes trực tiếp lên Google Drive (không cần lưu file local).
    Trả về file_id nếu thành công, None nếu thất bại.
    """
    try:
        import io
        from googleapiclient.http import MediaIoBaseUpload

        service = _get_gdrive_service()
        if service is None:
            return None

        file_metadata = {
            'name': filename,
            'parents': [gdrive_folder_id]
        }
        fh = io.BytesIO(file_bytes)
        media = MediaIoBaseUpload(fh, mimetype=mime_type, resumable=False)
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=True,
        ).execute()

        file_id = uploaded.get('id')
        logger.info(f"[GDRIVE] Upload bytes thành công: {filename} → id={file_id}")
        return file_id

    except ImportError:
        logger.warning("[GDRIVE] Thiếu thư viện google-api-python-client.")
        return None
    except Exception as e:
        logger.error(f"[GDRIVE] Lỗi upload bytes {filename}: {e}")
        return None


def upload_to_gdrive(local_path: str, gdrive_folder_id: str = GDRIVE_FOLDER_ID) -> Optional[str]:
    """
    Upload một file lên Google Drive folder, trả về file_id nếu thành công.
    Hỗ trợ cả OAuth user credentials và Service Account.
    """
    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_gdrive_service()
        if service is None:
            return None

        file_name = os.path.basename(local_path)
        mime_type = 'application/pdf' if local_path.endswith('.pdf') else 'application/octet-stream'

        file_metadata = {
            'name': file_name,
            'parents': [gdrive_folder_id]
        }
        media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=True,
        ).execute()

        file_id = uploaded.get('id')
        logger.info(f"[GDRIVE] Upload thanh cong: {file_name} → id={file_id}")
        return file_id

    except ImportError:
        logger.warning("[GDRIVE] Thieu thu vien google-api-python-client.")
        return None
    except Exception as e:
        logger.error(f"[GDRIVE] Loi upload {local_path}: {e}")
        return None



# ─────────────────────────────────────────────
#  Socket.IO helper
# ─────────────────────────────────────────────
def _emit(event: str, data: dict):
    try:
        if socketio and getattr(socketio, "server", None):
            socketio.emit(event, data)
        else:
            logger.info(f"[SOCKETIO_DISABLED] {event}: {data}")
    except Exception as e:
        logger.warning(f"[SOCKETIO] Lỗi emit {event}: {e}")


# ─────────────────────────────────────────────
#  SQLite checkpoint helpers
# ─────────────────────────────────────────────
def _init_db(db_path: str):
    """Tạo bảng SQLite nếu chưa có."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalog_jobs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            product_url TEXT UNIQUE NOT NULL,
            status    TEXT NOT NULL DEFAULT 'pending',
            name      TEXT,
            code      TEXT,
            catalog_url TEXT,
            local_pdf TEXT,
            gdrive_id TEXT,
            error     TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _insert_pending(db_path: str, urls: List[str]):
    """Thêm các URL chưa có vào DB với status = pending."""
    conn = sqlite3.connect(db_path)
    inserted = 0
    for url in urls:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_jobs (product_url, status) VALUES (?, 'pending')",
                (url,)
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return inserted


def _get_pending(db_path: str, limit: int = 500) -> List[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT product_url FROM catalog_jobs WHERE status = 'pending' LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _update_job(db_path: str, product_url: str, **kwargs):
    kwargs['updated_at'] = datetime.now().isoformat()
    sets = ', '.join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [product_url]
    conn = sqlite3.connect(db_path)
    conn.execute(f"UPDATE catalog_jobs SET {sets} WHERE product_url = ?", vals)
    conn.commit()
    conn.close()


def _count_by_status(db_path: str) -> Dict[str, int]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM catalog_jobs GROUP BY status"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ─────────────────────────────────────────────
#  HTTP helpers
# ─────────────────────────────────────────────
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
}


async def _fetch(session: aiohttp.ClientSession, url: str, timeout: int = 30) -> Optional[str]:
    """Fetch HTML với retry."""
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.text(errors='replace')
                elif resp.status in (429, 503):
                    await asyncio.sleep(5 * (attempt + 1))
        except asyncio.TimeoutError:
            await asyncio.sleep(2 * (attempt + 1))
        except Exception as e:
            logger.debug(f"[FETCH] Lỗi {url}: {e}")
            await asyncio.sleep(1)
    return None


async def _fetch_bytes(session: aiohttp.ClientSession, url: str, timeout: int = 120) -> Optional[bytes]:
    """Fetch binary (PDF/image) với retry."""
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.read()
                await asyncio.sleep(3 * (attempt + 1))
        except Exception as e:
            logger.debug(f"[FETCH_BYTES] Lỗi {url}: {e}")
            await asyncio.sleep(2)
    return None


# ─────────────────────────────────────────────
#  Category → product URL scraper
# ─────────────────────────────────────────────
async def _get_product_urls_from_category(
    session: aiohttp.ClientSession,
    category_url: str
) -> List[str]:
    """Thu thập tất cả URL sản phẩm từ một trang danh mục (có phân trang)."""
    product_urls: List[str] = []
    base_url = category_url.rstrip('/')
    page = 1

    while True:
        page_url = base_url if page == 1 else f"{base_url}/page/{page}/"
        html = await _fetch(session, page_url)
        if not html:
            break

        soup = BeautifulSoup(html, 'html.parser')

        # Lấy link sản phẩm
        links = soup.select('a[href*="/product/"]')
        new_links = []
        for a in links:
            href = a.get('href', '')
            if not href:
                continue
            if href.startswith('/'):
                href = urljoin(page_url, href)
            # Loại bỏ link danh mục, chỉ giữ sản phẩm
            parsed = urlparse(href)
            if '/product/' in parsed.path and href not in product_urls:
                new_links.append(href)

        if not new_links:
            break

        product_urls.extend(new_links)
        logger.info(f"[CAT] Trang {page}: +{len(new_links)} sản phẩm")

        # Kiểm tra có trang tiếp theo không
        next_btn = soup.select_one('a.next.page-numbers, a[class*="next"]')
        if not next_btn:
            break
        page += 1
        await asyncio.sleep(0.5)

    # Deduplicate
    seen = set()
    result = []
    for u in product_urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


# ─────────────────────────────────────────────
#  Parse catalog PDF URL từ trang sản phẩm
# ─────────────────────────────────────────────
def _extract_catalog_url(soup: BeautifulSoup, product_url: str) -> Optional[str]:
    """
    Trích xuất URL file PDF catalog từ trang sản phẩm.
    Plugin pdfemb-viewer lưu src PDF trong:
      1. <div class="pdfemb-viewer"> → thuộc tính data-src hoặc data-url
      2. Script block chứa pdfemb hoặc pdfEmbedder với src/url PDF
      3. <a> link trực tiếp đến .pdf
    """
    # 1) Thuộc tính data-src / data-url trên div pdfemb-viewer
    viewer = soup.select_one('.pdfemb-viewer, [class*="pdfemb"]')
    if viewer:
        for attr in ('data-src', 'data-url', 'data-pdf', 'src'):
            val = viewer.get(attr, '')
            if val and val.endswith('.pdf'):
                return urljoin(product_url, val)

    # 2) Tìm trong thẻ script – plugin pdfemb thường khai báo src PDF trong JS
    for script in soup.find_all('script'):
        text = script.string or ''
        # Pattern: "src":"https://...file.pdf" hoặc url:"https://...file.pdf"
        match = re.search(
            r'["\'](?:src|url|pdfUrl|file)["\'\s]*:\s*["\']([^"\']+\.pdf[^"\']*)["\']',
            text, re.IGNORECASE
        )
        if match:
            pdf_url = match.group(1)
            return urljoin(product_url, pdf_url)

        # Fallback: tìm bất kỳ URL .pdf nào trong script
        match2 = re.search(r'https?://[^\s"\']+\.pdf', text, re.IGNORECASE)
        if match2:
            return match2.group(0)

    # 3) <a href="...pdf">
    for a in soup.select('a[href$=".pdf"], a[href*=".pdf"]'):
        href = a.get('href', '')
        if href:
            return urljoin(product_url, href)

    # 4) Tìm URL PDF trong toàn bộ HTML (fallback cuối)
    raw_html = str(soup)
    match3 = re.search(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s"\'<>]*)?', raw_html, re.IGNORECASE)
    if match3:
        return match3.group(0)

    return None


def _extract_product_meta(soup: BeautifulSoup, product_url: str) -> Dict:
    """Trích xuất tên và mã sản phẩm."""
    # Tên
    name = ''
    for sel in ['h1.product_title', 'h1.entry-title', 'h1']:
        el = soup.select_one(sel)
        if el:
            name = el.get_text(strip=True)
            break

    # Mã sản phẩm (SKU)
    code = ''
    for sel in ['span.sku_wrapper span.sku', '.sku']:
        el = soup.select_one(sel)
        if el:
            code = el.get_text(strip=True)
            break

    return {'name': name, 'code': code}


# ─────────────────────────────────────────────
#  Main async crawler class
# ─────────────────────────────────────────────
class CDHACatalogCrawler:
    """
    Async crawler cào catalog PDF từ codienhaiau.com.

    Tham số:
        output_dir      : Thư mục lưu PDF và JSONL
        max_concurrency : Số worker async đồng thời (mặc định 5)
        gdrive_folder_id: ID thư mục Google Drive để upload
    """

    def __init__(
        self,
        output_dir: str = 'output_cdha_catalog',
        max_concurrency: int = 5,
        gdrive_folder_id: str = GDRIVE_FOLDER_ID,
        upload_gdrive: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = str(self.output_dir / 'catalog_jobs.db')
        self.jsonl_path = str(self.output_dir / 'catalog_results.jsonl')
        self.max_concurrency = max_concurrency
        self.gdrive_folder_id = gdrive_folder_id
        self.upload_gdrive = upload_gdrive

        _init_db(self.db_path)

        # Stats
        self._stats = {
            'total': 0, 'done': 0, 'failed': 0,
            'pdf_found': 0, 'pdf_downloaded': 0, 'gdrive_uploaded': 0
        }

    # ── Bước 1: Thu thập URL sản phẩm từ danh mục ──────────────────────────
    async def collect_product_urls(self, category_urls: List[str]) -> int:
        """
        Duyệt qua các URL danh mục, thu thập link sản phẩm,
        lưu vào SQLite với status = pending.
        Trả về số URL mới được thêm.
        """
        all_product_urls: List[str] = []
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            for cat_url in category_urls:
                _emit('catalog_progress', {
                    'step': 'collect',
                    'message': f'Đang thu thập URL sản phẩm từ: {cat_url}',
                    'percent': 5
                })
                urls = await _get_product_urls_from_category(session, cat_url)
                logger.info(f"[COLLECT] {cat_url} → {len(urls)} sản phẩm")
                all_product_urls.extend(urls)

        inserted = _insert_pending(self.db_path, all_product_urls)
        self._stats['total'] = len(_get_pending(self.db_path, limit=999999))
        logger.info(f"[DB] Đã thêm {inserted} URL mới, tổng pending = {self._stats['total']}")
        _emit('catalog_progress', {
            'step': 'collect_done',
            'message': f'Thu thập xong {inserted} URL sản phẩm',
            'percent': 15,
            'total': self._stats['total']
        })
        return inserted

    # ── Bước 2: Worker async xử lý từng sản phẩm ────────────────────────────
    async def _process_one(
        self,
        session: aiohttp.ClientSession,
        product_url: str,
        semaphore: asyncio.Semaphore
    ):
        async with semaphore:
            try:
                html = await _fetch(session, product_url)
                if not html:
                    _update_job(self.db_path, product_url,
                                status='failed', error='Không tải được HTML')
                    self._stats['failed'] += 1
                    return

                soup = BeautifulSoup(html, 'html.parser')
                meta = _extract_product_meta(soup, product_url)
                catalog_url = _extract_catalog_url(soup, product_url)

                if not catalog_url:
                    # Không có catalog PDF → đánh dấu done nhưng không có file
                    _update_job(self.db_path, product_url,
                                status='no_catalog',
                                name=meta['name'],
                                code=meta['code'])
                    self._stats['done'] += 1
                    self._append_jsonl({
                        'product_url': product_url,
                        'name': meta['name'],
                        'code': meta['code'],
                        'catalog_url': None,
                        'local_pdf': None,
                        'gdrive_id': None,
                        'status': 'no_catalog'
                    })
                    return

                self._stats['pdf_found'] += 1
                logger.info(f"[PDF] Tìm thấy catalog: {catalog_url}")

                # ── Tải PDF ──────────────────────────────────────────────
                local_pdf = None
                gdrive_id = None

                pdf_bytes = await _fetch_bytes(session, catalog_url)
                if pdf_bytes:
                    # Đặt tên file theo mã sản phẩm
                    safe_code = re.sub(r'[^\w\-_]', '_', meta['code'] or 'catalog')
                    safe_code = safe_code.strip('_') or 'catalog'
                    pdf_name = f"{safe_code}.pdf"
                    pdf_dir = self.output_dir / 'pdfs'
                    pdf_dir.mkdir(exist_ok=True)
                    pdf_path = pdf_dir / pdf_name

                    async with aiofiles.open(str(pdf_path), 'wb') as f:
                        await f.write(pdf_bytes)

                    local_pdf = str(pdf_path)
                    self._stats['pdf_downloaded'] += 1
                    logger.info(f"[PDF] Lưu: {pdf_path}")

                    # ── Upload Google Drive ───────────────────────────────
                    if self.upload_gdrive:
                        loop = asyncio.get_event_loop()
                        gdrive_id = await loop.run_in_executor(
                            None, upload_to_gdrive, local_pdf, self.gdrive_folder_id
                        )
                        if gdrive_id:
                            self._stats['gdrive_uploaded'] += 1

                # ── Lưu JSONL ────────────────────────────────────────────
                record = {
                    'product_url': product_url,
                    'name': meta['name'],
                    'code': meta['code'],
                    'catalog_url': catalog_url,
                    'local_pdf': local_pdf,
                    'gdrive_id': gdrive_id,
                    'gdrive_link': (
                        f"https://drive.google.com/file/d/{gdrive_id}/view"
                        if gdrive_id else None
                    ),
                    'status': 'done',
                    'crawled_at': datetime.now().isoformat()
                }
                self._append_jsonl(record)

                # ── Update DB ─────────────────────────────────────────────
                _update_job(self.db_path, product_url,
                            status='done',
                            name=meta['name'],
                            code=meta['code'],
                            catalog_url=catalog_url,
                            local_pdf=local_pdf,
                            gdrive_id=gdrive_id)
                self._stats['done'] += 1

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"[ERR] {product_url}: {e}\n{tb}")
                _update_job(self.db_path, product_url,
                            status='failed', error=str(e)[:500])
                self._stats['failed'] += 1

    def _append_jsonl(self, record: dict):
        with open(self.jsonl_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    # ── Bước 3: Chạy workers ─────────────────────────────────────────────────
    async def run_workers(self):
        """Đọc tất cả URL pending, xử lý song song có giới hạn."""
        pending = _get_pending(self.db_path, limit=999999)
        if not pending:
            logger.info("[WORKER] Không có URL pending.")
            return

        total = len(pending)
        logger.info(f"[WORKER] Bắt đầu xử lý {total} URL...")
        _emit('catalog_progress', {
            'step': 'worker_start',
            'message': f'Bắt đầu cào {total} sản phẩm',
            'percent': 20,
            'total': total
        })

        semaphore = asyncio.Semaphore(self.max_concurrency)
        processed = 0

        async with aiohttp.ClientSession(headers=HEADERS) as session:
            tasks = [
                self._process_one(session, url, semaphore)
                for url in pending
            ]

            for coro in asyncio.as_completed(tasks):
                await coro
                processed += 1

                if processed % 10 == 0 or processed == total:
                    pct = 20 + int((processed / total) * 70)
                    counts = _count_by_status(self.db_path)
                    _emit('catalog_progress', {
                        'step': 'worker_progress',
                        'message': (
                            f'Đã xử lý {processed}/{total} sản phẩm '
                            f'| PDF tìm thấy: {self._stats["pdf_found"]} '
                            f'| Tải xong: {self._stats["pdf_downloaded"]} '
                            f'| GDrive: {self._stats["gdrive_uploaded"]}'
                        ),
                        'percent': pct,
                        'done': counts.get('done', 0),
                        'failed': counts.get('failed', 0),
                        'pending': counts.get('pending', 0),
                        'pdf_found': self._stats['pdf_found'],
                        'pdf_downloaded': self._stats['pdf_downloaded'],
                        'gdrive_uploaded': self._stats['gdrive_uploaded']
                    })
                    logger.info(
                        f"[PROGRESS] {processed}/{total} | "
                        f"done={self._stats['done']} failed={self._stats['failed']} "
                        f"pdf={self._stats['pdf_downloaded']} drive={self._stats['gdrive_uploaded']}"
                    )

        logger.info(f"[WORKER] Hoàn thành. Stats: {self._stats}")

    # ── Entry point chính ─────────────────────────────────────────────────────
    async def crawl_catalog(self, category_urls: List[str]) -> Dict:
        """
        Hàm chính: thu thập URL → xử lý → trả về thống kê.
        """
        start = time.time()
        logger.info(f"[START] CDHACatalogCrawler — {len(category_urls)} danh mục")

        await self.collect_product_urls(category_urls)
        await self.run_workers()

        duration = time.time() - start
        counts = _count_by_status(self.db_path)

        summary = {
            'duration': round(duration, 2),
            'total': sum(counts.values()),
            'done': counts.get('done', 0) + counts.get('no_catalog', 0),
            'failed': counts.get('failed', 0),
            'pdf_found': self._stats['pdf_found'],
            'pdf_downloaded': self._stats['pdf_downloaded'],
            'gdrive_uploaded': self._stats['gdrive_uploaded'],
            'output_dir': str(self.output_dir),
            'jsonl_path': self.jsonl_path,
            'db_path': self.db_path,
        }

        _emit('catalog_progress', {
            'step': 'done',
            'message': (
                f'✅ Hoàn thành! {summary["done"]} sản phẩm | '
                f'{summary["pdf_downloaded"]} PDF tải xong | '
                f'{summary["gdrive_uploaded"]} lên Drive'
            ),
            'percent': 100,
            **summary
        })

        logger.info(f"[DONE] {summary}")
        return summary


# ─────────────────────────────────────────────
#  Synchronous wrapper (gọi từ Flask route)
# ─────────────────────────────────────────────
def crawl_cdha_catalog_sync(
    category_urls: List[str],
    output_dir: str = 'output_cdha_catalog',
    max_concurrency: int = 5,
    upload_gdrive: bool = True,
    gdrive_folder_id: str = GDRIVE_FOLDER_ID,
) -> Dict:
    """
    Wrapper đồng bộ để Flask route có thể gọi async crawler.
    Chạy trong thread riêng với event loop mới.
    """
    crawler = CDHACatalogCrawler(
        output_dir=output_dir,
        max_concurrency=max_concurrency,
        upload_gdrive=upload_gdrive,
        gdrive_folder_id=gdrive_folder_id,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(crawler.crawl_catalog(category_urls))
    finally:
        loop.close()
