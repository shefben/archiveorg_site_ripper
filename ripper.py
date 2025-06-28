import argparse
import os
import re
import json
import time
import threading
import requests
import concurrent.futures
from bs4 import BeautifulSoup, Comment
from urllib.parse import urlparse, urljoin
import hashlib


def log(msg: str):
    """Print a message to stdout immediately."""
    print(msg, flush=True)


ARCHIVE_PREFIX = 'https://web.archive.org/web/'

# Connection and retry tuning
CONNECTION_POOL_SIZE = 5
MAX_RETRIES = 3
RETRY_DELAY = 2
RATE_LIMIT = 1

# Configure shared HTTP session with a connection pool
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=CONNECTION_POOL_SIZE,
    pool_maxsize=CONNECTION_POOL_SIZE,
)
session.mount('http://', adapter)
session.mount('https://', adapter)
session.headers.update({'User-Agent': 'ArchiveRipper/1.0'})

# File types that are likely textual and can be cleaned of Wayback comments
TEXT_EXTS = {'.css', '.js', '.html', '.htm', '.svg', '.json', '.xml', '.txt'}

# Regex patterns to extract asset URLs from CSS and JavaScript files
CSS_URL_RE = re.compile(r"url\(([^)]+)\)")
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\()?['\"]([^'\"]+)['\"]\)?")
# Common asset extensions that might be referenced from JavaScript
JS_URL_RE = re.compile(
    r"['\"]([^'\"]+\.(?:css|js|png|jpe?g|gif|svg|webp|mp4|mp3|webm|woff2?|woff|ttf|eot|otf|json|xml))['\"]"
)

# Tags that contain asset URLs we want to download locally
SRC_ASSET_TAGS = {
    'img', 'script', 'iframe', 'embed', 'source', 'audio',
    'video', 'track', 'object'
}
HREF_ASSET_TAGS = {'link'}


def parse_archive_url(url: str):
    match = re.match(r'^https?://web\.archive\.org/web/(\d+)[^/]*/(https?://.*)$', url)
    if not match:
        raise ValueError('URL is not a direct archive.org snapshot')
    timestamp, original = match.groups()
    return timestamp, original


def strip_archive_comments(text: str) -> str:
    pattern_js = re.compile(r'/\*.*?(?:wayback|archive).*?\*/\s*$', re.DOTALL | re.IGNORECASE)
    pattern_html = re.compile(r'<!--.*?(?:wayback|archive).*?-->\s*$', re.DOTALL | re.IGNORECASE)
    text = pattern_js.sub('', text)
    text = pattern_html.sub('', text)
    return text


def save_file(content: bytes, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(content)
    log(f"Saved {path}")


def fetch_url(url: str) -> bytes:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"Downloading {url}")
            resp = session.get(url)
            resp.raise_for_status()
            time.sleep(RATE_LIMIT)
            return resp.content
        except Exception:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_DELAY)


def compute_local_path(output_dir: str, original_url: str, add_ext: bool = False) -> str:
    parsed = urlparse(original_url)
    path = parsed.path
    if path.endswith('/'):
        path += 'index.html'
    local = os.path.join(output_dir, parsed.netloc, path.lstrip('/'))
    if parsed.query:
        h = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        base, ext = os.path.splitext(local)
        local = base + '_' + h + ext
    if add_ext:
        local = local + '.html'
    return local




def load_downloaded(output_dir: str):
    path = os.path.join(output_dir, '.downloaded.txt')
    if not os.path.exists(path):
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())


def mark_downloaded(output_dir: str, url: str, lock: threading.Lock, downloaded: set):
    path = os.path.join(output_dir, '.downloaded.txt')
    with lock:
        if url in downloaded:
            return
        downloaded.add(url)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(url + '\n')


def make_archive_url(timestamp: str, original_url: str) -> str:
    return f"{ARCHIVE_PREFIX}{timestamp}/{original_url}"


