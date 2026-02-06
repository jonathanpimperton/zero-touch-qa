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

# Gemini API for AI-powered image analysis (primary)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Optional: Google GenAI SDK for vision analysis
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

# Anthropic API as fallback
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    anthropic = None

# Determine which AI provider to use
AI_PROVIDER = None
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    AI_PROVIDER = "gemini"
elif ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
    AI_PROVIDER = "anthropic"


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
    report_filename: str = ""  # Set by app.py before generating HTML
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
            launch_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ]
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


def check_long_text_blocks(pages: dict, rule: dict) -> list[CheckResult]:
    """
    Check for long paragraphs that aren't broken up with subheads, bullets, or visual elements.
    Flags paragraphs over 150 words that may be hard to read.
    """
    issues = []
    max_words = 150

    for url, page in pages.items():
        if not page.soup:
            continue

        paragraphs = page.soup.find_all("p")
        for p in paragraphs:
            text = p.get_text(strip=True)
            word_count = len(text.split())
            if word_count > max_words:
                # Check if it's in main content (not footer/nav)
                parent_classes = " ".join(p.get("class", []))
                if "footer" in parent_classes.lower() or "nav" in parent_classes.lower():
                    continue
                issues.append(f"{url}: Paragraph with {word_count} words")

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"Found {len(issues)} long text block(s) that could be broken up:\n" + "\n".join(issues[:5]),
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No excessively long text blocks found. Content appears well-structured.",
    )]


def check_outcome_promises(pages: dict, rule: dict) -> list[CheckResult]:
    """
    Check for marketing language that promises specific outcomes.
    Veterinary sites should avoid guarantees about treatment results.
    """
    # Patterns that indicate outcome promises - must be specific to avoid false positives
    promise_patterns = [
        (r"\bguarantee[sd]?\s+\w+", "guarantee"),  # "guaranteed results", "guarantees success"
        (r"\bwe\s+promise\b", "promise"),  # "we promise" (but not "promise" in other contexts)
        (r"\bwill\s+cure\b", "will cure"),
        (r"\b100\s*%\s*(effective|success|cure)", "100% claim"),
        (r"\bcertain\s+to\s+(cure|heal|fix)", "certain to cure"),
        (r"\balways\s+(works?|succeeds?|cures?)", "always works"),
        (r"\bguaranteed\s+(results?|outcomes?|recovery)", "guaranteed results"),
    ]

    issues = []

    for url, page in pages.items():
        if not page.soup:
            continue

        # Get visible text
        body = page.soup.find("body")
        if not body:
            continue

        text = body.get_text(" ", strip=True).lower()
        short_url = url.split("//")[-1] if "//" in url else url

        for pattern, label in promise_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                matched_text = match.group(0)[:30]
                issues.append(f"{short_url}: \"{matched_text}\"")
                break  # One issue per page is enough

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=f"Outcome promise language found on {len(issues)} page(s)",
            status="WARN", weight=rule["weight"],
            details="Pages with potential outcome guarantees:\n" + "\n".join(f"• {i}" for i in issues[:5]) +
                   "\n\nVeterinary sites should avoid guaranteeing specific treatment outcomes.",
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No inappropriate outcome promises found.",
    )]


def check_internal_links(pages: dict, rule: dict) -> list[CheckResult]:
    """
    Check that pages have internal links to other site content.
    Good internal linking helps users navigate and improves SEO.
    """
    if not pages:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="SKIP", weight=rule["weight"],
            details="No pages to check",
        )]

    # Get base domain
    any_url = next(iter(pages.keys()), "")
    parsed_base = urllib.parse.urlparse(any_url)
    base_domain = parsed_base.netloc

    pages_without_links = []

    for url, page in pages.items():
        if not page.soup:
            continue

        # Skip home page (it naturally has fewer internal links from nav)
        if urllib.parse.urlparse(url).path in ("/", ""):
            continue

        # Find internal links in main content (not nav/footer)
        body = page.soup.find("body")
        if not body:
            continue

        # Count internal links in main content area
        internal_links = 0
        for a in body.find_all("a", href=True):
            href = a.get("href", "")
            # Skip nav/footer/header links
            parent_classes = " ".join(a.find_parent().get("class", []) if a.find_parent() else [])
            if any(x in parent_classes.lower() for x in ["nav", "footer", "header", "menu"]):
                continue

            if href.startswith("/") or base_domain in href:
                internal_links += 1

        if internal_links < 2:
            pages_without_links.append(url)

    if len(pages_without_links) > len(pages) * 0.3:  # More than 30% lacking links
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"{len(pages_without_links)} pages have few internal content links:\n" + "\n".join(pages_without_links[:5]),
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="Pages have adequate internal linking.",
    )]


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


