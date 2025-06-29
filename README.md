# archiveorg_site_ripper

A small Python tool for ripping individual pages from the Internet Archive and recreating their original structure locally.

Features:

- Downloads the snapshot page and every asset referenced from the HTML, CSS and JavaScript
- Strips the Wayback toolbar and injected scripts
- Removes archive.org comments from HTML, CSS and JavaScript
- Rewrites asset and link paths to remove archive.org prefixes and normalize them to simple relative locations when possible; each file is fetched using its own Wayback timestamp if present
- HTML, CSS and JavaScript files are downloaded using the `id_` form of the Wayback URL so the content is untouched by the archive
- Removes `<script>` and `<link>` tags that load files from `web-static.archive.org`
- Scans downloaded CSS and JavaScript for additional resources and rewrites their paths; simple dynamic JavaScript constructions are also parsed so referenced images are fetched
- Asset paths rewritten inside CSS and JavaScript are normalized to remove any leading slashes and relative path prefixes like `./` or `../`
- Handles `web` paths missing the archive domain by prepending `https://web.archive.org` before downloading
- `background` attributes are processed like other asset references
- If an asset is missing, the CDX API is queried to find the nearest snapshot
- Stores the main page with `.html` appended
- Files are saved directly within the chosen output directory rather than under a domain folder
- Cleans the HTML before fetching assets so only referenced resources are saved
- Verifies downloaded files by comparing hashes and retries on mismatch
- No more than three connections are opened at once, regardless of requested concurrency

## Requirements

- Python 3.9+
- `requests` and `beautifulsoup4`

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Usage

Run the ripper with a direct archive.org URL. The page and all its assets are downloaded to the `output/` directory by default. The downloaded page is saved with `.html` appended to the original file name.

```bash
python ripper.py https://web.archive.org/web/20210101010101/https://example.com/index.html
```

Use `-o` to select a different output directory.

```bash
python ripper.py <archive url> -o mydir
```

Increase `-c/--concurrency` to download assets in parallel. No more than three
connections are used even if you specify a higher value. The default of `1`
fetches one file at a time:

```bash
python ripper.py <archive url> -c 10
```

The ripper records downloaded assets in `.downloaded.txt`. Re-running the same
command skips files already fetched so you can resume an interrupted run. Use
`--reset` to clear this log and start fresh.

During execution, the script prints out each URL as it is fetched so you can
observe progress immediately.
