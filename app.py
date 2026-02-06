"""
Zero-Touch QA - Web Application
Flask web app that provides:
  1. A browser-based UI for manual scans (no code knowledge needed)
  2. A Wrike webhook endpoint that auto-triggers scans
  3. Posts results back to Wrike tasks via API
"""

import json
import os
import time
import base64
import threading
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string, send_from_directory

from qa_rules import get_rules_for_scan, get_automatable_rules, get_human_review_rules, get_all_rules, get_partner_rule_map, _save_rules
from qa_scanner import SiteCrawler, ScanReport, CheckResult, CHECK_FUNCTIONS
from qa_report import generate_html_report, generate_wrike_comment, generate_json_report
from wp_api import PetDeskQAPluginClient, WordPressAPIClient, WP_CHECK_FUNCTIONS
from db import is_db_available, init_db, db_get_scan_id, db_save_scan, \
    db_load_scan_history, db_get_report, db_seed_from_filesystem

load_dotenv()  # Loads .env file automatically

app = Flask(__name__)

# ---------------------------------------------------------------------------
# PetDesk brand assets (base64 for embedding in HTML)
# ---------------------------------------------------------------------------
_ASSETS_DIR = os.path.dirname(__file__)


def _load_asset(filename: str) -> str:
    try:
        with open(os.path.join(_ASSETS_DIR, filename), "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
    except FileNotFoundError:
        return ""


LOGO_DATA_URI = _load_asset("Petdesk Logo.png")  # Purple text (white bg)
LOGO_WHITE_URI = _load_asset("Petdesk Logo White Text.png")  # White text (dark bg)
BG_PURPLE_URI = _load_asset("Petdesk background purple.png")  # Brand texture

# ---------------------------------------------------------------------------
# Config - loaded from .env file
# ---------------------------------------------------------------------------
WRIKE_API_TOKEN = os.environ.get("WRIKE_API_TOKEN", "")
WRIKE_CUSTOM_FIELD_SITE_URL = os.environ.get("WRIKE_CF_SITE_URL", "")     # custom field ID for site URL
WRIKE_CUSTOM_FIELD_PARTNER = os.environ.get("WRIKE_CF_PARTNER", "")       # custom field ID for partner
WRIKE_CUSTOM_FIELD_PHASE = os.environ.get("WRIKE_CF_PHASE", "")           # custom field ID for phase

# PetDesk QA Plugin API key (shared across all sites with the plugin installed)
PETDESK_QA_API_KEY = os.environ.get("PETDESK_QA_API_KEY", "petdesk-qa-2026-hackathon-key")

# Store scan history in memory (backed by PostgreSQL when DATABASE_URL is set)
scan_history = []

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

_SCAN_COUNTER_FILE = os.path.join(REPORTS_DIR, "scan_counter.json")

# Initialize database (creates tables if needed, no-op if no DATABASE_URL)
init_db()
db_seed_from_filesystem(REPORTS_DIR)


def _get_scan_id(site_url: str, phase: str) -> str:
    """Get or create a scan ID for a site+phase combination.

    Re-scanning the same site at the same build phase reuses the existing ID.
    A new site or new phase gets the next available ID.
    """
    # Try database first
    try:
        scan_id = db_get_scan_id(site_url, phase)
        if scan_id is not None:
            return scan_id
    except Exception as e:
        print(f"[DB] Error getting scan ID, falling back to filesystem: {e}")

    # Fallback: filesystem
    try:
        with open(_SCAN_COUNTER_FILE, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    next_num = data.get("next_id", 1)
    mapping = data.get("mapping", {})

    key = f"{site_url}|{phase}"
    if key in mapping:
        return mapping[key]

    scan_id = f"QA-{next_num:04d}"
    mapping[key] = scan_id
    data["next_id"] = next_num + 1
    data["mapping"] = mapping
    with open(_SCAN_COUNTER_FILE, "w") as f:
        json.dump(data, f, indent=2)
    return scan_id


# ---------------------------------------------------------------------------
# Core scan engine (shared between web UI and Wrike webhook)
# ---------------------------------------------------------------------------

def run_scan(site_url: str, partner: str, phase: str, max_pages: int = 30) -> ScanReport:
    """Run the full QA scan against a site.

    Args:
        site_url: URL of the site to scan
        partner: Partner name (e.g., 'western', 'independent')
        phase: Build phase ('prototype', 'full', 'final')
        max_pages: Maximum pages to crawl
    """
    rules = get_rules_for_scan(partner, phase)
    auto_rules = get_automatable_rules(rules)
    human_rules = get_human_review_rules(rules)

    # Crawl
    crawler = SiteCrawler(site_url)
    pages = crawler.crawl(max_pages=max_pages)

    # Try PetDesk QA Plugin first (recommended - single API key for all sites)
    # Falls back to HUMAN_REVIEW if plugin not installed
    wp_client = PetDeskQAPluginClient(site_url, PETDESK_QA_API_KEY)
    if not wp_client.is_available():
        wp_client = None  # Will trigger HUMAN_REVIEW fallback in check functions

    # Run checks
    all_results = []
    for rule in auto_rules:
        fn_name = rule.get("check_fn")
        if fn_name and fn_name in CHECK_FUNCTIONS:
            fn = CHECK_FUNCTIONS[fn_name]
            try:
                # WordPress checks need the wp_client parameter
                if fn_name in WP_CHECK_FUNCTIONS:
                    results = fn(pages, rule, wp_client=wp_client)
                else:
                    results = fn(pages, rule)
                all_results.extend(results)
            except Exception as e:
                all_results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
                    details=f"Automated check encountered an error. Verify manually. ({str(e)})",
                ))
        else:
            all_results.append(CheckResult(
                rule_id=rule["id"], category=rule["category"],
                check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
                details="Automated check not yet implemented. Verify manually.",
            ))

    for rule in human_rules:
        all_results.append(CheckResult(
            rule_id=rule["id"], category=rule["category"],
            check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
            details="Requires human judgment",
        ))

    total_points_lost = sum(r.points_lost for r in all_results)
    score = max(0, 100 - total_points_lost)

    report = ScanReport(
        site_url=site_url, partner=partner, phase=phase,
        scan_time=datetime.now().isoformat(),
        pages_scanned=len(pages), total_checks=len(all_results),
        passed=sum(1 for r in all_results if r.status == "PASS"),
        failed=sum(1 for r in all_results if r.status == "FAIL"),
        warnings=sum(1 for r in all_results if r.status == "WARN"),
        human_review=sum(1 for r in all_results if r.status == "HUMAN_REVIEW"),
        score=score, results=all_results,
    )
    return report


# ---------------------------------------------------------------------------
# Wrike API helpers
# ---------------------------------------------------------------------------

def _sanitize_wrike_id(task_id: str) -> str:
    """Sanitize a Wrike task ID to prevent URL injection."""
    import re as _re
    return _re.sub(r"[^a-zA-Z0-9_-]", "", task_id)


def wrike_post_comment(task_id: str, html_comment: str):
    """Post a comment to a Wrike task."""
    import requests
    if not WRIKE_API_TOKEN:
        print("[Wrike] No API token configured - skipping comment post")
        return
    task_id = _sanitize_wrike_id(task_id)
    url = f"https://www.wrike.com/api/v4/tasks/{task_id}/comments"
    headers = {"Authorization": f"Bearer {WRIKE_API_TOKEN}"}
    data = {"text": html_comment}
    resp = requests.post(url, headers=headers, json=data)
    print(f"[Wrike] Posted comment to task {task_id}: {resp.status_code}")
    return resp


def wrike_get_task(task_id: str) -> dict:
    """Get task details from Wrike, including custom fields."""
    import requests
    if not WRIKE_API_TOKEN:
        return {}
    task_id = _sanitize_wrike_id(task_id)
    url = f"https://www.wrike.com/api/v4/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {WRIKE_API_TOKEN}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("data"):
            return data["data"][0]
    return {}


def extract_wrike_custom_fields(task: dict) -> dict:
    """Extract partner, phase, and site URL from Wrike custom fields."""
    result = {"site_url": "", "partner": "independent", "phase": "full"}
    for cf in task.get("customFields", []):
        if cf["id"] == WRIKE_CUSTOM_FIELD_SITE_URL:
            result["site_url"] = cf.get("value", "")
        elif cf["id"] == WRIKE_CUSTOM_FIELD_PARTNER:
            result["partner"] = cf.get("value", "independent").lower()
        elif cf["id"] == WRIKE_CUSTOM_FIELD_PHASE:
            result["phase"] = cf.get("value", "full").lower()
    return result


