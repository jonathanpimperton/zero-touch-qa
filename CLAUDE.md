# Zero-Touch QA

Automated website QA scanner for PetDesk veterinary clinic websites built on WordPress/Divi. Hackathon submission for the Automation & AI track (deadline: February 6, 2026).

## What This Does

Scans a live veterinary clinic website against the same QA checklist rules that human QA specialists check manually. Produces a scored pass/fail report with specific failures, warnings, and a checklist of items that still need human judgment. Includes grammar/spelling checks via LanguageTool API, broken image detection, Open Graph social sharing validation, and mixed content security checks.

## Architecture

```
User (browser)  or  Wrike (webhook)
         \              /
          \            /
           app.py (Flask web server)
              |
     +--------+--------+
     |                  |
qa_rules.py      qa_scanner.py
(rule engine)    (crawler + 41 check functions)
                        |
                  qa_report.py
                  (HTML + JSON output)
```

## Web App Pages

| Route | Purpose |
|-------|---------|
| `/` | Scanner home page. Paste a URL, pick Partner and Phase, click Run QA Scan. |
| `/rules` | Browse all QA rules. Filter by partner and build phase. See each rule's category, weight, and whether it's automated or human-review. |
| `/rules/edit` | Add, delete, or modify QA rules through the browser. No coding needed. |
| `/history` | View all past scans with scores, dates, and links to full HTML reports. Includes search by URL/scan ID, filter by partner/phase/score, sortable columns, and summary stats. |
| `/reports/<filename>` | View a specific scan report. |
| `/api/scan` | API endpoint (POST) for running scans programmatically. |
| `/webhook/wrike` | Wrike webhook endpoint for automated scan triggering (future). |

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask web app. Main entry point. Serves the browser UI with Scanner, Rules, and History pages. Handles `/api/scan` for scans and `/webhook/wrike` for Wrike automation. Embeds PetDesk logo. |
| `qa_rules.py` | Rule engine. Loads rules from `rules.json`. Each rule has an ID, category, phase, weight, partner scope, and a pointer to its check function. Call `get_rules_for_scan(partner, phase)` to get applicable rules. |
| `qa_scanner.py` | Site crawler and 41 check functions. `SiteCrawler` fetches pages via `requests`, with optional Playwright (headless browser) fallback for JS-rendered content. `CHECK_FUNCTIONS` maps rule names to functions. Integrates with Google PageSpeed Insights API and LanguageTool API (grammar/spelling). Checks include broken links, broken images, Open Graph tags, mixed content, and more. |
| `qa_report.py` | Report generators. `generate_html_report()` produces a polished HTML report with PetDesk brand assets, SVG score ring, colored section banners, collapsible detail lists, interactive human review checklist (Pass/Fail/N/A buttons + comments), a non-printing toolbar (Copy URL, Save as PDF, navigation), and print-friendly layout. `generate_wrike_comment()` produces Wrike-formatted HTML. `generate_json_report()` produces JSON for audit trail. |
| `rules.json` | All QA rules as JSON data. Editable via `/rules/edit` in the web app. Contains universal rules and partner-specific overlays. |
| `run_qa.py` | CLI fallback for testing. Not the primary interface. |
| `proposal.html` | Professional HTML presentation (17 slides) for hackathon submission. Print to PDF from browser. Uses PetDesk template colors and fonts. |
| `_build_proposal.py` | Script to regenerate `proposal.html`. Run `python _build_proposal.py` if slides need updating. |
| `PROPOSAL.md` | Markdown version of the hackathon proposal. |
| `Petdesk Logo.png` | PetDesk logo (purple text, high-res) for white backgrounds. Base64-embedded in reports and web UI. |
| `Petdesk Logo White Text.png` | PetDesk logo (white text, high-res) for dark/purple backgrounds. Used in report headers and dark proposal slides. |
| `Petdesk background purple.png` | PetDesk brand purple diamond texture background. Used in report headers, web UI headers, and dark proposal slides. |
| `demo_test_site.html/json` | Demo scan of the test site (thestorybookal). HTML report + JSON audit trail. |
| `demo_final_site.html/json` | Demo scan of the final site (essentialsfina). HTML report + JSON audit trail. |
| `.env` | Credentials and API keys. Never committed. |
| `.gitignore` | Excludes `.env`, `__pycache__`, `reports/`, `*.xlsx`, `*.pdf`, `*.pptx`, and temp files from version control. |
| `requirements.txt` | Python dependencies including gunicorn and playwright for production deployment. |
| `Dockerfile` | Container config for cloud deployment. Includes Chromium for Playwright headless browser. |
| `render.yaml` | Render.com deployment configuration. Uses Docker runtime for Playwright/Chromium support. |
| `Procfile` | Heroku/Railway-compatible process file. |

