# Zero-Touch QA

Automated website QA scanner for PetDesk veterinary clinic websites built on WordPress/Divi. Hackathon submission for the Automation & AI track (deadline: February 6, 2026).

## What This Does

Scans a live veterinary clinic website against the same QA checklist rules that human QA specialists check manually. Produces a scored pass/fail report with specific failures, warnings, and a checklist of items that still need human judgment. Includes grammar/spelling checks via LanguageTool API, broken image detection, Open Graph social sharing validation, mixed content security checks, AI-powered image analysis and visual consistency checks, and automated responsive viewport testing.

## Architecture

```
User (browser)  or  Wrike (webhook)
         \              /
          \            /
           app.py (Flask web server)
              |
     +--------+--------+----------+----------+
     |                 |           |          |
qa_rules.py     qa_scanner.py    db.py    wp_api.py
(122 rules)     (74 checks +     (PostgreSQL (5 WP checks,
                 crawling +       persistence) plugin client)
                 AI vision)
                       |
                 qa_report.py
                 (HTML + JSON output)
```

## Web App Pages

| Route | Purpose |
|-------|---------|
| `/` | Scanner home page. Paste a URL, pick Partner and Phase, click Run QA Scan. |
| `/rules` | Browse all QA rules. Filter by partner and build phase. See each rule's category, weight, and whether it's automated or human-review. |
| `/rules/edit` | Add, delete, or modify QA rules through the browser. Guided UI explains what non-coders can do: "Search for text" rules and human review checklist items. |
| `/history` | View all past scans with scores, dates, and links to full HTML reports. Includes search by URL/scan ID, filter by partner/phase/score, sortable columns, and summary stats. |
| `/reports/<filename>` | View a specific scan report (served from database or filesystem). |
| `/api/scan` | API endpoint (POST) for running scans programmatically. |
| `/webhook/wrike` | Wrike webhook endpoint for automated scan triggering (future). |
| `/admin/clear-history` | Admin endpoint (POST) to clear all scan history. Requires `ADMIN_KEY` env var and `X-Admin-Key` header. |

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask web app. Main entry point. Serves the browser UI with Scanner, Rules, and History pages. Handles `/api/scan` for scans and `/webhook/wrike` for Wrike automation. Orchestrates scan memory lifecycle: parallel checks → pre-extract → free soups → PSI Playwright fallback (if needed) → sequential browser checks with restarts. Includes `psutil` memory instrumentation. Limits concurrent scans to 1 via semaphore. |
| `db.py` | PostgreSQL database layer. Stores scan results, reports, and scan IDs. Falls back gracefully when `DATABASE_URL` is not set (local dev uses filesystem). |
| `qa_rules.py` | Rule engine. Loads rules from `rules.json`. Each rule has an ID, category, phase, weight, partner scope, and a pointer to its check function. Call `get_rules_for_scan(partner, phase)` to get applicable rules. |
| `qa_scanner.py` | Site crawler and 74 check functions. `SiteCrawler` fetches pages via `requests` with connection pooling (`HTTPAdapter`), with optional Playwright fallback for JS-rendered content and sitemap-based fallback when BFS finds too few pages. Handles WP Engine staging domain mismatches (rewrites sitemap/logo URLs). `CHECK_FUNCTIONS` maps rule names to functions (merged with 5 WordPress checks from wp_api.py = 79 total). Integrates with Google PageSpeed Insights API (with retry/backoff and Playwright-based local fallback), LanguageTool API (grammar/spelling), and Gemini 2.5 Flash Vision API (AI image analysis, with adaptive throttle and thread-safe rate limiting). |
| `wp_api.py` | WordPress API clients for back-end checks. `PetDeskQAPluginClient` (recommended) uses the PetDesk QA Connector plugin with a shared API key. `WordPressAPIClient` (fallback) uses Application Password auth. Both verify plugin/theme updates, timezone, media cleanup, and form notifications. |
| `petdesk-qa-plugin/` | WordPress plugin directory containing `petdesk-qa-connector.php`. Install on sites to enable automated back-end checks. |
| `petdesk-qa-plugin.zip` | Zipped plugin ready for upload via WordPress admin > Plugins > Add New > Upload Plugin. |
| `qa_report.py` | Report generators. `generate_html_report()` produces a polished HTML report with PetDesk brand assets, SVG score ring, colored section banners, collapsible detail lists, interactive human review checklist (Pass/Fail/N/A buttons + comments), a non-printing toolbar (Copy URL, Save as PDF, navigation), and print-friendly layout. `generate_wrike_comment()` produces Wrike-formatted HTML. `generate_json_report()` produces JSON for audit trail. |
| `rules.json` | All QA rules as JSON data. Editable via `/rules/edit` in the web app. Contains universal rules and partner-specific overlays. |
| `run_qa.py` | CLI fallback for testing. Not the primary interface. |
| `proposal.html` | Professional HTML presentation (18 slides) for hackathon submission with embedded screenshots of the web interface. Print to PDF from browser. Uses PetDesk template colors and fonts. |
| `_build_proposal.py` | Script to regenerate `proposal.html`. Run `python _build_proposal.py` if slides need updating. Loads brand assets and screenshot PNGs as base64. |
| `PROPOSAL.md` | Markdown version of the hackathon proposal. |
| `Petdesk Logo.png` | PetDesk logo (purple text, high-res) for white backgrounds. Base64-embedded in reports and web UI. |
| `Petdesk Logo White Text.png` | PetDesk logo (white text, high-res) for dark/purple backgrounds. Used in report headers and dark proposal slides. |
| `Petdesk background purple.png` | PetDesk brand purple diamond texture background. Used in report headers, web UI headers, and dark proposal slides. |
| `screenshot_scanner.png` | Screenshot of the scanner home page. Embedded in proposal slide 8. |
| `screenshot_report.png` | Screenshot of a QA report. Embedded in proposal slide 10. |
| `screenshot_history.png` | Screenshot of the scan history page. Embedded in proposal slide 13. |
| `screenshot_rules.png` | Screenshot of the rules viewer page. Embedded in proposal slide 11. |
| `demo_test_site.html/json` | Demo scan of the test site (thestorybookal). HTML report + JSON audit trail. |
| `demo_final_site.html/json` | Demo scan of the final site (essentialsfina). HTML report + JSON audit trail. |
| `.env` | Credentials and API keys. Never committed. |
| `.gitignore` | Excludes `.env`, `__pycache__`, `reports/`, `*.xlsx`, `*.pdf`, `*.pptx`, and temp files from version control. |
| `requirements.txt` | Python dependencies including gunicorn, playwright, and psycopg2-binary for production deployment. |
| `Dockerfile` | Container config for cloud deployment. Uses `playwright install --with-deps chromium` for proper browser installation with all system dependencies. |
| `render.yaml` | Render.com deployment configuration. Uses Docker runtime. Includes PostgreSQL database service definition. |
| `Procfile` | Heroku/Railway-compatible process file. |