def check_form_submission(pages: dict, rule: dict) -> list[CheckResult]:
    """Actually submit forms using Playwright and verify they redirect to success pages.

    This is a more thorough test than check_form_success_pages - it fills forms
    with test data and submits them to verify the full flow works.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Playwright not available. Test form submission manually.",
        )]

    # Find all contact/inquiry forms (skip search forms, login forms, etc.)
    forms_to_test = []
    for url, page in pages.items():
        if not page.soup:
            continue
        for form in page.soup.find_all("form"):
            # Skip search forms
            if form.get("role") == "search":
                continue
            action = form.get("action", "").lower()
            if "search" in action or "login" in action or "subscribe" in action:
                continue

            # Look for contact/inquiry form indicators
            form_html = str(form).lower()
            is_contact_form = any(x in form_html for x in [
                "contact", "inquiry", "appointment", "request", "message",
                "name", "email", "phone", "comment", "question"
            ])

            # Check for required fields that suggest it's a contact form
            inputs = form.find_all(["input", "textarea", "select"])
            has_email = any("email" in (i.get("name", "") + i.get("type", "")).lower() for i in inputs)
            has_name = any("name" in i.get("name", "").lower() for i in inputs)

            # Skip forms with CAPTCHA (can't automate)
            has_captcha = any(x in form_html for x in [
                "recaptcha", "captcha", "hcaptcha", "g-recaptcha", "turnstile"
            ])

            # Skip complex forms with many required fields (new client forms, etc.)
            required_fields = [i for i in inputs if i.get("required") is not None
                              or "required" in i.get("class", [])]
            is_complex = len(required_fields) > 10

            if is_contact_form and (has_email or has_name):
                forms_to_test.append({
                    "url": url,
                    "form": form,
                    "inputs": inputs,
                    "has_captcha": has_captcha,
                    "is_complex": is_complex,
                    "required_count": len(required_fields),
                })

    if not forms_to_test:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="No contact/inquiry forms found to test",
        )]

    # Test forms using Playwright
    results = []
    forms_passed = 0
    forms_failed = []

    try:
        playwright_instance = sync_playwright().start()
        browser = playwright_instance.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )

        forms_skipped_captcha = []
        forms_skipped_complex = []

        for form_info in forms_to_test[:3]:  # Test up to 3 forms
            url = form_info["url"]
            form = form_info["form"]
            inputs = form_info["inputs"]
            short_url = url.split("//")[-1] if "//" in url else url

            # Skip CAPTCHA-protected forms
            if form_info.get("has_captcha"):
                forms_skipped_captcha.append(short_url)
                continue

            # Skip complex forms (new client forms with many fields)
            if form_info.get("is_complex"):
                forms_skipped_complex.append(f"{short_url} ({form_info.get('required_count', '?')} required fields)")
                continue

            try:
                context = browser.new_context(user_agent=USER_AGENT)
                pw_page = context.new_page()
                pw_page.goto(url, timeout=15000, wait_until="networkidle")

                # Fill form fields with test data
                test_data = {
                    "name": "QA Test User",
                    "first_name": "QA Test",
                    "last_name": "User",
                    "email": "qa-test@petdesk-scanner.test",
                    "phone": "555-000-0000",
                    "message": "This is an automated QA test submission. Please ignore.",
                    "comment": "Automated QA test - please ignore",
                    "comments": "Automated QA test - please ignore",
                }

                for inp in inputs:
                    inp_name = inp.get("name", "").lower()
                    inp_type = inp.get("type", "text").lower()
                    inp_tag = inp.name

                    if inp_type in ("submit", "hidden", "button"):
                        continue

                    # Find matching test data
                    value = None
                    for key, val in test_data.items():
                        if key in inp_name:
                            value = val
                            break

                    if value:
                        selector = f'[name="{inp.get("name")}"]'
                        try:
                            if inp_tag == "textarea":
                                pw_page.fill(selector, value)
                            elif inp_tag == "select":
                                # Select first non-empty option
                                pw_page.select_option(selector, index=1)
                            elif inp_type == "checkbox":
                                pw_page.check(selector)
                            elif inp_type == "email":
                                pw_page.fill(selector, test_data["email"])
                            else:
                                pw_page.fill(selector, value)
                        except Exception:
                            pass  # Field might not be visible/fillable

                # Find and click submit button
                submit_btn = None
                for selector in [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'button:has-text("Submit")',
                    'button:has-text("Send")',
                    'button:has-text("Request")',
                    '.submit-button',
                    '#submit',
                ]:
                    try:
                        if pw_page.locator(selector).count() > 0:
                            submit_btn = selector
                            break
                    except Exception:
                        continue

                if not submit_btn:
                    forms_failed.append(f"{url} - No submit button found")
                    context.close()
                    continue

                # Submit and wait for navigation or AJAX response
                original_url = pw_page.url
                try:
                    pw_page.click(submit_btn)
                    pw_page.wait_for_load_state("networkidle", timeout=10000)
                    # Extra wait for AJAX responses to render
                    pw_page.wait_for_timeout(2000)
                except Exception:
                    pass  # Some forms use AJAX, won't navigate

                # Check result
                new_url = pw_page.url.lower()
                page_content = pw_page.content().lower()

                # Success indicators
                success_indicators = [
                    "thank" in new_url,
                    "success" in new_url,
                    "confirmation" in new_url,
                    "thank you" in page_content,
                    "thanks for" in page_content,
                    "message sent" in page_content,
                    "we'll be in touch" in page_content,
                    "received your" in page_content,
                    "submission successful" in page_content,
                ]

                # Error indicators
                error_indicators = [
                    "error" in page_content and "required" in page_content,
                    "please fill" in page_content,
                    "invalid" in page_content,
                    "failed" in page_content,
                ]

                if any(success_indicators) and not any(error_indicators):
                    forms_passed += 1
                else:
                    short_url = url.split("//")[-1] if "//" in url else url
                    forms_failed.append(f"{short_url} - No success page/message after submission")

                context.close()

            except Exception as e:
                short_url = url.split("//")[-1] if "//" in url else url
                forms_failed.append(f"{short_url} - Error: {str(e)[:50]}")

        browser.close()
        playwright_instance.stop()

    except Exception as e:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details=f"Could not launch browser for form testing: {str(e)[:100]}",
        )]

    # Report results
    total_tested = forms_passed + len(forms_failed)
    total_skipped = len(forms_skipped_captcha) + len(forms_skipped_complex)

    # Build skip notes
    skip_notes = []
    if forms_skipped_captcha:
        skip_notes.append(f"Skipped {len(forms_skipped_captcha)} form(s) with CAPTCHA (test manually)")
    if forms_skipped_complex:
        skip_notes.append(f"Skipped {len(forms_skipped_complex)} complex form(s) with many fields (test manually)")
    skip_detail = "\n".join(skip_notes) if skip_notes else ""

    if total_tested == 0 and total_skipped > 0:
        # All forms were skipped
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details=f"All forms require manual testing:\n{skip_detail}",
        ))
    elif forms_failed:
        detail = f"{len(forms_failed)} of {total_tested} form(s) failed submission test:\n"
        detail += "\n".join(f"• {f}" for f in forms_failed)
        if skip_detail:
            detail += f"\n\n{skip_detail}"
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=detail,
            points_lost=rule["weight"],
        ))
    else:
        detail = f"All {total_tested} form(s) successfully redirect to thank-you/success pages"
        if skip_detail:
            detail += f"\n\n{skip_detail}"
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details=detail,
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


# Partner-specific check implementations
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
    # Dental/medical terms
    "gumline", "gumlines",
    # Wildlife/rehabilitation
    "rehabilitator", "rehabilitators",
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


def _languagetool_request_with_retry(text: str, max_retries: int = 3) -> dict | None:
    """Make a LanguageTool API request with exponential backoff retry.

    Returns the JSON response dict, or None if all retries failed.
    """
    base_delay = 1.0  # Start with 1 second delay

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                _LANGUAGETOOL_URL,
                data={"text": text, "language": "en-US"},
                timeout=30,
            )

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:  # Rate limited
                delay = base_delay * (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                print(f"    [Grammar] Rate limited, waiting {delay}s before retry...")
                time.sleep(delay)
                continue
            else:
                # Other error, retry with backoff
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)
                continue

        except requests.Timeout:
            delay = base_delay * (2 ** attempt)
            print(f"    [Grammar] Timeout, waiting {delay}s before retry...")
            time.sleep(delay)
            continue
        except Exception:
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)
            continue

    return None


def check_grammar_spelling(pages: dict, rule: dict) -> list[CheckResult]:
    """Check visible page text for grammar and spelling errors using LanguageTool API.

    Checks ALL pages with exponential backoff retry for rate limiting.
    Returns separate results for spelling (FAIL) and grammar (WARN).
    """
    spelling_issues = []
    grammar_issues = []
    pages_checked = 0
    pages_failed = 0

    # Check ALL pages (no limit)
    for url, page in pages.items():
        if not page.soup:
            continue

        soup_copy = BeautifulSoup(page.html, "lxml") if page.html else None
        if not soup_copy:
            continue

        text = _extract_visible_text(soup_copy)
        if not text or len(text) < 50:
            continue

        text = text[:10000]

        # Use retry wrapper with exponential backoff
        data = _languagetool_request_with_retry(text)

        if data is None:
            pages_failed += 1
            continue

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

    if not pages_checked:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details=f"Could not reach LanguageTool API after retries ({pages_failed} page(s) failed). "
                    "Check grammar and spelling manually.",
        )]

    results = []

    # Note about any pages that failed despite retries
    failed_note = f" ({pages_failed} page(s) failed API checks)" if pages_failed > 0 else ""

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
                    f"occurrences) across {pages_checked} page(s){failed_note}:\n{detail}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check="Spelling errors in visible page content",
            status="PASS", weight=rule["weight"],
            details=f"No spelling errors found across {pages_checked} page(s){failed_note}",
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
                    f"across {pages_checked} page(s){failed_note}:\n{detail}",
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
# NEW PARTNER-SPECIFIC AND ENHANCED CHECK FUNCTIONS
# =============================================================================

def check_cta_on_pages(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that CTAs appear on required pages (e.g., Home, About, Services)."""
    results = []
    required_pages = rule.get("required_pages", ["home", "about", "services"])
    missing_cta = []

    # Map page types to URL patterns
    page_patterns = {
        "home": ["/", ""],
        "about": ["about", "about-us"],
        "services": ["services", "our-services"],
        "reviews": ["reviews", "testimonials"],
        "aaha": ["aaha"],
        "faq": ["faq", "faqs", "frequently-asked"],
        "contact": ["contact", "contact-us"],
    }

    for page_type in required_pages:
        patterns = page_patterns.get(page_type.lower(), [page_type.lower()])
        found_page = None
        has_cta = False

        for url, page in pages.items():
            parsed = urllib.parse.urlparse(url)
            path = parsed.path.lower().strip("/")

            # Check if this URL matches the page type
            is_match = False
            if page_type == "home" and path in ("", "/"):
                is_match = True
            elif any(p in path for p in patterns):
                is_match = True

            if is_match and page.soup:
                found_page = url
                # Look for CTA buttons/links
                for el in page.soup.find_all(["a", "button"], class_=re.compile(r"cta|btn|button", re.I)):
                    text = el.get_text(strip=True).lower()
                    if any(w in text for w in ["book", "appointment", "schedule", "get started"]):
                        has_cta = True
                        break
                # Also check for links with appointment-related text
                if not has_cta:
                    for a in page.soup.find_all("a", href=True):
                        text = a.get_text(strip=True).lower()
                        if any(w in text for w in ["book appointment", "schedule", "make appointment"]):
                            has_cta = True
                            break
                break

        if found_page and not has_cta:
            missing_cta.append(page_type)

    if missing_cta:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"CTA missing on {len(missing_cta)} required page(s): {', '.join(missing_cta)}",
            points_lost=rule["weight"],
        ))
    else:
        results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details=f"CTAs found on all {len(required_pages)} required pages",
        ))
    return results


