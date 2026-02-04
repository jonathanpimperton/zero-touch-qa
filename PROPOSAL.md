# Zero-Touch QA: Automated Website Quality at Scale

## Hackathon Submission - Automation & AI Track

---

## 1. Problem Statement

PetDesk's QA team manually validates every veterinary clinic website against
partner-specific checklists containing 40-70+ checks per build phase. This
process is:

- **Slow**: Each site requires a QA specialist to walk through every checklist
  item by hand, switching between the live site, WordPress admin, checklists,
  and Wrike.
- **Repetitive**: ~80% of checks are deterministic rules (link validation,
  template text replacement, footer compliance, SEO tags) that produce the
  same pass/fail outcome every time.
- **Error-prone**: Human attention fatigue means identical issues are missed
  or inconsistently flagged across sites.
- **Unscalable**: Every new Express site requires the same manual effort,
  meaning QA headcount must grow linearly with volume.

The core insight: **most QA checks don't require judgment -- they require
verification against known rules.** Machines are better at this than humans.

---

## 2. Proposed QA Flow: Current to Future

### Current State (Manual)

```
Designer completes build
        |
        v
QA specialist opens checklist spreadsheet
        |
        v
Manually checks each item (40-70+ items)
  - Opens site in browser
  - Switches to WP admin
  - Cross-references job aids
  - Marks pass/fail in spreadsheet
  - Writes feedback notes in Wrike
        |
        v
If failures: send back to designer with notes
        |
        v
Repeat until all checks pass
```

### Future State (Zero-Touch QA)

```
Designer completes build
        |
        v
[AUTOMATED] QA Scanner runs automatically
  - Crawls the site (30-50 pages in seconds)
  - Runs all rule-based checks against the actual checklist
  - Applies partner-specific rule overlays (Western, Independent, etc.)
  - Applies build-phase rules (Prototype vs Full vs Final)
  - Generates scored pass/fail report
        |
        v
[AUTOMATED] Report is posted to Wrike task
  - Structured failures with exact details
  - Pre-written feedback for common issues
  - Score + assessment (Ready / Needs Work / Major Issues)
        |
        v
If score >= 95: auto-advance to next phase
If score < 95: human QA reviews ONLY flagged items
        |
        v
Human QA specialist reviews:
  - Visual/brand items (alignment, imagery, tone)
  - Partner edge cases
  - Judgment calls
  - (~20% of current workload)
        |
        v
Final sign-off
```

### Time Savings Estimate

| Phase | Current (Manual) | Future (Automated + Human) | Savings |
|-------|-----------------|---------------------------|---------|
| Prototype QA | ~45 min/site | ~5 min scan + ~10 min review | ~65% |
| Full Build QA | ~90 min/site | ~5 min scan + ~25 min review | ~67% |
| Final QA | ~60 min/site | ~5 min scan + ~15 min review | ~67% |

---

## 3. What Is Fully Automated vs. Human-Reviewed

### Fully Automated (37 checks implemented in prototype)

| Category | Checks | Examples |
|----------|--------|----------|
| Template Text | 7 | Leftover "WhiskerFrame", placeholder addresses, phones, emails |
| Links & Images | 4 | Broken links, broken images, phone tel: links, email mailto: links |
| Footer Compliance | 3 | Privacy Policy, Accessibility Statement, "Powered by PetDesk" |
| SEO & Social Sharing | 8 | H1 count, alt text, meta titles, meta descriptions, favicon, Open Graph tags |
| Content Compliance | 3 | Placeholder text, no Whiskercloud mentions, UserWay widget |
| Navigation | 2 | Nav link validation, social links placement |
| Grammar & Spelling | 1 | LanguageTool API checks all visible text for errors |
| Security | 1 | Mixed content detection (HTTP resources on HTTPS pages) |
| Partner-Specific | 9 | CTA text, H1 format, Birdeye widget, service count, form presence |

### Human Review (flagged with context)

