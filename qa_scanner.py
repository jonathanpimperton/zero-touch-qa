"""
Zero-Touch QA Scanner
Automated website QA scanner for PetDesk veterinary clinic websites.
Crawls a WordPress/Divi site and runs checklist rules against it.
Uses PageSpeed Insights API for rendered-page validation (mobile, performance, accessibility).
"""

import copy
import os
import re
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

# Optional: Playwright for JS-rendered page support
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None

# Timeout for HTTP requests
REQUEST_TIMEOUT = 15
MAX_PAGES_TO_CRAWL = 50

# PageSpeed Insights API (free with a Google Cloud API key)
PSI_API_KEY = os.environ.get("PSI_API_KEY", "")
PLAYWRIGHT_ALWAYS = os.environ.get("PLAYWRIGHT_ALWAYS", "").strip() == "1"
USER_AGENT = "ZeroTouchQA/1.0 (PetDesk Internal QA Scanner)"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CheckResult:
    rule_id: str
    category: str
    check: str
    status: str  # "PASS", "FAIL", "WARN", "SKIP", "HUMAN_REVIEW"
    weight: int
    details: str = ""
    page_url: str = ""
    points_lost: int = 0


@dataclass
class PageData:
    url: str
    status_code: int
    html: str = ""
    soup: Optional[BeautifulSoup] = None
    title: str = ""
    links: list = field(default_factory=list)
    load_time: float = 0.0


@dataclass
class ScanReport:
    site_url: str
    partner: str
    phase: str
    scan_time: str
    scan_id: str = ""
    pages_scanned: int = 0
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    human_review: int = 0
    score: float = 100.0
    results: list = field(default_factory=list)


# =============================================================================
# SITE CRAWLER
# =============================================================================

class SiteCrawler:
    """Crawls a WordPress site and collects page data."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.domain = urllib.parse.urlparse(self.base_url).netloc
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.visited = set()
        self.pages: dict[str, PageData] = {}
        # Playwright (optional, for JS-rendered pages)
        self.playwright_available = PLAYWRIGHT_AVAILABLE
        self._playwright_instance = None
        self._browser = None
        self.js_rendered_pages: set = set()

    def _needs_js_rendering(self, page: PageData) -> bool:
        """Detect if a page likely has JS-rendered content that requests missed."""
        if PLAYWRIGHT_ALWAYS:
            return True
        if not page.soup:
            return False
        body = page.soup.find("body")
        if not body:
            return False
        visible_text = body.get_text(strip=True)
        html_size = len(page.html) if page.html else 0
        # Large HTML but very little visible body text = JS-rendered
        if html_size > 5000 and len(visible_text) < 200:
            return True
        return False

    def _ensure_browser(self):
        """Lazy-init Chromium browser. Returns browser or None on failure."""
        if self._browser is not None:
            return self._browser
        try:
            self._playwright_instance = sync_playwright().start()
            launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
            # --single-process helps in Docker containers but can crash on Windows
            if os.environ.get("DOCKER_CONTAINER"):
                launch_args.append("--single-process")
            self._browser = self._playwright_instance.chromium.launch(
                headless=True,
                args=launch_args,
            )
            return self._browser
        except Exception as e:
            print(f"  [JS] Could not launch browser: {e}")
            self.playwright_available = False
            return None

    def _cleanup_browser(self):
        """Close browser and Playwright instance."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright_instance:
            try:
                self._playwright_instance.stop()
            except Exception:
                pass
            self._playwright_instance = None

    def _fetch_with_playwright(self, url: str) -> Optional[PageData]:
        """Re-fetch a page with Playwright to get JS-rendered content."""
        browser = self._ensure_browser()
        if not browser:
            return None
        context = None
        pw_page = None
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 720},
            )
            pw_page = context.new_page()
            pw_page.goto(url, timeout=15000, wait_until="networkidle")
            rendered_html = pw_page.content()

            soup = BeautifulSoup(rendered_html, "lxml")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            links = []
            for a in soup.find_all("a", href=True):
                absolute = urllib.parse.urljoin(url, a["href"])
                links.append(absolute)

            return PageData(
                url=url, status_code=200,
                html=rendered_html, soup=soup,
                title=title, links=links, load_time=0.0,
            )
        except Exception as e:
            print(f"  [JS] Playwright error for {url}: {e}")
            return None
        finally:
            try:
                if pw_page:
                    pw_page.close()
                if context:
                    context.close()
            except Exception:
                pass

    def fetch_page(self, url: str) -> Optional[PageData]:
        """Fetch a single page and return PageData."""
        if url in self.visited:
            return self.pages.get(url)
        self.visited.add(url)

        try:
            start = time.time()
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            load_time = time.time() - start

            if "text/html" not in resp.headers.get("content-type", ""):
                return None

            soup = BeautifulSoup(resp.text, "lxml")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""

            # Collect all links on the page
            links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                absolute = urllib.parse.urljoin(url, href)
                links.append(absolute)

            page = PageData(
                url=resp.url,
                status_code=resp.status_code,
                html=resp.text,
                soup=soup,
                title=title,
                links=links,
                load_time=load_time,
            )

            # If page looks JS-heavy, re-fetch with Playwright for rendered DOM
            if self.playwright_available and page.soup and self._needs_js_rendering(page):
                print(f"  [JS] Re-rendering {url} with headless browser...")
                rendered = self._fetch_with_playwright(url)
                if rendered and rendered.soup:
                    rendered.load_time = load_time
                    rendered.status_code = page.status_code
                    page = rendered
                    self.js_rendered_pages.add(url)

            self.pages[url] = page
            return page

        except requests.RequestException as e:
            page = PageData(url=url, status_code=0, html="")
            page.details = str(e)
            self.pages[url] = page
            return page

    def crawl(self, max_pages: int = MAX_PAGES_TO_CRAWL) -> dict[str, PageData]:
        """Crawl the site starting from base_url, following internal links."""
        queue = [self.base_url]
        crawled = 0

        try:
            while queue and crawled < max_pages:
                url = queue.pop(0)

                # Only follow internal links
                parsed = urllib.parse.urlparse(url)
                if parsed.netloc and parsed.netloc != self.domain:
                    continue

                # Skip non-page resources
                if any(url.lower().endswith(ext) for ext in
                       [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".css",
                        ".js", ".zip", ".mp4", ".mp3", ".woff", ".woff2", ".ttf"]):
                    continue

                # Skip WordPress admin, feeds, etc.
                skip_patterns = ["/wp-admin", "/wp-login", "/wp-json", "/feed", "/xmlrpc",
                                 "/wp-content/", "?replytocom=", "#"]
                if any(p in url for p in skip_patterns):
                    continue

                page = self.fetch_page(url)
                if page and page.soup:
                    crawled += 1
                    print(f"  [{crawled}/{max_pages}] {page.status_code} - {url}")

                    # Add discovered internal links to queue
                    for link in page.links:
                        link_parsed = urllib.parse.urlparse(link)
                        clean_link = f"{link_parsed.scheme}://{link_parsed.netloc}{link_parsed.path}"
                        if (link_parsed.netloc == self.domain and
                                clean_link not in self.visited and
                                clean_link not in queue):
                            queue.append(clean_link)
        finally:
            self._cleanup_browser()

        if self.js_rendered_pages:
            print(f"  [JS] {len(self.js_rendered_pages)} page(s) re-rendered with headless browser")

        return self.pages