def check_nav_structure(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that navigation structure matches expected partner layout."""
    results = []
    expected_nav = rule.get("expected_nav", [])

    if not expected_nav:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="No expected navigation structure defined in rule config.",
        )]

    for url, page in pages.items():
        if not page.soup:
            continue

        # Find the main navigation
        nav = page.soup.find("nav") or page.soup.find(id=re.compile(r"menu|nav", re.I))
        if not nav:
            header = page.soup.find("header")
            if header:
                nav = header.find("ul") or header.find(class_=re.compile(r"menu|nav", re.I))

        if nav:
            # Extract top-level nav items
            nav_items = []
            for a in nav.find_all("a", href=True):
                text = a.get_text(strip=True)
                # Skip empty or very short items
                if text and len(text) > 1:
                    # Get only top-level items (not deep nested)
                    parent_li = a.find_parent("li")
                    if parent_li:
                        # Check if this is a top-level item
                        parent_ul = parent_li.find_parent("ul")
                        if parent_ul:
                            grandparent_li = parent_ul.find_parent("li")
                            if not grandparent_li or "sub" not in " ".join(parent_ul.get("class", [])).lower():
                                if text not in nav_items:
                                    nav_items.append(text)

            # Compare with expected nav (case-insensitive, partial match)
            found_items = []
            missing_items = []

            for expected in expected_nav:
                found = False
                for actual in nav_items:
                    if expected.lower() in actual.lower() or actual.lower() in expected.lower():
                        found = True
                        found_items.append(expected)
                        break
                if not found:
                    missing_items.append(expected)

            if missing_items:
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details=f"Missing nav items: {', '.join(missing_items)}. Found: {', '.join(nav_items[:10])}",
                    points_lost=rule["weight"],
                ))
            else:
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Navigation contains expected items: {', '.join(found_items)}",
                ))
            return results

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="WARN", weight=rule["weight"],
        details="Could not locate navigation menu to verify structure.",
    )]


def check_topbar_layout(pages: dict, rule: dict) -> list[CheckResult]:
    """Check top bar layout: Phone/Email on left, Download App/Pharmacy on right."""
    results = []

    for url, page in pages.items():
        if not page.soup:
            continue

        # Look for top bar / secondary menu
        topbar = None
        for selector in [
            page.soup.find(class_=re.compile(r"top[-_]?bar|secondary[-_]?menu|header[-_]?top", re.I)),
            page.soup.find(id=re.compile(r"top[-_]?bar|secondary", re.I)),
        ]:
            if selector:
                topbar = selector
                break

        if not topbar:
            # Try finding by common Divi patterns
            header = page.soup.find("header")
            if header:
                topbar = header.find(class_=re.compile(r"et[-_]?top", re.I))

        if topbar:
            topbar_text = topbar.get_text(separator=" ", strip=True).lower()
            topbar_html = str(topbar).lower()

            # Check for expected elements
            has_phone = bool(re.search(r"tel:|phone|\d{3}[-.\s]?\d{3}[-.\s]?\d{4}", topbar_html))
            has_email = bool(re.search(r"mailto:|email|@", topbar_html))
            has_app = "download" in topbar_text or "app" in topbar_text or "petdesk" in topbar_text
            has_pharmacy = "pharmacy" in topbar_text or "online store" in topbar_text

            issues = []
            if not has_phone:
                issues.append("phone number")
            if not has_email:
                issues.append("email")
            if not has_app:
                issues.append("Download App button")
            if not has_pharmacy:
                issues.append("Online Pharmacy/Store button")

            if issues:
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="WARN", weight=rule["weight"],
                    details=f"Top bar may be missing: {', '.join(issues)}. Verify layout manually.",
                ))
            else:
                results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Top bar contains phone, email, app download, and pharmacy links.",
                ))
            return results

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Could not locate top bar. Verify layout manually: Phone/Email left, App/Pharmacy right.",
    )]


def check_career_tracking_url(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that career page uses tracking URL (Jobvite, Workday, etc.) instead of form."""
    results = []
    tracking_domains = rule.get("tracking_domains", ["workday", "lever", "jobvite", "greenhouse", "icims"])

    for url, page in pages.items():
        if "career" in url.lower():
            if not page.soup:
                continue

            # Check for tracking URLs
            page_html = str(page.soup).lower()
            found_tracker = None

            for domain in tracking_domains:
                if domain in page_html:
                    found_tracker = domain
                    break

            # Check for iframes with tracking URLs
            for iframe in page.soup.find_all("iframe", src=True):
                src = iframe["src"].lower()
                for domain in tracking_domains:
                    if domain in src:
                        found_tracker = domain
                        break

            # Check if there's a Gravity Form (bad - should use tracking URL)
            has_gravity_form = "gform" in page_html or "gravity" in page_html

            if found_tracker:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Career page uses {found_tracker} tracking URL.",
                )]
            elif has_gravity_form:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details="Career page uses Gravity Form instead of applicant tracking URL.",
                    points_lost=rule["weight"],
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="WARN", weight=rule["weight"],
        details="Career page not found or tracking URL not detected. Verify manually.",
    )]