## Cloud Deployment

**Live app**: https://zero-touch-qa.onrender.com
**GitHub repo**: https://github.com/jonathanpimperton/zero-touch-qa (public)

The app is deployed on Render.com (free tier) via Docker. Pushing to `main` on GitHub triggers an automatic redeploy. Environment variables (`PSI_API_KEY`) are set in the Render dashboard. The `render.yaml` also defines a free PostgreSQL database (`zero-touch-qa-db`) that Render provisions automatically and links via `DATABASE_URL`.

Note: The free tier spins down after ~15 minutes of inactivity. First request after sleep takes ~50 seconds to wake up, then runs normally. The free PostgreSQL tier expires after 90 days — upgrade to $7/month for permanent persistence.

### Render.com setup (already done)
1. GitHub repo connected to Render.com
2. Render reads `render.yaml` and builds from `Dockerfile`
3. PostgreSQL database provisioned automatically via `render.yaml`
4. Environment variables set in Render dashboard: `PSI_API_KEY`
5. `DATABASE_URL` auto-linked from the database service
6. Auto-deploys on every push to `main`

### Docker (any cloud)
```
docker build -t zero-touch-qa .
docker run -p 5000:5000 --env-file .env zero-touch-qa
```

### Local development
```
pip install -r requirements.txt
python app.py
```

Local development works without PostgreSQL — when `DATABASE_URL` is not set, the app falls back to filesystem storage in `reports/`.

## Production Deployment (Recommended)

For production use beyond the hackathon, **Google Cloud Run + Firestore** is recommended over Render:

| Factor | Render (Hackathon) | Google Cloud (Production) |
|--------|-------------------|---------------------------|
| Platform | Render.com | Cloud Run |
| Database | Render PostgreSQL | Firestore or Cloud SQL |
| Cost | $14/mo (Web $7 + DB $7) | ~$0-5/mo (free tier eligible) |
| Identity | Separate login | Google Workspace SSO |
| Data residency | Third-party infrastructure | PetDesk's GCP project |
| Compliance | SOC 2 | SOC 2, HIPAA, ISO 27001 |
| Vendor agreements | New third party | Already in place |

**Security benefits of Google Cloud:**
- Integrates with existing Google Workspace identity management
- Keeps scan data within PetDesk's GCP boundary
- Granular IAM roles and VPC networking
- Cloud Audit Logs for compliance
- No new vendor data handling agreements needed

**Migration path:** The Flask app runs on Cloud Run with minimal changes. Firestore would require schema changes (NoSQL), or use Cloud SQL for drop-in PostgreSQL compatibility.

## Database Persistence

Scan results are stored in PostgreSQL (when `DATABASE_URL` is set) for persistence across deploys. The database layer (`db.py`) provides:

- **`scans` table** — stores scan metadata, full HTML report, and JSON audit trail for every scan
- **`scan_id_map` table** — maps site+phase combinations to scan IDs (QA-0001, etc.) using a PostgreSQL SEQUENCE for atomic ID generation
- **Automatic fallback** — all `db_*` functions return `None` when no database is configured, and `app.py` falls through to filesystem logic
- **One-time seed** — on first startup with a database, existing filesystem reports are automatically imported via `db_seed_from_filesystem()`