# =============================================================================
# CHECK FUNCTIONS
# =============================================================================

def check_leftover_text(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for leftover template/placeholder text across all pages."""
    results = []
    search = rule.get("search_text", "")
    found_on = []

    for url, page in pages.items():
        if page.html and search.lower() in page.html.lower():
            found_on.append(url)

    if found_on:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="FAIL",
            weight=rule["weight"],
            details=f'Found "{search}" on {len(found_on)} page(s): {", ".join(found_on[:3])}',
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details=f'No instances of "{search}" found',
        ))
    return results


def check_broken_links(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for broken links across all crawled pages."""
    results = []
    broken = []

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # Collect all unique links
    all_links = set()
    for url, page in pages.items():
        if page.soup:
            for a in page.soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                    continue
                absolute = urllib.parse.urljoin(url, href)
                all_links.add((absolute, url))

    # Deduplicate by URL, keeping the first source page found
    unique_links = {}
    for link_url, source in all_links:
        if link_url not in unique_links:
            unique_links[link_url] = source

    def check_link(link_info):
        link_url, source = link_info
        try:
            r = session.head(link_url, timeout=10, allow_redirects=True)
            if r.status_code >= 400:
                return (link_url, source, r.status_code)
        except requests.RequestException:
            return (link_url, source, 0)
        return None

    # Check links in parallel
    items = list(unique_links.items())[:100]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_link, li): li for li in items}
        for future in as_completed(futures):
            result = future.result()
            if result:
                broken.append(result)

    # Separate truly broken (404, 500, timeout) from bot-blocked (403)
    truly_broken = [(u, s, c) for u, s, c in broken if c != 403]
    bot_blocked = [(u, s, c) for u, s, c in broken if c == 403]

    if truly_broken:
        detail_lines = [f"{url} (status {code}) — found on: {src}" for url, src, code in truly_broken[:8]]
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="FAIL",
            weight=rule["weight"],
            details=f"{len(truly_broken)} broken link(s):\n" + "\n".join(detail_lines),
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details=f"All {len(unique_links)} links valid",
        ))

    # Report 403s as a separate warning (these sites block bots but work in browsers)
    if bot_blocked:
        detail_lines = [f"{url} — found on: {src}" for url, src, _ in bot_blocked[:5]]
        results.append(CheckResult(
            rule_id=rule["id"] + "-W",
            category=rule["category"],
            check="Some links returned 403 (may block automated checks but work in browsers)",
            status="WARN",
            weight=rule["weight"],
            details=f"{len(bot_blocked)} link(s) returned 403 Forbidden (verify manually in browser):\n"
                    + "\n".join(detail_lines),
            points_lost=0,
        ))

    return results


def check_phone_links(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that the clinic's phone number(s) are wrapped in tel: links.
    Only flags numbers that appear on multiple pages (likely the clinic number),
    not one-off numbers in content (e.g. ASPCA hotline in an FAQ)."""
    results = []

    # Phone pattern: requires at least one separator (hyphen, space, dot, or parens)
    phone_re = re.compile(
        r"\(\d{3}\)\s*\d{3}[-.\s]?\d{4}"   # (303) 555-1234 or (303) 5551234
        r"|\d{3}-\d{3}[-.]?\d{4}"           # 303-555-1234
        r"|\d{3}\.\d{3}\.\d{4}"             # 303.555.1234 (dots between ALL groups)
        r"|\d{3}\s\d{3}\s?\d{4}"            # 303 555 1234
    )

    # Tags that contain visible user-facing content
    visible_tags = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "span", "a", "li",
                    "td", "th", "div", "strong", "em", "b", "i", "label", "dd", "dt"}

    # Collect all tel: link digits across the site
    all_tel_digits = set()
    for url, page in pages.items():
        if not page.soup:
            continue
        for a in page.soup.find_all("a", href=True):
            if a["href"].startswith("tel:"):
                all_tel_digits.add(re.sub(r"\D", "", a["href"]))

    # Count how many pages each phone number appears on
    phone_page_count: dict[str, set] = {}  # digits -> set of page URLs
    for url, page in pages.items():
        if not page.soup:
            continue
        body = page.soup.find("body")
        if not body:
            continue
        phones = []
        for tag in body.find_all(visible_tags):
            style = tag.get("style", "")
            if re.search(r"display\s*:\s*none", style):
                continue
            if tag.get("aria-hidden") == "true":
                continue
            classes = " ".join(tag.get("class", []))
            if re.search(r"sr-only|screen-reader|visually-hidden", classes):
                continue
            text = tag.get_text(strip=True)
            if text:
                for m in phone_re.findall(text):
                    digits = re.sub(r"\D", "", m)
                    if digits not in phone_page_count:
                        phone_page_count[digits] = {"display": m, "pages": set()}
                    phone_page_count[digits]["pages"].add(url)

    # Only flag phone numbers that appear on 2+ pages (likely the clinic number)
    # and are NOT already in a tel: link
    issues = []
    for digits, info in phone_page_count.items():
        if len(info["pages"]) >= 2 and digits not in all_tel_digits:
            issues.append((info["display"], len(info["pages"])))

    if issues:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="WARN",
            weight=rule["weight"],
            details=f"{len(issues)} clinic phone number(s) not hyperlinked: " +
                    ", ".join(f"{phone} (found on {count} pages)" for phone, count in issues),
            points_lost=0,
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details="All visible phone numbers appear to be hyperlinked",
        ))
    return results


def check_email_links(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that email addresses are wrapped in mailto: links."""
    results = []
    issues = []
    email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

    for url, page in pages.items():
        if not page.soup:
            continue
        body = page.soup.find("body")
        if not body:
            continue
        text = body.get_text()
        emails = email_re.findall(text)
        mailto_links = [a["href"] for a in page.soup.find_all("a", href=True) if a["href"].startswith("mailto:")]
        mailto_text = " ".join(mailto_links).lower()

        for email in emails:
            if email.lower() not in mailto_text:
                issues.append((email, url))

    if issues:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="WARN",
            weight=rule["weight"],
            details=f"{len(issues)} email(s) may not be hyperlinked: " +
                    ", ".join(f"{e} on {u}" for e, u in issues[:3]),
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
        ))
    return results