def check_photo_gallery_instructions(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that photo gallery page includes form submission instructions."""
    results = []
    instruction_patterns = [
        r"submit.*photo", r"upload.*photo", r"send.*photo",
        r"email.*photo", r"share.*photo", r"photo.*form",
        r"submit.*image", r"how to.*submit"
    ]

    for url, page in pages.items():
        if "gallery" in url.lower() or "photo" in url.lower():
            if not page.soup:
                continue

            page_text = page.soup.get_text(separator=" ", strip=True).lower()

            for pattern in instruction_patterns:
                if re.search(pattern, page_text):
                    return [CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="PASS", weight=rule["weight"],
                        details=f"Photo gallery page contains submission instructions.",
                    )]

            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="FAIL", weight=rule["weight"],
                details="Photo gallery page found but no submission instructions detected.",
                points_lost=rule["weight"],
            )]

    # No gallery page found - nothing to check, this is fine
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No photo gallery page on this site (not required).",
    )]


def check_team_page_structure(pages: dict, rule: dict) -> list[CheckResult]:
    """Check team page groups staff by role (Vets, Techs, Admin, etc.)."""
    results = []
    role_keywords = ["veterinarian", "doctor", "dvm", "technician", "cvt", "manager",
                     "administrative", "receptionist", "groomer", "team"]

    for url, page in pages.items():
        if any(x in url.lower() for x in ["team", "staff", "our-team", "doctors", "about"]):
            if not page.soup:
                continue

            page_text = page.soup.get_text(separator=" ", strip=True).lower()
            found_roles = [kw for kw in role_keywords if kw in page_text]

            if len(found_roles) >= 2:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Team page contains role groupings: {', '.join(found_roles[:5])}",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify team page groups staff by role (Veterinarians, Technicians, Admin, etc.).",
    )]


def check_review_content(pages: dict, rule: dict) -> list[CheckResult]:
    """Check review page content is appropriate (positive, no euthanasia, proper name format)."""
    results = []
    issues = []

    for url, page in pages.items():
        if "review" in url.lower() or "testimonial" in url.lower():
            if not page.soup:
                continue

            page_text = page.soup.get_text(separator=" ", strip=True).lower()

            # Check for euthanasia mentions
            if any(word in page_text for word in ["euthanasia", "put down", "put to sleep", "passed away"]):
                issues.append("Contains euthanasia-related content")

            # Check for negative indicators
            negative_words = ["terrible", "awful", "worst", "never again", "do not recommend", "horrible"]
            if any(word in page_text for word in negative_words):
                issues.append("May contain negative reviews")

            if issues:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details=f"Review page issues: {'; '.join(issues)}",
                    points_lost=rule["weight"],
                )]
            else:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Review page content appears appropriate (no euthanasia mentions, positive reviews).",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Review page not found. Verify reviews are positive and use first name + last initial.",
    )]


def check_booking_widget(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for Vetstoria or PetDesk booking widget integration."""
    results = []
    widget_patterns = ["vetstoria", "petdesk", "booking-widget", "appointment-widget"]

    for url, page in pages.items():
        if page.html:
            html_lower = page.html.lower()
            for pattern in widget_patterns:
                if pattern in html_lower:
                    return [CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="PASS", weight=rule["weight"],
                        details=f"Booking widget detected: {pattern}",
                    )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="WARN", weight=rule["weight"],
        details="No Vetstoria or PetDesk booking widget detected. Verify booking integration manually.",
    )]


def check_no_popups(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that no popups are implemented on the site."""
    results = []
    popup_indicators = ["popup", "modal", "lightbox", "overlay"]

    for url, page in pages.items():
        if page.html:
            html_lower = page.html.lower()
            # Check for popup scripts/plugins
            for indicator in popup_indicators:
                # Look for popup classes/IDs that suggest active popups
                if re.search(rf'class=["\'][^"\']*{indicator}[^"\']*["\']', html_lower):
                    # Check if it's a visible popup (not just hidden structure)
                    if "display:none" not in html_lower.replace(" ", ""):
                        return [CheckResult(
                            rule_id=rule["id"], category=rule["category"],
                            check=rule["check"], status="WARN", weight=rule["weight"],
                            details=f"Popup indicator found ({indicator}). Verify no active popups.",
                        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No obvious popup implementations detected.",
    )]


def check_meta_title_quality(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that meta titles are unique and appropriate length."""
    results = []
    titles = {}
    issues = []

    for url, page in pages.items():
        if not page.soup:
            continue
        title_tag = page.soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            # Check for duplicates
            if title in titles:
                issues.append(f"Duplicate title '{title[:50]}' on {url} and {titles[title]}")
            else:
                titles[title] = url
            # Check length
            if len(title) < 20:
                issues.append(f"Title too short on {url}: '{title}'")
            elif len(title) > 70:
                issues.append(f"Title too long on {url} ({len(title)} chars)")

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"{len(issues)} title issue(s):\n" + "\n".join(issues[:5]),
        )]
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"All {len(titles)} page titles are unique and appropriate length.",
    )]


def check_privacy_policy_verbiage(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that privacy policy uses updated verbiage (Western-specific)."""
    results = []
    # Key phrases that should be in updated privacy policy
    required_phrases = ["petdesk", "personal information", "privacy"]

    for url, page in pages.items():
        if "privacy" in url.lower():
            if not page.soup:
                continue
            page_text = page.soup.get_text(separator=" ", strip=True).lower()

            found_phrases = [p for p in required_phrases if p in page_text]

            if len(found_phrases) >= 2:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Privacy policy contains expected verbiage.",
                )]
            else:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
                    details="Privacy policy found but may need verbiage update. Compare with reference.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="Privacy policy page not found.",
        points_lost=rule["weight"],
    )]


# Partner-specific checks: Heartland
def check_sticky_header(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for sticky/fixed header on mobile."""
    for url, page in pages.items():
        if page.html:
            html_lower = page.html.lower()
            if any(x in html_lower for x in ["position:fixed", "position: fixed", "sticky", "et_fixed_nav"]):
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Sticky/fixed header detected.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Could not confirm sticky header. Test on mobile device.",
    )]


def check_service_card_layout(pages: dict, rule: dict) -> list[CheckResult]:
    """Check main service page uses card-style layout."""
    for url, page in pages.items():
        if "service" in url.lower() and page.soup:
            # Look for card-style classes
            card_indicators = page.soup.find_all(class_=re.compile(r"card|grid|column|et_pb_column", re.I))
            if len(card_indicators) >= 3:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Service page appears to use card/grid layout.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify main service page uses card-style layout.",
    )]


def check_no_mobile_popups(pages: dict, rule: dict) -> list[CheckResult]:
    """Check no popups on mobile (desktop OK)."""
    # This requires actual mobile testing - flag for review
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Test on mobile device: popups should be disabled on mobile (desktop OK).",
    )]


def check_footer_centered(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that footer content is centered."""
    for url, page in pages.items():
        if page.soup:
            footer = page.soup.find("footer")
            if footer:
                footer_style = str(footer.get("style", "")).lower()
                footer_class = " ".join(footer.get("class", [])).lower()
                footer_html = str(footer).lower()

                if any(x in footer_html for x in ["text-align:center", "text-align: center", "center"]):
                    return [CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="PASS", weight=rule["weight"],
                        details="Footer appears to be centered.",
                    )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify footer content is centered visually.",
    )]


# Partner-specific checks: United
def check_no_pet_prefix(pages: dict, rule: dict) -> list[CheckResult]:
    """Check service titles don't use 'Pet' prefix (United requirement)."""
    issues = []

    for url, page in pages.items():
        if "service" in url.lower() and page.soup:
            h1s = page.soup.find_all("h1")
            h2s = page.soup.find_all("h2")

            for heading in h1s + h2s:
                text = heading.get_text(strip=True)
                if text.lower().startswith("pet "):
                    issues.append(f"'{text}' on {url}")

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"Service titles with 'Pet' prefix: {'; '.join(issues[:3])}",
            points_lost=rule["weight"],
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="Service titles do not use 'Pet' prefix.",
    )]


def check_contact_form_placement(pages: dict, rule: dict) -> list[CheckResult]:
    """Check contact form is only on contact page (United requirement)."""
    forms_found = []

    for url, page in pages.items():
        if page.soup:
            footer = page.soup.find("footer")
            if footer:
                forms = footer.find_all("form")
                if forms:
                    forms_found.append(url)

    if forms_found:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"Contact form found in footer on {len(forms_found)} page(s). Should only be on contact page.",
            points_lost=rule["weight"],
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No contact forms found in page footers.",
    )]


