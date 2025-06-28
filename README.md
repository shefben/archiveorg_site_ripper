# archiveorg_site_ripper

A small Python tool for ripping individual pages from the Internet Archive and recreating their original structure locally.

Features:

- Downloads the snapshot page and all referenced assets
- Strips the Wayback toolbar and injected scripts
- Removes archive.org comments from HTML, CSS and JavaScript
- Rewrites asset paths to relative locations and cleans links back to the original URLs
- Stores the main page with `.html` appended

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

The tool caches the snapshot index (`snapshot.cdx.json`) and records completed
downloads in `.downloaded.txt`. Re-running the same command resumes where it
left off. Use `--reset` to clear the cache and start fresh.