def rewrite_css(
    text: str,
    base_url: str,
    asset_dir: str,
    output_dir: str,
    timestamp: str,
    downloaded: set,
    lock: threading.Lock,
) -> str:
    def repl_url(match):
        url = match.group(1).strip().strip("'\"")
        if url.startswith('data:'):
            return match.group(0)
        abs_url = urljoin(base_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                return match.group(0)
        rel = process_asset(
            abs_url,
            asset_dir,
            output_dir,
            timestamp,
            downloaded,
            lock,
        )
        return f"url('{rel}')"

    def repl_import(match):
        url = match.group(1).strip().strip("'\"")
        if url.startswith('data:'):
            return match.group(0)
        abs_url = urljoin(base_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                return match.group(0)
        rel = process_asset(
            abs_url,
            asset_dir,
            output_dir,
            timestamp,
            downloaded,
            lock,
        )
        return f"@import url('{rel}')"

    text = CSS_URL_RE.sub(repl_url, text)
    text = CSS_IMPORT_RE.sub(repl_import, text)
    return text


def rewrite_js(
    text: str,
    base_url: str,
    asset_dir: str,
    output_dir: str,
    timestamp: str,
    downloaded: set,
    lock: threading.Lock,
) -> str:
    def repl(match):
        url = match.group(1)
        if url.startswith('data:'):
            return match.group(0)
        abs_url = urljoin(base_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                return match.group(0)
        rel = process_asset(
            abs_url,
            asset_dir,
            output_dir,
            timestamp,
            downloaded,
            lock,
        )
        quote = match.group(0)[0]
        return f"{quote}{rel}{quote}"

    return JS_URL_RE.sub(repl, text)


def process_asset(
    asset_url: str,
    page_dir: str,
    output_dir: str,
    timestamp: str,
    downloaded: set,
    lock: threading.Lock,
) -> str:
    original = asset_url
    local_path = compute_local_path(output_dir, original)
    if original in downloaded or os.path.exists(local_path):
        return os.path.relpath(local_path, page_dir)

    log(f"Fetching asset {asset_url}")

    archive_url = make_archive_url(timestamp, original)
    try:
        data = fetch_url(archive_url)
        ext = os.path.splitext(urlparse(original).path)[1].lower()
        if ext in TEXT_EXTS:
            text = data.decode('utf-8', 'ignore')
            text = strip_archive_comments(text)
            asset_dir = os.path.dirname(local_path)
            if ext == '.css':
                text = rewrite_css(
                    text,
                    original,
                    asset_dir,
                    output_dir,
                    timestamp,
                    downloaded,
                    lock,
                )
            elif ext == '.js':
                text = rewrite_js(
                    text,
                    original,
                    asset_dir,
                    output_dir,
                    timestamp,
                    downloaded,
                    lock,
                )
            data = text.encode('utf-8')
        save_file(data, local_path)
        mark_downloaded(output_dir, original, lock, downloaded)
    except Exception:
        pass
    return os.path.relpath(local_path, page_dir)


def process_html(
    html: str,
    original_url: str,
    timestamp: str,
    output_dir: str,
    concurrency: int,
    downloaded: set,
    lock: threading.Lock,
) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    # remove wayback toolbar
    wb = soup.find(id='wm-ipp')
    if wb:
        wb.decompose()
    for s in soup.find_all('script'):
        src = s.get('src', '')
        text = s.string or ''
        full_src = urljoin(original_url, src)
        try:
            if 'web.archive.org' in full_src:
                _, full_src = parse_archive_url(full_src)
        except ValueError:
            pass
        if urlparse(full_src).netloc == 'web-static.archive.org':
            s.decompose()
            continue
        if (
            'archive.org' in src
            or 'wayback' in src
            or 'wayback' in text.lower()
            or 'archive' in text.lower()
        ):
            s.decompose()
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if 'archive' in c.lower() or 'wayback' in c.lower():
            c.extract()

    page_local = compute_local_path(output_dir, original_url, add_ext=True)
    page_dir = os.path.dirname(page_local)

    def prepare_asset(tag, attr, collection):
        url = tag.get(attr)
        if not url or url.startswith('data:'):
            return
        abs_url = urljoin(original_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                tag.decompose()
                return
        if abs_url.startswith('http'):
            collection.append((tag, attr, abs_url))

    def rewrite_link(tag, attr):
        url = tag.get(attr)
        if not url or url.startswith('data:'):
            return
        abs_url = urljoin(original_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                tag[attr] = abs_url
                return
        tag[attr] = abs_url

    assets = []
    for t in soup.find_all(src=True):
        if t.name in SRC_ASSET_TAGS:
            prepare_asset(t, 'src', assets)
    for t in soup.find_all(href=True):
        if t.name in HREF_ASSET_TAGS:
            prepare_asset(t, 'href', assets)
        elif t.name == 'a':
            rewrite_link(t, 'href')

    if assets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
            mapping = {
                ex.submit(
                    process_asset,
                    url,
                    page_dir,
                    output_dir,
                    timestamp,
                    downloaded,
                    lock,
                ): (tag, attr)
                for tag, attr, url in assets
            }
            for fut in concurrent.futures.as_completed(mapping):
                tag, attr = mapping[fut]
                try:
                    tag[attr] = fut.result()
                except Exception:
                    tag.decompose()
    for t in soup.find_all(attrs={'srcset': True}):
        srcset = []
        for part in t['srcset'].split(','):
            url_part = part.strip().split(' ')
            abs_url = urljoin(original_url, url_part[0])
            if 'web.archive.org' in abs_url:
                try:
                    _, abs_url = parse_archive_url(abs_url)
                except ValueError:
                    t.decompose()
                    srcset = []
                    break
            rel = process_asset(
                abs_url,
                page_dir,
                output_dir,
                timestamp,
                downloaded,
                lock,
            )
            url_part[0] = rel
            srcset.append(' '.join(url_part))
        if srcset:
            t['srcset'] = ', '.join(srcset)

    html = str(soup)
    html = strip_archive_comments(html)
    save_file(html.encode('utf-8'), page_local)
    return page_local


def download_page(archive_url: str, output_dir: str, concurrency: int):
    timestamp, original_url = parse_archive_url(archive_url)
    downloaded = load_downloaded(output_dir)
    lock = threading.Lock()
    log(f"Fetching {original_url} from {archive_url}")

    html_bytes = fetch_url(archive_url)
    html = html_bytes.decode('utf-8', 'ignore')
    local_page = process_html(
        html,
        original_url,
        timestamp,
        output_dir,
        concurrency,
        downloaded,
        lock,
    )
    log(f"Page saved to {local_page}")
    return local_page


def main():
    parser = argparse.ArgumentParser(description='Archive.org site ripper')
    parser.add_argument('url', help='Direct archive.org URL')
    parser.add_argument('-o', '--output', default='output', help='Output directory')
    parser.add_argument('-c', '--concurrency', type=int, default=1, help='Number of parallel downloads')
    parser.add_argument('--reset', action='store_true', help='Clear downloaded log before running')
    args = parser.parse_args()
    if args.reset:
        path = os.path.join(args.output, '.downloaded.txt')
        if os.path.exists(path):
            os.remove(path)
    page = download_page(args.url, args.output, args.concurrency)
    log(f'Saved page to {page}')


if __name__ == '__main__':
    main()