# Partner-specific checks: Rarebreed
def check_jobvite_careers(pages: dict, rule: dict) -> list[CheckResult]:
    """Check careers page links to Jobvite."""
    for url, page in pages.items():
        if "career" in url.lower() and page.html:
            if "jobvite" in page.html.lower():
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Jobvite integration found on careers page.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="Jobvite not found on careers page.",
        points_lost=rule["weight"],
    )]


def check_service_pages_exist(pages: dict, rule: dict) -> list[CheckResult]:
    """Check that individual service pages exist (not just placeholders)."""
    service_pages = []

    for url, page in pages.items():
        if "/service" in url.lower() and page.soup:
            # Check if page has actual content (not just placeholder)
            body = page.soup.find("body")
            if body:
                text = body.get_text(strip=True)
                if len(text) > 200:  # Has substantial content
                    service_pages.append(url)

    if len(service_pages) >= 3:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details=f"Found {len(service_pages)} individual service pages with content.",
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="WARN", weight=rule["weight"],
        details=f"Only {len(service_pages)} service pages found. Verify individual pages are created.",
    )]


# Partner-specific checks: EverVet
def check_landing_page_links(pages: dict, rule: dict) -> list[CheckResult]:
    """Check landing page has minimal external links (EverVet)."""
    # This is highly specific - flag for review
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify landing page only links to: map, appointment, pharmacy.",
    )]


# Partner-specific checks: Encore
def check_lever_careers(pages: dict, rule: dict) -> list[CheckResult]:
    """Check careers page links to job.lever.co."""
    for url, page in pages.items():
        if "career" in url.lower() and page.html:
            if "lever.co" in page.html.lower() or "jobs.lever" in page.html.lower():
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Lever.co integration found on careers page.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="job.lever.co not found on careers page.",
        points_lost=rule["weight"],
    )]


# Partner-specific checks: AmeriVet
def check_workday_careers(pages: dict, rule: dict) -> list[CheckResult]:
    """Check careers page links to Workday recruiting."""
    for url, page in pages.items():
        if "career" in url.lower() and page.html:
            if "workday" in page.html.lower() or "myworkday" in page.html.lower():
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details="Workday integration found on careers page.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="FAIL", weight=rule["weight"],
        details="Workday not found on careers page.",
        points_lost=rule["weight"],
    )]


def check_service_column_layout(pages: dict, rule: dict) -> list[CheckResult]:
    """Check main services page uses expected column layout (AmeriVet: 3 columns)."""
    expected_columns = rule.get("expected_columns", 3)

    for url, page in pages.items():
        if "service" in url.lower() and page.soup:
            # Look for column structures
            columns = page.soup.find_all(class_=re.compile(r"et_pb_column_1_3|col-md-4|column.*third", re.I))
            if len(columns) >= expected_columns:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Services page appears to use {expected_columns}-column layout.",
                )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details=f"Verify services page uses {expected_columns}-column layout.",
    )]


def check_heading_structure(pages: dict, rule: dict) -> list[CheckResult]:
    """Check H1 is facility name, H2 has SEO keywords (AmeriVet)."""
    for url, page in pages.items():
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ("/", "") and page.soup:  # Homepage
            h1 = page.soup.find("h1")
            h2 = page.soup.find("h2")

            if h1 and h2:
                h1_text = h1.get_text(strip=True)
                h2_text = h2.get_text(strip=True)

                # H1 should be short (facility name)
                # H2 should be longer (overview with keywords)
                if len(h1_text) < 100 and len(h2_text) > 20:
                    return [CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="PASS", weight=rule["weight"],
                        details=f"H1: '{h1_text[:50]}...', H2 present with content.",
                    )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify H1 is facility name, H2 is brief overview with SEO keywords.",
    )]


def check_reviews_carousel(pages: dict, rule: dict) -> list[CheckResult]:
    """Check for reviews teaser/carousel below hero (AmeriVet)."""
    carousel_indicators = ["carousel", "slider", "testimonial", "review", "swiper"]

    for url, page in pages.items():
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ("/", "") and page.soup:  # Homepage
            body = str(page.soup).lower()
            for indicator in carousel_indicators:
                if indicator in body:
                    return [CheckResult(
                        rule_id=rule["id"], category=rule["category"],
                        check=rule["check"], status="PASS", weight=rule["weight"],
                        details=f"Reviews/testimonial section detected ({indicator}).",
                    )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Verify reviews teaser/carousel exists below hero with link to Reviews page.",
    )]


def check_responsive_cta_text(pages: dict, rule: dict) -> list[CheckResult]:
    """Check CTA text changes for responsive (AmeriVet: desktop vs mobile)."""
    # This requires actual responsive testing
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
        details="Test responsive CTA: Desktop should say 'Call xxx-xxx-xxxx', Mobile should say 'Call for appointment'.",
    )]


# =============================================================================
# NEW AUTOMATED CHECKS (formerly human review)
# =============================================================================

def check_responsive_viewports(pages: dict, rule: dict, crawler=None) -> list[CheckResult]:
    """
    Automated responsive testing using Playwright at multiple viewport sizes.
    Replaces HUMAN-016 (browser testing) and HUMAN-017 (tablet testing).
    """
    if not PLAYWRIGHT_AVAILABLE:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Playwright not available. Manually verify site displays correctly on desktop, tablet, and mobile.",
        )]

    # Get homepage URL
    homepage_url = None
    for url in pages.keys():
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ("/", ""):
            homepage_url = url
            break

    if not homepage_url:
        homepage_url = list(pages.keys())[0] if pages else None

    if not homepage_url:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="SKIP", weight=rule["weight"],
            details="No pages to test.",
        )]

    viewports = [
        {"name": "Desktop", "width": 1920, "height": 1080},
        {"name": "Tablet", "width": 768, "height": 1024},
        {"name": "Mobile", "width": 375, "height": 812},
    ]

    issues = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ],
            )

            for vp in viewports:
                try:
                    context = browser.new_context(
                        viewport={"width": vp["width"], "height": vp["height"]},
                        user_agent=USER_AGENT
                    )
                    page = context.new_page()
                    page.goto(homepage_url, timeout=30000)
                    page.wait_for_load_state("networkidle", timeout=15000)

                    # Check for horizontal overflow (common responsive issue)
                    has_overflow = page.evaluate("""
                        () => document.documentElement.scrollWidth > document.documentElement.clientWidth
                    """)

                    if has_overflow:
                        issues.append(f"{vp['name']} ({vp['width']}px): Horizontal scroll detected")

                    # Check if main content is visible
                    main_visible = page.evaluate("""
                        () => {
                            const main = document.querySelector('main, .main-content, #main, article, .et_pb_section');
                            if (!main) return true;  // No main element to check
                            const rect = main.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        }
                    """)

                    if not main_visible:
                        issues.append(f"{vp['name']} ({vp['width']}px): Main content not visible")

                    context.close()

                except Exception as e:
                    issues.append(f"{vp['name']}: Error testing - {str(e)[:50]}")

            browser.close()

    except Exception as e:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"Responsive test failed: {str(e)[:100]}. Manual verification recommended.",
        )]

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details="Responsive issues found:\n" + "\n".join(issues),
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="Site displays correctly at Desktop (1920px), Tablet (768px), and Mobile (375px) viewports.",
    )]


