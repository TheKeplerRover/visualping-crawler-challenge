#!/usr/bin/env python3
import base64
import concurrent.futures
import io
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from html.parser import HTMLParser


DEFAULT_BASE_URL = "http://example.invalid/"
PASSWORD_RE = re.compile(rb"VISUALPING\{[0-9a-fA-F]{16}\}")
EXAMPLE_PASSWORDS = {"VISUALPING{0000deadbeef0000}"}
HEX16_RE = re.compile(rb"^[0-9a-fA-F]{16}$")
INT_ARRAY_RE = re.compile(r"\[((?:\s*\d{1,3}\s*,){10,}\s*\d{1,3}\s*)\]")

# Tags that can point to pages or resources a browser would load or navigate to.
ATTR_URLS = {
    "a": ("href",),
    "area": ("href",),
    "iframe": ("src",),
    "frame": ("src",),
    "script": ("src",),
    "link": ("href",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "form": ("action",),
}
URL_TEXT_RE = re.compile(
    r"""(?:"|')(?P<url>/[^"'<>\\\s]+|https?://[^"'<>\\\s]+)(?:"|')"""
)
CSS_URL_RE = re.compile(r"""url\(\s*['"]?(?P<url>[^'")\s]+)['"]?\s*\)""", re.I)


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        # Collect regular HTML references like links, scripts, images, iframes,
        # and forms. These are the main edges in the same-site resource graph.
        for attr in ATTR_URLS.get(tag.lower(), ()):
            value = attrs.get(attr)
            if value:
                self._add_srcset(value) if attr == "srcset" else self.urls.append(value)

        # A browser follows meta refresh redirects, so the crawler treats them
        # as discoverable URLs too.
        if tag.lower() == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            match = re.search(r"url\s*=\s*([^;]+)", attrs.get("content", ""), re.I)
            if match:
                self.urls.append(match.group(1).strip("'\" "))

        # Some challenge links are tucked into attributes or small JS snippets,
        # not only into href/src fields.
        for value in attrs.values():
            if value:
                self.urls.extend(m.group("url") for m in URL_TEXT_RE.finditer(value))

    def _add_srcset(self, value):
        for part in value.split(","):
            url = part.strip().split(" ", 1)[0]
            if url:
                self.urls.append(url)


def base_url():
    return os.environ.get("VP_BASE_URL", DEFAULT_BASE_URL)


def auth_header():
    user = os.environ.get("VP_USERNAME")
    password = os.environ.get("VP_PASSWORD")
    if not user or not password:
        sys.exit("Set VP_USERNAME and VP_PASSWORD first.")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "User-Agent": "visualping-crawler-local"}