def check_privacy_policy_footer(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that Privacy Policy is linked in the footer."""
    results = []
    for url, page in pages.items():
        if not page.soup:
            continue
        footer = page.soup.find("footer") or page.soup.find(id=re.compile(r"footer", re.I)) or page.soup.find(class_=re.compile(r"footer", re.I))
        if footer:
            footer_links = [a.get_text(strip=True).lower() for a in footer.find_all("a")]
            if any("privacy" in l for l in footer_links):
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Privacy Policy link found in footer on {url}",
                ))
                return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="Privacy Policy link not found in footer",
        points_lost=rule["weight"],
    ))
    return results


def check_accessibility_footer(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that Accessibility Statement is linked in the footer."""
    results = []
    for url, page in pages.items():
        if not page.soup:
            continue
        footer = page.soup.find("footer") or page.soup.find(id=re.compile(r"footer", re.I)) or page.soup.find(class_=re.compile(r"footer", re.I))
        if footer:
            footer_links = [a.get_text(strip=True).lower() for a in footer.find_all("a")]
            if any("accessibility" in l for l in footer_links):
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Accessibility Statement link found in footer on {url}",
                ))
                return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="Accessibility Statement link not found in footer",
        points_lost=rule["weight"],
    ))
    return results


def check_powered_by_petdesk(pages: dict, rule: dict) -> list[CheckResult]:
    """Check footer shows 'Powered by PetDesk' and no Whiskercloud mention."""
    results = []
    for url, page in pages.items():
        if not page.soup:
            continue
        footer = page.soup.find("footer") or page.soup.find(id=re.compile(r"footer", re.I)) or page.soup.find(class_=re.compile(r"footer", re.I))
        if footer:
            footer_text = footer.get_text(strip=True).lower()
            has_petdesk = "powered by petdesk" in footer_text
            has_whiskercloud = "whiskercloud" in footer_text

            if has_petdesk and not has_whiskercloud:
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Footer correctly shows 'Powered by PetDesk'",
                ))
                return results
            elif has_whiskercloud:
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details=f"Whiskercloud mention found in footer on {url}",
                    points_lost=rule["weight"],
                ))
                return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="WARN", weight=rule["weight"],
        details="Could not locate footer to verify 'Powered by PetDesk'",
    ))
    return results


def check_single_h1(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that each page has exactly one H1 tag."""
    results = []
    violations = []

    for url, page in pages.items():
        if not page.soup:
            continue
        h1_tags = page.soup.find_all("h1")
        if len(h1_tags) != 1:
            violations.append((url, len(h1_tags)))

    if violations:
        detail = "; ".join(f"{url} has {count} H1(s)" for url, count in violations[:5])
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"{len(violations)} page(s) with incorrect H1 count: {detail}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details=f"All {len(pages)} pages have exactly one H1",
        ))
    return results


def check_alt_text(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that all images have alt text."""
    results = []
    missing = []
    seen_srcs = set()

    for url, page in pages.items():
        if not page.soup:
            continue
        for img in page.soup.find_all("img"):
            src = img.get("src", "")
            # Skip SVG data URI placeholders (lazy-loading placeholders)
            if src.startswith("data:"):
                continue
            # Skip tiny tracking pixels
            if img.get("width") in ("1", "0") or img.get("height") in ("1", "0"):
                continue
            # Deduplicate by src to avoid counting the same image multiple times
            if src in seen_srcs:
                continue
            seen_srcs.add(src)

            alt = img.get("alt", None)
            if alt is None or alt.strip() == "":
                short_src = src.split("/")[-1][:50] if "/" in src else src[:50]
                missing.append((short_src, url))

    if missing:
        detail_lines = [f"{src} — found on: {url}" for src, url in missing]
        detail = "\n".join(detail_lines)
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"{len(missing)} unique image(s) missing alt text:\n{detail}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="All images have alt text",
        ))
    return results


def check_meta_titles(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that all pages have meta titles."""
    results = []
    missing = []

    for url, page in pages.items():
        if not page.soup:
            continue
        title = page.soup.find("title")
        if not title or not title.get_text(strip=True):
            missing.append(url)

    if missing:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"{len(missing)} page(s) missing meta title: {', '.join(missing[:3])}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="All pages have meta titles",
        ))
    return results


def check_meta_descriptions(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that all pages have meta descriptions."""
    results = []
    missing = []

    for url, page in pages.items():
        if not page.soup:
            continue
        meta = page.soup.find("meta", attrs={"name": "description"})
        if not meta or not meta.get("content", "").strip():
            missing.append(url)

    if missing:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"{len(missing)} page(s) missing meta description: {', '.join(missing[:3])}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="All pages have meta descriptions",
        ))
    return results


def check_placeholder_text(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for Lorem Ipsum and other placeholder text."""
    results = []
    placeholders = ["lorem ipsum", "dolor sit amet", "placeholder text",
                     "sample text", "insert text here", "your text here",
                     "coming soon", "under construction"]
    found = []

    for url, page in pages.items():
        if not page.soup:
            continue
        text = page.soup.get_text().lower()
        for p in placeholders:
            if p in text:
                found.append((p, url))

    if found:
        detail = "; ".join(f'"{p}" on {u}' for p, u in found[:5])
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"Placeholder text found: {detail}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="No placeholder text detected",
        ))
    return results


def check_favicon(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that a favicon is set."""
    results = []
    for url, page in pages.items():
        if not page.soup:
            continue
        favicon = (page.soup.find("link", rel="icon") or
                   page.soup.find("link", rel="shortcut icon") or
                   page.soup.find("link", rel=re.compile(r"icon")))
        if favicon:
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details="Favicon found",
            ))
            return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="No favicon link found in page head",
        points_lost=rule["weight"],
    ))
    return results