def check_map_location(pages: dict, rule: dict) -> list[CheckResult]:
    """
    Verify map iframe location matches the clinic address on the page.
    Replaces HUMAN-018 (map location correct).
    Uses Nominatim (OpenStreetMap) for free geocoding.
    """
    # Find pages with contact info or maps
    address_pattern = re.compile(
        r'\d+\s+[\w\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Circle|Cir|Plaza|Plz)[\s,]+[\w\s]+,?\s*[A-Z]{2}\s+\d{5}',
        re.IGNORECASE
    )

    map_coords = None
    page_address = None
    map_page_url = None

    for url, page in pages.items():
        if not page.soup:
            continue

        # Look for Google Maps iframe
        iframes = page.soup.find_all("iframe")
        for iframe in iframes:
            src = iframe.get("src", "")
            if "google.com/maps" in src or "maps.google" in src:
                # Extract coordinates from embed URL
                # Format: !2d-122.4194!3d37.7749 or q=lat,lng or center=lat,lng
                coord_match = re.search(r'!3d(-?\d+\.?\d*)!2d(-?\d+\.?\d*)', src)
                if coord_match:
                    map_coords = (float(coord_match.group(1)), float(coord_match.group(2)))
                    map_page_url = url
                else:
                    # Try q= or center= format
                    coord_match = re.search(r'(?:q=|center=)(-?\d+\.?\d*),(-?\d+\.?\d*)', src)
                    if coord_match:
                        map_coords = (float(coord_match.group(1)), float(coord_match.group(2)))
                        map_page_url = url

        # Look for address on page
        text = page.soup.get_text()
        addr_match = address_pattern.search(text)
        if addr_match:
            page_address = addr_match.group(0).strip()

    if not map_coords:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="No map iframe with coordinates found. Verify map location manually.",
        )]

    if not page_address:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"Map found but could not extract address from page. Map at: {map_coords}. Verify manually.",
        )]

    # Geocode the address using Nominatim (free, no API key)
    try:
        geocode_url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": page_address,
            "format": "json",
            "limit": 1
        }
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(geocode_url, params=params, headers=headers, timeout=10)

        if resp.status_code == 200 and resp.json():
            result = resp.json()[0]
            geocoded_lat = float(result["lat"])
            geocoded_lon = float(result["lon"])

            # Calculate distance (rough approximation)
            # 0.01 degree ≈ 1.1 km at equator
            lat_diff = abs(geocoded_lat - map_coords[0])
            lon_diff = abs(geocoded_lon - map_coords[1])

            # Allow ~2km tolerance (0.02 degrees)
            if lat_diff < 0.02 and lon_diff < 0.02:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="PASS", weight=rule["weight"],
                    details=f"Map location matches address: {page_address[:50]}...",
                )]
            else:
                return [CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="FAIL", weight=rule["weight"],
                    details=f"Map location may be incorrect. Address: {page_address[:50]}. Map coords: {map_coords}, Expected: ({geocoded_lat:.4f}, {geocoded_lon:.4f})",
                    page_url=map_page_url,
                )]
        else:
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="WARN", weight=rule["weight"],
                details=f"Could not geocode address: {page_address[:50]}. Verify map manually.",
            )]

    except Exception as e:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"Geocoding failed: {str(e)[:50]}. Verify map location manually.",
        )]


def _analyze_image_with_ai(image_bytes: bytes, prompt: str) -> str:
    """Helper to analyze an image using Gemini (primary) or Anthropic (fallback)."""
    import base64
    from google.genai import types

    if AI_PROVIDER == "gemini":
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                prompt,
            ]
        )
        return response.text.strip()

    elif AI_PROVIDER == "anthropic":
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        return response.content[0].text.strip()

    return None


def check_image_appropriateness(pages: dict, rule: dict) -> list[CheckResult]:
    """
    AI-powered check for inappropriate images on sensitive pages.
    Replaces HUMAN-002 (image appropriateness for euthanasia/end-of-life pages).
    Uses Gemini Vision API (primary) or Claude Vision API (fallback).
    """
    if not AI_PROVIDER:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="AI image analysis not available. Manually check images on sensitive pages (euthanasia, end-of-life) for appropriateness.",
        )]

    # Find sensitive pages
    sensitive_keywords = ["euthanasia", "end-of-life", "end of life", "goodbye", "memorial", "loss", "grief", "compassionate"]
    sensitive_pages = []

    for url, page in pages.items():
        url_lower = url.lower()
        if any(kw in url_lower for kw in sensitive_keywords):
            sensitive_pages.append((url, page))
            continue
        if page.soup:
            title = page.soup.find("title")
            if title and any(kw in title.get_text().lower() for kw in sensitive_keywords):
                sensitive_pages.append((url, page))

    if not sensitive_pages:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="No sensitive pages (euthanasia/end-of-life) detected requiring image review.",
        )]

    issues = []
    checked_images = 0

    prompt = """This image is on a veterinary clinic's euthanasia/end-of-life page.
Analyze if this image is appropriate. Flag as INAPPROPRIATE if it shows:
- Real pets that appear deceased or dying
- Graphic medical imagery (needles, syringes, blood)
- Distressing imagery that could upset grieving pet owners

Stock photos of peaceful pets, nature scenes, candles, or abstract comfort imagery are APPROPRIATE.

Respond with only: APPROPRIATE or INAPPROPRIATE: [brief reason]"""

    for url, page in sensitive_pages[:3]:  # Limit to 3 pages to control API costs
        if not page.soup:
            continue

        images = page.soup.find_all("img")
        for img in images[:5]:  # Limit to 5 images per page
            src = img.get("src", "")
            if not src or "placeholder" in src.lower() or "icon" in src.lower():
                continue

            # Make absolute URL
            if src.startswith("/"):
                parsed = urllib.parse.urlparse(url)
                src = f"{parsed.scheme}://{parsed.netloc}{src}"
            elif not src.startswith("http"):
                continue

            try:
                # Fetch image
                img_resp = requests.get(src, timeout=10, headers={"User-Agent": USER_AGENT})
                if img_resp.status_code != 200:
                    continue

                content_type = img_resp.headers.get("content-type", "")
                if "image" not in content_type:
                    continue

                # Use AI helper to analyze image
                result_text = _analyze_image_with_ai(img_resp.content, prompt)
                if result_text is None:
                    continue

                checked_images += 1

                if result_text.startswith("INAPPROPRIATE"):
                    issues.append(f"{url}: {src[:50]}... - {result_text}")

            except Exception as e:
                # Skip failed images silently
                continue

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"Potentially inappropriate images on sensitive pages:\n" + "\n".join(issues),
        )]

    if checked_images == 0:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details=f"Found {len(sensitive_pages)} sensitive page(s) but could not analyze images. Manual review needed.",
        )]

    ai_name = "Gemini" if AI_PROVIDER == "gemini" else "Claude"
    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"[{ai_name} Vision] Analyzed {checked_images} images on {len(sensitive_pages)} sensitive page(s). All images appear appropriate.",
    )]


def check_visual_consistency(pages: dict, rule: dict) -> list[CheckResult]:
    """
    AI-powered check for visual consistency (alignment, spacing, colors).
    Replaces HUMAN-003 (visual consistency across site).
    Uses Claude Vision API to analyze page screenshots.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Playwright not available for screenshots. Manually verify visual consistency.",
        )]

    if not AI_PROVIDER:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="AI analysis not available. Manually verify alignment, spacing, and color consistency.",
        )]

    # Get homepage URL
    homepage_url = None
    for url in pages.keys():
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ("/", ""):
            homepage_url = url
            break

    if not homepage_url:
        homepage_url = list(pages.keys())[0] if pages else None

    if not homepage_url:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="SKIP", weight=rule["weight"],
            details="No pages to analyze.",
        )]

    try:
        # Take screenshot
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT
            )
            page = context.new_page()
            page.goto(homepage_url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            # Take full page screenshot
            screenshot = page.screenshot(full_page=False)  # Above-fold only for speed
            browser.close()

        prompt = """Analyze this veterinary clinic website screenshot for visual consistency issues.

