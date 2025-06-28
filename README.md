# archiveorg_site_ripper

A small Python tool for ripping individual pages from the Internet Archive and recreating their original structure locally.

Features:

- Downloads the snapshot page and every asset referenced from the HTML, CSS and JavaScript
- Strips the Wayback toolbar and injected scripts
- Removes archive.org comments from HTML, CSS and JavaScript
- Rewrites asset and link paths to remove archive.org prefixes using relative locations when possible
- Removes `<script>` and `<link>` tags that load files from `web-static.archive.org`
- Stores the main page with `.html` appended
- Cleans the HTML before fetching assets so only referenced resources are saved

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

Increase `-c/--concurrency` to download assets in parallel. The default of `1`
fetches one file at a time, but higher values speed up large pages:

```bash
python ripper.py <archive url> -c 10
```

The ripper records downloaded assets in `.downloaded.txt`. Re-running the same
command skips files already fetched so you can resume an interrupted run. Use
`--reset` to clear this log and start fresh.

During execution, the script prints out each URL as it is fetched so you can
observe progress immediately.