| Category | Why Human Needed |
|----------|-----------------|
| Brand Tone | Subjective -- requires understanding client voice |
| Image Appropriateness | Sensitive contexts (euthanasia pages) need judgment |
| Visual Consistency | Alignment, spacing, color matching requires visual assessment |
| Layout & Branding | Must match client's specific brand identity |
| Photo Content Rules | No syringes/gloves -- needs image recognition (future AI) |
| Partner Edge Cases | Unique situations not covered by standard rules |

### Key Principle

The scanner handles the **tedious, deterministic 80%** of QA. Humans spend
their time on the **judgment-based 20%** where they add real value. The scanner
also pre-surfaces context for human reviewers (page URLs, specific elements)
so they don't have to hunt for issues.

---

## 4. Tooling Assumptions

### Prototype (Built and Working)

- **Language**: Python 3.12
- **Web Crawling**: `requests` + `BeautifulSoup4` (HTML parsing)
- **Concurrency**: `ThreadPoolExecutor` for parallel link checking
- **Output**: Text report + JSON (for machine consumption / Wrike integration)
- **No external dependencies**: No paid APIs, no browser automation required
  for core checks

### Production Recommendations

| Tool | Purpose | Why |
|------|---------|-----|
| Python + BeautifulSoup | Core scanning engine | Simple, fast, maintainable |
| Playwright/Puppeteer | Visual/responsive checks | Headless browser for screenshots, mobile emulation |
| Google Lighthouse CI | Performance scoring | Industry-standard, automatable |
| Wrike API | Report delivery + task management | Direct integration with existing workflow |
| JSON rule files | Partner-specific rules | Easy to update without code changes |
| PostgreSQL/SQLite | Audit trail | Persistent QA history for every scan |

### What We Don't Need

