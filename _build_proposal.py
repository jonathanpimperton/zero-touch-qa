import os
import base64


def _load_asset(filename: str) -> str:
    """Load a PNG file as a base64 data URI."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{b64}"
    except FileNotFoundError:
        return ""


def main():
    # Brand assets
    logo_uri = _load_asset("Petdesk Logo.png")  # Purple text (light backgrounds)
    logo_white_uri = _load_asset("Petdesk Logo White Text.png")  # White text (dark backgrounds)
    bg_purple_uri = _load_asset("Petdesk background purple.png")  # Brand purple texture

    # Screenshots of the web interface
    ss_scanner = _load_asset("screenshot_scanner.png")
    ss_report = _load_asset("screenshot_report.png")
    ss_history = _load_asset("screenshot_history.png")
    ss_rules = _load_asset("screenshot_rules.png")

    slides = []

    # SLIDE 1: Title slide (DARK)
    slides.append(f'''
    <div class="slide dark-slide" style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;">
        <div class="slide-logo-wrap" style="position: absolute; top: 0.5in; left: 50%; transform: translateX(-50%); bottom: auto;">
            <img src="{logo_white_uri or logo_uri}" alt="PetDesk Logo" />
        </div>
        <h1 style="font-size: 44px; color: #fff; margin-bottom: 16px;">Zero-Touch QA</h1>
        <p style="font-size: 22px; color: #DDEE91; font-weight: 600; margin-bottom: 24px;">Automated Website Quality at Scale</p>
        <p style="font-size: 16px; color: rgba(255,255,255,0.9); margin-bottom: 8px;">Jonathan Pimperton</p>
        <p style="font-size: 14px; color: rgba(255,255,255,0.6);">Hackathon Submission — Automation & AI Track</p>
        <div class="slide-num">1</div>
    </div>
    ''')

    # SLIDE 2: The Problem
    slides.append(f'''
    <div class="slide">
        <h2>The Problem</h2>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px;">
            <div class="card">
                <h3 style="color: #5820BA;">Slow</h3>
                <p style="font-size: 13px; line-height: 1.5;">45–90 minutes per site, manually checking every item</p>
            </div>
            <div class="card">
                <h3 style="color: #5820BA;">Repetitive</h3>
                <p style="font-size: 13px; line-height: 1.5;">80% of checks are the same deterministic rules every time</p>
            </div>
            <div class="card">
                <h3 style="color: #5820BA;">Error-Prone</h3>
                <p style="font-size: 13px; line-height: 1.5;">Attention fatigue means issues get missed inconsistently</p>
            </div>
            <div class="card">
                <h3 style="color: #5820BA;">Unscalable</h3>
                <p style="font-size: 13px; line-height: 1.5;">More sites = more QA hours. Headcount must grow linearly.</p>
            </div>
        </div>
        <div class="callout">
            <p style="font-size: 14px; line-height: 1.6; font-weight: 500;">80% of QA checks don't require judgment — they require verification against known rules. Machines are better at this than humans.</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">2</div>
    </div>
    ''')

    # SLIDE 3: Current vs Future (COMPACT - 11px fonts)
    slides.append(f'''
    <div class="slide">
        <h2>Current QA vs Zero-Touch QA</h2>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
            <div>
                <div style="background: #3C1161; color: white; padding: 6px 12px; border-radius: 4px; margin-bottom: 10px; font-weight: 600; font-size: 13px;">Current (Manual)</div>
                <div class="flow-box">Designer completes build</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">QA specialist opens checklist</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">Manually checks each item (40-70+)</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">If failures, send back</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">Repeat</div>
            </div>
            <div>
                <div style="background: #22c55e; color: white; padding: 6px 12px; border-radius: 4px; margin-bottom: 10px; font-weight: 600; font-size: 13px;">Zero-Touch QA</div>
                <div class="flow-box">Designer completes build</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">Scanner runs automatically</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">Report generated</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box" style="font-size: 10px; padding: 6px 10px;">If 0 failures: Ready for Delivery<br/>If failures: human reviews flagged items</div>
                <div class="flow-arrow">↓</div>
                <div class="flow-box">Final sign-off</div>
            </div>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">3</div>
    </div>
    ''')

    # SLIDE 4: Time Savings
    slides.append(f'''
    <div class="slide">
        <h2>Projected Time Savings</h2>
        <table class="data-table" style="margin-bottom: 24px;">
            <thead>
                <tr>
                    <th>Phase</th>
                    <th>Current (Manual)</th>
                    <th>Future (Automated + Human)</th>
                    <th>Savings</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Prototype QA</td>
                    <td>~45 min/site</td>
                    <td>~5 min scan + ~10 min review</td>
                    <td style="color: #22c55e; font-weight: 700;">~65%</td>
                </tr>
                <tr>
                    <td>Full Build QA</td>
                    <td>~90 min/site</td>
                    <td>~5 min scan + ~25 min review</td>
                    <td style="color: #22c55e; font-weight: 700;">~67%</td>
                </tr>
                <tr>
                    <td>Final QA</td>
                    <td>~60 min/site</td>
                    <td>~5 min scan + ~15 min review</td>
                    <td style="color: #22c55e; font-weight: 700;">~67%</td>
                </tr>
            </tbody>
        </table>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
            <div class="metric-box">
                <div class="metric-value">65-67%</div>
                <div class="metric-label">Time Savings</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">41</div>
                <div class="metric-label">Automated Checks</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">80%</div>
                <div class="metric-label">Work Automated</div>
            </div>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">4</div>
    </div>
    ''')

    # SLIDE 5: What Gets Automated
    slides.append(f'''
    <div class="slide">
        <h2>What Gets Automated</h2>
        <table class="data-table" style="margin-bottom: 12px; font-size: 12px;">
            <thead>
                <tr>
                    <th style="padding: 8px 12px;">Category</th>
                    <th style="padding: 8px 12px;">Checks</th>
                </tr>
            </thead>
            <tbody>
                <tr><td style="padding: 6px 12px;">Template Text</td><td style="padding: 6px 12px;">7 checks</td></tr>
                <tr><td style="padding: 6px 12px;">Links & Images</td><td style="padding: 6px 12px;">4 checks</td></tr>
                <tr><td style="padding: 6px 12px;">Footer Compliance</td><td style="padding: 6px 12px;">3 checks</td></tr>
                <tr><td style="padding: 6px 12px;">SEO & Social Sharing</td><td style="padding: 6px 12px;">8 checks</td></tr>
                <tr><td style="padding: 6px 12px;">Content</td><td style="padding: 6px 12px;">3 checks</td></tr>
                <tr><td style="padding: 6px 12px;">Navigation</td><td style="padding: 6px 12px;">2 checks</td></tr>
                <tr><td style="padding: 6px 12px;">Grammar & Spelling</td><td style="padding: 6px 12px;">1 check</td></tr>
                <tr><td style="padding: 6px 12px;">Security (Mixed Content)</td><td style="padding: 6px 12px;">1 check</td></tr>
                <tr><td style="padding: 6px 12px;">Partner-Specific</td><td style="padding: 6px 12px;">9 checks</td></tr>
            </tbody>
        </table>
        <div style="background: #FAF5FF; border-left: 4px solid #5820BA; padding: 10px 16px; border-radius: 4px;">
            <p style="font-size: 13px; font-weight: 500;">41 checks run automatically. 4+ items flagged for human review.</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">5</div>
    </div>
    ''')

    # SLIDE 6: What Humans Still Do
    slides.append(f'''
    <div class="slide">
        <h2>What Humans Still Review</h2>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin-bottom: 24px;">
            <div class="card">
                <h3 style="color: #5820BA;">Brand Tone</h3>
                <p style="font-size: 13px; line-height: 1.5;">Subjective — requires understanding client voice</p>
            </div>
            <div class="card">
                <h3 style="color: #5820BA;">Image Appropriateness</h3>
                <p style="font-size: 13px; line-height: 1.5;">Sensitive contexts (euthanasia pages) need judgment</p>
            </div>
            <div class="card">
                <h3 style="color: #5820BA;">Visual Consistency</h3>
                <p style="font-size: 13px; line-height: 1.5;">Alignment, spacing, color matching</p>
            </div>
            <div class="card">
                <h3 style="color: #5820BA;">Layout & Branding</h3>
                <p style="font-size: 13px; line-height: 1.5;">Must match client's specific brand identity</p>
            </div>
        </div>
        <div class="callout">
            <p style="font-size: 14px; line-height: 1.6; font-weight: 500;">The scanner handles the tedious 80%. Humans focus on the judgment-based 20% where they add real value.</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">6</div>
    </div>
    ''')

    # SLIDE 7: How It Works (Architecture)
    slides.append(f'''
    <div class="slide">
        <h2>How It Works</h2>
        <div style="display: flex; align-items: center; justify-content: center; gap: 24px; margin: 40px 0;">
            <div class="big-flow-box" style="background: #3C1161;">
                <div class="big-flow-title">Someone triggers a scan</div>
                <div class="big-flow-subtitle">From the web app or from Wrike</div>
            </div>
            <div style="font-size: 32px; color: #5820BA;">→</div>
            <div class="big-flow-box" style="background: #22c55e;">
                <div class="big-flow-title">Scanner reads the website</div>
                <div class="big-flow-subtitle">Crawls pages, checks against QA rules</div>
            </div>
            <div style="font-size: 32px; color: #5820BA;">→</div>
            <div class="big-flow-box" style="background: #2DCCE8;">
                <div class="big-flow-title">Report appears</div>
                <div class="big-flow-subtitle">HTML report with score, failures, and checklist</div>
            </div>
        </div>
        <div style="background: #FAF5FF; border-left: 4px solid #2DCCE8; padding: 12px 16px; border-radius: 4px; margin-top: 24px;">
            <p style="font-size: 14px; font-weight: 500;">No code knowledge needed. Open the web app, paste a URL, click Scan.</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">7</div>
    </div>
    ''')

    # SLIDE 8: How Users Interact
    slides.append(f'''
    <div class="slide">
        <h2>How Users Interact</h2>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start;">
            <div>
                <div class="interaction-card" style="border: 2px solid #22c55e; position: relative; margin-bottom: 12px;">
                    <div class="tag" style="background: #22c55e;">AVAILABLE NOW</div>
                    <h3 style="color: #22c55e; margin-bottom: 8px; font-size: 14px;">Web Browser</h3>
                    <ol style="font-size: 12px; line-height: 1.7; padding-left: 18px; margin: 0;">
                        <li>Open the scanner web app</li>
                        <li>Paste the site URL, pick Partner and Phase</li>
                        <li>Click <strong>Run QA Scan</strong></li>
                        <li>View and share the HTML report</li>
                    </ol>
                </div>
                <div class="interaction-card" style="border: 2px solid #9ca3af; position: relative;">
                    <div class="tag" style="background: #9ca3af;">FUTURE</div>
                    <h3 style="color: #6b7280; margin-bottom: 8px; font-size: 14px;">Wrike Integration</h3>
                    <p style="font-size: 12px; line-height: 1.6; margin: 0;">Move a Wrike task to &ldquo;QA In Progress&rdquo; &rarr; scan runs automatically &rarr; PDF report attached to the task.</p>
                </div>
            </div>
            {f'<div style="border: 2px solid #e5e7eb; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);"><img src="{ss_scanner}" alt="Scanner UI" style="width: 100%; display: block;" /></div>' if ss_scanner else ''}
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">8</div>
    </div>
    ''')

    # SLIDE 9: Live Demo Results
    slides.append(f'''
    <div class="slide">
        <h2>Live Demo: Real Scan Results</h2>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 20px;">
            <div class="score-card">
                <div class="score-ring" style="background: conic-gradient(#5820BA 0% 82%, #e5e7eb 82% 100%);">
                    <div class="score-inner">
                        <div class="score-number">82</div>
                        <div class="score-label">/100</div>
                    </div>
                </div>
                <h3 style="text-align: center; margin-top: 16px;">Test Site</h3>
                <p style="text-align: center; font-size: 12px; color: #6b7280;">thestorybookal</p>
                <p style="text-align: center; font-size: 13px; margin-top: 8px;">9 failures, 4 warnings</p>
            </div>
            <div class="score-card">
                <div class="score-ring" style="background: conic-gradient(#5820BA 0% 94%, #e5e7eb 94% 100%);">
                    <div class="score-inner">
                        <div class="score-number">94</div>
                        <div class="score-label">/100</div>
                    </div>
                </div>
                <h3 style="text-align: center; margin-top: 16px;">Final QA Site</h3>
                <p style="text-align: center; font-size: 12px; color: #6b7280;">essentialsfina</p>
                <p style="text-align: center; font-size: 13px; margin-top: 8px;">2 failures, 3 warnings</p>
            </div>
        </div>
        <div style="background: #FAF5FF; border-left: 4px solid #5820BA; padding: 12px 16px; border-radius: 4px;">
            <p style="font-size: 13px; font-weight: 500;">These are real scans against the provided test sites, not mock data.
            <br/>Try it live: <a href="https://zero-touch-qa.onrender.com" target="_blank" style="color: #5820BA; font-weight: 600;">zero-touch-qa.onrender.com</a></p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">9</div>
    </div>
    ''')

    # SLIDE 10: Sample Report Preview
    slides.append(f'''
    <div class="slide">
        <h2>What the Report Looks Like</h2>
        <div style="display: grid; grid-template-columns: 3fr 2fr; gap: 20px; align-items: start;">
            {f'<div style="border: 2px solid #e5e7eb; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);"><img src="{ss_report}" alt="QA Report" style="width: 100%; display: block;" /></div>' if ss_report else ''}
            <div>
                <div style="margin-bottom: 12px;">
                    <div style="background: #fef2f2; border-left: 3px solid #ef4444; padding: 8px 12px; border-radius: 4px; margin-bottom: 6px;">
                        <p style="font-size: 12px; font-weight: 600; color: #dc2626;">Failures &mdash; action required</p>
                    </div>
                    <div style="background: #fffbeb; border-left: 3px solid #f59e0b; padding: 8px 12px; border-radius: 4px; margin-bottom: 6px;">
                        <p style="font-size: 12px; font-weight: 600; color: #d97706;">Warnings &mdash; review recommended</p>
                    </div>
                    <div style="background: #faf5ff; border-left: 3px solid #5820BA; padding: 8px 12px; border-radius: 4px; margin-bottom: 6px;">
                        <p style="font-size: 12px; font-weight: 600; color: #5820BA;">Human Review &mdash; interactive checklist</p>
                    </div>
                    <div style="background: #f3f4f6; border-left: 3px solid #9ca3af; padding: 8px 12px; border-radius: 4px;">
                        <p style="font-size: 12px; font-weight: 600; color: #6b7280;">Full Breakdown &mdash; every check by category</p>
                    </div>
                </div>
                <div style="background: #FAF5FF; border-left: 4px solid #2DCCE8; padding: 10px 14px; border-radius: 4px;">
                    <p style="font-size: 12px; font-weight: 500;">HTML reports: viewable in any browser, printable as PDF, shareable via link.</p>
                </div>
            </div>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">10</div>
    </div>
    ''')

    # SLIDE 11: QA Rules System
    slides.append(f'''
    <div class="slide">
        <h2>How QA Rules Work</h2>
        <div style="display: grid; grid-template-columns: 2fr 3fr; gap: 20px; align-items: start;">
            <div>
                <ul style="font-size: 12px; line-height: 1.8; padding-left: 18px; margin-bottom: 12px;">
                    <li>Rules stored in a simple data file &mdash; not buried in code</li>
                    <li>Each rule has a weight (1x&ndash;5x) that determines score impact</li>
                    <li>Different rules for different partners and build phases</li>
                </ul>
                <div class="card" style="background: #ecfdf5; border-left: 4px solid #22c55e; margin-bottom: 8px;">
                    <h3 style="color: #22c55e; font-size: 12px;">QA Team Self-Service</h3>
                    <p style="font-size: 11px; line-height: 1.5;">View, add, edit, or delete rules through the browser. No coding needed. Changes take effect on the next scan.</p>
                </div>
            </div>
            {f'<div style="border: 2px solid #e5e7eb; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);"><img src="{ss_rules}" alt="Rules Viewer" style="width: 100%; display: block;" /></div>' if ss_rules else ''}
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">11</div>
    </div>
    ''')

    # SLIDE 12: Grammar & Spelling Checks
    slides.append(f'''
    <div class="slide">
        <h2>Grammar & Spelling Checks</h2>
        <p style="font-size: 14px; line-height: 1.6; margin-bottom: 16px;">The scanner automatically checks visible text on every page for grammar and spelling errors using LanguageTool (free, open-source).</p>
        <div class="card" style="margin-bottom: 16px;">
            <h3 style="margin-bottom: 10px;">How it works:</h3>
            <ul style="font-size: 13px; line-height: 1.8; padding-left: 20px;">
                <li>Extracts visible text from each page (excluding nav, footer, scripts)</li>
                <li>Sends text to LanguageTool API for analysis</li>
                <li>Reports spelling errors, grammar issues, and suggestions</li>
                <li>Medical/veterinary terms are automatically filtered using pattern matching (e.g. micro-, endo-, cardio-, -ectomy, -ology, -itis) &mdash; no manual allowlist needed</li>
                <li>Spelling = FAIL (loses points), Grammar = WARNING (no point loss)</li>
            </ul>
        </div>
        <div style="background: #DDEE91; border-left: 4px solid #84cc16; padding: 12px 16px; border-radius: 4px;">
            <p style="font-size: 13px; font-weight: 500; color: #3C1161;">Catches typos and misspellings while automatically allowing veterinary terminology. No false positives on medical terms.</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">12</div>
    </div>
    ''')

    # SLIDE 13: Scan History & Audit Trail
    slides.append(f'''
    <div class="slide">
        <h2>Scan History & Audit Trail</h2>
        <div style="display: grid; grid-template-columns: 3fr 2fr; gap: 20px; align-items: start;">
            {f'<div style="border: 2px solid #e5e7eb; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);"><img src="{ss_history}" alt="Scan History" style="width: 100%; display: block;" /></div>' if ss_history else ''}
            <div>
                <p style="font-size: 13px; line-height: 1.6; margin-bottom: 12px;">Every scan is saved with its full HTML report and JSON audit trail.</p>
                <ul style="font-size: 12px; line-height: 1.8; padding-left: 18px; margin-bottom: 12px;">
                    <li>Search by site URL or scan ID</li>
                    <li>Filter by partner, phase, or score range</li>
                    <li>Sortable columns &mdash; click any header</li>
                    <li>Score bar visualization</li>
                    <li>Direct links to full reports and JSON data</li>
                </ul>
                <div style="background: #FAF5FF; border-left: 4px solid #5820BA; padding: 10px 14px; border-radius: 4px;">
                    <p style="font-size: 12px; font-weight: 500;">Scan history is stored in a PostgreSQL database &mdash; persists across deploys.</p>
                </div>
            </div>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">13</div>
    </div>
    ''')

    # SLIDE 14: Technology (DUMBED DOWN)
    slides.append(f'''
    <div class="slide">
        <h2>What Powers This</h2>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 20px;">
            <div class="card" style="background: linear-gradient(135deg, #faf5ff, #f3e8ff); border-left: 4px solid #5820BA;">
                <h3 style="color: #5820BA;">Website Scanner</h3>
                <p style="font-size: 13px; line-height: 1.6;">Crawls every page. Checks links, images, text, structure, metadata, and security. Automatically uses a headless browser for pages with JavaScript-rendered content.</p>
            </div>
            <div class="card" style="background: linear-gradient(135deg, #ecfdf5, #d1fae5); border-left: 4px solid #22c55e;">
                <h3 style="color: #22c55e;">QA Rules Engine</h3>
                <p style="font-size: 13px; line-height: 1.6;">55 rules across 10 categories. Applies the right rules for each partner and build phase. QA team edits rules via the web app.</p>
            </div>
            <div class="card" style="background: linear-gradient(135deg, #ecfeff, #cffafe); border-left: 4px solid #2DCCE8;">
                <h3 style="color: #0891b2;">Report Generator</h3>
                <p style="font-size: 13px; line-height: 1.6;">Scored report with failures, warnings, and a human review checklist. Grammar/spelling, broken images, social sharing, and mixed content all flagged automatically.</p>
            </div>
        </div>
        <div style="background: #FAF5FF; border-left: 4px solid #2DCCE8; padding: 12px 16px; border-radius: 4px;">
            <p style="font-size: 14px; font-weight: 500;">Runs in the cloud. No software to install. Just open the web app.</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">14</div>
    </div>
    ''')

    # SLIDE 15: Gaps & Future Enhancements
    slides.append(f'''
    <div class="slide">
        <h2>Known Gaps & Future Enhancements</h2>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
            <div>
                <div style="background: #ef4444; color: white; padding: 6px 12px; border-radius: 4px; margin-bottom: 12px; font-weight: 600; font-size: 13px;">Current Gaps</div>
                <div class="card" style="margin-bottom: 10px; font-size: 12px;">
                    <h3 style="font-size: 13px;">WordPress Admin Checks</h3>
                    <p style="line-height: 1.5;">Back-end checks (plugin versions, settings) require WordPress API access</p>
                </div>
                <div class="card" style="margin-bottom: 10px; font-size: 12px;">
                    <h3 style="font-size: 13px;">Form Submission Testing</h3>
                    <p style="line-height: 1.5;">Actually submitting forms to verify success pages needs a browser automation tool</p>
                </div>
                <div class="card" style="font-size: 12px;">
                    <h3 style="font-size: 13px;">Visual Layout Judgment</h3>
                    <p style="line-height: 1.5;">Alignment, spacing, and "does this look right" still needs a human eye</p>
                </div>
            </div>
            <div>
                <div style="background: #2DCCE8; color: white; padding: 6px 12px; border-radius: 4px; margin-bottom: 12px; font-weight: 600; font-size: 13px;">Phase 2 Enhancements</div>
                <div class="card" style="margin-bottom: 10px; font-size: 12px;">
                    <h3 style="font-size: 13px;">Wrike Integration</h3>
                    <p style="line-height: 1.5;">Auto-trigger scans when Wrike tasks move to QA, attach PDF reports to the task</p>
                </div>
                <div class="card" style="margin-bottom: 10px; font-size: 12px;">
                    <h3 style="font-size: 13px;">Analytics Dashboard</h3>
                    <p style="line-height: 1.5;">Build trending reports and aggregate analytics from the PostgreSQL scan history &mdash; track QA pass rates over time, common failure patterns, partner comparisons.</p>
                </div>
                <div class="card" style="font-size: 12px;">
                    <h3 style="font-size: 13px;">More Partner Templates</h3>
                    <p style="line-height: 1.5;">Expand partner-specific rules as QA team identifies unique requirements</p>
                </div>
            </div>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">15</div>
    </div>
    ''')

    # SLIDE 16: Pilot Recommendation
    slides.append(f'''
    <div class="slide">
        <h2>Pilot Recommendation</h2>
        <div class="callout" style="margin-bottom: 16px;">
            <p style="font-size: 14px; font-weight: 600; margin-bottom: 4px;">Start with: Express Builds, Independent Partner, Full Build Phase</p>
            <p style="font-size: 12px; line-height: 1.5;">Highest volume, simplest rule set, longest manual QA phase</p>
        </div>
        <div style="margin-bottom: 16px;">
            <h3 style="font-size: 15px; margin-bottom: 10px;">4-Week Plan:</h3>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;">
                <div style="background: #faf5ff; border: 1px solid #e9d5ff; border-radius: 4px; padding: 10px;">
                    <div style="font-weight: 700; color: #5820BA; font-size: 12px; margin-bottom: 4px;">Week 1</div>
                    <p style="font-size: 11px; line-height: 1.4;">Shadow mode — run alongside manual QA, compare results</p>
                </div>
                <div style="background: #faf5ff; border: 1px solid #e9d5ff; border-radius: 4px; padding: 10px;">
                    <div style="font-weight: 700; color: #5820BA; font-size: 12px; margin-bottom: 4px;">Week 2</div>
                    <p style="font-size: 11px; line-height: 1.4;">Tune rules based on discrepancies, target >95% agreement</p>
                </div>
                <div style="background: #faf5ff; border: 1px solid #e9d5ff; border-radius: 4px; padding: 10px;">
                    <div style="font-weight: 700; color: #5820BA; font-size: 12px; margin-bottom: 4px;">Week 3</div>
                    <p style="font-size: 11px; line-height: 1.4;">Scanner-first workflow — human reviews only flagged items</p>
                </div>
                <div style="background: #faf5ff; border: 1px solid #e9d5ff; border-radius: 4px; padding: 10px;">
                    <div style="font-weight: 700; color: #5820BA; font-size: 12px; margin-bottom: 4px;">Week 4</div>
                    <p style="font-size: 11px; line-height: 1.4;">Expand to Western partner</p>
                </div>
            </div>
        </div>
        <div style="background: #DDEE91; border-left: 4px solid #84cc16; padding: 12px 16px; border-radius: 4px;">
            <h3 style="font-size: 13px; margin-bottom: 6px;">Success Metrics:</h3>
            <p style="font-size: 12px; line-height: 1.5;">≥95% catch rate • <10% false positives • ≥50% time reduction • zero escapes</p>
        </div>
        <img src="{logo_uri}" alt="PetDesk Logo" class="slide-logo" />
        <div class="slide-num">16</div>
    </div>
    ''')

    # SLIDE 17: Closing (DARK)
    slides.append(f'''
    <div class="slide dark-slide" style="display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;">
        <div class="slide-logo-wrap" style="position: static; margin-bottom: 24px; transform: none;">
            <img src="{logo_white_uri or logo_uri}" alt="PetDesk Logo" style="height: 40px;" />
        </div>
        <h1 style="font-size: 36px; color: #fff; margin-bottom: 16px;">Zero-Touch QA</h1>
        <p style="font-size: 18px; color: #DDEE91; font-weight: 600; margin-bottom: 20px;">Scan smarter. Ship faster.</p>
        <p style="font-size: 14px; color: rgba(255,255,255,0.8); margin-bottom: 8px;">Ready for pilot</p>
        <p style="font-size: 13px; color: rgba(255,255,255,0.5);">Live demo: <a href="https://zero-touch-qa.onrender.com" target="_blank" id="live-url" style="color: #DDEE91; text-decoration: underline;">zero-touch-qa.onrender.com</a></p>
        <div class="slide-num">17</div>
    </div>
    ''')

    # Write CSS + slides to proposal.html
    html = build_html(slides, logo_uri, bg_purple_uri)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proposal.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Written to {out}")

def build_html(slides, logo_uri, bg_purple_uri=""):
    css = '''
@import url('https://fonts.googleapis.com/css2?family=Red+Hat+Display:wght@700;900&family=DM+Sans:wght@400;500;700&display=swap');
@page { size: 10in 5.625in; margin: 0; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'DM Sans', 'Segoe UI', sans-serif; color: #3C1161; }
.slide {
    width: 10in; height: 5.625in; padding: 0.45in 0.6in; position: relative;
    page-break-after: always; overflow: hidden; background: #fff;
}
.slide:last-child { page-break-after: auto; }
.slide-logo { position: absolute; bottom: 0.3in; left: 0.6in; height: 20px; }
.slide-num { position: absolute; bottom: 0.33in; right: 0.6in; font-size: 10px; color: #9ca3af; }
h1 { font-family: 'Red Hat Display', sans-serif; font-weight: 900; }
h2 { font-family: 'Red Hat Display', sans-serif; font-weight: 700; font-size: 24px; color: #3C1161; margin-bottom: 16px; }
h3 { font-family: 'DM Sans', sans-serif; font-weight: 700; font-size: 14px; color: #3C1161; margin-bottom: 6px; }
.dark-slide { background: linear-gradient(135deg, #190729, #3C1161, #5820BA); color: #fff; }
.dark-slide h2, .dark-slide h3 { color: #fff; }
.dark-slide .slide-num { color: rgba(255,255,255,0.3); }
.dark-slide .slide-logo-wrap {
    display: inline-block;
    position: absolute;
    bottom: 0.3in;
    left: 0.6in;
}
.dark-slide .slide-logo-wrap img { height: 18px; display: block; }
.card {
    background: #FAF5FF;
    border: 1px solid #e9d5ff;
    border-radius: 6px;
    padding: 16px;
}
.callout {
    background: #DDEE91;
    border-left: 4px solid #5820BA;
    padding: 14px 18px;
    border-radius: 4px;
}
.flow-box {
    background: #FAF5FF;
    border: 1px solid #c4b5fd;
    border-radius: 4px;
    padding: 8px 12px;
    text-align: center;
    font-size: 11px;
    line-height: 1.4;
}
.flow-arrow {
    text-align: center;
    color: #5820BA;
    font-size: 18px;
    margin: 4px 0;
}
.big-flow-box {
    color: white;
    border-radius: 8px;
    padding: 20px;
    text-align: center;
    flex: 1;
    max-width: 220px;
}
.big-flow-title {
    font-size: 15px;
    font-weight: 700;
    margin-bottom: 8px;
}
.big-flow-subtitle {
    font-size: 12px;
    opacity: 0.9;
    line-height: 1.4;
}
.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.data-table thead {
    background: #3C1161;
    color: white;
}
.data-table th {
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
}
.data-table td {
    padding: 10px 12px;
    border-bottom: 1px solid #e5e7eb;
}
.data-table tbody tr:nth-child(even) {
    background: #fafafa;
}
.metric-box {
    background: linear-gradient(135deg, #faf5ff, #f3e8ff);
    border: 2px solid #5820BA;
    border-radius: 8px;
    padding: 20px;
    text-align: center;
}
.metric-value {
    font-size: 32px;
    font-weight: 900;
    color: #5820BA;
    font-family: 'Red Hat Display', sans-serif;
}
.metric-label {
    font-size: 12px;
    color: #6b7280;
    margin-top: 4px;
}
.interaction-card {
    background: #fafafa;
    border-radius: 8px;
    padding: 20px;
}
.tag {
    position: absolute;
    top: 12px;
    right: 12px;
    color: white;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
}
.score-card {
    text-align: center;
}
.score-ring {
    width: 120px;
    height: 120px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto;
}
.score-inner {
    width: 90px;
    height: 90px;
    background: white;
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}
.score-number {
    font-size: 36px;
    font-weight: 900;
    color: #5820BA;
    font-family: 'Red Hat Display', sans-serif;
    line-height: 1;
}
.score-label {
    font-size: 12px;
    color: #6b7280;
}
'''

    # Replace dark slide gradient with brand texture if available
    if bg_purple_uri:
        css = css.replace(
            "background: linear-gradient(135deg, #190729, #3C1161, #5820BA);",
            f"background: url('{bg_purple_uri}') center/cover no-repeat;"
        )

    slides_html = '\n'.join(slides)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Zero-Touch QA - Hackathon Proposal</title>
<style>
{css}
</style>
</head>
<body>
{slides_html}
</body>
</html>'''

if __name__ == "__main__":
    main()