Key functions in `db.py`:
| Function | Purpose |
|----------|---------|
| `init_db()` | Creates tables/sequence if they don't exist. Called at startup. |
| `db_get_scan_id(site_url, phase)` | Gets or creates a scan ID using PostgreSQL SEQUENCE. |
| `db_save_scan(meta, html, json)` | Inserts a scan row with full report content. |
| `db_load_scan_history()` | Loads all scans for the history page. |
| `db_get_report(filename, type)` | Fetches HTML or JSON report by filename. |
| `db_seed_from_filesystem(reports_dir)` | One-time import of existing files into DB. |
| `db_save_human_review(filename, idx, rule_id, decision, comments)` | Saves a human review decision. |
| `db_load_human_reviews(filename)` | Loads all human reviews for a report. |

## Partners and Phases

**Partners**: independent, western, heartland, united, rarebreed, evervet, encore, amerivet. Each can have partner-specific rules layered on top of universal rules.

**Phases**: prototype, full, final. Different phases check different things. Prototype is lightweight (search-replace, basic setup). Full checks everything. Final re-verifies after fixes.

## Rule Categories

- `search_replace` - Leftover template text (WhiskerFrame, placeholder addresses/phones/emails)
- `wordpress_backend` - Plugin/theme updates, timezone settings, media library cleanup, form notifications (requires PetDesk QA Connector plugin)
- `functionality` - Broken links (403s reported as warnings, not failures), broken images, mixed content (HTTP on HTTPS), phone/email hyperlinks, form submission testing (Playwright fills and submits forms, verifies redirect to thank-you page), mobile responsiveness, Lighthouse (uses PSI_API_KEY with retry/backoff; falls back to Playwright-based local performance check if PSI unreachable — never HUMAN_REVIEW)
- `craftsmanship` - Logo, favicon, contrast/accessibility
- `content` - H1 tags, alt text, meta titles/descriptions, Open Graph social sharing tags, placeholder text, privacy policy, accessibility statement, Whiskercloud removal
- `grammar_spelling` - Grammar and spelling errors checked via LanguageTool API (free, open-source)
- `footer` - Social links placement, map iframe, powered-by text
- `navigation` - Nav link validation, navigation structure
- `cta` - CTA text and placement (partner-specific)
- `forms` - New client form, career page tracking URL
- `partner_specific` - Partner-specific rules for Western, Heartland, United, Rarebreed, EverVet, Encore, AmeriVet (career widgets, layout requirements, naming conventions)
- `human_review` - Brand tone, image appropriateness, visual consistency (cannot be automated). Report provides Pass/Fail/N/A buttons and a comments field for each item. The initial score includes a 30% pending penalty for each unreviewed human item. PASS or N/A restores that penalty (score goes up). FAIL increases it from 30% to 100% of the item's weight (score goes down). Score updates in real-time via JavaScript. Human review items only appear in the dedicated checklist section, not duplicated in category tables.

## Human Review Persistence

Human review decisions (Pass/Fail/N/A + comments) are automatically saved to the database via the `/api/review` endpoint. When a report is reopened, saved decisions are loaded and the score recalculated. This allows reviewers to:
- Close a report and return later without losing work
- Share reports with other team members who can see completed reviews
- Build an audit trail of who reviewed what and when

**Score updates in real-time as reviews are completed.** The initial score already includes a 30% pending penalty for every human review item (assumes partial failure until reviewed). As items are reviewed:
- **PASS or N/A** → restores the 30% penalty, so the score goes **up**
- **FAIL** → increases the penalty from 30% to 100% of the item's weight, so the score goes **down**
- The score ring, color, assessment text, and progress bar all update instantly in the browser
- "Ready for Delivery" (green) requires score >= 95, zero automated failures, **and** all human review items completed

Database table: `human_reviews` stores report_filename, item_index, rule_id, decision, comments, and reviewed_at timestamp. API endpoints:
- `POST /api/review` - saves a single review decision
- `GET /api/reviews/<filename>` - loads all saved reviews for a report