- No AI/LLM required for the core rule checks (they're deterministic)
- No expensive SaaS QA tools (we own the rules, we can run them directly)
- AI can be added later for content quality scoring, image analysis, and
  grammar checking as an enhancement layer

---

## 5. Risks, Gaps, and Edge Cases

### Risks

| Risk | Mitigation |
|------|------------|
| Scanner misses issues (false negatives) | Run in parallel with human QA during pilot; compare results |
| Scanner flags non-issues (false positives) | Tunable thresholds; WARN vs FAIL distinction |
| Staging sites behind auth | Scanner supports authenticated sessions; WP admin checks need API access |
| Rule changes not reflected | Rules defined in data files, not hardcoded; QA team can update |
| Dynamic content (JS-rendered) | Use headless browser (Playwright) for pages that need JS execution |

### Current Gaps (Addressable)

1. **WordPress Admin Checks**: The prototype scans front-end only. Back-end
   checks (plugin versions, Divi settings, timezone, form notifications)
   require WP REST API or WP-CLI access.
2. **Visual Layout Judgment**: Alignment, spacing, and "does this look right"
   still requires a human eye — this is inherently subjective.
3. **Form Submission Testing**: Actually submitting forms to verify redirects
   and notifications requires a browser automation framework.

### Edge Cases

- **Multi-location clinics**: Some sites serve multiple locations; scanner
  needs to validate per-location data
- **International sites**: UK/Australia date formats, spelling conventions
  (colour vs color)
- **Partner template updates**: When a partner changes their style guide,
  rules must be updated in the rule files
- **One-off customizations**: Some hospitals have unique requirements noted
  in Wrike -- the scanner flags these for human review

---

## 6. Pilot Recommendation

### Start With: Express Builds, Independent Partner, Full Build Phase

**Why Express**: Highest volume, most standardized, largest time savings.

**Why Independent**: Simplest rule set (no partner-specific style guide
overlays), so easiest to validate scanner accuracy.

**Why Full Build Phase**: This is the longest manual QA phase (~90 min/site)
with the most automatable checks, so it demonstrates the biggest impact.

### Pilot Plan

1. **Week 1**: Run scanner in shadow mode alongside manual QA on 5 Express
   Independent Full builds. Compare scanner results to human results. Measure
   agreement rate.

2. **Week 2**: Tune rules based on discrepancies. Add any missing checks
   identified. Target >95% agreement with human QA.

3. **Week 3**: Switch to scanner-first workflow: scanner runs first, human
   reviews only flagged items + spot-checks 3-5 automated passes.

4. **Week 4**: Expand to Western partner (add partner-specific rules).
   Repeat validation cycle.

### Success Metrics for Pilot

- Scanner catches >= 95% of issues that human QA catches
- False positive rate < 10%
- QA time per site reduced by >= 50%
- Zero defects escape to production that scanner should have caught

---

## Architecture Overview

```
   WRIKE                          WEB BROWSER
   (auto-trigger)                 (manual trigger)
        |                              |
        v                              v
  /webhook/wrike              http://localhost:5000
        |                              |
        +-------> app.py <-------------+
                  (Flask web server)
                     |
        +------------+------------+
        |                         |
  qa_rules.py              qa_scanner.py
  (41 rules, partner       (site crawler +
   overlays, phase          30 check functions)
   filtering)                    |
                           qa_report.py
                           (HTML reports,
                            Wrike comments,
                            JSON audit trail)
```

### How Users Interact (No Code Required)

**Option A -- Web Browser (manual scan):**
1. Open http://localhost:5000 in any browser
2. Paste the site URL, pick Partner and Phase from dropdowns
3. Click "Run QA Scan"
4. View the HTML report in your browser, share the link

**Option B -- Wrike Automation (zero-touch):**
1. Designer fills in 3 custom fields on the Wrike task:
   - Site URL, Partner, Build Phase
2. Designer moves the task to "QA In Progress"
3. Wrike automation fires a webhook to the scanner
4. Scanner runs, posts results back as a Wrike comment
5. QA specialist sees the results on the task -- no tools to open

### Files Delivered

| File | Description |
|------|-------------|
| `app.py` | Web app + Wrike webhook -- the main thing users interact with |
| `qa_rules.py` | Rule engine with 41 rules, partner overlays, phase filtering |
| `qa_scanner.py` | Site crawler + 30 automated check functions |
| `qa_report.py` | HTML report, Wrike comment, and JSON audit trail generators |
| `run_qa.py` | CLI fallback for testing / CI pipelines |
| `demo_report.html` | Example HTML report from scanning the Western test site |
| `demo_report.json` | Machine-readable version for audit trail |
| `proposal.html` | Professional 17-slide presentation (print to PDF from browser) |
| `PROPOSAL.md` | This document (markdown version) |

### Wrike Integration Setup

1. Create 3 custom fields on QA tasks in Wrike:
   - **Site URL** (text field)
   - **Partner** (dropdown: Independent, Western, Heartland, etc.)
   - **Build Phase** (dropdown: Prototype, Full Build, Final)
2. Create a Wrike Automation rule:
   - Trigger: "When task status changes to QA In Progress"
   - Action: "Send webhook to https://your-server/webhook/wrike"
3. Set environment variables: `WRIKE_API_TOKEN`, custom field IDs
4. The scanner handles everything else automatically
5. Reports are generated as PDFs and attached to the Wrike task (not posted
   as text comments, so the full formatted report is preserved)

---

## Why This Wins

1. **It works today.** Not a concept -- a running tool that scans real sites
   and produces real reports.

2. **It maps directly to the actual checklists.** Every rule ID traces back
   to a specific row in the QA spreadsheet. No guessing about coverage.

3. **Partner-specific rules are first-class.** Western gets different checks
   than Independent. Adding a new partner is adding a list of rules, not
   rewriting code.

4. **It produces auditable output.** JSON reports create a permanent,
   searchable QA history. No more manual logging.

5. **Humans do less busywork, more judgment.** The scanner handles the 80%
   that's mechanical. QA specialists focus on the 20% that requires expertise.

6. **It scales.** Scanning 100 sites costs the same as scanning 1 -- machine
   time, not human hours.