def check_logo_links_home(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that the site logo links back to the homepage."""
    # Get the base domain from any page URL
    any_url = next(iter(pages.keys()), "")
    parsed_base = urllib.parse.urlparse(any_url)
    base_domain = parsed_base.netloc

    for url, page in pages.items():
        if not page.soup:
            continue
        # Look for logo in header area - try multiple selectors
        header = page.soup.find("header")
        if not header:
            header = page.soup.find(id=re.compile(r"header", re.I))
        if not header:
            header = page.soup.find(class_=re.compile(r"header", re.I))
        if not header:
            continue

        # Find links that contain images (likely logo)
        for a in header.find_all("a", href=True):
            img = a.find("img")
            if not img:
                continue
            href = a["href"].strip()
            parsed = urllib.parse.urlparse(href)
            # Check if it links to homepage: /, empty path, or full URL to same domain root
            is_home = (
                parsed.path.rstrip("/") in ("", "/") and
                (not parsed.netloc or parsed.netloc == base_domain)
            )
            if is_home:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Logo links to homepage",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="WARN", weight=rule["weight"],
        details="Could not confirm logo links to homepage",
    )]


def check_no_whiskercloud(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that Whiskercloud is not mentioned anywhere on the site."""
    found_visible = []
    found_source = []

    for url, page in pages.items():
        if not page.soup:
            continue
        # Check visible text (what users see)
        visible_text = page.soup.get_text(separator=" ", strip=True).lower()
        if "whiskercloud" in visible_text:
            found_visible.append(url)
        # Check full HTML source (scripts, class names, comments)
        elif page.html and "whiskercloud" in page.html.lower():
            found_source.append(url)

    if found_visible:
        detail_lines = [url for url in found_visible]
        detail = "\n".join(detail_lines)
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"\"Whiskercloud\" visible to users on {len(found_visible)} page(s):\n{detail}",
            points_lost=rule["weight"],
        )]
    elif found_source:
        detail_lines = [url for url in found_source[:3]]
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"\"Whiskercloud\" found in HTML source (not visible to users) on {len(found_source)} page(s): "
                    + ", ".join(detail_lines),
            points_lost=0,
        )]
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No Whiskercloud mentions found",
    )]


def check_social_links_footer_only(pages: dict, rule: dict) -> list[CheckResult]:
    """Verify social media links appear only in the footer, not in nav/top bar."""
    results = []
    social_domains = ["facebook.com", "instagram.com", "twitter.com", "x.com",
                      "youtube.com", "linkedin.com", "tiktok.com"]
    violations = set()  # Use set to deduplicate

    for url, page in pages.items():
        if not page.soup:
            continue

        # Normalize URL (strip trailing slash for deduplication)
        normalized_url = url.rstrip("/")

        # Check header/nav for social links
        header = page.soup.find("header") or page.soup.find("nav")
        if header:
            for a in header.find_all("a", href=True):
                href = a["href"].lower()
                for sd in social_domains:
                    if sd in href:
                        violations.add((sd, normalized_url))

    if violations:
        sorted_violations = sorted(violations)
        detail_lines = [f"{sd} found in header/nav on {u}" for sd, u in sorted_violations]
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"Social links found outside footer ({len(violations)} pages)\n" + "\n".join(detail_lines),
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="Social links correctly placed in footer only",
        ))
    return results


def check_nav_links(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that navigation links resolve correctly."""
    results = []
    broken_nav = []

    for url, page in pages.items():
        if not page.soup:
            continue
        nav = page.soup.find("nav") or page.soup.find(id=re.compile(r"menu|nav", re.I))
        if nav:
            for a in nav.find_all("a", href=True):
                href = a["href"]
                if href.startswith(("#", "javascript:", "tel:", "mailto:")):
                    continue
                absolute = urllib.parse.urljoin(url, href)
                # Check if the nav link target was crawled and is valid
                if absolute in pages and pages[absolute].status_code >= 400:
                    broken_nav.append((a.get_text(strip=True), absolute))
            break  # Only need to check nav once

    if broken_nav:
        detail = "; ".join(f'"{label}" -> {href}' for label, href in broken_nav[:5])
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"Broken nav links: {detail}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="Navigation links resolve correctly",
        ))
    return results


def check_map_iframe(pages: dict, rule: dict) -> list[CheckResult]:
    """Check Google Maps iframe doesn't show GMB reviews."""
    results = []
    for url, page in pages.items():
        if not page.soup:
            continue
        for iframe in page.soup.find_all("iframe"):
            src = iframe.get("src", "")
            if "google.com/maps" in src and "reviews" in src.lower():
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details=f"Map iframe may include GMB reviews on {url}",
                    points_lost=rule["weight"],
                ))
                return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="Map iframes appear to use location (not GMB listing)",
    ))
    return results


def check_userway_widget(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for UserWay accessibility widget."""
    results = []
    for url, page in pages.items():
        if page.html and "userway" in page.html.lower():
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details="UserWay widget detected",
            ))
            return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="UserWay accessibility widget not found",
        points_lost=rule["weight"],
    ))
    return results


# Partner-specific checks
def check_cta_text(pages: dict, rule: dict) -> list[CheckResult]:
    """Check CTA button text matches expected (e.g., 'Book Appointment' for Western)."""
    results = []
    expected = rule.get("expected_cta_text", "Book Appointment").lower()
    issues = []

    for url, page in pages.items():
        if not page.soup:
            continue
        # Look for CTA-style elements
        for el in page.soup.find_all(["a", "button"], class_=re.compile(r"cta|btn|button", re.I)):
            text = el.get_text(strip=True).lower()
            if "appointment" in text or "book" in text or "schedule" in text:
                if text != expected:
                    issues.append((el.get_text(strip=True), url))

    if issues:
        detail = "; ".join(f'Found "{txt}" on {u}' for txt, u in issues[:3])
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f'CTA text mismatch (expected "{rule.get("expected_cta_text", "Book Appointment")}"): {detail}',
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details=f'CTA text correctly shows "{rule.get("expected_cta_text", "Book Appointment")}"',
        ))
    return results


def check_h1_no_welcome(pages: dict, rule: dict) -> list[CheckResult]:
    """Check homepage H1 doesn't start with 'Welcome to'."""
    results = []
    for url, page in pages.items():
        if not page.soup:
            continue
        # Identify homepage
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ("/", ""):
            h1 = page.soup.find("h1")
            if h1:
                h1_text = h1.get_text(strip=True)
                if h1_text.lower().startswith("welcome to"):
                    results.append(CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="FAIL", weight=rule["weight"],
                        details=f'Homepage H1 starts with "Welcome to": "{h1_text}"',
                        points_lost=rule["weight"],
                    ))
                    return results
                else:
                    results.append(CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="PASS", weight=rule["weight"],
                        details=f'Homepage H1 is correct: "{h1_text}"',
                    ))
                    return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Could not identify homepage. Verify H1 text manually.",
    ))
    return results