# ---------------------------------------------------------------------------
# Routes - Web UI
# ---------------------------------------------------------------------------

HOME_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zero-Touch QA Scanner - PetDesk</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, 'Helvetica Neue', Arial, sans-serif;
               background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%); color: #111827; min-height: 100vh; -webkit-font-smoothing: antialiased; overflow-y: scroll; }

        /* Fade in animation */
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .fade-in { animation: fadeInUp 0.4s ease-out forwards; }
        .fade-in-delay { animation: fadeInUp 0.4s ease-out 0.1s forwards; opacity: 0; }

        .header {
            {% if bg_purple_uri %}background: url('{{ bg_purple_uri }}') center/cover no-repeat;{% else %}background: linear-gradient(135deg, #1e1b4b 0%, #312e81 30%, #4f46e5 70%, #6366f1 100%);{% endif %}
            color: white; padding: 32px 40px; position: relative; overflow: hidden;
            box-shadow: 0 4px 20px rgba(79,70,229,0.25);
        }
        .header::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background: radial-gradient(ellipse at 30% 0%, rgba(255,255,255,0.1) 0%, transparent 50%);
        }
        .header::after {
            content: ''; position: absolute; top: -80%; right: -20%; width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 60%); border-radius: 50%;
        }
        .header-inner { display: flex; justify-content: space-between; align-items: center; position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; width: 100%; }
        .header-left { display: flex; align-items: center; gap: 18px; }
        .header-logo { height: 34px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }
        .header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; text-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        .header p { font-size: 13px; opacity: 0.8; margin-top: 3px; font-weight: 500; }
        .nav-links { display: flex; gap: 8px; }
        .nav-links a { color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 600;
            text-decoration: none; padding: 8px 16px; border-radius: 8px; transition: all 0.2s;
            border: 1px solid transparent; }
        .nav-links a:hover { background: rgba(255,255,255,0.15); color: #fff; border-color: rgba(255,255,255,0.2); }

        .container { max-width: 720px; margin: 40px auto; padding: 0 24px; }

        .card {
            background: #fff; border-radius: 20px; padding: 36px 40px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 10px 20px -5px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid rgba(0,0,0,0.03);
            position: relative; overflow: hidden;
        }
        .card::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, #4f46e5, #6366f1, #818cf8);
        }
        .card-title { font-size: 18px; font-weight: 700; margin-bottom: 6px; color: #1e1b4b; }
        .card-subtitle { font-size: 14px; color: #6b7280; margin-bottom: 24px; }

        .form-group { margin-top: 22px; }
        .form-group:first-of-type { margin-top: 0; }
        label { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 700; color: #374151; margin-bottom: 8px;
                text-transform: uppercase; letter-spacing: 0.5px; }
        .label-icon { width: 16px; height: 16px; opacity: 0.6; }
        input[type=text], select {
            width: 100%; padding: 13px 16px; border: 2px solid #e5e7eb; border-radius: 12px;
            font-size: 15px; transition: all 0.2s ease; background: #fafafa; color: #111827;
        }
        input:focus, select:focus { outline: none; border-color: #6366f1; background: #fff;
                                     box-shadow: 0 0 0 4px rgba(99,102,241,0.1); }
        input::placeholder { color: #9ca3af; }
        select { cursor: pointer; }

        .btn {
            background: linear-gradient(135deg, #4f46e5, #6366f1); color: white; border: none;
            padding: 15px 32px; border-radius: 12px; font-size: 16px; font-weight: 600;
            cursor: pointer; margin-top: 32px; width: 100%; transition: all 0.25s ease;
            box-shadow: 0 4px 14px rgba(79,70,229,0.35);
            position: relative; overflow: hidden;
        }
        .btn::before {
            content: ''; position: absolute; top: 0; left: -100%; width: 100%; height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
            transition: left 0.5s ease;
        }
        .btn:hover::before { left: 100%; }
        .btn:hover { background: linear-gradient(135deg, #4338ca, #4f46e5); box-shadow: 0 6px 20px rgba(79,70,229,0.4); transform: translateY(-2px); }
        .btn:active { transform: translateY(0); }
        .btn:disabled { background: linear-gradient(135deg, #a5b4fc, #c7d2fe); cursor: not-allowed; box-shadow: none; transform: none; }

        .spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid rgba(255,255,255,0.3);
                   border-top-color: white; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 10px; }
        @keyframes spin { to { transform: rotate(360deg); } }

        .status { text-align: center; padding: 20px 24px; font-size: 14px; color: #4b5563; margin-top: 20px;
                  background: linear-gradient(135deg, #f8fafc, #f1f5f9); border-radius: 12px; border: 1px solid #e5e7eb;
                  line-height: 1.6; }
        .status-steps { display: flex; flex-direction: column; gap: 8px; text-align: left; }
        .status-step { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 8px; font-size: 13px; }
        .status-step.active { background: #eef2ff; color: #4338ca; font-weight: 600; }
        .status-step.done { color: #16a34a; }
        .status-step .step-icon { width: 20px; text-align: center; }

        .history { margin-top: 40px; }
        .history-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .history-title { font-size: 16px; font-weight: 700; color: #1e1b4b; }
        .history-count { font-size: 12px; color: #9ca3af; background: #f1f5f9; padding: 4px 10px; border-radius: 12px; }
        .history-item {
            display: flex; justify-content: space-between; align-items: center;
            padding: 16px 20px; background: #fff; border-radius: 14px; margin-bottom: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 2px 6px rgba(0,0,0,0.02);
            transition: all 0.2s ease; border: 1px solid transparent;
        }
        .history-item:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); transform: translateY(-1px); border-color: #e5e7eb; }
        .history-left { display: flex; align-items: center; gap: 14px; }
        .scan-id-badge { background: linear-gradient(135deg, #eef2ff, #e0e7ff); color: #4338ca; font-size: 11px; font-weight: 700;
                         padding: 6px 10px; border-radius: 8px; font-family: 'SF Mono', Monaco, monospace; letter-spacing: 0.3px; }
        .history-site { font-size: 14px; font-weight: 600; color: #111827; }
        .history-meta { font-size: 12px; color: #9ca3af; margin-top: 3px; display: flex; align-items: center; gap: 6px; }
        .meta-dot { width: 3px; height: 3px; background: #d1d5db; border-radius: 50%; }
        .score-badge { font-weight: 800; font-size: 18px; min-width: 65px; text-align: center; }
        .score-good { color: #16a34a; } .score-ok { color: #d97706; } .score-bad { color: #dc2626; }
        .score-ring { width: 48px; height: 48px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
                      font-weight: 800; font-size: 14px; flex-shrink: 0; }
        .score-ring.good { background: linear-gradient(135deg, #dcfce7, #bbf7d0); color: #166534; border: 2px solid #86efac; }
        .score-ring.ok { background: linear-gradient(135deg, #fef3c7, #fde68a); color: #92400e; border: 2px solid #fcd34d; }
        .score-ring.bad { background: linear-gradient(135deg, #fee2e2, #fecaca); color: #991b1b; border: 2px solid #fca5a5; }
        a { color: #4f46e5; text-decoration: none; font-weight: 600; font-size: 13px; transition: color 0.2s; }
        a:hover { color: #4338ca; }
        .view-link { display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px; border-radius: 8px;
                     background: #f8fafc; border: 1px solid #e5e7eb; transition: all 0.2s; }
        .view-link:hover { background: #eef2ff; border-color: #c7d2fe; }
        .empty-history { text-align: center; padding: 40px 28px; color: #6b7280; font-size: 14px;
                         background: linear-gradient(135deg, #fff, #f8fafc); border-radius: 16px;
                         box-shadow: 0 1px 3px rgba(0,0,0,0.04); border: 2px dashed #e5e7eb; }
        .empty-icon { font-size: 32px; margin-bottom: 12px; opacity: 0.5; }
        .footer { text-align: center; padding: 32px 0; font-size: 12px; color: #9ca3af; }
        .footer-inner { display: flex; align-items: center; justify-content: center; gap: 8px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div class="header-left">
                {% if logo_white_uri %}<img src="{{ logo_white_uri }}" alt="PetDesk" class="header-logo">{% elif logo_uri %}<img src="{{ logo_uri }}" alt="PetDesk" class="header-logo">{% endif %}
                <div>
                    <h1>Zero-Touch QA</h1>
                    <p>Automated website quality assurance</p>
                </div>
            </div>
            <div class="nav-links">
                <a href="/">Scanner</a>
                <a href="/rules">View Rules</a>
                <a href="/rules/edit">Edit Rules</a>
                <a href="/history">History</a>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="card fade-in">
            <div class="card-title">Run a QA Scan</div>
            <div class="card-subtitle">Enter a WP Engine staging URL to scan against the QA checklist</div>
            <form id="scanForm">
                <div class="form-group">
                    <label for="site_url">
                        <svg class="label-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                        Website URL
                    </label>
                    <input type="text" id="site_url" name="site_url" placeholder="e.g. clinic-name.wpenginepowered.com" required>
                </div>

                <div class="form-group">
                    <label for="partner">
                        <svg class="label-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                        Partner
                    </label>
                    <select id="partner" name="partner">
                        <option value="independent">Independent</option>
                        <option value="western">Western Veterinary Partners</option>
                        <option value="heartland">Heartland</option>
                        <option value="united">United</option>
                        <option value="rarebreed">Rarebreed</option>
                        <option value="evervet">EverVet</option>
                        <option value="encore">Encore</option>
                        <option value="amerivet">AmeriVet</option>
                    </select>
                </div>

                <div class="form-group">
                    <label for="phase">
                        <svg class="label-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                        Build Phase
                    </label>
                    <select id="phase" name="phase">
                        <option value="prototype">Prototype</option>
                        <option value="full" selected>Full Build</option>
                        <option value="final">Final</option>
                    </select>
                </div>

                <button type="submit" class="btn" id="submitBtn">
                    <svg style="width:18px;height:18px;vertical-align:middle;margin-right:8px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    Run QA Scan
                </button>
            </form>
            <div class="status" id="status" style="display:none;"></div>
        </div>

        <div class="history fade-in-delay">
            <div class="history-header">
                <div class="history-title">Recent Scans</div>
                {% if history %}<span class="history-count">{{ history | length }} scan{{ 's' if history | length != 1 else '' }}</span>{% endif %}
            </div>
            <div id="historyList">
                {% for scan in history %}
                <div class="history-item">
                    <div class="history-left">
                        {% if scan.scan_id %}<span class="scan-id-badge">{{ scan.scan_id }}</span>{% endif %}
                        <div>
                            <div class="history-site">{{ scan.site_url }}</div>
                            <div class="history-meta">
                                <span>{{ scan.partner | title }}</span>
                                <span class="meta-dot"></span>
                                <span>{{ scan.phase | title }}</span>
                                <span class="meta-dot"></span>
                                <span>{{ scan.scan_time[:10] }}</span>
                            </div>
                        </div>
                    </div>
                    <div style="display:flex;align-items:center;gap:16px;">
                        <div class="score-ring {% if scan.score >= 85 %}good{% elif scan.score >= 70 %}ok{% else %}bad{% endif %}">{{ scan.score }}</div>
                        <a href="/reports/{{ scan.report_file }}" target="_blank" class="view-link">View &rarr;</a>
                    </div>
                </div>
                {% endfor %}
                {% if not history %}
                <div class="empty-history">
                    <div class="empty-icon">📋</div>
                    <div>No scans yet</div>
                    <div style="font-size:13px;margin-top:4px;color:#9ca3af;">Run your first scan above to see results here</div>
                </div>
                {% endif %}
            </div>
        </div>

        <div class="footer">
            <div class="footer-inner">
                <span>Zero-Touch QA Scanner</span>
                <span style="color:#e5e7eb;">•</span>
                <span>PetDesk</span>
            </div>
        </div>
    </div>

    <script>
        document.getElementById('scanForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const status = document.getElementById('status');

            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Scanning...';
            status.style.display = 'block';

            // Animated step-by-step progress
            const steps = [
                { text: 'Connecting to site...', icon: '🔗' },
                { text: 'Crawling pages...', icon: '🕷️' },
                { text: 'Running QA checks...', icon: '✓' },
                { text: 'Checking WordPress backend...', icon: '🔐' },
                { text: 'Checking grammar & spelling...', icon: '📝' },
                { text: 'Generating report...', icon: '📊' },
            ];
            let currentStep = 0;

            function showStep() {
                if (currentStep < steps.length) {
                    const step = steps[currentStep];
                    status.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;gap:12px;">' +
                        '<span class="spinner" style="border-color:rgba(79,70,229,0.2);border-top-color:#4f46e5;width:22px;height:22px;"></span>' +
                        '<span style="font-weight:600;color:#374151;">' + step.icon + ' ' + step.text + '</span></div>';
                    currentStep++;
                }
            }
            showStep();
            const stepInterval = setInterval(showStep, 2500);

            try {
                const resp = await fetch('/api/scan', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        site_url: document.getElementById('site_url').value,
                        partner: document.getElementById('partner').value,
                        phase: document.getElementById('phase').value,
                    })
                });
                clearInterval(stepInterval);
                const data = await resp.json();
                if (data.success) {
                    const scoreColor = data.score >= 85 ? '#16a34a' : data.score >= 70 ? '#d97706' : '#dc2626';
                    status.innerHTML = '<div style="padding:8px 0;">' +
                        '<div style="font-size:15px;font-weight:600;color:#111827;margin-bottom:8px;">✅ Scan Complete!</div>' +
                        '<div style="display:flex;align-items:center;justify-content:center;gap:16px;">' +
                        '<span style="font-size:28px;font-weight:800;color:' + scoreColor + ';">' + data.score + '</span>' +
                        '<span style="color:#9ca3af;font-size:14px;">/ 100</span>' +
                        '<a href="' + data.report_url + '" target="_blank" class="view-link" style="margin-left:8px;">View Report &rarr;</a>' +
                        '</div></div>';
                    setTimeout(() => location.reload(), 2500);
                } else {
                    status.innerHTML = '<div style="color:#dc2626;font-weight:600;">❌ Error: ' + (data.error || 'Unknown error') + '</div>';
                }
            } catch(err) {
                clearInterval(stepInterval);
                status.innerHTML = '<div style="color:#dc2626;font-weight:600;">❌ Error: ' + err.message + '</div>';
            }
            btn.disabled = false;
            btn.innerHTML = '<svg style="width:18px;height:18px;vertical-align:middle;margin-right:8px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>Run QA Scan';
        });
    </script>
</body>
</html>"""


@app.route("/")
def home():
    _load_scan_history()
    return render_template_string(HOME_PAGE, history=list(reversed(scan_history)), logo_uri=LOGO_DATA_URI, logo_white_uri=LOGO_WHITE_URI, bg_purple_uri=BG_PURPLE_URI)


@app.route("/reports/<path:filename>")
def serve_report(filename):
    # Try database first
    try:
        if filename.endswith(".json"):
            html_name = filename.replace(".json", ".html")
            content = db_get_report(html_name, "json")
            if content is not None:
                return app.response_class(content, mimetype="application/json")
        else:
            content = db_get_report(filename, "html")
            if content is not None:
                return app.response_class(content, mimetype="text/html")
    except Exception as e:
        print(f"[DB] Error serving report, falling back to filesystem: {e}")

    # Fallback: filesystem
    return send_from_directory(REPORTS_DIR, filename)


# ---------------------------------------------------------------------------
# Persistent scan history (loads from reports/ directory)
# ---------------------------------------------------------------------------

def _load_scan_history():
    """Load scan history from database or filesystem."""
    global scan_history

    # Try database first
    try:
        db_history = db_load_scan_history()
        if db_history is not None:
            scan_history = db_history
            return
    except Exception as e:
        print(f"[DB] Error loading scan history, falling back to filesystem: {e}")

    # Fallback: filesystem
    known_files = {s.get("_json_file") for s in scan_history if s.get("_json_file")}
    json_files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.endswith(".json") and f != "scan_counter.json"],
        key=lambda x: os.path.getmtime(os.path.join(REPORTS_DIR, x)),
    )
    for jf in json_files:
        if jf in known_files:
            continue
        try:
            with open(os.path.join(REPORTS_DIR, jf), "r") as f:
                data = json.load(f)
            meta = data.get("metadata", {})
            summary = data.get("summary", {})
            if not meta.get("site_url"):
                continue
            html_file = jf.replace(".json", ".html")
            scan_history.append({
                "scan_id": meta.get("scan_id", ""),
                "site_url": meta.get("site_url", ""),
                "partner": meta.get("partner", ""),
                "phase": meta.get("phase", ""),
                "score": summary.get("score", 0),
                "scan_time": meta.get("scan_time", ""),
                "report_file": html_file,
                "_json_file": jf,
            })
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Routes - QA Rules Viewer
# ---------------------------------------------------------------------------

RULES_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QA Rules - Zero-Touch QA</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif;
               background: #f9fafb; color: #111827; -webkit-font-smoothing: antialiased; overflow-y: scroll; }
        .header {
            {% if bg_purple_uri %}background: url('{{ bg_purple_uri }}') center/cover no-repeat;{% else %}background: linear-gradient(135deg, #1e1b4b 0%, #312e81 30%, #4f46e5 70%, #6366f1 100%);{% endif %}
            color: white; padding: 32px 40px; position: relative; overflow: hidden;
            box-shadow: 0 4px 20px rgba(79,70,229,0.25);
        }
        .header::before { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background: radial-gradient(ellipse at 30% 0%, rgba(255,255,255,0.1) 0%, transparent 50%); }
        .header::after { content: ''; position: absolute; top: -80%; right: -20%; width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 60%); border-radius: 50%; }
        .header-inner { display: flex; justify-content: space-between; align-items: center; position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; width: 100%; }
        .header-left { display: flex; align-items: center; gap: 18px; }
        .header-logo { height: 34px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }
        .header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; text-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        .header p { font-size: 13px; opacity: 0.8; margin-top: 3px; font-weight: 500; }
        .nav-links { display: flex; gap: 8px; }
        .nav-links a { color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 600;
            text-decoration: none; padding: 8px 16px; border-radius: 8px; transition: all 0.2s;
            border: 1px solid transparent; }
        .nav-links a:hover { background: rgba(255,255,255,0.15); color: #fff; border-color: rgba(255,255,255,0.2); }
        .container { max-width: 1000px; margin: 28px auto; padding: 0 24px; }
        .intro { background: #eef2ff; border-radius: 12px; padding: 18px 24px; margin-bottom: 24px;
                 font-size: 14px; color: #3730a3; line-height: 1.6; }
        .filter-bar { display: flex; gap: 12px; margin-bottom: 20px; align-items: center; flex-wrap: wrap; }
        .filter-bar label { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; color: #374151; }
        .filter-bar select { padding: 8px 12px; border: 1.5px solid #e5e7eb; border-radius: 8px; font-size: 13px; background: #fff; }
        .rule-card { background: #fff; border-radius: 12px; padding: 16px 20px; margin-bottom: 8px;
                     box-shadow: 0 1px 2px rgba(0,0,0,0.04); display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
        .rule-id { font-family: 'Courier New', monospace; font-size: 11px; font-weight: 700; color: #6b7280;
                   background: #f3f4f6; padding: 2px 8px; border-radius: 4px; white-space: nowrap; }
        .rule-check { font-size: 14px; font-weight: 600; color: #111827; flex: 1; }
        .rule-meta { display: flex; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
        .tag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 10px; font-weight: 700;
               text-transform: uppercase; letter-spacing: 0.3px; }
        .tag-cat { background: #e0e7ff; color: #3730a3; }
        .tag-phase { background: #dcfce7; color: #166534; }
        .tag-weight { background: #fef3c7; color: #92400e; }
        .tag-auto { background: #dcfce7; color: #166534; }
        .tag-human { background: #fee2e2; color: #991b1b; }
        .tag-partner { background: #f3e8ff; color: #6b21a8; }
        .section-title { font-size: 17px; font-weight: 700; margin: 24px 0 12px; color: #374151;
                         border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }
        .count-badge { font-size: 13px; color: #6b7280; font-weight: 400; }
        .footer { text-align: center; padding: 24px 0; font-size: 12px; color: #d1d5db; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div class="header-left">
                {% if logo_white_uri %}<img src="{{ logo_white_uri }}" alt="PetDesk" class="header-logo">{% elif logo_uri %}<img src="{{ logo_uri }}" alt="PetDesk" class="header-logo">{% endif %}
                <div>
                    <h1>QA Rules</h1>
                    <p>All checks the scanner runs against websites</p>
                </div>
            </div>
            <div class="nav-links">
                <a href="/">Scanner</a>
                <a href="/rules">View Rules</a>
                <a href="/rules/edit">Edit Rules</a>
                <a href="/history">History</a>
            </div>
        </div>
    </div>
    <div class="container">
        <div class="intro">
            <strong>How rules work:</strong> Each rule below is a specific check the scanner runs automatically against a website.
            Rules are organized by category, and different rules apply depending on the <strong>partner</strong> (Independent, Western, etc.)
            and <strong>build phase</strong> (Prototype, Full Build, Final). The weight (1x-5x) determines how much a failure impacts the score.
            <br><br>
            To add, change, or remove rules, go to the <a href="/rules/edit" style="color:#4f46e5;font-weight:700;">Rules Editor</a>.
            No coding required &mdash; the QA team owns this entirely.
        </div>

        <div class="filter-bar">
            <label>Partner:</label>
            <select id="partnerFilter" onchange="filterRules()">
                <option value="all">All Partners</option>
                <option value="universal">Universal Only</option>
                <option value="western">Western</option>
                <option value="independent">Independent</option>
                <option value="heartland">Heartland</option>
            </select>
            <label style="margin-left:12px;">Phase:</label>
            <select id="phaseFilter" onchange="filterRules()">
                <option value="all">All Phases</option>
                <option value="prototype">Prototype</option>
                <option value="full">Full Build</option>
                <option value="final">Final</option>
            </select>
        </div>

        {% for cat_name, cat_rules in categories.items() %}
        <div class="section-title">{{ cat_name }} <span class="count-badge">({{ cat_rules|length }} rules)</span></div>
        {% for rule in cat_rules %}
        <div class="rule-card" data-partners="{{ rule.partners }}" data-phases="{{ rule.phase|join(',') }}">
            <div>
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
                    <span class="rule-id">{{ rule.id }}</span>
                    <span class="rule-check">{{ rule.check }}</span>
                </div>
                <div class="rule-meta">
                    {% for p in rule.phase %}<span class="tag tag-phase">{{ p }}</span>{% endfor %}
                    <span class="tag tag-weight">Weight {{ rule.weight }}x</span>
                    {% if rule.automated %}<span class="tag tag-auto">Automated</span>{% else %}<span class="tag tag-human">Human Review</span>{% endif %}
                    {% if rule.partners != 'all' %}<span class="tag tag-partner">{{ rule.partners|join(', ') }}</span>{% endif %}
                </div>
            </div>
        </div>
        {% endfor %}
        {% endfor %}

        <div class="footer">Zero-Touch QA Scanner &bull; {{ total_rules }} total rules</div>
    </div>
    <script>
    function filterRules() {
        const partner = document.getElementById('partnerFilter').value;
        const phase = document.getElementById('phaseFilter').value;
        document.querySelectorAll('.rule-card').forEach(card => {
            const partners = card.dataset.partners;
            const phases = card.dataset.phases;
            let show = true;
            if (partner !== 'all') {
                if (partner === 'universal') { show = partners === 'all'; }
                else { show = partners === 'all' || partners.includes(partner); }
            }
            if (show && phase !== 'all') { show = phases.includes(phase); }
            card.style.display = show ? 'flex' : 'none';
        });
    }
    </script>
</body>
</html>"""


HISTORY_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scan History - Zero-Touch QA</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif;
               background: #f9fafb; color: #111827; -webkit-font-smoothing: antialiased; overflow-y: scroll; }
        .header {
            {% if bg_purple_uri %}background: url('{{ bg_purple_uri }}') center/cover no-repeat;{% else %}background: linear-gradient(135deg, #1e1b4b 0%, #312e81 30%, #4f46e5 70%, #6366f1 100%);{% endif %}
            color: white; padding: 32px 40px; position: relative; overflow: hidden;
            box-shadow: 0 4px 20px rgba(79,70,229,0.25);
        }
        .header::before { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background: radial-gradient(ellipse at 30% 0%, rgba(255,255,255,0.1) 0%, transparent 50%); }
        .header::after { content: ''; position: absolute; top: -80%; right: -20%; width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 60%); border-radius: 50%; }
        .header-inner { display: flex; justify-content: space-between; align-items: center; position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; width: 100%; }
        .header-left { display: flex; align-items: center; gap: 18px; }
        .header-logo { height: 34px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }
        .header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; text-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        .header p { font-size: 13px; opacity: 0.8; margin-top: 3px; font-weight: 500; }
        .nav-links { display: flex; gap: 8px; }
        .nav-links a { color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 600;
            text-decoration: none; padding: 8px 16px; border-radius: 8px; transition: all 0.2s;
            border: 1px solid transparent; }
        .nav-links a:hover { background: rgba(255,255,255,0.15); color: #fff; border-color: rgba(255,255,255,0.2); }
        .container { max-width: 1100px; margin: 28px auto; padding: 0 24px; }
        .intro { background: #eef2ff; border-radius: 12px; padding: 18px 24px; margin-bottom: 24px;
                 font-size: 14px; color: #3730a3; line-height: 1.6; }

        /* Filter bar */
        .filter-bar {
            display: flex; gap: 12px; margin-bottom: 20px; align-items: center; flex-wrap: wrap;
            background: #fff; padding: 16px 20px; border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }
        .filter-bar label { font-size: 11px; font-weight: 700; text-transform: uppercase;
                            letter-spacing: 0.4px; color: #6b7280; }
        .filter-bar input, .filter-bar select {
            padding: 8px 12px; border: 1.5px solid #e5e7eb; border-radius: 8px;
            font-size: 13px; background: #f9fafb; color: #111827;
        }
        .filter-bar input:focus, .filter-bar select:focus {
            outline: none; border-color: #6366f1; background: #fff;
            box-shadow: 0 0 0 2px rgba(99,102,241,0.1);
        }
        .filter-bar input[type=text] { flex: 1; min-width: 180px; }
        .filter-count { font-size: 12px; color: #9ca3af; margin-left: auto; }

        /* Stats row */
        .stats-row {
            display: flex; gap: 16px; margin-bottom: 20px;
        }
        .stat-card {
            flex: 1; background: #fff; border-radius: 12px; padding: 16px 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06); text-align: center;
        }
        .stat-card .stat-value { font-size: 28px; font-weight: 800; line-height: 1; }
        .stat-card .stat-label { font-size: 11px; color: #6b7280; margin-top: 4px;
                                  text-transform: uppercase; letter-spacing: 0.3px; font-weight: 600; }
        .stat-total { color: #4f46e5; }
        .stat-passing { color: #16a34a; }
        .stat-needs-work { color: #d97706; }

        table { width: 100%; border-collapse: separate; border-spacing: 0; background: #fff;
                border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
        th { background: #4f46e5; color: #fff; padding: 12px 16px; text-align: left; font-size: 12px;
             font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px;
             cursor: pointer; user-select: none; white-space: nowrap; }
        th:hover { background: #4338ca; }
        th .sort-arrow { font-size: 10px; margin-left: 4px; opacity: 0.5; }
        th.sorted .sort-arrow { opacity: 1; }
        td { padding: 14px 16px; border-bottom: 1px solid #f3f4f6; font-size: 14px; }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #f5f3ff; }
        tr.hidden { display: none; }
        .score-good { color: #16a34a; font-weight: 800; }
        .score-ok { color: #d97706; font-weight: 800; }
        .score-bad { color: #dc2626; font-weight: 800; }
        .score-bar { display: inline-block; width: 60px; height: 6px; border-radius: 3px;
                     background: #e5e7eb; margin-left: 8px; vertical-align: middle; }
        .score-bar-fill { height: 100%; border-radius: 3px; }
        a { color: #4f46e5; text-decoration: none; font-weight: 600; }
        a:hover { text-decoration: underline; }
        .report-links { display: flex; gap: 8px; align-items: center; }
        .report-links a { font-size: 13px; }
        .btn-sm { padding: 5px 12px; border-radius: 6px; font-size: 11px; font-weight: 600;
                  border: 1.5px solid #e5e7eb; background: #fff; color: #374151; cursor: pointer;
                  text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
        .btn-sm:hover { background: #f9fafb; border-color: #d1d5db; text-decoration: none; }
        .empty { text-align: center; padding: 40px; color: #9ca3af; font-size: 14px; }
        .footer { text-align: center; padding: 24px 0; font-size: 12px; color: #d1d5db; }
        @media (max-width: 768px) {
            .stats-row { flex-direction: column; }
            .filter-bar { flex-direction: column; }
            table { font-size: 12px; }
            td, th { padding: 10px 12px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div class="header-left">
                {% if logo_white_uri %}<img src="{{ logo_white_uri }}" alt="PetDesk" class="header-logo">{% elif logo_uri %}<img src="{{ logo_uri }}" alt="PetDesk" class="header-logo">{% endif %}
                <div>
                    <h1>Scan History</h1>
                    <p>All QA scans and their reports</p>
                </div>
            </div>
            <div class="nav-links">
                <a href="/">Scanner</a>
                <a href="/rules">View Rules</a>
                <a href="/rules/edit">Edit Rules</a>
                <a href="/history">History</a>
            </div>
        </div>
    </div>
    <div class="container">
        {% if history %}
        <!-- Stats -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-value stat-total">{{ history|length }}</div>
                <div class="stat-label">Total Scans</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-passing">{{ history|selectattr('score', 'ge', 85)|list|length }}</div>
                <div class="stat-label">Passing (85+)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-needs-work">{{ history|selectattr('score', 'lt', 85)|list|length }}</div>
                <div class="stat-label">Needs Work (&lt;85)</div>
            </div>
        </div>

        <!-- Filters -->
        <div class="filter-bar">
            <label>Search:</label>
            <input type="text" id="searchInput" placeholder="Filter by site URL or scan ID..." oninput="filterTable()">
            <label>Partner:</label>
            <select id="partnerFilter" onchange="filterTable()">
                <option value="all">All</option>
                {% for p in partners %}<option value="{{ p }}">{{ p | title }}</option>{% endfor %}
            </select>
            <label>Phase:</label>
            <select id="phaseFilter" onchange="filterTable()">
                <option value="all">All</option>
                <option value="prototype">Prototype</option>
                <option value="full">Full Build</option>
                <option value="final">Final</option>
            </select>
            <label>Score:</label>
            <select id="scoreFilter" onchange="filterTable()">
                <option value="all">All</option>
                <option value="pass">85+ (Passing)</option>
                <option value="warn">70-84 (Needs Work)</option>
                <option value="fail">&lt;70 (Major Issues)</option>
            </select>
            <span class="filter-count" id="filterCount">{{ history|length }} scan(s)</span>
        </div>

        <div class="intro" style="padding: 12px 20px;">
            <strong>Tip:</strong> Click any column header to sort. Click "View" to open a report in the browser.
            To save a report as PDF, open it and use your browser's Print function (Ctrl+P / Cmd+P).
        </div>

        <table id="historyTable">
            <thead>
                <tr>
                    <th onclick="sortTable(0)" data-col="0">Scan ID <span class="sort-arrow">&#9650;</span></th>
                    <th onclick="sortTable(1)" data-col="1">Site URL <span class="sort-arrow">&#9650;</span></th>
                    <th onclick="sortTable(2)" data-col="2">Partner <span class="sort-arrow">&#9650;</span></th>
                    <th onclick="sortTable(3)" data-col="3">Phase <span class="sort-arrow">&#9650;</span></th>
                    <th onclick="sortTable(4)" data-col="4" class="sorted">Score <span class="sort-arrow">&#9660;</span></th>
                    <th onclick="sortTable(5)" data-col="5">Date <span class="sort-arrow">&#9650;</span></th>
                    <th>Report</th>
                </tr>
            </thead>
            <tbody>
                {% for scan in history %}
                <tr data-site="{{ scan.site_url|lower }}" data-partner="{{ scan.partner }}" data-phase="{{ scan.phase }}" data-score="{{ scan.score }}" data-id="{{ (scan.scan_id or '')|lower }}">
                    <td style="font-weight: 700; color: #4f46e5; font-family: monospace;">{{ scan.scan_id or '—' }}</td>
                    <td style="max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{{ scan.site_url }}">{{ scan.site_url }}</td>
                    <td>{{ scan.partner | title }}</td>
                    <td>{{ scan.phase | title }}</td>
                    <td>
                        <span class="{% if scan.score >= 85 %}score-good{% elif scan.score >= 70 %}score-ok{% else %}score-bad{% endif %}">{{ scan.score }}/100</span>
                        <span class="score-bar"><span class="score-bar-fill" style="width:{{ scan.score }}%;background:{% if scan.score >= 85 %}#16a34a{% elif scan.score >= 70 %}#d97706{% else %}#dc2626{% endif %};"></span></span>
                    </td>
                    <td style="white-space: nowrap;">{{ scan.scan_time[:10] }}</td>
                    <td>
                        <div class="report-links">
                            <a href="/reports/{{ scan.report_file }}" target="_blank">View</a>
                            <a href="/reports/{{ scan.report_file.replace('.html', '.json') }}" target="_blank" class="btn-sm" title="Download JSON audit trail">JSON</a>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="empty">No scans recorded yet. Run a scan from the <a href="/">Scanner</a> page to see results here.</div>
        {% endif %}

        <div class="footer">Zero-Touch QA Scanner &bull; {{ history|length }} scan(s) recorded</div>
    </div>

    <script>
    function filterTable() {
        var search = document.getElementById('searchInput').value.toLowerCase();
        var partner = document.getElementById('partnerFilter').value;
        var phase = document.getElementById('phaseFilter').value;
        var scoreFilter = document.getElementById('scoreFilter').value;
        var rows = document.querySelectorAll('#historyTable tbody tr');
        var visible = 0;
        rows.forEach(function(row) {
            var site = row.dataset.site || '';
            var id = row.dataset.id || '';
            var rPartner = row.dataset.partner || '';
            var rPhase = row.dataset.phase || '';
            var score = parseInt(row.dataset.score || '0');
            var show = true;
            if (search && site.indexOf(search) === -1 && id.indexOf(search) === -1) show = false;
            if (partner !== 'all' && rPartner !== partner) show = false;
            if (phase !== 'all' && rPhase !== phase) show = false;
            if (scoreFilter === 'pass' && score < 85) show = false;
            if (scoreFilter === 'warn' && (score < 70 || score >= 85)) show = false;
            if (scoreFilter === 'fail' && score >= 70) show = false;
            row.style.display = show ? '' : 'none';
            if (show) visible++;
        });
        document.getElementById('filterCount').textContent = visible + ' scan(s)';
    }

    var sortDir = {};
    function sortTable(colIdx) {
        var table = document.getElementById('historyTable');
        var tbody = table.querySelector('tbody');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        var dir = sortDir[colIdx] === 'asc' ? 'desc' : 'asc';
        sortDir[colIdx] = dir;
        rows.sort(function(a, b) {
            var aText = a.cells[colIdx].textContent.trim();
            var bText = b.cells[colIdx].textContent.trim();
            if (colIdx === 4) { // Score column - numeric sort
                aText = parseInt(aText) || 0;
                bText = parseInt(bText) || 0;
            }
            if (typeof aText === 'number') {
                return dir === 'asc' ? aText - bText : bText - aText;
            }
            return dir === 'asc' ? aText.localeCompare(bText) : bText.localeCompare(aText);
        });
        rows.forEach(function(row) { tbody.appendChild(row); });
        // Update sort arrows
        table.querySelectorAll('th').forEach(function(th) {
            th.classList.remove('sorted');
            th.querySelector('.sort-arrow').innerHTML = '&#9650;';
        });
        var th = table.querySelector('th[data-col="' + colIdx + '"]');
        th.classList.add('sorted');
        th.querySelector('.sort-arrow').innerHTML = dir === 'asc' ? '&#9650;' : '&#9660;';
    }
    </script>
</body>
</html>"""


@app.route("/rules")
def rules_page():
    """Show all QA rules in a browsable, filterable page."""
    data = get_all_rules()
    all_rules = []
    for group_name, group_rules in data.items():
        for rule in group_rules:
            rule["_group"] = group_name
            all_rules.append(rule)

    cat_labels = {
        "search_replace": "Better Search Replace",
        "functionality": "Functionality",
        "craftsmanship": "Craftsmanship",
        "content": "Content",
        "grammar_spelling": "Grammar & Spelling",
        "footer": "Footer",
        "navigation": "Navigation",
        "cta": "Call-to-Action",
        "forms": "Forms",
        "human_review": "Requires Human Review",
    }
    categories = {}
    for rule in all_rules:
        cat = cat_labels.get(rule.get("category", ""), rule.get("category", "other").replace("_", " ").title())
        if cat not in categories:
            categories[cat] = []
        if not any(r["id"] == rule["id"] for r in categories[cat]):
            categories[cat].append(rule)

    return render_template_string(RULES_PAGE, categories=categories,
                                  total_rules=len(all_rules), logo_uri=LOGO_DATA_URI, logo_white_uri=LOGO_WHITE_URI, bg_purple_uri=BG_PURPLE_URI)


# ---------------------------------------------------------------------------
# Routes - Rules Editor (web-based, no coding needed)
# ---------------------------------------------------------------------------

RULES_EDIT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Edit QA Rules - Zero-Touch QA</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif;
               background: #f9fafb; color: #111827; -webkit-font-smoothing: antialiased; overflow-y: scroll; }
        .header {
            {% if bg_purple_uri %}background: url('{{ bg_purple_uri }}') center/cover no-repeat;{% else %}background: linear-gradient(135deg, #1e1b4b 0%, #312e81 30%, #4f46e5 70%, #6366f1 100%);{% endif %}
            color: white; padding: 32px 40px; position: relative; overflow: hidden;
            box-shadow: 0 4px 20px rgba(79,70,229,0.25);
        }
        .header::before { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background: radial-gradient(ellipse at 30% 0%, rgba(255,255,255,0.1) 0%, transparent 50%); }
        .header::after { content: ''; position: absolute; top: -80%; right: -20%; width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 60%); border-radius: 50%; }
        .header-inner { display: flex; justify-content: space-between; align-items: center; position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; width: 100%; }
        .header-left { display: flex; align-items: center; gap: 18px; }
        .header-logo { height: 34px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.1)); }
        .header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; text-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        .header p { font-size: 13px; opacity: 0.8; margin-top: 3px; font-weight: 500; }
        .nav-links { display: flex; gap: 8px; }
        .nav-links a { color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 600;
            text-decoration: none; padding: 8px 16px; border-radius: 8px; transition: all 0.2s;
            border: 1px solid transparent; }
        .nav-links a:hover { background: rgba(255,255,255,0.15); color: #fff; border-color: rgba(255,255,255,0.2); }
        .container { max-width: 900px; margin: 28px auto; padding: 0 24px; }
        .intro { background: #eef2ff; border-radius: 12px; padding: 18px 24px; margin-bottom: 24px;
                 font-size: 14px; color: #3730a3; line-height: 1.6; }
        .success-msg { background: #dcfce7; color: #166534; padding: 14px 20px; border-radius: 10px; margin-bottom: 16px; font-weight: 600; font-size: 14px; }
        .rule-list { margin-bottom: 32px; }
        .group-title { font-size: 16px; font-weight: 700; margin: 24px 0 10px; color: #4f46e5; text-transform: capitalize;
                       border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; }
        .rule-row { background: #fff; border-radius: 10px; padding: 14px 18px; margin-bottom: 6px;
                    box-shadow: 0 1px 2px rgba(0,0,0,0.04); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
        .rule-info { flex: 1; }
        .rule-id-label { font-family: monospace; font-size: 12px; color: #6b7280; background: #f3f4f6; padding: 1px 6px; border-radius: 3px; }
        .rule-check-text { font-size: 14px; font-weight: 600; margin-top: 4px; }
        .rule-meta { font-size: 11px; color: #9ca3af; margin-top: 3px; }
        .rule-actions { display: flex; gap: 6px; }
        .btn-sm { padding: 6px 14px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; border: none; }
        .btn-edit { background: #eef2ff; color: #4f46e5; }
        .btn-edit:hover { background: #c7d2fe; }
        .btn-delete { background: #fee2e2; color: #dc2626; }
        .btn-delete:hover { background: #fecaca; }
        .btn-add { background: #4f46e5; color: #fff; padding: 12px 24px; border-radius: 10px; font-size: 14px;
                   font-weight: 600; cursor: pointer; border: none; margin-bottom: 24px; }
        .btn-add:hover { background: #4338ca; }

        /* Modal */
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 100; align-items: center; justify-content: center; }
        .modal-overlay.active { display: flex; }
        .modal { background: #fff; border-radius: 16px; padding: 28px 32px; max-width: 600px; width: 90%; max-height: 85vh; overflow-y: auto; }
        .modal h2 { font-size: 18px; font-weight: 700; margin-bottom: 20px; }
        .form-row { margin-bottom: 14px; }
        .form-row label { display: block; font-size: 12px; font-weight: 700; color: #374151; margin-bottom: 4px;
                          text-transform: uppercase; letter-spacing: 0.3px; }
        .form-row input, .form-row select, .form-row textarea {
            width: 100%; padding: 10px 12px; border: 1.5px solid #e5e7eb; border-radius: 8px; font-size: 14px; background: #f9fafb; }
        .form-row input:focus, .form-row select:focus, .form-row textarea:focus {
            outline: none; border-color: #6366f1; background: #fff; box-shadow: 0 0 0 3px rgba(99,102,241,0.12); }
        .form-row textarea { resize: vertical; min-height: 60px; }
        .form-row .hint { font-size: 11px; color: #9ca3af; margin-top: 3px; }
        .checkbox-row { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
        .checkbox-row input { width: auto; }
        .checkbox-row label { margin-bottom: 0; text-transform: none; font-size: 14px; }
        .phase-checks { display: flex; gap: 12px; }
        .phase-checks label { text-transform: none; font-size: 13px; display: flex; align-items: center; gap: 4px; }
        .phase-checks input { width: auto; }
        .modal-actions { display: flex; gap: 10px; margin-top: 20px; justify-content: flex-end; }
        .btn-cancel { background: #f3f4f6; color: #374151; padding: 10px 20px; border-radius: 8px; font-size: 14px;
                      font-weight: 600; cursor: pointer; border: none; }
        .btn-save { background: #4f46e5; color: #fff; padding: 10px 20px; border-radius: 8px; font-size: 14px;
                    font-weight: 600; cursor: pointer; border: none; }
        .btn-save:hover { background: #4338ca; }
        .footer { text-align: center; padding: 24px 0; font-size: 12px; color: #d1d5db; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div class="header-left">
                {% if logo_white_uri %}<img src="{{ logo_white_uri }}" alt="PetDesk" class="header-logo">{% elif logo_uri %}<img src="{{ logo_uri }}" alt="PetDesk" class="header-logo">{% endif %}
                <div>
                    <h1>Edit QA Rules</h1>
                    <p>Add, edit, or remove rules &mdash; no coding required</p>
                </div>
            </div>
            <div class="nav-links">
                <a href="/">Scanner</a>
                <a href="/rules">View Rules</a>
                <a href="/rules/edit">Edit Rules</a>
                <a href="/history">History</a>
            </div>
        </div>
    </div>
    <div class="container">
        <div class="intro">
            <strong>QA team: this is your page.</strong> You can add new rules, change existing ones, or remove rules you no longer need.
            Changes take effect immediately on the next scan.
            <div style="margin-top: 12px; padding: 12px 16px; background: #fff; border-radius: 8px; border: 1px solid #e5e7eb;">
                <p style="font-weight: 700; margin-bottom: 8px; font-size: 13px;">What you can add without coding:</p>
                <ul style="font-size: 13px; line-height: 1.8; padding-left: 20px; margin: 0;">
                    <li><strong>Search for text on site</strong> &mdash; The scanner will check every page for specific text (e.g. a placeholder name, old branding, wrong phone number). If found, it fails.</li>
                    <li><strong>Human review checklist item</strong> &mdash; Adds an item to the report's manual review checklist. The reviewer marks Pass/Fail/N/A during review.</li>
                </ul>
                <p style="font-size: 12px; color: #6b7280; margin-top: 8px;">Other types of automated checks (broken links, alt text, etc.) require a developer to add the check function.</p>
            </div>
        </div>

        {% if success %}
        <div class="success-msg">{{ success }}</div>
        {% endif %}

        <button class="btn-add" onclick="openModal('add')">+ Add New Rule</button>

        <div class="rule-list">
        {% for group_name, group_rules in rules_data.items() %}
            {% if group_rules %}
            <div class="group-title">{{ group_name }} Rules{% if group_name == 'universal' %} (apply to all partners){% endif %}</div>
            {% for rule in group_rules %}
            <div class="rule-row">
                <div class="rule-info">
                    <span class="rule-id-label">{{ rule.id }}</span>
                    <div class="rule-check-text">{{ rule.check }}</div>
                    <div class="rule-meta">
                        Category: {{ rule.category }} &bull;
                        Phases: {{ rule.phase | join(', ') }} &bull;
                        Weight: {{ rule.weight }}x &bull;
                        {{ 'Automated' if rule.automated else 'Human Review' }}
                    </div>
                </div>
                <div class="rule-actions">
                    <form method="POST" action="/rules/edit" style="display:inline;">
                        <input type="hidden" name="action" value="delete">
                        <input type="hidden" name="group" value="{{ group_name }}">
                        <input type="hidden" name="rule_id" value="{{ rule.id }}">
                        <button type="submit" class="btn-sm btn-delete" onclick="return confirm('Delete rule {{ rule.id }}?')">Delete</button>
                    </form>
                </div>
            </div>
            {% endfor %}
            {% endif %}
        {% endfor %}
        </div>

        <div class="footer">Changes are saved to rules.json and take effect on the next scan.</div>
    </div>

    <!-- Add/Edit Modal -->
    <div class="modal-overlay" id="modal">
        <div class="modal">
            <h2 id="modalTitle">Add New Rule</h2>
            <form method="POST" action="/rules/edit">
                <input type="hidden" name="action" value="add">
                <div class="form-row">
                    <label for="rule_id">Rule ID</label>
                    <input type="text" name="rule_id" id="rule_id" placeholder="e.g. CUSTOM-001" required>
                    <div class="hint">A unique code. Use a prefix like CUSTOM- for your own rules.</div>
                </div>
                <div class="form-row">
                    <label for="check">What to Check (Description)</label>
                    <textarea name="check" id="check" placeholder="e.g. Homepage must include a map widget" required></textarea>
                </div>
                <div class="form-row">
                    <label for="category">Category</label>
                    <select name="category" id="category">
                        <option value="content">Content</option>
                        <option value="functionality">Functionality</option>
                        <option value="craftsmanship">Craftsmanship</option>
                        <option value="footer">Footer</option>
                        <option value="navigation">Navigation</option>
                        <option value="cta">Call-to-Action</option>
                        <option value="forms">Forms</option>
                        <option value="search_replace">Better Search Replace</option>
                        <option value="grammar_spelling">Grammar & Spelling</option>
                        <option value="human_review">Human Review</option>
                    </select>
                </div>
                <div class="form-row">
                    <label>Build Phases (when does this rule apply?)</label>
                    <div class="phase-checks">
                        <label><input type="checkbox" name="phase" value="prototype"> Prototype</label>
                        <label><input type="checkbox" name="phase" value="full" checked> Full Build</label>
                        <label><input type="checkbox" name="phase" value="final" checked> Final</label>
                    </div>
                </div>
                <div class="form-row">
                    <label for="weight">Weight (importance: 1 = minor, 5 = critical)</label>
                    <select name="weight" id="weight">
                        <option value="1">1 - Minor</option>
                        <option value="2">2</option>
                        <option value="3" selected>3 - Medium</option>
                        <option value="4">4</option>
                        <option value="5">5 - Critical</option>
                    </select>
                </div>
                <div class="form-row">
                    <label for="group">Partner Group</label>
                    <select name="group" id="group">
                        <option value="universal">Universal (all partners)</option>
                        <option value="western">Western only</option>
                        <option value="independent">Independent only</option>
                        <option value="heartland">Heartland only</option>
                        <option value="evervet">EverVet only</option>
                        <option value="encore">Encore only</option>
                        <option value="amerivet">AmeriVet only</option>
                        <option value="rarebreed">Rarebreed only</option>
                        <option value="united">United only</option>
                    </select>
                </div>
                <div class="form-row">
                    <label for="rule_type">Rule Type</label>
                    <select name="rule_type" id="rule_type" onchange="toggleSearchText()">
                        <option value="human_review">Human Review — adds a checklist item for manual review</option>
                        <option value="search_text">Search for Text — scanner checks every page for specific text</option>
                    </select>
                </div>
                <div class="form-row" id="searchTextRow" style="display:none;">
                    <label for="search_text">Text to Search For</label>
                    <input type="text" name="search_text" id="search_text" placeholder="e.g. WhiskerFrame, 555-0100, old clinic name">
                    <div class="hint">The scanner will search every page for this exact text. If found anywhere on the site, the check fails. Case-insensitive. Good for catching leftover placeholder text, old branding, or wrong contact details.</div>
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn-cancel" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn-save">Save Rule</button>
                </div>
            </form>
        </div>
    </div>

    <script>
    function openModal(mode) {
        document.getElementById('modal').classList.add('active');
    }
    function closeModal() {
        document.getElementById('modal').classList.remove('active');
    }
    function toggleSearchText() {
        var sel = document.getElementById('rule_type').value;
        document.getElementById('searchTextRow').style.display = sel === 'search_text' ? 'block' : 'none';
    }
    document.getElementById('modal').addEventListener('click', function(e) {
        if (e.target === this) closeModal();
    });
    </script>
</body>
</html>"""


@app.route("/rules/edit", methods=["GET", "POST"])
def rules_edit():
    """Web-based rules editor. QA team can add/delete rules with no coding."""
    success = None
    if request.method == "POST":
        action = request.form.get("action", "")
        data = get_all_rules()

        if action == "add":
            group = request.form.get("group", "universal")
            phases = request.form.getlist("phase")
            if not phases:
                phases = ["full", "final"]
            rule_type = request.form.get("rule_type", "human_review")
            search_text = request.form.get("search_text", "").strip()
            is_automated = rule_type == "search_text" and search_text
            new_rule = {
                "id": request.form.get("rule_id", "").strip(),
                "category": request.form.get("category", "content"),
                "phase": phases,
                "check": request.form.get("check", "").strip(),
                "automated": is_automated,
                "weight": int(request.form.get("weight", 1)),
                "partners": "all" if group == "universal" else [group],
            }
            if is_automated:
                new_rule["search_text"] = search_text
                new_rule["check_fn"] = "check_leftover_text"
            if new_rule["id"] and new_rule["check"]:
                if group not in data:
                    data[group] = []
                data[group].append(new_rule)
                _save_rules(data)
                success = f'Rule "{new_rule["id"]}" added to {group} rules.'

        elif action == "delete":
            group = request.form.get("group", "")
            rule_id = request.form.get("rule_id", "")
            if group in data:
                data[group] = [r for r in data[group] if r.get("id") != rule_id]
                _save_rules(data)
                success = f'Rule "{rule_id}" deleted from {group} rules.'

    rules_data = get_all_rules()
    return render_template_string(RULES_EDIT_PAGE, rules_data=rules_data,
                                  success=success, logo_uri=LOGO_DATA_URI, logo_white_uri=LOGO_WHITE_URI, bg_purple_uri=BG_PURPLE_URI)


@app.route("/history")
def history_page():
    """Show scan history with search, filters, and sorting."""
    _load_scan_history()
    history = list(reversed(scan_history))
    partners = sorted(set(s.get("partner", "") for s in scan_history if s.get("partner")))
    return render_template_string(HISTORY_PAGE, history=history, partners=partners,
                                  logo_uri=LOGO_DATA_URI, logo_white_uri=LOGO_WHITE_URI, bg_purple_uri=BG_PURPLE_URI)


@app.route("/admin/clear-history", methods=["POST"])
def admin_clear_history():
    """Clear all scan history from database. Admin use only."""
    global scan_history
    if is_db_available():
        try:
            from db import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scans")
                    cur.execute("DELETE FROM scan_id_map")
                    cur.execute("ALTER SEQUENCE scan_id_seq RESTART WITH 1")
            scan_history = []
            return jsonify({"success": True, "message": "History cleared"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": False, "error": "Database not available"}), 400


# ---------------------------------------------------------------------------
# Routes - API (called by web UI and Wrike webhook)
# ---------------------------------------------------------------------------

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Run a QA scan. Called by the web UI form."""
    data = request.get_json() or {}
    site_url = data.get("site_url", "").strip()
    partner = data.get("partner", "independent").strip().lower()
    phase = data.get("phase", "full").strip().lower()
    wrike_task_id = data.get("wrike_task_id", "")

    if not site_url:
        return jsonify({"success": False, "error": "site_url is required"}), 400

    if not site_url.startswith("http"):
        site_url = "https://" + site_url

    try:
        report = run_scan(site_url, partner, phase)
        report.scan_id = _get_scan_id(site_url, phase)

        # Save HTML report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = site_url.replace("https://", "").replace("http://", "").replace("/", "_").replace(".", "-")
        report_filename = f"{safe_name}_{timestamp}.html"
        report_path = os.path.join(REPORTS_DIR, report_filename)

        html_report = generate_html_report(report)
        json_filename = f"{safe_name}_{timestamp}.json"
        json_report_data = generate_json_report(report)

        # Save to database (primary persistence)
        scan_meta = {
            "scan_id": report.scan_id,
            "site_url": site_url,
            "partner": partner,
            "phase": phase,
            "score": report.score,
            "scan_time": report.scan_time,
            "pages_scanned": report.pages_scanned,
            "total_checks": report.total_checks,
            "passed": report.passed,
            "failed": report.failed,
            "warnings": report.warnings,
            "human_review": report.human_review,
            "report_filename": report_filename,
        }
        try:
            db_save_scan(scan_meta, html_report, json_report_data)
        except Exception as e:
            print(f"[DB] Error saving scan: {e}")

        # Save to filesystem (fallback / local dev)
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html_report)
            json_path = os.path.join(REPORTS_DIR, json_filename)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_report_data, f, indent=2, default=str)
        except OSError as e:
            print(f"[FS] Could not write report files: {e}")

        # Add to in-memory history
        scan_history.append({
            "scan_id": report.scan_id,
            "site_url": site_url,
            "partner": partner,
            "phase": phase,
            "score": report.score,
            "scan_time": report.scan_time,
            "report_file": report_filename,
            "_json_file": json_filename,
        })

        # Post to Wrike if task ID provided
        if wrike_task_id and WRIKE_API_TOKEN:
            comment = generate_wrike_comment(report)
            wrike_post_comment(wrike_task_id, comment)

        return jsonify({
            "success": True,
            "score": report.score,
            "passed": report.passed,
            "failed": report.failed,
            "warnings": report.warnings,
            "human_review": report.human_review,
            "report_url": f"/reports/{report_filename}",
            "report_file": report_filename,
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Routes - Wrike Webhook
# ---------------------------------------------------------------------------

@app.route("/webhook/wrike", methods=["POST"])
def wrike_webhook():
    """
    Wrike webhook endpoint.

    Setup in Wrike:
      1. Create 3 custom fields on your QA tasks:
         - "Site URL" (text)
         - "Partner" (dropdown: Independent, Western, Heartland, etc.)
         - "Build Phase" (dropdown: Prototype, Full, Final)
      2. Create a Wrike automation:
         "When task status changes to 'QA In Progress' -> call webhook"
         Point it at: https://your-server.com/webhook/wrike
      3. Set environment variables with the custom field IDs and API token.

    When a designer moves a task to "QA In Progress", Wrike calls this endpoint.
    The scanner reads the custom fields, runs the scan, and posts results
    back as a Wrike comment on the same task.
    """
    payload = request.get_json() or {}

    # Wrike sends task ID(s) in the webhook payload
    task_ids = []
    for event in payload.get("data", [payload]):
        task_id = event.get("taskId") or event.get("id", "")
        if task_id:
            task_ids.append(task_id)

    if not task_ids:
        return jsonify({"status": "no task IDs in payload"}), 200

    # Process each task asynchronously
    for task_id in task_ids:
        thread = threading.Thread(target=_process_wrike_task, args=(task_id,))
        thread.start()

    return jsonify({"status": "processing", "tasks": task_ids}), 200


def _process_wrike_task(task_id: str):
    """Background worker: fetch task details, run scan, post results."""
    try:
        task = wrike_get_task(task_id)
        if not task:
            print(f"[Wrike] Could not fetch task {task_id}")
            return

        fields = extract_wrike_custom_fields(task)
        site_url = fields["site_url"]
        partner = fields["partner"]
        phase = fields["phase"]

        if not site_url:
            wrike_post_comment(task_id,
                "<b>Zero-Touch QA:</b> No Site URL found in custom fields. "
                "Please fill in the 'Site URL' field and move the task back to QA.")
            return

        print(f"[Wrike] Scanning {site_url} (partner={partner}, phase={phase}) for task {task_id}")
        report = run_scan(site_url, partner, phase)
        report.scan_id = _get_scan_id(site_url, phase)

        # Save report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = site_url.replace("https://", "").replace("http://", "").replace("/", "_").replace(".", "-")
        report_filename = f"{safe_name}_{timestamp}.html"
        report_path = os.path.join(REPORTS_DIR, report_filename)
        html_report = generate_html_report(report)
        json_report_data = generate_json_report(report)

        # Save to database
        try:
            db_save_scan({
                "scan_id": report.scan_id, "site_url": site_url,
                "partner": partner, "phase": phase, "score": report.score,
                "scan_time": report.scan_time, "pages_scanned": report.pages_scanned,
                "total_checks": report.total_checks, "passed": report.passed,
                "failed": report.failed, "warnings": report.warnings,
                "human_review": report.human_review, "report_filename": report_filename,
            }, html_report, json_report_data)
        except Exception as e:
            print(f"[DB] Error saving Wrike scan: {e}")

        # Save to filesystem
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html_report)
        except OSError as e:
            print(f"[FS] Could not write report file: {e}")

        # Post comment to Wrike
        comment = generate_wrike_comment(report)
        wrike_post_comment(task_id, comment)

        scan_history.append({
            "scan_id": report.scan_id,
            "site_url": site_url, "partner": partner, "phase": phase,
            "score": report.score, "scan_time": report.scan_time,
            "report_file": report_filename,
        })

        print(f"[Wrike] Scan complete for task {task_id}: score {report.score}/100")

    except Exception as e:
        print(f"[Wrike] Error processing task {task_id}: {e}")
        wrike_post_comment(task_id, f"<b>Zero-Touch QA Error:</b> {str(e)}")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  Zero-Touch QA Scanner")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    print()
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG", "").strip() == "1")
