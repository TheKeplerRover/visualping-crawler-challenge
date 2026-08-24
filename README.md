# Visualping crawler challenge

Small crawler for the Visualping take-home challenge.

## Run

```bash
export VP_BASE_URL="http://challenge-host/"
export VP_USERNAME="your-username"
export VP_PASSWORD="your-password"
python3 crawl_visualping.py
```

Optional limits:

```bash
MAX_URLS=450 MAX_REPORT_PAGES=500 WORKERS=20 python3 crawl_visualping.py
```

## Approach

The crawler treats the site as a same-origin resource graph.

1. Start from the homepage.
2. Fetch every discovered same-origin resource with HTTP Basic Auth.
3. Scan response bodies, not response headers.
4. Extract more same-origin URLs from HTML tags, meta refresh, CSS `url(...)`, and string-like URLs in JS/text resources.
5. Repeat until the queue is empty, or until an optional local limit is reached.

It also handles the non-obvious storage patterns found during the crawl:

- direct `VISUALPING{...}` text in HTML/JS;
- HTML comments and attributes;
- simple JS character-code arrays;
- JPEG comment segments containing sixteen hex characters.

The image-text password was confirmed separately by visual inspection of the downloaded image resource.

## Notes

Credentials, the challenge URL, and the final passwords are intentionally not committed.