def check_birdeye_widget(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for Birdeye testimonial widget on homepage."""
    results = []
    for url, page in pages.items():
        if page.html and "birdeye" in page.html.lower():
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details="Birdeye widget detected",
            ))
            return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="Birdeye testimonial widget not found",
        points_lost=rule["weight"],
    ))
    return results


def check_faq_no_hours(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that FAQ page doesn't contain hours of operation."""
    results = []
    hours_patterns = [r"\d{1,2}:\d{2}\s*(am|pm|AM|PM)", r"hours of operation",
                      r"monday.*friday", r"mon.*fri", r"open\s+\d"]

    for url, page in pages.items():
        if "faq" in url.lower():
            if not page.soup:
                continue
            text = page.soup.get_text().lower()
            for pattern in hours_patterns:
                if re.search(pattern, text, re.I):
                    results.append(CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="FAIL", weight=rule["weight"],
                        details=f"FAQ page appears to contain hours of operation: {url}",
                        points_lost=rule["weight"],
                    ))
                    return results

    results.append(CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="FAQ page does not contain hours of operation",
    ))
    return results


# Stub checks for features that need more context to fully implement
def check_form_success_pages(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for thank-you/success pages for forms."""
    results = []
    has_forms = False
    has_success = False

    for url, page in pages.items():
        if not page.soup:
            continue
        forms = page.soup.find_all("form")
        if forms:
            has_forms = True
        if any(x in url.lower() for x in ["thank-you", "success", "thank_you", "confirmation"]):
            has_success = True

    if has_forms and not has_success:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details="Forms found but no thank-you/success pages detected in crawl",
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="Thank-you/success pages found or no forms detected",
        ))
    return results


# =============================================================================
# PAGESPEED INSIGHTS (RENDERED PAGE CHECKS)
# =============================================================================
# These use Google's PageSpeed Insights API which actually renders the page
# in a real browser (Chrome), giving us accurate mobile usability, performance,
# accessibility, and visual stability data -- not just HTML parsing.

_psi_cache: dict = {}  # Cache PSI results per URL to avoid duplicate calls


def _get_psi_data(url: str) -> dict | None:
    """Fetch PageSpeed Insights data for a URL. Returns None on failure."""
    if url in _psi_cache:
        return _psi_cache[url]

    api_url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {
        "url": url,
        "strategy": "mobile",
        "category": ["performance", "accessibility"],
    }
    if PSI_API_KEY:
        params["key"] = PSI_API_KEY

    try:
        resp = requests.get(api_url, params=params, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            _psi_cache[url] = data
            return data
        else:
            print(f"  [PSI] API returned {resp.status_code} for {url}")
            _psi_cache[url] = None
            return None
    except Exception as e:
        print(f"  [PSI] Error fetching {url}: {e}")
        _psi_cache[url] = None
        return None


def _get_psi_audit(data: dict, audit_key: str) -> dict:
    """Extract a specific audit from PSI Lighthouse data."""
    if not data:
        return {}
    return data.get("lighthouseResult", {}).get("audits", {}).get(audit_key, {})


def _get_psi_category_score(data: dict, category: str) -> float | None:
    """Extract a category score (0-1) from PSI data."""
    if not data:
        return None
    cat = data.get("lighthouseResult", {}).get("categories", {}).get(category, {})
    return cat.get("score")


def check_mobile_responsive(pages: dict, rule: dict) -> list[CheckResult]:
    """Check mobile responsiveness using PSI rendered check + HTML fallback."""
    results = []

    # Get the homepage URL
    homepage_url = list(pages.keys())[0] if pages else ""
    psi = _get_psi_data(homepage_url) if homepage_url else None

    if psi:
        # Use real rendered data from PSI
        viewport = _get_psi_audit(psi, "viewport")
        tap_targets = _get_psi_audit(psi, "tap-targets")
        font_size = _get_psi_audit(psi, "font-size")

        issues = []
        if viewport.get("score") == 0:
            issues.append("Viewport not configured for mobile")
        if tap_targets.get("score") is not None and tap_targets["score"] < 1:
            issues.append(f"Tap targets too small: {tap_targets.get('displayValue', 'see report')}")
        if font_size.get("score") is not None and font_size["score"] < 1:
            issues.append(f"Font sizes too small for mobile: {font_size.get('displayValue', 'see report')}")

        if issues:
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="FAIL", weight=rule["weight"],
                details="PageSpeed Insights (rendered check): " + "; ".join(issues),
                points_lost=rule["weight"],
            ))
        else:
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details="PageSpeed Insights confirms mobile-friendly (viewport, tap targets, font sizes OK)",
            ))
    else:
        # Fallback to HTML-only check
        missing = []
        for url, page in pages.items():
            if page.soup and not page.soup.find("meta", attrs={"name": "viewport"}):
                missing.append(url)
        if missing:
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="FAIL", weight=rule["weight"],
                details=f"Viewport meta tag missing on {len(missing)} page(s) (HTML check; add PSI_API_KEY for rendered check)",
                points_lost=rule["weight"],
            ))
        else:
            results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details="Viewport meta tag present (HTML check; add PSI_API_KEY for full rendered check)",
            ))
    return results


def check_featured_images(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for Open Graph images (proxy for featured images)."""
    results = []
    missing = []

    for url, page in pages.items():
        if not page.soup:
            continue
        og_image = page.soup.find("meta", property="og:image")
        if not og_image or not og_image.get("content"):
            missing.append(url)

    if missing:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"{len(missing)} page(s) missing OG image (featured image proxy): {', '.join(missing[:3])}",
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="All pages have OG images set",
        ))
    return results


def check_contrast(pages: dict, rule: dict) -> list[CheckResult]:
    """Check color contrast using PSI accessibility audit."""
    homepage_url = list(pages.keys())[0] if pages else ""
    psi = _get_psi_data(homepage_url) if homepage_url else None

    if psi:
        contrast = _get_psi_audit(psi, "color-contrast")
        if contrast.get("score") is not None:
            if contrast["score"] >= 1:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="PageSpeed Insights confirms sufficient color contrast",
                )]
            else:
                detail = contrast.get("displayValue", "Insufficient contrast")
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details=f"PageSpeed Insights: {detail}",
                    points_lost=rule["weight"],
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Contrast check requires PSI_API_KEY for automated check. Flagged for human review.",
    )]


