# Visualping crawler

This is my small crawler for the Visualping take-home challenge.

It starts from the given home page, follows same-site pages and resources, and
looks through the response bodies for challenge passwords. It also checks a few
less obvious places, like HTML comments, element attributes, simple JS character
arrays, JPEG comments, and image text when optional OCR is installed.

## How to run

```bash
export VP_BASE_URL="http://challenge-host/"
export VP_USERNAME="your-username"
export VP_PASSWORD="your-password"
python3 crawl_visualping.py
```

Optional OCR support:

```bash
pip install -r requirements-ocr.txt
# macOS: brew install tesseract
# Ubuntu/Debian: sudo apt-get install tesseract-ocr
```

You can tune the crawl if needed:

```bash
MAX_URLS=450 MAX_REPORT_PAGES=500 WORKERS=20 python3 crawl_visualping.py
```

## What it does

- sends Basic Auth on every request;
- follows same-origin links and referenced resources;
- scans response bodies, not response headers;
- pulls URLs from HTML, CSS, and simple JS/text strings;
- uses optional OCR for passwords rendered inside images;
- keeps a visited set so the crawl stops when no new resources are found.

## Notes

The real challenge URL, credentials, and final answers are not committed.