## Cloud Deployment

The app is designed to run in the cloud, not locally. Options:

### Render.com (recommended for hackathon)
1. Push code to a GitHub repo
2. Connect repo to Render.com (free tier)
3. Render reads `render.yaml` and deploys automatically
4. Set environment variables (PSI_API_KEY, WRIKE_API_TOKEN) in Render dashboard
5. App gets a public URL like `https://zero-touch-qa.onrender.com`

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

## Partners and Phases

**Partners**: independent, western, heartland, united, rarebreed, evervet, encore, amerivet. Each can have partner-specific rules layered on top of universal rules.

**Phases**: prototype, full, final. Different phases check different things. Prototype is lightweight (search-replace, basic setup). Full checks everything. Final re-verifies after fixes.

## Rule Categories

- `search_replace` - Leftover template text (WhiskerFrame, placeholder addresses/phones/emails)
- `functionality` - Broken links (403s reported as warnings, not failures), broken images, mixed content (HTTP on HTTPS), phone/email hyperlinks, form success pages, mobile responsiveness, Lighthouse (requires PSI_API_KEY, otherwise flagged for human review)
- `craftsmanship` - Logo, favicon, contrast/accessibility
- `content` - H1 tags, alt text, meta titles/descriptions, Open Graph social sharing tags, placeholder text, privacy policy, accessibility statement, Whiskercloud removal
- `grammar_spelling` - Grammar and spelling errors checked via LanguageTool API (free, open-source)
- `footer` - Social links placement, map iframe, powered-by text
- `navigation` - Nav link validation
- `cta` - CTA text and placement (partner-specific)
- `forms` - New client form, career page tracking URL
- `human_review` - Brand tone, image appropriateness, visual consistency (cannot be automated). Report provides Pass/Fail/N/A buttons and a comments field for each item. Human FAIL decisions lower the score (by the item's weight); PASS keeps it unchanged. Human review items only appear in the dedicated checklist section, not duplicated in category tables.

## Grammar & Spelling Checks

The scanner uses the LanguageTool API (free, no API key required) to check visible page text for grammar and spelling errors. It:
- Extracts visible text from each page (excluding nav, footer, scripts)
- Sends text to LanguageTool for analysis
- **Spelling errors** = red FAIL (loses points). Deduplicated by word: each misspelled word appears once with occurrence count and list of pages. Proper nouns, brand names, and capitalized words are automatically filtered out.
- **Medical/veterinary term filtering** uses pattern matching (medical prefixes like micro-, endo-, cardio- and suffixes like -ectomy, -ology, -itis, -worm) instead of a manual allowlist. This automatically accepts domain-specific terminology without needing to add individual words.
- **Grammar issues** = yellow WARNING (no point loss). Deduplicated by message type with occurrence count.
- Checks up to 5 pages per scan to stay within API rate limits

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

## Hybrid JS Rendering (Playwright)

The scanner primarily uses `requests` (fast, lightweight HTTP) to fetch pages. Most WordPress/Divi sites serve full server-rendered HTML, so this works well. However, some pages render content via JavaScript (dynamic widgets, phone numbers in headers, etc.) which `requests` cannot see.

When a fetched page has very little visible body text (under 200 chars) despite having a large HTML payload (over 5KB), the scanner automatically re-fetches it using Playwright (headless Chromium) to get the fully rendered DOM. This is transparent to all check functions — they see the same `PageData` structure regardless of how it was fetched.

- **Playwright is optional** — if not installed, the scanner falls back to `requests`-only mode
- **Lazy browser init** — Chromium only launches when a JS-heavy page is detected, and is reused across the scan
- Set `PLAYWRIGHT_ALWAYS=1` in `.env` to force Playwright for all pages (useful for debugging)
- Docker deployment includes Chromium automatically

## QA Rules Management

Rules are stored in `rules.json` (not hardcoded in Python). The QA team manages rules entirely through the web app:

- **View rules** at `/rules` - Browse all rules organized by category, filter by partner and build phase
- **Edit rules** at `/rules/edit` - Add new rules, delete rules, change weights. No coding, no files to edit, no developer needed
- Changes are saved to `rules.json` and take effect on the next scan
- Each rule has: ID, description, category, weight (1-5), applicable phases, and partner scope

## Scan History

Every scan produces two files in the `reports/` directory:
- **HTML report** - Visual report viewable in any browser, printable as PDF. Includes interactive human review section with Pass/Fail/N/A buttons and comments for each manual check item.
- **JSON file** - Machine-readable audit trail with every check result

View scan history at `/history` in the web app. The history page features:
- **Summary stats** at the top (total scans, passing count, needs-work count)
- **Search** by site URL or scan ID
- **Filters** for partner, build phase, and score range (85+, 70-84, <70)
- **Sortable columns** — click any header to sort ascending/descending
- **Score bars** — visual mini progress bar next to each score
- **JSON download** — direct link to the JSON audit trail for each scan

History is loaded from saved JSON files on app startup, so records persist across restarts.

## Scan IDs

Each scan gets a unique ID (QA-0001, QA-0002, ...). The ID is determined by the combination of site URL + build phase — re-scanning the same site at the same phase reuses the same ID. A new site or different phase gets the next available number.

IDs are stored in `reports/scan_counter.json` and appear in:
- Report header badge (e.g., "QA-0001 · QA Report")
- Report footer
- JSON audit trail (`metadata.scan_id`)
- History page table
- CLI output

## Key Configuration (.env)

- `PSI_API_KEY` - Google PageSpeed Insights key. Enables rendered-page checks (mobile usability, performance, contrast, CLS). Free from Google Cloud Console. Without it, scanner falls back to HTML-only checks.
- `WRIKE_API_TOKEN` - For posting scan results back to Wrike tasks. Not yet configured (no Wrike access during hackathon).
- `WRIKE_CF_*` - Wrike custom field IDs for site URL, partner, and phase.
- `TEST_SITE_*` / `FINAL_SITE_*` - WordPress admin credentials for the hackathon test sites.

## Test Sites (Demo Results)

- `thestorybookal.wpenginepowered.com` - Test site with known QA issues. Scored **82/100** (9 failures, 3 warnings, 5 human review).
- `essentialsfina.wpenginepowered.com` - Site that passed Final QA. Scored **94/100** (2 failures, 3 warnings, 5 human review).

Demo reports available as `demo_test_site.html` / `demo_final_site.html`.

## Scan Flow

1. `get_rules_for_scan(partner, phase)` loads applicable rules from `rules.json`
2. `SiteCrawler.crawl()` fetches and parses up to 30 pages
3. For each automated rule, the mapped check function runs against all crawled pages
4. PageSpeed Insights API is called once for the homepage (if PSI_API_KEY is set) for rendered-page checks; otherwise flagged for human review
5. LanguageTool API is called for grammar/spelling on up to 5 pages. Spelling = FAIL (deduplicated by word, medical terms filtered by pattern), Grammar = WARN (deduplicated by message)
6. Broken images, Open Graph tags, and mixed content are checked across all pages
7. Broken links: 404/500 = FAIL, 403 (bot-blocked) = WARN
8. Every error includes the exact page URL where it was found
9. Results are collected, scored (100 - weighted failures), and formatted
10. HTML report + JSON audit trail saved to `reports/`
11. Human review checklist allows Pass/Fail/N/A with comments; FAIL decisions lower the score in real-time

## Report Layout

Each HTML report includes a **toolbar** at the top (hidden when printing) with navigation links back to the Scanner and Scan History (greyed out when viewing as a local file), a "Copy Report URL" button for easy sharing, and a "Save as PDF" button that opens the browser's print dialog for one-click PDF export.

The report body has four visually distinct sections:

1. **Failures** (red cards) — Action required. Details formatted as bullet lists for easy scanning.
2. **Warnings** (yellow cards) — Review recommended. Same bullet-list format.
3. **Human Review Checklist** (indigo cards) — Interactive Pass/Fail/N/A buttons with comments. Human FAIL subtracts the item's weight from the score and updates the score ring in real-time. Rule IDs shown for traceability.
4. **All Results by Category** — Separated by a horizontal divider with "FULL BREAKDOWN" label, grey background panel, and italic note: "Items flagged above are repeated here for reference." This is the complete reference view of every automated check organised by category. Human review items are excluded here (they only appear in the checklist above to avoid duplication).

No SKIP status exists in the report. Checks that can't run automatically are flagged as HUMAN_REVIEW with actionable instructions telling the reviewer what to verify manually.

## Viewing Old Reports

All scan reports are permanently saved to the `reports/` directory as HTML + JSON file pairs. There are three ways to access past reports:

1. **History page** (`/history`) — Searchable, filterable table of all scans. Filter by site URL, partner, phase, or score range. Click any row to view the full report. Download the JSON audit trail directly.
2. **Direct URL** (`/reports/<filename>`) — Each report has a permanent URL. Share the link and anyone with access to the app can view it in their browser.
3. **Print to PDF** — Every report has a "Print / Save PDF" button in its toolbar. The print stylesheet preserves all colors, badges, and formatting for a professional PDF export.

## Wrike Integration (Theoretical - Future Target)

The `/webhook/wrike` endpoint in app.py is built and ready but untested (no Wrike access during hackathon). Design:
1. Create custom fields on Wrike QA tasks: Site URL, Partner, Build Phase
2. Create Wrike automation: "When status changes to QA In Progress, send webhook"
3. Scanner reads custom fields, runs scan automatically
4. HTML report is converted to PDF and attached to the Wrike task (not posted as a text comment, which would lose the formatting)
5. PDF attachment preserves the full scored report with color-coded failures, warnings, and charts

Note: PDF generation requires adding WeasyPrint or a similar library in production.

## Hackathon Deliverables

1. **Working prototype** - Flask web app with 41 check functions across 55 rules, grammar/spelling, broken images, social sharing, mixed content, real scan results
2. **Professional proposal** - `proposal.html` (17-slide deck, print to PDF via browser)
3. **Demo results** - Scans of both provided test sites with real scores
4. **Cloud-ready** - Dockerfile + Render.com config for instant deployment
5. **QA self-service** - Rules editor and scan history accessible via web app

## Deploying to the Cloud (Step by Step)

### Using Render.com (free tier):

1. **Create a GitHub repository**
   - Go to github.com, click "New repository"
   - Name it `zero-touch-qa`, set to Private
   - Follow the instructions to push local code:
     ```
     git init
     git add -A
     git commit -m "Initial commit"
     git remote add origin https://github.com/YOUR_USERNAME/zero-touch-qa.git
     git push -u origin main
     ```

2. **Connect to Render.com**
   - Go to render.com and sign up (free)
   - Click "New" > "Web Service"
   - Connect your GitHub account, select the `zero-touch-qa` repo
   - Render reads the `render.yaml` file and configures everything automatically

3. **Set environment variables** in Render dashboard:
   - `PSI_API_KEY` (optional - from Google Cloud Console)
   - `WRIKE_API_TOKEN` (when Wrike access is available)

4. **Access the app** at the URL Render provides (e.g., `https://zero-touch-qa.onrender.com`)

### Note on `.env` file
The `.env` file is for local development only. In cloud deployment, environment variables are set through the hosting provider's dashboard (Render, Heroku, etc.) — they are never committed to the repository.