def check_lighthouse(pages: dict, rule: dict) -> list[CheckResult]:
    """Check performance via PageSpeed Insights Lighthouse."""
    homepage_url = list(pages.keys())[0] if pages else ""
    psi = _get_psi_data(homepage_url) if homepage_url else None

    if psi:
        perf_score = _get_psi_category_score(psi, "performance")
        access_score = _get_psi_category_score(psi, "accessibility")

        details_parts = []
        if perf_score is not None:
            details_parts.append(f"Performance: {int(perf_score * 100)}/100")
        if access_score is not None:
            details_parts.append(f"Accessibility: {int(access_score * 100)}/100")

        cls = _get_psi_audit(psi, "cumulative-layout-shift")
        if cls.get("displayValue"):
            details_parts.append(f"CLS: {cls['displayValue']}")

        lcp = _get_psi_audit(psi, "largest-contentful-paint")
        if lcp.get("displayValue"):
            details_parts.append(f"LCP: {lcp['displayValue']}")

        detail = " | ".join(details_parts) if details_parts else "Data retrieved"

        if perf_score is not None and perf_score < 0.5:
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="WARN", weight=rule["weight"],
                details=f"PageSpeed Insights: {detail}",
            )]
        else:
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details=f"PageSpeed Insights: {detail}",
            )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify page performance manually (load time, responsiveness). "
                "Set PSI_API_KEY for automated scoring.",
    )]


# Stubs for partner-specific checks
def check_cta_on_pages(pages, rule):
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Verify CTAs appear on required pages (Home, About, Services).")]

def check_nav_structure(pages, rule):
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Verify navigation matches partner layout specification.")]

def check_topbar_layout(pages, rule):
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Verify top bar layout: Phone/Email on left, Download App/Pharmacy on right.")]

def check_service_count(pages, rule):
    results = []
    max_svc = rule.get("max_services", 5)
    for url, page in pages.items():
        if not page.soup:
            continue
        nav = page.soup.find("nav")
        if nav:
            for a in nav.find_all("a"):
                if "services" in a.get_text(strip=True).lower():
                    parent_li = a.find_parent("li")
                    if parent_li:
                        sub_menu = parent_li.find("ul")
                        if sub_menu:
                            items = sub_menu.find_all("li")
                            count = len(items)
                            if count > max_svc + 1:  # +1 for "All Services" link
                                return [CheckResult(rule["id"], rule["category"], rule["check"],
                                                    "FAIL", rule["weight"],
                                                    f"Services dropdown has {count} items (max {max_svc} + 'All Services')",
                                                    points_lost=rule["weight"])]
                            else:
                                return [CheckResult(rule["id"], rule["category"], rule["check"],
                                                    "PASS", rule["weight"],
                                                    f"Services dropdown has {count} items")]
            break
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Could not locate services dropdown. Verify service count manually.")]

def check_new_client_form(pages, rule):
    results = []
    for url, page in pages.items():
        if "new-client" in url.lower() or "new_client" in url.lower():
            return [CheckResult(rule["id"], rule["category"], rule["check"], "PASS", rule["weight"],
                                f"New Client Form page found: {url}")]
        if page.soup:
            for a in page.soup.find_all("a"):
                if "new client" in a.get_text(strip=True).lower():
                    return [CheckResult(rule["id"], rule["category"], rule["check"], "PASS",
                                        rule["weight"], "New Client Form link found")]
    return [CheckResult(rule["id"], rule["category"], rule["check"], "WARN", rule["weight"],
                        "New Client Form page not found in crawl")]

def check_privacy_policy_verbiage(pages, rule):
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Privacy policy verbiage match requires manual comparison")]

def check_photo_gallery_instructions(pages, rule):
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Check that the photo gallery page includes form submission instructions for clients.")]

def check_no_appt_cta_euthanasia(pages, rule):
    results = []
    euth_keywords = ["euthanasia", "end-of-life", "end_of_life", "cremation", "memorial"]
    for url, page in pages.items():
        if any(k in url.lower() for k in euth_keywords):
            if page.soup:
                footer = page.soup.find("footer")
                if footer:
                    footer_text = footer.get_text().lower()
                    if "ready for a visit" in footer_text or "book appointment" in footer_text:
                        return [CheckResult(rule["id"], rule["category"], rule["check"], "FAIL",
                                            rule["weight"],
                                            f"Appointment CTA found in footer on sensitive page: {url}",
                                            points_lost=rule["weight"])]
    return [CheckResult(rule["id"], rule["category"], rule["check"], "PASS", rule["weight"],
                        "No appointment CTAs on euthanasia/end-of-life pages")]

def check_career_tracking_url(pages, rule):
    return [CheckResult(rule["id"], rule["category"], rule["check"], "HUMAN_REVIEW", rule["weight"],
                        "Career page tracking URL requires manual verification")]


# =============================================================================
# GRAMMAR & SPELLING CHECK (LanguageTool API)
# =============================================================================

_LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"


def _extract_visible_text(soup) -> str:
    """Extract visible text from a page, excluding scripts/styles/nav/footer."""
    if not soup:
        return ""
    # Remove script, style, nav, footer, header elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    body = soup.find("body")
    if not body:
        return ""
    text = body.get_text(separator=" ", strip=True)
    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Pattern-based medical/veterinary term detection.
# Instead of maintaining a manual allowlist, we match common medical
# prefixes and suffixes to automatically accept domain-specific terminology.
_MEDICAL_PREFIXES = (
    "micro", "macro", "endo", "echo", "gastro", "ortho", "dermato", "derma",
    "ophthalmo", "cardio", "neuro", "hepato", "nephro", "hemato", "hemo",
    "osteo", "arthro", "rhino", "laryngo", "broncho", "pneumo",
    "laparo", "thoraco", "cranio", "splen", "pancreat",
    "tele", "ultra", "radio", "electro", "thermo", "cryo", "immuno",
    "hyper", "hypo", "peri", "intra", "supra",
)
_MEDICAL_SUFFIXES = (
    "ectomy", "otomy", "ostomy", "ology", "ologist", "itis", "osis",
    "emia", "uria", "penia", "pathy", "plasty", "pexy", "scopy",
    "scopic", "gram", "graph", "graphy", "centesis", "lysis",
    "trophy", "genesis", "stasis", "worm",
)
# Small allowlist for common domain terms that don't match prefix/suffix patterns
_DOMAIN_TERMS = {
    "spay", "spayed", "neuter", "neutered", "fecal", "euthanasia",
    "bloodwork", "deworming", "dewormer", "trupanion", "petdesk",
    "webapp", "signup", "login", "dropdown", "popup", "tooltip",
    "webpage", "website", "sitemap", "homepage", "blog",
}