Falls back gracefully when viewing reports as local files (decisions update in-browser but can't persist).

## Grammar & Spelling Checks

The scanner uses the LanguageTool API (free, no API key required) to check visible page text for grammar and spelling errors. It:
- Extracts visible text from each page (excluding nav, footer, scripts)
- Sends text to LanguageTool for analysis
- **Spelling errors** = red FAIL (loses points). Deduplicated by word: each misspelled word appears once with occurrence count and list of pages. Proper nouns, brand names, and capitalized words are automatically filtered out.
- **Medical/veterinary term filtering** uses pattern matching (medical prefixes like micro-, endo-, cardio- and suffixes like -ectomy, -ology, -itis, -worm) plus a small allowlist for domain terms that don't fit patterns (gumline, rehabilitator, spay, neuter, etc.). This automatically accepts domain-specific terminology.
- **British/Canadian English filtering** — LanguageTool is hardcoded to `en-US`, so words like "behaviour", "honour", "grey", "anaesthesia", "centre" are accepted via `_BRITISH_CANADIAN_SPELLINGS` set. British `-ise`/`-ised`/`-ising`/`-isation` verb forms (e.g. "personalised", "specialising") are auto-matched by suffix pattern (with `len > 5` guard to avoid matching "rise", "wise").
- **Pet breed name filtering** — common breed names (yorkie, goldendoodle, labradoodle, corgi, cockapoo, etc.) and cat terms (tabby, calico, tortoiseshell) are in the `_DOMAIN_TERMS` allowlist. These appear in lowercase on vet sites and would otherwise be flagged.
- **Additional vet terms** — bordetella, giardia, leptospirosis, photobiomodulation, parvovirus, distemper, and other terms not caught by the medical prefix/suffix patterns are explicitly allowlisted.
- **Grammar issues** = yellow WARNING (no point loss). Deduplicated by message type with occurrence count.
- Checks up to 10 pages in parallel (4 concurrent API calls) for speed
- Automatically retries with increasing delays (1s, 2s, 4s) when rate-limited by LanguageTool API

Rule ID: `GRAM-001` (spelling) / `GRAM-001-G` (grammar), weight 3x, applies to Full Build and Final phases.

## Broken Image Detection

Checks every `<img>` tag across the site to verify the image file actually loads (HTTP 200). Catches broken image references that show as missing image icons in the browser. Uses parallel requests for speed (up to 200 images checked per scan). Each broken image shows the image URL and the exact page it was found on.

Rule ID: `IMG-001`, weight 3x, applies to Full Build and Final phases.

## Open Graph / Social Sharing Tags

Checks that pages have the three key Open Graph meta tags: `og:title`, `og:description`, and `og:image`. These control what appears when someone shares the clinic's website on Facebook, LinkedIn, or other social platforms. Without them, shared links show as blank or generic previews.

- 3+ pages missing tags = FAIL, fewer = WARNING
- Rule ID: `SEO-001`, weight 2x, applies to Full Build and Final phases.

## Mixed Content Detection

Checks for HTTP (insecure) resources loaded on HTTPS pages. This causes browser security warnings ("Not Secure" in the address bar) and can block images/scripts from loading entirely. Scans all `<img>`, `<script>`, `<link>`, `<iframe>`, `<source>`, `<video>`, and `<audio>` tags.

Rule ID: `SEC-001`, weight 3x, applies to all phases.

## Form Submission Testing

The scanner uses Playwright (headless browser) to actually submit contact forms and verify they work:

1. **Finds contact forms** - identifies forms with name/email/phone/message fields (skips search, login, and newsletter forms)
2. **Detects untestable forms** - automatically skips forms with CAPTCHA (reCAPTCHA, hCaptcha, Turnstile) or complex forms (>10 required fields), flagging them for manual testing
3. **Fills with test data** - uses obviously fake data: "QA Test User", "qa-test@petdesk-scanner.test", "555-000-0000"
4. **Submits the form** - clicks the submit button via Playwright
5. **Verifies success** - checks for redirect to thank-you page, success message, or AJAX confirmation (waits 2 seconds for dynamic responses)

This goes beyond just checking if thank-you pages exist — it verifies the entire form submission flow works end-to-end.

Rule ID: `FUNC-004`, weight 3x, applies to Full Build and Final phases. Requires Playwright; falls back to human review if unavailable.

## Hybrid JS Rendering (Playwright)

The scanner primarily uses `requests` (fast, lightweight HTTP) to fetch pages. Most WordPress/Divi sites serve full server-rendered HTML, so this works well. However, some pages render content via JavaScript (dynamic widgets, phone numbers in headers, etc.) which `requests` cannot see.

When a fetched page has very little visible body text (under 200 chars) despite having a large HTML payload (over 5KB), the scanner automatically re-fetches it using Playwright (headless Chromium) to get the fully rendered DOM. This is transparent to all check functions — they see the same `PageData` structure regardless of how it was fetched.

- **Playwright is optional** — if not installed, the scanner falls back to `requests`-only mode
- **Lazy browser init** — Chromium only launches when a JS-heavy page is detected, and is reused across the scan
- Set `PLAYWRIGHT_ALWAYS=1` in `.env` to force Playwright for all pages (useful for debugging)
- Docker deployment includes Chromium automatically

## Sitemap-Based Crawl Fallback

The BFS crawl discovers pages by following `<a>` tags in static HTML. When a site renders its navigation via JavaScript (common with Divi themes), `requests` can't see those links and the crawl stalls at 3-5 pages (just footer links like privacy-policy, accessibility-statement).

**Fallback**: After BFS, if fewer than 5 pages were found, the crawler fetches the site's WordPress sitemap to discover URLs. It tries three standard locations in order:
1. `/sitemap_index.xml` (Yoast SEO / Rank Math)
2. `/sitemap.xml` (generic)
3. `/wp-sitemap.xml` (WordPress core since 5.5)

For sitemap indexes, it follows child sitemaps (e.g. `/post-sitemap.xml`, `/page-sitemap.xml`). Sitemap-discovered URLs are added to the BFS queue, so links found on those pages are also followed.

**Security**: Child sitemap fetches are validated to be on the same domain (SSRF prevention), recursion is capped at depth 2, and XML with DOCTYPE declarations is rejected (Billion Laughs prevention).

## WP Engine Staging Domain Mismatch

WP Engine staging sites (e.g. `trinityvethosp.wpenginepowered.com`) have a pervasive issue: WordPress stores the production domain internally, so sitemaps, navigation links, logo hrefs, and other internal URLs all reference the production domain (e.g. `trinityvet716.com`) instead of the staging domain. This affects:

- **Sitemap URLs** — `<loc>` entries in sitemaps point to production domain. The sitemap parser rewrites these to the scanner's target domain via `_rewrite_sitemap_url()`.
- **Logo link** — The logo's `<a href>` points to the production domain root. The logo check now accepts any root-path link regardless of domain.
- **Internal navigation links** — Links in the HTML point to production domain. BFS crawl only follows same-domain links, so these are invisible to the crawler (which is why the sitemap fallback is needed).

## Memory Management (512MB Render Free Tier)

The app runs on Render.com's free tier with a 512MB container limit. A full scan uses Python (~200MB) plus Chromium (~247MB in `--single-process` mode), leaving very little headroom. The scan engine uses several strategies to stay within budget:

### Page data lifecycle
1. **Crawl** — `SiteCrawler` fetches up to 30 pages, each stored as a `PageData` with `.html` (raw HTML string) and `.soup` (parsed BeautifulSoup tree, ~2MB each = ~60MB total for 30 pages)
2. **Parallel checks** — fast non-browser checks (broken links, grammar, alt text, etc.) run in parallel using the soup data
3. **Pre-extraction** — before launching Chromium, `pre_extract_page_data()` extracts form metadata (field names, types, captcha flags) and map data (iframe srcs, page text) into plain Python dicts
4. **Free ALL soups** — every `page.soup` and `page.html` is set to `None`, then `gc.collect()` reclaims ~60MB
5. **Sequential browser checks** — form submission, visual consistency, responsive viewports, and map location run one at a time with browser restarts between each

### Browser memory strategy
- **`--single-process`** mode keeps Chromium at 247MB baseline (vs 300MB+ in multi-process)
- **Trade-off**: `--single-process` doesn't free page memory when browser contexts close — each navigation adds memory permanently
- **Solution**: restart Chromium before EVERY sequential check (`cleanup_shared_browser()` + `_get_shared_browser()`) to reset memory
- **With `domcontentloaded`** wait strategy (not `networkidle`), each restart cycle takes ~5 seconds

### Visual consistency screenshot optimization
- Viewport 1024x768 (smaller = faster render), timeout 20s, max 2 attempts
- Browser restart between retries to reset leaked memory
- Route blocking: `resource_type == "media"` only — blocks video/audio to save memory. Fonts are NOT blocked (Divi uses icon fonts like ETmodules; blocking fonts causes icons to render as "3")
- CSS animations disabled via `add_init_script` (prevents mid-animation screenshots)
- AI prompt instructs Gemini to ignore black/empty video placeholder areas (caused by media blocking)

### Connection pooling
`SiteCrawler` uses `requests.HTTPAdapter(pool_connections=10, pool_maxsize=10, pool_block=True)` for TLS connection reuse across crawl requests. This avoids TLS handshake overhead on every page fetch.

### Why `domcontentloaded` not `networkidle`
WordPress/Divi sites load analytics scripts (Google Analytics, Facebook Pixel, HotJar) that fire continuously and never go idle. Using `wait_until="networkidle"` caused every Playwright operation to timeout at 30-60 seconds. `domcontentloaded` fires in 2-3 seconds when the DOM is ready, plus a short `wait_for_timeout()` settle time for images/styles.

### Memory instrumentation
`app.py` includes `_mem_mb(label)` using `psutil` to log Python RSS + all child process memory (Chromium). Logs appear as `[MEM]` lines in Render logs. `PYTHONUNBUFFERED=1` in Dockerfile ensures logs flush immediately.

### Typical memory profile
| Phase | Python | Chromium | Total |
|-------|--------|----------|-------|
| Scan start | 110MB | — | 110MB |
| After crawl (30 pages) | 165MB | — | 165MB |
| After parallel checks | 200MB | — | 200MB |
| After freeing soups | 145MB | — | 145MB |
| Browser launch | 145MB | 247MB | 392MB |
| During check (peak) | 145MB | ~360MB | ~505MB |
| After restart | 145MB | — | 145MB |

## QA Rules Management

Rules are stored in `rules.json` (not hardcoded in Python). The QA team manages rules entirely through the web app:

- **View rules** at `/rules` - Browse all rules organized by category, filter by partner and build phase
- **Edit rules** at `/rules/edit` - Guided interface explains two types of rules non-coders can add:
  1. **Search for text** — scanner checks every page for specific text (placeholder names, old branding, wrong contact details). Fully automated.
  2. **Human review checklist item** — adds a manual check to the report with Pass/Fail/N/A buttons.
  - Other automated checks (broken links, alt text, etc.) require a developer to add the check function.
- Changes are saved to `rules.json` and take effect on the next scan
- Each rule has: ID, description, category, weight (1-5), applicable phases, and partner scope

## Scan History

Every scan is stored in the PostgreSQL database with its full HTML report and JSON audit trail. In local dev (no database), reports are saved as files in `reports/`.

View scan history at `/history` in the web app. The history page features:
- **Summary stats** at the top (total scans, passing count, needs-work count)
- **Search** by site URL or scan ID
- **Filters** for partner, build phase, and score range (85+, 70-84, <70)
- **Sortable columns** — click any header to sort ascending/descending
- **Score bars** — visual mini progress bar next to each score
- **JSON download** — direct link to the JSON audit trail for each scan

History persists across deploys via PostgreSQL. No data lost on redeploy.

## Scan IDs

Each scan gets a unique ID (QA-0001, QA-0002, ...). The ID is determined by the combination of site URL + build phase — re-scanning the same site at the same phase reuses the same ID. A new site or different phase gets the next available number. IDs are generated atomically via PostgreSQL SEQUENCE (or `scan_counter.json` as fallback).

## WordPress Back-End Checks (via PetDesk QA Plugin)

The scanner includes 5 WordPress back-end checks that validate settings not visible on the front-end:

| Check | Rule ID | Description |
|-------|---------|-------------|
| Plugin updates | WPBE-001 | Verifies all plugins are up to date |
| Theme updates | WPBE-002 | Verifies all themes are up to date |
| Timezone | WPBE-003 | Extracts clinic address from website and validates WordPress timezone matches the location's expected timezone |
| Media cleanup | WPBE-004 | Flags old/template media files (placeholder images) |
| Form notifications | WPBE-005 | Verifies form emails go to clinic, not template addresses (supports Gravity Forms and WPForms) |

### PetDesk QA Connector Plugin

These checks require the **PetDesk QA Connector** plugin to be installed on each site. The plugin:
- Exposes a secure REST endpoint at `/wp-json/petdesk-qa/v1/site-check`
- Authenticates via a shared API key (no per-site credentials needed)
- Returns plugin/theme versions, timezone, media info, and form notification settings
- Is invisible to site visitors (backend only)

**Installation:**
1. Download `petdesk-qa-plugin.zip` from this repo
2. In WordPress admin, go to Plugins > Add New > Upload Plugin
3. Upload the zip file and activate
4. That's it - the scanner will automatically detect and use the plugin

**For the website building team:** Add "Install PetDesk QA Connector plugin" to the site build checklist. The plugin is lightweight (<10KB) and has no front-end impact.

**Fallback:** If the plugin is not installed, checks fall back to HUMAN_REVIEW with instructions for manual verification in wp-admin.

**Auto-Update:** The plugin includes GitHub auto-update support. When a new release is published to `jonathanpimperton/zero-touch-qa`, all sites with the plugin will see "Update Available" in wp-admin and can one-click update. To release a plugin update:
1. Update `PETDESK_QA_VERSION` in `petdesk-qa-connector.php`
2. Recreate `petdesk-qa-plugin.zip`
3. Create a GitHub release: `gh release create v1.2.0 petdesk-qa-plugin.zip --title "Plugin v1.2.0"`

## Key Configuration (.env)

- `DATABASE_URL` - PostgreSQL connection string. Set automatically by Render via `render.yaml`. Without it, app falls back to filesystem storage.
- `PSI_API_KEY` - Google PageSpeed Insights key. Enables rendered-page checks (mobile usability, performance, contrast, CLS). Free from Google Cloud Console. PSI API is called with retry/backoff (3 attempts, 5s/15s/30s delays, switches to desktop strategy on retries). If PSI is unreachable (common for staging URLs like *.wpenginepowered.com), falls back to Playwright-based local performance check — never HUMAN_REVIEW.
- `PETDESK_QA_API_KEY` - Shared API key for the PetDesk QA Connector plugin. Must match the key in the plugin. Default is `petdesk-qa-2026-hackathon-key` for demo purposes. Change in production.
- `WRIKE_API_TOKEN` - For posting scan results back to Wrike tasks. Not yet configured (no Wrike access during hackathon).
- `WRIKE_CF_*` - Wrike custom field IDs for site URL, partner, and phase.
- `ADMIN_KEY` - Secret key for admin endpoints like `/admin/clear-history`. Set via Render dashboard or `.env`.
- `GEMINI_API_KEY` - Google Gemini API key for AI-powered vision checks (primary). Uses the `google-genai` SDK (not the deprecated `google-generativeai`). Enables image appropriateness and visual consistency analysis. Get a free key from Google AI Studio (ai.google.dev). This is the recommended AI provider.
- `ANTHROPIC_API_KEY` - Anthropic API key for AI-powered checks (fallback). Used if Gemini is not configured. Requires Claude credits.

## AI-Powered Checks

The scanner uses **Gemini 2.5 Flash** Vision API (primary) or Claude Vision API (fallback) for automated checks that previously required human judgment. AI calls use adaptive throttling (0.3s default, backs off to 2.0s on 429, halves after 10 successes) protected by a `threading.Lock` for thread safety. Image fetches are cached within a scan to avoid re-downloading. These are prefixed with `AI-*` rule IDs:

| Rule ID | Check | What It Does |
|---------|-------|--------------|
| AI-001 | Image Appropriateness | Analyzes images on sensitive pages (euthanasia, end-of-life) for inappropriate content |
| AI-002 | Visual Consistency | Takes homepage screenshot and checks for alignment, spacing, and color consistency issues |
| AI-003 | Branding Consistency | Checks for default Divi fonts and inconsistent button colors |
| AI-004 | Image Cropping | Detects awkwardly cropped images (cut-off faces, partial logos) |
| AI-005 | Responsive Viewports | Uses Playwright to test site at desktop (1920px), tablet (768px), and mobile (375px) |
| AI-006 | Map Location | Uses Playwright to find JS-rendered Google Maps, geocodes clinic address, and verifies coordinates match |

These 6 AI-powered checks reduce human review items from 28 to 22 while improving consistency and speed.

**Gemini error diagnostics**: When Gemini returns a non-rate-limit error (e.g. 400 INVALID_ARGUMENT), the log includes the full error message, image size in KB, and the source URL of the image that failed. This helps diagnose issues like unsupported image formats or oversized files without guessing.

## Test Sites (Demo Results)

- `thestorybookal.wpenginepowered.com` - Test site with known QA issues. Scored **74/100** (15 failures, 5 warnings).
- `essentialsfina.wpenginepowered.com` - Site that passed Final QA. Scored **89/100** (5 failures, 4 warnings).
- `trinityvethosp.wpenginepowered.com` - JS-rendered nav site (BFS only found 3 pages; sitemap fallback discovers 30). Scored **79/100** (3 failures, 7 warnings). Good test case for staging domain mismatch and sitemap fallback.

Demo reports available as `demo_test_site.html` / `demo_final_site.html`.

## Scan Flow

1. `get_rules_for_scan(partner, phase)` loads applicable rules from `rules.json`
2. `SiteCrawler.crawl()` fetches and parses up to 30 pages via BFS (uses `requests` with connection pooling via `HTTPAdapter`, with Playwright fallback for JS-rendered pages). If BFS finds <5 pages, falls back to WordPress sitemap discovery (see "Sitemap-Based Crawl Fallback" above).
3. **Parallel phase**: Non-browser checks run in parallel (ThreadPoolExecutor, 4 workers) — broken links, grammar, alt text, images, Open Graph, mixed content, PSI API call, etc.
4. **Pre-extraction**: Form metadata and map iframe data extracted from soups into plain dicts
5. **Free memory**: All page soups and HTML freed (~60MB reclaimed) before launching Chromium
6. **PSI fallback** (if PSI API failed): Playwright-based local performance check runs first — gathers LCP, CLS, viewport, tap targets, font sizes, contrast via JS. Then PSI-dependent checks re-run with local data.
7. **Sequential browser phase**: Form submission, map location, visual consistency, and responsive viewports run one at a time with browser restarts between each check
8. LanguageTool API called for grammar/spelling on up to 10 pages (4 concurrent API calls). Spelling = FAIL (deduplicated by word, medical terms filtered), Grammar = WARN
9. Every error includes the exact page URL where it was found
10. Scoring: 100 - sum(weight) for FAIL, warnings at 50% weight, pending human reviews at 30% weight
11. **Scan health issues** collected throughout (crawl issues, WP plugin missing, PSI fallback, check errors, AI vision errors) and surfaced in the report
12. Scan saved to PostgreSQL (primary) and filesystem (fallback)
13. Human review checklist allows Pass/Fail/N/A with comments; FAIL decisions lower the score in real-time. Decisions persist to database via `/api/review`

## Report Layout

Each HTML report includes a **toolbar** at the top (hidden when printing) with navigation links back to the Scanner and Scan History (greyed out when viewing as a local file), a "Copy Report URL" button for easy sharing, and a "Save as PDF" button that opens the browser's print dialog for one-click PDF export.

The report body has these sections:

0. **Scan Health Warning** (amber banner, conditional) — Only shown when `scan_issues` is non-empty. Lists issues that may affect scan reliability (low page count, WP plugin missing, PSI fallback, check errors, AI vision errors). Advises re-scanning.
1. **Failures** (red cards) — Each card shows:
   - **Headline**: Short summary of what's wrong (e.g., "Social links found outside footer (2 pages)")
   - **Fix advice**: Green box with specific guidance on how to fix the issue
   - **Rule reference**: Rule ID and description in smaller text
   - **Details**: Collapsible list of specific instances (hidden by default, click "Show details" to expand)
2. **Warnings** (yellow cards) — Same format as failures, but for non-blocking issues.
3. **Human Review Checklist** (indigo cards) — Interactive Pass/Fail/N/A buttons with comments. Human FAIL subtracts the item's weight from the score and updates the score ring in real-time. Rule IDs shown for traceability.
4. **All Results by Category** — Separated by a horizontal divider with "FULL BREAKDOWN" label, grey background panel, and italic note: "Items flagged above are repeated here for reference." This is the complete reference view of every automated check organised by category. Human review items are excluded here (they only appear in the checklist above to avoid duplication).

No SKIP status exists in the report. Checks that can't run automatically are flagged as HUMAN_REVIEW with actionable instructions telling the reviewer what to verify manually.

## Scoring System

The scoring system ensures honest assessments:

- **Score calculation**: 100 - sum(weight) for each FAIL - sum(weight * 0.5) for each WARN - sum(weight * 0.3) for each pending human review item.
- **Penalty weights**: FAIL = 100% of rule weight, WARN = 50%, pending HUMAN_REVIEW = 30%.
- **Human review penalty**: Unreviewed items carry a 30% pending penalty (honest scoring — the score assumes partial failure until a human confirms). PASS/N/A restores the penalty; FAIL increases it to 100%.
- **Weights**: 1 (minor) to 5 (critical). Total possible penalty: 83 points from automated checks.
- **"Ready for Delivery" requires**: Score 95+ AND zero failures. Any failure blocks this status.
- **Critical failures (weight 5)**: Broken links, social links in wrong place, meta titles/descriptions missing. These show "Critical Issues - Fix Before Delivery" even with high scores.

| Score | Failures | Color | Assessment |
|-------|----------|-------|------------|
| 95+ | 0 | Green | Ready for Delivery |
| Any | Critical (weight 5) | Amber | Critical Issues - Fix Before Delivery |
| 85+ | Minor only | Lime | Minor Issues - Fix Before Delivery |
| 85+ | 0 (warnings only) | Lime | Almost Ready - Review Warnings |
| 70-84 | Any | Amber | Needs Work - Several Issues |
| <70 | Any | Red | Significant Issues - Major Rework |

**Key rule**: Critical failures (weight 5) always show amber and block green status, regardless of score.

## Web UI Polish

The web interface features:
- **Consistent headers** across all pages with PetDesk branding and subtle glow effects
- **Animated transitions**: Fade-in on page load, smooth hover states
- **Form icons**: SVG icons next to URL, Partner, and Phase fields
- **Real-time scanning progress**: Uses Server-Sent Events (SSE) to show actual progress as it happens - connecting, crawling (with page count), WordPress checks, grammar checks, AI analysis, report generation. No fake timers.
- **Score rings**: Circular colored badges in history (green/yellow/red based on score)
- **Scan ID badges**: Monospace font with gradient background for easy identification

## Wrike Integration (Theoretical - Future Target)

The `/webhook/wrike` endpoint in app.py is built and ready but untested (no Wrike access during hackathon). Design:
1. Create custom fields on Wrike QA tasks: Site URL, Partner, Build Phase
2. Create Wrike automation: "When status changes to QA In Progress, send webhook"
3. Scanner reads custom fields, runs scan automatically
4. HTML report is converted to PDF and attached to the Wrike task (not posted as a text comment, which would lose the formatting)
5. PDF attachment preserves the full scored report with color-coded failures, warnings, and charts

Note: PDF generation requires adding WeasyPrint or a similar library in production.

## Hackathon Deliverables

1. **Working prototype** - Flask web app with 74 check functions across 122 rules (100 automated, 22 human review) covering all 8 partners, grammar/spelling, broken images, social sharing, mixed content, WordPress back-end checks, AI vision checks, real scan results
2. **Professional proposal** - `proposal.html` (18-slide deck with embedded screenshots, includes WordPress plugin explanation, print to PDF via browser)
3. **Demo results** - Scans of both provided test sites with real scores
4. **Cloud-ready** - Dockerfile + Render.com config with PostgreSQL for persistent storage
5. **QA self-service** - Rules editor and scan history accessible via web app
6. **Full partner coverage** - Partner-specific rules for all 8 partners (Independent, Western, Heartland, United, Rarebreed, EverVet, Encore, AmeriVet)
7. **WordPress API integration** - Back-end checks for plugins, themes, timezone, media, and form notifications

## Known Issues (Low Severity)

- **`page.details` dead code** (qa_scanner.py:493) — `PageData` has no `details` field. Setting it on a request exception is harmless but nothing reads it.
- **XSS in error display** (app.py:706,717) — Error messages displayed via `innerHTML`. Low risk: error text comes from internal Python exceptions, not user input. Fix: use `textContent` instead.
- **Admin GET reset has no auth** (app.py:1818) — `GET /admin/clear-history?confirm=reset` bypasses admin key check. Intentional for hackathon testing. Remove GET path for production.
- **Wrike comment HTML not escaped** (qa_report.py) — Rule details injected into Wrike HTML without escaping. Wrike integration is not yet active.
- **`scan_history` not thread-locked** (app.py:68-75) — List mutated from multiple threads without a lock. Mitigated by gunicorn `--workers 1 --threads 1` config.