Check for:
1. Alignment problems (elements not aligned properly)
2. Inconsistent spacing (uneven margins/padding)
3. Color mismatches (elements that don't match the color scheme)
4. Text overflow or truncation issues
5. Overlapping elements

If the page looks professionally designed with consistent alignment, spacing, and colors, respond: PASS

If there are noticeable issues, respond: ISSUES: [list specific problems]

Be concise. Only flag clear, noticeable problems - not minor variations."""

        result_text = _analyze_image_with_ai(screenshot, prompt)

        if not result_text:
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
                details="AI analysis returned no result. Manual review needed.",
            )]

        ai_name = "Gemini" if AI_PROVIDER == "gemini" else "Claude"
        if result_text.startswith("PASS"):
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details=f"[{ai_name} Vision] Homepage shows consistent alignment, spacing, and color usage.",
            )]
        elif result_text.startswith("ISSUES"):
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="WARN", weight=rule["weight"],
                details=f"[{ai_name} Vision] Found potential issues: {result_text}",
                page_url=homepage_url,
            )]
        else:
            return [CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="PASS", weight=rule["weight"],
                details=f"[{ai_name} Vision] {result_text[:100]}",
            )]

    except Exception as e:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details=f"Visual analysis failed: {str(e)[:50]}. Manual review needed.",
        )]


def check_branding_consistency(pages: dict, rule: dict) -> list[CheckResult]:
    """
    Check for consistent branding: fonts not default Divi, button colors consistent.
    Consolidates HUMAN-005, HUMAN-006, HUMAN-010.
    """
    # Default Divi fonts to flag
    default_divi_fonts = ["open sans", "raleway", "roboto"]

    issues = []
    fonts_found = set()
    button_colors = set()

    for url, page in pages.items():
        if not page.soup:
            continue

        # Check for font declarations in style tags
        styles = page.soup.find_all("style")
        for style in styles:
            text = style.get_text().lower()
            for font in default_divi_fonts:
                if f"font-family" in text and font in text:
                    fonts_found.add(font)

        # Check inline styles
        elements_with_font = page.soup.find_all(style=re.compile(r"font-family", re.I))
        for el in elements_with_font:
            style = el.get("style", "").lower()
            for font in default_divi_fonts:
                if font in style:
                    fonts_found.add(font)

        # Check button colors
        buttons = page.soup.find_all(class_=re.compile(r"button|btn|cta", re.I))
        for btn in buttons[:10]:  # Limit checks
            style = btn.get("style", "")
            bg_match = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,6}|rgb[^)]+\))", style)
            if bg_match:
                button_colors.add(bg_match.group(1).lower())

    # Analyze results
    if fonts_found:
        font_list = ', '.join(fonts_found)
        issues.append(f"Default Divi fonts detected ({font_list}). These should be replaced with the clinic's brand fonts in Divi Theme Options > General > Typography.")

    # Too many different button colors suggests inconsistency
    if len(button_colors) > 3:
        issues.append(f"Found {len(button_colors)} different button colors ({', '.join(list(button_colors)[:4])}...). Buttons should use consistent brand colors.")

    if issues:
        # Create a clear headline for each issue type
        if fonts_found and len(button_colors) <= 3:
            headline = f"Default Divi fonts found: {', '.join(fonts_found)}"
        elif len(button_colors) > 3 and not fonts_found:
            headline = f"Inconsistent button colors ({len(button_colors)} different colors)"
        else:
            headline = "Branding inconsistencies detected"

        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=headline, status="WARN", weight=rule["weight"],
            details="\n".join(issues),
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details="No default Divi fonts detected. Button colors appear consistent.",
    )]


def check_no_medical_equipment(pages: dict, rule: dict) -> list[CheckResult]:
    """
    AI-powered check for medical equipment in photos (WVP-011).
    Flags images showing syringes, gloves, or inappropriate medical gear.
    """
    if not AI_PROVIDER:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="AI image analysis not available. Manually check photos for syringes, gloves, or medical equipment.",
        )]

    issues = []
    checked_images = 0

    prompt = """Analyze this veterinary clinic website image.
Check if it shows any of these inappropriate items:
- Syringes or needles
- Medical gloves (latex/nitrile)
- Bloody or graphic medical scenes
- Surgical instruments visible in concerning way

If the image shows pets, staff, facilities, or general veterinary care without concerning medical equipment visible, respond: APPROPRIATE

If concerning medical equipment is prominently visible, respond: INAPPROPRIATE: [describe what's visible]

Be lenient - normal exam room backgrounds are fine. Only flag prominent/concerning items."""

    for url, page in list(pages.items())[:5]:  # Limit pages
        if not page.soup:
            continue

        images = page.soup.find_all("img")
        for img in images[:8]:  # Limit images per page
            src = img.get("src", "")
            if not src or "icon" in src.lower() or "logo" in src.lower() or len(src) < 10:
                continue

            # Make absolute URL
            if src.startswith("/"):
                parsed = urllib.parse.urlparse(url)
                src = f"{parsed.scheme}://{parsed.netloc}{src}"
            elif not src.startswith("http"):
                continue

            try:
                img_resp = requests.get(src, timeout=10, headers={"User-Agent": USER_AGENT})
                if img_resp.status_code != 200 or "image" not in img_resp.headers.get("content-type", ""):
                    continue

                result_text = _analyze_image_with_ai(img_resp.content, prompt)
                if result_text is None:
                    continue

                checked_images += 1
                if result_text.startswith("INAPPROPRIATE"):
                    issues.append(f"{url}: {src[-50:]} - {result_text}")

            except Exception:
                continue

    ai_name = "Gemini" if AI_PROVIDER == "gemini" else "Claude"

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="FAIL", weight=rule["weight"],
            details=f"[{ai_name} Vision] Found medical equipment in photos:\n" + "\n".join(issues[:5]),
        )]

    if checked_images == 0:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Could not analyze images. Manual review needed.",
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"[{ai_name} Vision] Checked {checked_images} images. No inappropriate medical equipment found.",
    )]


def check_stock_imagery_euthanasia(pages: dict, rule: dict) -> list[CheckResult]:
    """
    AI-powered check that euthanasia/end-of-life pages use stock imagery only (WVP-013).
    Flags real pet photos on sensitive pages.
    """
    if not AI_PROVIDER:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="AI image analysis not available. Manually verify euthanasia pages use stock imagery only.",
        )]

    # Find euthanasia/end-of-life pages
    sensitive_keywords = ["euthanasia", "end-of-life", "end of life", "goodbye", "memorial", "loss", "grief"]
    sensitive_pages = []

    for url, page in pages.items():
        url_lower = url.lower()
        if any(kw in url_lower for kw in sensitive_keywords):
            sensitive_pages.append((url, page))
            continue
        if page.soup:
            title = page.soup.find("title")
            if title and any(kw in title.get_text().lower() for kw in sensitive_keywords):
                sensitive_pages.append((url, page))

    if not sensitive_pages:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="PASS", weight=rule["weight"],
            details="No euthanasia/end-of-life pages found requiring stock imagery check.",
        )]

    issues = []
    checked_images = 0

    prompt = """This image is on a veterinary euthanasia/end-of-life memorial page.

Determine if this appears to be:
1. STOCK PHOTO - Professional studio lighting, generic/posed pets, watermark remnants, overly perfect composition
2. REAL PET PHOTO - Casual setting, personal/candid shot, specific identifiable pet, home environment

Stock photos, nature scenes, candles, abstract comfort imagery, and illustrations are APPROPRIATE.
Real photos of specific pets that appear to be client-submitted memorial photos are INAPPROPRIATE for this context.

Respond: STOCK (appropriate) or REAL_PET: [brief reason why it appears to be a real pet photo]"""

    for url, page in sensitive_pages[:2]:  # Limit to 2 pages
        if not page.soup:
            continue

        images = page.soup.find_all("img")
        for img in images[:5]:
            src = img.get("src", "")
            if not src or "icon" in src.lower() or "logo" in src.lower():
                continue

            if src.startswith("/"):
                parsed = urllib.parse.urlparse(url)
                src = f"{parsed.scheme}://{parsed.netloc}{src}"
            elif not src.startswith("http"):
                continue

            try:
                img_resp = requests.get(src, timeout=10, headers={"User-Agent": USER_AGENT})
                if img_resp.status_code != 200 or "image" not in img_resp.headers.get("content-type", ""):
                    continue

                result_text = _analyze_image_with_ai(img_resp.content, prompt)
                if result_text is None:
                    continue

                checked_images += 1
                if result_text.startswith("REAL_PET"):
                    issues.append(f"{url}: {src[-40:]} - {result_text}")

            except Exception:
                continue

    ai_name = "Gemini" if AI_PROVIDER == "gemini" else "Claude"

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"[{ai_name} Vision] Possible real pet photos on euthanasia pages (should use stock only):\n" + "\n".join(issues[:3]),
        )]

    if checked_images == 0 and sensitive_pages:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details=f"Found {len(sensitive_pages)} euthanasia page(s) but could not analyze images.",
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"[{ai_name} Vision] Checked {checked_images} images on {len(sensitive_pages)} euthanasia page(s). All appear to be appropriate stock imagery.",
    )]


def check_image_cropping(pages: dict, rule: dict) -> list[CheckResult]:
    """
    AI-powered check for image cropping issues (HUMAN-015).
    Verifies subjects are properly visible and not cut off.
    """
    if not AI_PROVIDER:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="AI image analysis not available. Manually check image cropping on all devices.",
        )]

    issues = []
    checked_images = 0

    prompt = """Analyze this website image for cropping problems.

Check if:
1. Main subjects (people, pets, buildings) are fully visible or appropriately framed
2. Important elements are not awkwardly cut off at edges
3. Faces/heads are not cropped at unfortunate points
4. The composition looks intentional, not accidentally cropped

If the image is well-cropped with subjects properly visible, respond: GOOD

If there are obvious cropping problems, respond: CROPPING_ISSUE: [describe the problem, e.g., "person's head cut off at forehead", "pet's body truncated awkwardly"]

Be lenient - artistic crops and intentional close-ups are fine. Only flag obvious problems."""

    # Check hero images and featured images on key pages
    for url, page in list(pages.items())[:5]:
        if not page.soup:
            continue

        # Focus on larger/featured images
        images = page.soup.find_all("img")
        for img in images[:6]:
            src = img.get("src", "")
            # Skip tiny images, icons
            if not src or "icon" in src.lower() or "logo" in src.lower():
                continue

            if src.startswith("/"):
                parsed = urllib.parse.urlparse(url)
                src = f"{parsed.scheme}://{parsed.netloc}{src}"
            elif not src.startswith("http"):
                continue

            try:
                img_resp = requests.get(src, timeout=10, headers={"User-Agent": USER_AGENT})
                if img_resp.status_code != 200 or "image" not in img_resp.headers.get("content-type", ""):
                    continue

                result_text = _analyze_image_with_ai(img_resp.content, prompt)
                if result_text is None:
                    continue

                checked_images += 1
                if result_text.startswith("CROPPING_ISSUE"):
                    issues.append(f"{url}: {result_text}")

            except Exception:
                continue

    ai_name = "Gemini" if AI_PROVIDER == "gemini" else "Claude"

    if issues:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="WARN", weight=rule["weight"],
            details=f"[{ai_name} Vision] Potential image cropping issues:\n" + "\n".join(issues[:5]),
        )]

    if checked_images == 0:
        return [CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Could not analyze images for cropping. Manual review needed.",
        )]

    return [CheckResult(
        rule_id=rule["id"], category=rule["category"],
        check=rule["check"], status="PASS", weight=rule["weight"],
        details=f"[{ai_name} Vision] Checked {checked_images} images. No obvious cropping issues found.",
    )]


# =============================================================================
# CHECK FUNCTION REGISTRY
# =============================================================================

CHECK_FUNCTIONS = {
    # Universal checks
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
    "check_form_success_pages": check_form_success_pages,
    "check_form_submission": check_form_submission,
    "check_mobile_responsive": check_mobile_responsive,
    "check_featured_images": check_featured_images,
    "check_contrast": check_contrast,
    "check_lighthouse": check_lighthouse,
    "check_grammar_spelling": check_grammar_spelling,
    "check_broken_images": check_broken_images,
    "check_open_graph": check_open_graph,
    "check_mixed_content": check_mixed_content,
    "check_meta_title_quality": check_meta_title_quality,
    # Content quality checks
    "check_long_text_blocks": check_long_text_blocks,
    "check_outcome_promises": check_outcome_promises,
    "check_internal_links": check_internal_links,
    # Partner-specific: Western
    "check_cta_text": check_cta_text,
    "check_cta_on_pages": check_cta_on_pages,
    "check_nav_structure": check_nav_structure,
    "check_topbar_layout": check_topbar_layout,
    "check_h1_no_welcome": check_h1_no_welcome,
    "check_birdeye_widget": check_birdeye_widget,
    "check_service_count": check_service_count,
    "check_new_client_form": check_new_client_form,
    "check_privacy_policy_verbiage": check_privacy_policy_verbiage,
    "check_no_appt_cta_euthanasia": check_no_appt_cta_euthanasia,
    "check_career_tracking_url": check_career_tracking_url,
    "check_team_page_structure": check_team_page_structure,
    "check_review_content": check_review_content,
    "check_photo_gallery_instructions": check_photo_gallery_instructions,
    "check_booking_widget": check_booking_widget,
    "check_no_popups": check_no_popups,
    # Partner-specific: Independent
    "check_faq_no_hours": check_faq_no_hours,
    # Partner-specific: Heartland
    "check_sticky_header": check_sticky_header,
    "check_service_card_layout": check_service_card_layout,
    "check_no_mobile_popups": check_no_mobile_popups,
    "check_footer_centered": check_footer_centered,
    # Partner-specific: United
    "check_no_pet_prefix": check_no_pet_prefix,
    "check_contact_form_placement": check_contact_form_placement,
    # Partner-specific: Rarebreed
    "check_jobvite_careers": check_jobvite_careers,
    "check_service_pages_exist": check_service_pages_exist,
    # Partner-specific: EverVet
    "check_landing_page_links": check_landing_page_links,
    # Partner-specific: Encore
    "check_lever_careers": check_lever_careers,
    # Partner-specific: AmeriVet
    "check_workday_careers": check_workday_careers,
    "check_service_column_layout": check_service_column_layout,
    "check_heading_structure": check_heading_structure,
    "check_reviews_carousel": check_reviews_carousel,
    "check_responsive_cta_text": check_responsive_cta_text,
    # New automated checks (formerly human review)
    "check_responsive_viewports": check_responsive_viewports,
    "check_map_location": check_map_location,
    "check_image_appropriateness": check_image_appropriateness,
    "check_visual_consistency": check_visual_consistency,
    "check_branding_consistency": check_branding_consistency,
    # AI-powered photo checks (Western + universal)
    "check_no_medical_equipment": check_no_medical_equipment,
    "check_stock_imagery_euthanasia": check_stock_imagery_euthanasia,
    "check_image_cropping": check_image_cropping,
}

# Import WordPress API check functions and merge
try:
    from wp_api import WP_CHECK_FUNCTIONS
    CHECK_FUNCTIONS.update(WP_CHECK_FUNCTIONS)
except ImportError:
    pass  # wp_api not available