def _should_skip_spelling(word: str) -> bool:
    """Check if a flagged word should be skipped.
    Uses pattern matching for medical/vet terms instead of a manual allowlist.
    """
    if not word:
        return False
    w = word.lower().strip()
    # Known domain terms (small list of words that don't fit patterns)
    if w in _DOMAIN_TERMS:
        return True
    # Capitalized words (Title Case) are likely names/places/brands
    if word[0].isupper() and len(word) > 1:
        return True
    # ALL CAPS words (abbreviations, acronyms)
    if word.isupper() and len(word) >= 2:
        return True
    # Words with mixed case (PetDesk, WordPress, WPEngine)
    if any(c.isupper() for c in word[1:]):
        return True
    # Medical prefix patterns (microchip, endoscopy, echocardiogram, etc.)
    for prefix in _MEDICAL_PREFIXES:
        if w.startswith(prefix) and len(w) > len(prefix) + 1:
            return True
    # Medical suffix patterns (heartworm, gastropexy, dermatology, etc.)
    for suffix in _MEDICAL_SUFFIXES:
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            return True
    return False


def check_grammar_spelling(pages: dict, rule: dict) -> list[CheckResult]:
    """Check visible page text for grammar and spelling errors using LanguageTool API.
    Returns separate results for spelling (FAIL) and grammar (WARN)."""
    spelling_issues = []
    grammar_issues = []
    pages_checked = 0
    max_pages = 5

    for url, page in list(pages.items())[:max_pages]:
        if not page.soup:
            continue

        soup_copy = BeautifulSoup(page.html, "lxml") if page.html else None
        if not soup_copy:
            continue

        text = _extract_visible_text(soup_copy)
        if not text or len(text) < 50:
            continue

        text = text[:10000]

        try:
            resp = requests.post(
                _LANGUAGETOOL_URL,
                data={"text": text, "language": "en-US"},
                timeout=30,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            matches = data.get("matches", [])
            pages_checked += 1

            noisy_rules = {"WHITESPACE_RULE", "COMMA_PARENTHESIS_WHITESPACE",
                           "UPPERCASE_SENTENCE_START", "CONSECUTIVE_SPACES",
                           "EN_QUOTES", "DASH_RULE", "MULTIPLICATION_SIGN",
                           "ELLIPSIS", "TYPOGRAPHICAL_APOSTROPHE"}

            for m in matches:
                issue_type = m.get("rule", {}).get("issueType", "")
                rule_id_lt = m.get("rule", {}).get("id", "")
                if rule_id_lt in noisy_rules:
                    continue

                context_obj = m.get("context", {})
                context_text = context_obj.get("text", "")
                offset = context_obj.get("offset", 0)
                length = context_obj.get("length", 0)
                # Extract the flagged word
                flagged_word = context_text[offset:offset + length] if length else ""

                is_spelling = ("spell" in issue_type.lower() or
                               "misspelling" in issue_type.lower())

                # Skip proper nouns, brand names, and medical/vet terms
                if is_spelling and _should_skip_spelling(flagged_word):
                    continue

                message = m.get("message", "")
                replacements = [r["value"] for r in m.get("replacements", [])[:2]]
                suggestion = f" -> {', '.join(replacements)}" if replacements else ""

                issue = {
                    "url": url,
                    "flagged": flagged_word,
                    "message": message,
                    "context": context_text[:100],
                    "suggestion": suggestion,
                    "type": issue_type,
                }

                if is_spelling:
                    spelling_issues.append(issue)
                else:
                    grammar_issues.append(issue)

        except Exception:
            continue

    if not pages_checked:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Could not reach LanguageTool API. Check grammar and spelling manually.",
        )]

    results = []

    # --- Spelling result (FAIL if issues found) ---
    # Deduplicate: group by flagged word, show count and pages
    if spelling_issues:
        word_groups = {}
        for i in spelling_issues:
            word_key = i["flagged"].lower()
            if word_key not in word_groups:
                word_groups[word_key] = {
                    "word": i["flagged"],
                    "suggestion": i["suggestion"],
                    "pages": set(),
                    "count": 0,
                }
            word_groups[word_key]["pages"].add(i["url"])
            word_groups[word_key]["count"] += 1

        detail_lines = []
        for word_key, info in sorted(word_groups.items()):
            pages_list = sorted(info["pages"])
            line = f'"{info["word"]}"{info["suggestion"]} — {info["count"]} occurrence(s)'
            for pg in pages_list:
                short = pg.split("//")[-1] if "//" in pg else pg
                line += f"\n  - {short}"
            detail_lines.append(line)

        detail = "\n".join(detail_lines)

        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check="Spelling errors in visible page content",
            status="FAIL", weight=rule["weight"],
            details=f"{len(word_groups)} unique misspelled word(s) ({len(spelling_issues)} total "
                    f"occurrences) across {pages_checked} page(s):\n{detail}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check="Spelling errors in visible page content",
            status="PASS", weight=rule["weight"],
            details=f"No spelling errors found across {pages_checked} page(s)",
        ))

    # --- Grammar result (WARN only, never FAIL) ---
    # Deduplicate: group by message type, show count and pages
    if grammar_issues:
        msg_groups = {}
        for i in grammar_issues:
            msg_key = i["message"][:80]
            if msg_key not in msg_groups:
                msg_groups[msg_key] = {
                    "message": i["message"],
                    "suggestion": i["suggestion"],
                    "pages": set(),
                    "count": 0,
                }
            msg_groups[msg_key]["pages"].add(i["url"])
            msg_groups[msg_key]["count"] += 1

        detail_lines = []
        for msg_key, info in list(msg_groups.items())[:10]:
            pages_list = sorted(info["pages"])
            page_names = [pg.split("//")[-1] if "//" in pg else pg for pg in pages_list[:2]]
            line = f'{info["message"]}{info["suggestion"]} — {info["count"]} occurrence(s) on: {", ".join(page_names)}'
            detail_lines.append(line)

        detail = "\n".join(detail_lines)
        if len(msg_groups) > 10:
            detail += f"\n... and {len(msg_groups) - 10} more issue types"

        results.append(CheckResult(
            rule_id=rule["id"] + "-G", category=rule["category"],
            check="Grammar issues in visible page content",
            status="WARN", weight=rule["weight"],
            details=f"{len(msg_groups)} unique grammar issue(s) ({len(grammar_issues)} total) "
                    f"across {pages_checked} page(s):\n{detail}",
            points_lost=0,
        ))

    if not spelling_issues and not grammar_issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details=f"No grammar or spelling issues found across {pages_checked} page(s)",
        )]

    return results


