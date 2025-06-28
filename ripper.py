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
from typing import Optional


def log(msg: str):
    """Print a message to stdout immediately."""
    print(msg, flush=True)


ARCHIVE_PREFIX = 'https://web.archive.org/web/'

# Connection and retry tuning
CONNECTION_POOL_SIZE = 3
MAX_RETRIES = 3
RETRY_DELAY = 2
RATE_LIMIT = 1
MAX_CONCURRENCY = 3

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
    match = re.match(r'^https?://web\.archive\.org/web/(\d+)[^/]*/(.*)$', url)
    if not match:
        raise ValueError('URL is not a direct archive.org snapshot')
    timestamp, rest = match.groups()
    idx = rest.rfind('http')
    if idx == -1:
        raise ValueError('URL is not a direct archive.org snapshot')
    original = rest[idx:]
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


def make_archive_url(timestamp: str, original_url: str, raw: bool = False) -> str:
    """Construct a Wayback Machine URL for the given timestamp and resource."""
    if raw:
        return f"{ARCHIVE_PREFIX}{timestamp}id_/{original_url}"
    return f"{ARCHIVE_PREFIX}{timestamp}/{original_url}"


def find_nearest_snapshot(original_url: str, timestamp: str) -> Optional[str]:
    """Query the CDX API for the closest snapshot of the given URL."""
    cdx = (
        "https://web.archive.org/cdx/search/cdx?"\
        f"url={original_url}&output=json&limit=1"\
        f"&closest={timestamp}&filter=statuscode:200&fl=timestamp"
    )
    try:
        resp = session.get(cdx)
        resp.raise_for_status()
        data = resp.json()
        if len(data) > 1 and len(data[1]) > 0:
            return str(data[1][0])
    except Exception as e:
        log(f"CDX lookup failed for {original_url}: {e}")
    return None


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
        if url.startswith('/web/'):
            archive_base = make_archive_url(timestamp, base_url)
            abs_url = urljoin(archive_base, url)
        else:
            abs_url = urljoin(base_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                return match.group(0)
        if urlparse(abs_url).netloc == 'web-static.archive.org':
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
        if url.startswith('/web/'):
            archive_base = make_archive_url(timestamp, base_url)
            abs_url = urljoin(archive_base, url)
        else:
            abs_url = urljoin(base_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                return match.group(0)
        if urlparse(abs_url).netloc == 'web-static.archive.org':
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
        if url.startswith('/web/'):
            archive_base = make_archive_url(timestamp, base_url)
            abs_url = urljoin(archive_base, url)
        else:
            abs_url = urljoin(base_url, url)
        if 'web.archive.org' in abs_url:
            try:
                _, abs_url = parse_archive_url(abs_url)
            except ValueError:
                return match.group(0)
        if urlparse(abs_url).netloc == 'web-static.archive.org':
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

    text = JS_URL_RE.sub(repl, text)
    text = scan_dynamic_js(
        text,
        base_url,
        asset_dir,
        output_dir,
        timestamp,
        downloaded,
        lock,
    )
    return text


def _rel_base_path(base_url: str, base_path: str, asset_dir: str, output_dir: str) -> str:
    """Return a relative base path for rewriting JS dynamic asset prefixes."""
    dummy = urljoin(base_url, base_path.lstrip('/') + 'dummy.file')
    local = compute_local_path(output_dir, dummy)
    local_dir = os.path.dirname(local)
    rel = os.path.relpath(local_dir, asset_dir)
    if not rel.endswith('/'):
        rel += '/'
    return rel


def scan_dynamic_js(
    text: str,
    base_url: str,
    asset_dir: str,
    output_dir: str,
    timestamp: str,
    downloaded: set,
    lock: threading.Lock,
) -> str:
    """Look for simple dynamic asset constructions inside JavaScript."""
    base_vars = {}

    def replace_base(match):
        name = match.group(1)
        path = match.group(2)
        base_vars[name] = path
        rel = _rel_base_path(base_url, path, asset_dir, output_dir)
        return f'var {name} = "{rel}";'

    text = re.sub(r"var\s+(\w+)\s*=\s*['\"]([^'\"]+)['\"]\s*;", replace_base, text)

    arrays: dict[str, list[str]] = {}
    for pat in [r"var\s+(\w+)\s*=\s*new\s+Array\(([^)]*)\)", r"var\s+(\w+)\s*=\s*\[([^\]]*)\]"]:
        for m in re.finditer(pat, text):
            name = m.group(1)
            items = re.findall(r"['\"]([^'\"]+)['\"]", m.group(2))
            arrays[name] = items

    pattern = re.compile(
        r"(\w+)\s*\+\s*(?:['\"]([^'\"]+)['\"]\s*\+\s*)?(\w+)\[[^\]]+\]\s*\+\s*['\"]([^'\"]+)['\"]"
    )

    for m in pattern.finditer(text):
        base_var, prefix, arr_var, ext = m.groups()
        if base_var not in base_vars or arr_var not in arrays:
            continue
        base_path = base_vars[base_var]
        prefix = prefix or ''
        for item in arrays[arr_var]:
            asset = base_path + prefix + item + ext
            abs_url = urljoin(base_url, asset)
            process_asset(
                abs_url,
                asset_dir,
                output_dir,
                timestamp,
                downloaded,
                lock,
            )

    return text


def process_asset(
    asset_url: str,
    page_dir: str,
    output_dir: str,
    asset_timestamp: str,
    downloaded: set,
    lock: threading.Lock,
) -> str:
    original = asset_url
    local_path = compute_local_path(output_dir, original)
    if original in downloaded or os.path.exists(local_path):
        return os.path.relpath(local_path, page_dir)

    log(f"Fetching asset {asset_url}")

    ext = os.path.splitext(urlparse(original).path)[1].lower()
    raw = ext in {'.css', '.js', '.html', '.htm'} or not ext
    archive_url = make_archive_url(asset_timestamp, original, raw=raw)
    try:
        data = fetch_url(archive_url)
    except Exception as e:
        log(f"Failed to fetch {archive_url}: {e}")
        nearest = find_nearest_snapshot(original, asset_timestamp)
        if nearest and nearest != asset_timestamp:
            log(f"Retrying {original} with snapshot {nearest}")
            archive_url = make_archive_url(nearest, original, raw=raw)
            try:
                data = fetch_url(archive_url)
            except Exception as e2:
                log(f"Failed alternate snapshot for {asset_url}: {e2}")
                return asset_url
        else:
            return asset_url

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
                asset_timestamp,
                downloaded,
                lock,
            )
        elif ext == '.js':
            text = rewrite_js(
                text,
                original,
                asset_dir,
                output_dir,
                asset_timestamp,
                downloaded,
                lock,
            )
        data = text.encode('utf-8')

    for attempt in range(1, MAX_RETRIES + 1):
        save_file(data, local_path)
        expected = hashlib.md5(data).hexdigest()
        with open(local_path, 'rb') as f:
            actual = hashlib.md5(f.read()).hexdigest()
        if expected == actual:
            break
        if attempt == MAX_RETRIES:
            log(f"Hash mismatch for {archive_url}, giving up")
            break
        log(f"Hash mismatch for {archive_url}, retrying")
        time.sleep(RETRY_DELAY)
        try:
            data = fetch_url(archive_url)
        except Exception:
            break

    mark_downloaded(output_dir, original, lock, downloaded)
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
    for l in soup.find_all('link', href=True):
        href = l['href']
        abs_href = urljoin(original_url, href)
        try:
            if 'web.archive.org' in abs_href:
                _, abs_href = parse_archive_url(abs_href)
        except ValueError:
            continue
        if urlparse(abs_href).netloc == 'web-static.archive.org':
            l.decompose()
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
        ts = timestamp
        if 'web.archive.org' in abs_url:
            try:
                ts, abs_url = parse_archive_url(abs_url)
            except ValueError:
                tag.decompose()
                return
        if urlparse(abs_url).netloc == 'web-static.archive.org':
            tag.decompose()
            return
        if abs_url.startswith('http'):
            collection.append((tag, attr, abs_url, ts))

    def rewrite_link(tag, attr):
        val = tag.get(attr)
        if not val or val.startswith('data:'):
            return
        abs_val = urljoin(original_url, val)
        if 'web.archive.org' in abs_val:
            try:
                _, abs_val = parse_archive_url(abs_val)
            except ValueError:
                tag[attr] = abs_val
                return
        parsed_abs = urlparse(abs_val)
        parsed_base = urlparse(original_url)
        if parsed_abs.netloc == parsed_base.netloc:
            new_url = parsed_abs.path.lstrip('/')
            if parsed_abs.query:
                new_url += '?' + parsed_abs.query
            if parsed_abs.fragment:
                new_url += '#' + parsed_abs.fragment
            tag[attr] = new_url
        else:
            tag[attr] = abs_val

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
                    ts,
                    downloaded,
                    lock,
                ): (tag, attr)
                for tag, attr, url, ts in assets
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
            ts = timestamp
            if 'web.archive.org' in abs_url:
                try:
                    ts, abs_url = parse_archive_url(abs_url)
                except ValueError:
                    t.decompose()
                    srcset = []
                    break
            if urlparse(abs_url).netloc == 'web-static.archive.org':
                t.decompose()
                srcset = []
                break
            rel = process_asset(
                abs_url,
                page_dir,
                output_dir,
                ts,
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

    html_url = make_archive_url(timestamp, original_url, raw=True)
    html_bytes = fetch_url(html_url)
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
    parser.add_argument('-c', '--concurrency', type=int, default=1, help='Number of parallel downloads (max 3)')
    parser.add_argument('--reset', action='store_true', help='Clear downloaded log before running')
    args = parser.parse_args()
    if args.reset:
        path = os.path.join(args.output, '.downloaded.txt')
        if os.path.exists(path):
            os.remove(path)
    conc = min(args.concurrency, MAX_CONCURRENCY)
    page = download_page(args.url, args.output, conc)
    log(f'Saved page to {page}')


if __name__ == '__main__':
    main()
