# ArchiveRipper  
A command-line utility for reconstructing archived websites from the Wayback Machine

## 1. Introduction  

ArchiveRipper is a tool for developers, archivists, researchers, and digital preservation enthusiasts who need to reconstruct archived web pages as they appeared at a specific point in time.

Given a direct Wayback Machine snapshot URL, ArchiveRipper:

- Downloads the HTML for the captured page  
- Recursively fetches linked assets (HTML, CSS, JS, images, fonts, media)  
- Cleans out Wayback-specific overlays, scripts, and comments  
- Rewrites internal links to use relative paths and asset paths so the page works locally  

The goal is to produce a faithful offline version of an archived page or small site that can be viewed and analyzed without depending on archive.org.

---

## 2. Requirements  

- Python 3.8 or newer  
- Python packages:
  - `requests`
  - `beautifulsoup4`
- Working internet connection  
- Sufficient disk space for the downloaded content

Installation of dependencies, for example:

```bash
pip install requests beautifulsoup4
```

---

## 3. Launch Parameters  

The script is invoked from the command line as:

```bash
python ripper.py [url] [options...]
```

### 3.1 Positional Argument  

#### `url`  
A full Wayback Machine snapshot URL pointing directly to a captured page.

**Definition:**  
The source snapshot to rip. Must be a `web.archive.org/web/...` style URL.

**Example:**  
```bash
python ripper.py https://web.archive.org/web/20130401000000/http://example.com/
```

---

### 3.2 Optional Arguments  

#### `-o`, `--output`  
**Definition:**  
Directory where the reconstructed page and its assets will be saved. If not provided, a default directory such as `output` is used.

**Example:**  
```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ -o example_site
```

---

#### `-c`, `--concurrency`  
**Definition:**  
Number of parallel asset downloads. Internally capped by the script (for example, up to 3 parallel workers) to avoid being too aggressive with requests.

**Example:**  
```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ -c 3
```

---

#### `--reset`  
**Definition:**  
Clears any existing download log (such as `.downloaded.txt`) before running, forcing all assets to be fetched again instead of skipping previously completed ones.

**Example:**  
```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ --reset
```

---

#### `-s`, `--savename`  
**Definition:**  
Allows overriding the filename of the main page that is saved. If no extension is provided, the script chooses one based on the captured page (typically `.html`).

**Example:**  
```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ -s homepage.html
```

---

## 4. Basic Use / How To Use It  

### 4.1 Minimal single-page extraction  

Use the bare minimum: just the snapshot URL.

```bash
python ripper.py https://web.archive.org/web/20050101000000/http://example.com/
```

This will:

- Download the HTML for the captured page  
- Fetch referenced assets  
- Write everything into the default output directory  

---

### 4.2 Save to a specific output directory  

Organize different snapshots into separate folders.

```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ -o example_copy
```

---

### 4.3 Rename the main page  

If you prefer a custom main file name:

```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ -s index.html
```

---

### 4.4 Use limited parallelism  

For larger pages with many assets, enabling concurrency speeds things up while remaining modest:

```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ -c 3
```

---

### 4.5 Force a full clean redownload  

If a previous run was interrupted or you suspect partial corruption:

```bash
python ripper.py https://web.archive.org/web/.../http://example.com/ --reset
```

This discards the previous asset log and re-fetches everything.

---

## 5. Different Use Cases / Ways To Use The Ripper  

### 5.1 Historical snapshot reconstruction  

Use the tool to reconstruct a page exactly as it appeared at a specific time:

- Legal or compliance records  
- Academic research into web history  
- Comparison of how a site evolved over time  
- Preserving discontinued documentation or product pages  

Example:

```bash
python ripper.py https://web.archive.org/web/20061201000000/http://oldsite.com/ -o oldsite_2006
```

---

### 5.2 Automation and batch processing  

By importing the core logic from the script into your own code, or by wrapping it with a GUI or batch runner, you can:

- Feed it a list of snapshot URLs  
- Schedule recurring pulls of important archived resources  
- Integrate archived copies into a broader data processing pipeline  

This enables automated reconstruction of multiple snapshots without manually invoking the script for each one.

---

## 6. Contribute and Gimme Money  

Contributions are welcome:

- Bug reports  
- Performance improvements  
- Smarter handling of edge cases in CSS or JS rewriting  
- Support for additional content types or archive patterns  

If this tool helps you retrieve critical historical data, rebuild an ancient site, or just saves you hours of manual work, financial support is appreciated.  
You can contribute by:

- Sharing patches or improvements  
- Sponsoring continued development  
- Providing funding for maintenance, new features, and testing infrastructure  

In short: if this thing keeps you from losing your mind over broken archived pages, consider sending something back so it can keep evolving.