# =============================================================================
# BROKEN IMAGE DETECTION
# =============================================================================

def check_broken_images(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that all <img> src URLs actually load (HTTP 200)."""
    broken = []
    checked = set()
    total_images = 0
    # Track which page each image was found on
    img_to_page = {}

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for url, page in pages.items():
        if not page.soup:
            continue
        for img in page.soup.find_all("img", src=True):
            src = img["src"]
            if src.startswith("data:"):
                continue
            absolute = urllib.parse.urljoin(url, src)
            if absolute not in checked:
                checked.add(absolute)
                total_images += 1
                img_to_page[absolute] = url

    def check_img(img_url):
        try:
            r = session.head(img_url, timeout=10, allow_redirects=True)
            if r.status_code >= 400:
                return (img_url, r.status_code)
        except requests.RequestException:
            return (img_url, 0)
        return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_img, u): u for u in list(checked)[:200]}
        for future in as_completed(futures):
            result = future.result()
            if result:
                broken.append(result)

    if broken:
        detail_lines = []
        for img_url, code in broken:
            page_url = img_to_page.get(img_url, "unknown page")
            detail_lines.append(f"{img_url} (status {code}) — found on: {page_url}")
        detail = "\n".join(detail_lines)
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"{len(broken)} broken image(s) out of {total_images}:\n{detail}",
            points_lost=rule["weight"],
        )]
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"All {total_images} images load correctly",
    )]


# =============================================================================
# OPEN GRAPH / SOCIAL SHARING META TAGS
# =============================================================================

def check_open_graph(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that key pages have Open Graph meta tags for social sharing."""
    missing = []

    for url, page in pages.items():
        if not page.soup:
            continue
        head = page.soup.find("head")
        if not head:
            missing.append((url, "no <head> tag"))
            continue

        og_title = head.find("meta", property="og:title")
        og_desc = head.find("meta", property="og:description")
        og_image = head.find("meta", property="og:image")

        missing_tags = []
        if not og_title or not og_title.get("content", "").strip():
            missing_tags.append("og:title")
        if not og_desc or not og_desc.get("content", "").strip():
            missing_tags.append("og:description")
        if not og_image or not og_image.get("content", "").strip():
            missing_tags.append("og:image")

        if missing_tags:
            missing.append((url, ", ".join(missing_tags)))

    if missing:
        detail_lines = [f"{pg} — missing: {tags}" for pg, tags in missing]
        detail = "\n".join(detail_lines)
        status = "FAIL" if len(missing) > 2 else "WARN"
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status=status, weight=rule["weight"],
            details=f"{len(missing)} page(s) missing Open Graph tags: {detail}",
            points_lost=rule["weight"] if status == "FAIL" else 0,
        )]
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"All {len(pages)} pages have Open Graph meta tags",
    )]


# =============================================================================
# MIXED CONTENT DETECTION
# =============================================================================

def check_mixed_content(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for HTTP resources loaded on HTTPS pages (causes browser warnings)."""
    issues = []

    for url, page in pages.items():
        if not url.startswith("https://"):
            continue
        if not page.soup:
            continue

        http_resources = []
        for tag, attr in [("img", "src"), ("script", "src"), ("link", "href"),
                          ("source", "src"), ("iframe", "src"), ("video", "src"),
                          ("audio", "src")]:
            for el in page.soup.find_all(tag, **{attr: True}):
                val = el[attr]
                if val.startswith("http://"):
                    http_resources.append(f"<{tag}> {val[:80]}")

        if http_resources:
            short = url.split("/")[-1] or "homepage"
            issues.append((f"/{short}", http_resources))

    if issues:
        total = sum(len(res) for _, res in issues)
        detail_lines = []
        for pg, resources in issues:
            for res in resources:
                detail_lines.append(f"{pg}: {res}")
        detail = "\n".join(detail_lines)
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"{total} insecure (HTTP) resource(s) on {len(issues)} page(s): {detail}",
            points_lost=rule["weight"],
        )]
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No mixed content issues found",
    )]


# =============================================================================
# CHECK FUNCTION REGISTRY
# =============================================================================

CHECK_FUNCTIONS = {
    "check_leftover_text": check_leftover_text,
    "check_broken_links": check_broken_links,
    "check_phone_links": check_phone_links,
    "check_email_links": check_email_links,
    "check_privacy_policy_footer": check_privacy_policy_footer,
    "check_accessibility_footer": check_accessibility_footer,
    "check_powered_by_petdesk": check_powered_by_petdesk,
    "check_single_h1": check_single_h1,
    "check_alt_text": check_alt_text,
    "check_meta_titles": check_meta_titles,
    "check_meta_descriptions": check_meta_descriptions,
    "check_placeholder_text": check_placeholder_text,
    "check_favicon": check_favicon,
    "check_logo_links_home": check_logo_links_home,
    "check_no_whiskercloud": check_no_whiskercloud,
    "check_social_links_footer_only": check_social_links_footer_only,
    "check_nav_links": check_nav_links,
    "check_map_iframe": check_map_iframe,
    "check_userway_widget": check_userway_widget,
    "check_cta_text": check_cta_text,
    "check_h1_no_welcome": check_h1_no_welcome,
    "check_birdeye_widget": check_birdeye_widget,
    "check_faq_no_hours": check_faq_no_hours,
    "check_form_success_pages": check_form_success_pages,
    "check_mobile_responsive": check_mobile_responsive,
    "check_featured_images": check_featured_images,
    "check_contrast": check_contrast,
    "check_lighthouse": check_lighthouse,
    "check_cta_on_pages": check_cta_on_pages,
    "check_nav_structure": check_nav_structure,
    "check_topbar_layout": check_topbar_layout,
    "check_service_count": check_service_count,
    "check_new_client_form": check_new_client_form,
    "check_privacy_policy_verbiage": check_privacy_policy_verbiage,
    "check_photo_gallery_instructions": check_photo_gallery_instructions,
    "check_no_appt_cta_euthanasia": check_no_appt_cta_euthanasia,
    "check_career_tracking_url": check_career_tracking_url,
    "check_grammar_spelling": check_grammar_spelling,
    "check_broken_images": check_broken_images,
    "check_open_graph": check_open_graph,
    "check_mixed_content": check_mixed_content,
}