def normalize(base, raw):
    raw = raw.strip()
    if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    url = urllib.parse.urljoin(base, raw)
    parsed = urllib.parse.urlparse(url)
    base = urllib.parse.urlparse(base_url())
    if parsed.netloc != base.netloc:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
        return None

    # Most query params on this site are tracking noise. Keeping only page=N
    # avoids crawling the same document many times as ref/utm/hl variants.
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept_query = urllib.parse.urlencode([(k, v) for k, v in query if k == "page"])
    path = re.sub(r"/index\.html$", "/", parsed.path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    parsed = parsed._replace(path=path, query=kept_query, fragment="")
    return urllib.parse.urlunparse(parsed)


def allowed_url(url, max_report_pages):
    parsed = urllib.parse.urlparse(url)
    if parsed.path == "/report" and parsed.query.startswith("page="):
        page = urllib.parse.parse_qs(parsed.query).get("page", ["1"])[0]
        try:
            # ponytail: the report feed is generated and keeps paginating; raise
            # MAX_REPORT_PAGES if a wider audit is needed.
            return int(page) <= max_report_pages
        except ValueError:
            return False
    return True


def fetch(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.getheader("content-type", ""), response.geturl(), response.read()
    except urllib.error.HTTPError as err:
        # Error pages can still contain useful body text or links.
        return err.code, err.headers.get("content-type", ""), url, err.read()
    except Exception as exc:
        print(f"ERROR {url}: {exc}")
        return None, "", url, b""


def discover(url, content_type, body):
    text = body.decode("utf-8", "ignore")
    urls = []

    # HTML gives us structured links. JS/CSS/text are handled with simple
    # patterns below so we also catch resources a plain <a> crawler would miss.
    if "html" in content_type or text.lstrip().lower().startswith("<!doctype html"):
        parser = LinkParser()
        parser.feed(text)
        urls.extend(parser.urls)
    urls.extend(m.group("url") for m in URL_TEXT_RE.finditer(text))
    urls.extend(m.group("url") for m in CSS_URL_RE.finditer(text))
    return [normalized for raw in urls if (normalized := normalize(url, raw))]


def jpeg_comments(body):
    # JPEG comments are not visible page text, but they are still part of the
    # resource body returned by the server.
    comments = []
    i = 0
    while i < len(body) - 4:
        if body[i] == 0xFF and body[i + 1] == 0xFE:
            size = int.from_bytes(body[i + 2 : i + 4], "big")
            comments.append(body[i + 4 : i + 2 + size])
            i += 2 + size
        else:
            i += 1
    return comments


def extract_passwords(body, content_type):
    candidates = {match.decode() for match in PASSWORD_RE.findall(body)}

    # Some JS stores the password as character codes instead of a literal
    # string, so decode simple numeric arrays and scan the decoded text.
    if "javascript" in content_type or "text" in content_type or "html" in content_type:
        text = body.decode("utf-8", "ignore")
        for match in INT_ARRAY_RE.finditer(text):
            values = [int(value) for value in re.findall(r"\d{1,3}", match.group(1))]
            if all(0 <= value <= 255 for value in values):
                decoded = bytes(values).decode("utf-8", "ignore").encode()
                candidates.update(item.decode() for item in PASSWORD_RE.findall(decoded))
    if "jpeg" in content_type:
        for comment in jpeg_comments(body):
            if HEX16_RE.match(comment.strip()):
                # In the images I found, the JPEG comment was just the 16 hex
                # characters, so wrap it in the required challenge format.
                candidates.add(f"VISUALPING{{{comment.decode().lower()}}}")

    if content_type.startswith("image/"):
        # Optional OCR path for passwords rendered as visible image text. This
        # keeps the main crawler dependency-free, but lets it use Tesseract when
        # Pillow + pytesseract are installed.
        candidates.update(ocr_image_passwords(body))
    return sorted(candidate for candidate in candidates if candidate not in EXAMPLE_PASSWORDS)


def ocr_image_passwords(body):
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return set()

    try:
        image = Image.open(io.BytesIO(body))
        text = pytesseract.image_to_string(
            image,
            config=(
                "--psm 6 "
                "-c tessedit_char_whitelist=VISUALPING{}0123456789abcdefABCDEF"
            ),
        )
    except Exception:
        return set()

    return {match.decode() for match in PASSWORD_RE.findall(text.encode())}


def main():
    headers = auth_header()
    stop_after = int(os.environ.get("STOP_AFTER", "0"))
    max_urls = int(os.environ.get("MAX_URLS", "0"))
    max_report_pages = int(os.environ.get("MAX_REPORT_PAGES", "500"))
    workers = int(os.environ.get("WORKERS", "20"))
    start = normalize(base_url(), "/")
    queue = deque([start])
    seen = {start}
    visited = set()
    passwords = {}

    def crawl_one(url):
        _status, content_type, final_url, body = fetch(url, headers)
        return url, extract_passwords(body, content_type), discover(final_url, content_type, body)

    while queue:
        # Process a small batch in parallel. This keeps the code simple but
        # avoids waiting on hundreds of small generated pages one by one.
        batch = []
        while queue and len(batch) < workers:
            url = queue.popleft()
            if url in visited:
                continue
            if max_urls and len(visited) >= max_urls:
                break
            visited.add(url)
            batch.append(url)
        if not batch:
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for url, found_passwords, found_urls in executor.map(crawl_one, batch):
                for password in found_passwords:
                    if password not in passwords:
                        passwords[password] = url
                        print(f"FOUND {password}  {url}", flush=True)
                for found in found_urls:
                    if not allowed_url(found, max_report_pages):
                        continue
                    if found not in seen:
                        seen.add(found)
                        queue.append(found)
        if stop_after and len(passwords) >= stop_after:
            break
        if max_urls and len(visited) >= max_urls:
            break

    print("\npasswords")
    for password, source in sorted(passwords.items()):
        print(f"{password}  {source}")
    print(f"\nfound={len(passwords)} visited={len(visited)} discovered={len(seen)}")


if __name__ == "__main__":
    main()
