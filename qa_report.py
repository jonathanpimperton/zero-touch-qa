"""
Zero-Touch QA - Report Generator
Produces HTML reports and Wrike-formatted output.
"""

import json
import base64
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# PetDesk brand assets (base64-encoded PNG, embedded for portable reports)
# ---------------------------------------------------------------------------
_ASSETS_DIR = os.path.dirname(__file__)
_LOGO_PATH = os.path.join(_ASSETS_DIR, "Petdesk Logo.png")
_LOGO_WHITE_PATH = os.path.join(_ASSETS_DIR, "Petdesk Logo White Text.png")
_BG_PURPLE_PATH = os.path.join(_ASSETS_DIR, "Petdesk background purple.png")


def _get_data_uri(path: str, mime: str = "image/png") -> str:
    """Return a data URI for a file, or empty string if missing."""
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"
    except FileNotFoundError:
        return ""


def generate_html_report(report) -> str:
    """Generate a polished, professional HTML report."""
    global _collapse_counter
    _collapse_counter = 0

    logo_uri = _get_data_uri(_LOGO_PATH)  # Purple logo (for white backgrounds)
    logo_white_uri = _get_data_uri(_LOGO_WHITE_PATH)  # White logo (for dark backgrounds)
    bg_purple_uri = _get_data_uri(_BG_PURPLE_PATH)  # Brand purple background

    # Pre-compute header background CSS (brand texture or fallback gradient)
    if bg_purple_uri:
        header_bg_css = f"url('{bg_purple_uri}') center/cover no-repeat"
    else:
        header_bg_css = "linear-gradient(135deg, #1e1b4b 0%, #312e81 30%, #4f46e5 70%, #6366f1 100%)"

    # Count by status
    passed = sum(1 for r in report.results if r.status == "PASS")
    failed = sum(1 for r in report.results if r.status == "FAIL")
    warnings = sum(1 for r in report.results if r.status == "WARN")
    human_review = sum(1 for r in report.results if r.status == "HUMAN_REVIEW")
    skipped = sum(1 for r in report.results if r.status == "SKIP")

    # Score color & assessment
    if report.score >= 95:
        score_color = "#16a34a"
        score_bg = "#dcfce7"
        assessment = "Ready for Delivery"
        ring_color = "#22c55e"
    elif report.score >= 85:
        score_color = "#65a30d"
        score_bg = "#ecfccb"
        assessment = "Minor Issues - Fix Before Delivery"
        ring_color = "#84cc16"
    elif report.score >= 70:
        score_color = "#d97706"
        score_bg = "#fef3c7"
        assessment = "Needs Work - Several Issues"
        ring_color = "#f59e0b"
    else:
        score_color = "#dc2626"
        score_bg = "#fecaca"
        assessment = "Significant Issues - Major Rework"
        ring_color = "#ef4444"

    # SVG score ring
    pct = max(0, min(100, report.score))
    circumference = 2 * 3.14159 * 54
    dash_offset = circumference * (1 - pct / 100)
    score_ring_svg = f"""
    <svg width="140" height="140" viewBox="0 0 120 120">
      <circle cx="60" cy="60" r="54" fill="none" stroke="#e5e7eb" stroke-width="8"/>
      <circle cx="60" cy="60" r="54" fill="none" stroke="{ring_color}" stroke-width="8"
              stroke-dasharray="{circumference}" stroke-dashoffset="{dash_offset}"
              stroke-linecap="round" transform="rotate(-90 60 60)" id="score-ring"/>
      <text x="60" y="55" text-anchor="middle" font-size="32" font-weight="700" fill="{score_color}" id="score-value">{report.score}</text>
      <text x="60" y="72" text-anchor="middle" font-size="11" fill="#6b7280">/ 100</text>
    </svg>"""

    # Group results by category
    categories = {}
    for r in report.results:
        cat = r.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    category_labels = {
        "search_replace": "Better Search Replace",
        "functionality": "Functionality",
        "craftsmanship": "Craftsmanship",
        "content": "Content",
        "footer": "Footer",
        "navigation": "Navigation",
        "cta": "Call-to-Action",
        "forms": "Forms",
        "grammar_spelling": "Grammar & Spelling",
        "human_review": "Requires Human Review",
    }

    # Build failures section
    failures_html = ""
    failures = [r for r in report.results if r.status == "FAIL"]
    if failures:
        for r in failures:
            failures_html += f"""
            <div class="issue-card fail-card">
                <div class="issue-header">
                    <div class="issue-title"><span class="rule-tag">{r.rule_id}</span> {_esc(r.check)}</div>
                    <span class="points-badge fail-points">-{r.points_lost} pts (weight {r.weight}x)</span>
                </div>
                <div class="issue-detail">{_format_detail(r.details)}</div>
            </div>"""
    else:
        failures_html = '<div class="empty-state pass-state">No failures detected.</div>'

    # Build warnings section
    warnings_html = ""
    warns = [r for r in report.results if r.status == "WARN"]
    if warns:
        for r in warns:
            warnings_html += f"""
            <div class="issue-card warn-card">
                <div class="issue-header">
                    <div class="issue-title"><span class="rule-tag">{r.rule_id}</span> {_esc(r.check)}</div>
                </div>
                <div class="issue-detail">{_format_detail(r.details)}</div>
            </div>"""
    else:
        warnings_html = '<div class="empty-state">No warnings.</div>'

    # Build human review section
    human_html = ""
    humans = [r for r in report.results if r.status == "HUMAN_REVIEW"]
    total_human_weight = sum(r.weight for r in humans)
    if humans:
        for idx, r in enumerate(humans):
            human_html += f"""
            <div class="issue-card review-card" id="review-{idx}" data-weight="{r.weight}">
                <div class="review-header">
                    <span class="review-title"><span class="rule-tag">{r.rule_id}</span> {_esc(r.check)}</span>
                    <div class="review-buttons" data-idx="{idx}">
                        <button class="review-btn review-btn-pass" onclick="setReview({idx},'pass')">PASS</button>
                        <button class="review-btn review-btn-fail" onclick="setReview({idx},'fail')">FAIL</button>
                        <button class="review-btn review-btn-na" onclick="setReview({idx},'na')">N/A</button>
                    </div>
                </div>
                <div class="issue-detail">{_format_detail(r.details)}</div>
                <textarea class="review-comments" placeholder="Comments (optional) — note any issues found or why this passed/failed" rows="2"></textarea>
            </div>"""

    # Build category detail tables
    # Exclude human_review category (those items are in the dedicated checklist above)
    category_order = ["search_replace", "functionality", "craftsmanship", "content",
                      "grammar_spelling", "footer", "navigation", "cta", "forms"]
    details_html = ""
    for cat_key in category_order:
        if cat_key not in categories:
            continue
        # Filter out HUMAN_REVIEW items (they appear in the checklist section)
        cat_results = [r for r in categories[cat_key] if r.status != "HUMAN_REVIEW"]
        if not cat_results:
            continue
        label = category_labels.get(cat_key, cat_key.replace("_", " ").title())
        cat_passed = sum(1 for r in cat_results if r.status == "PASS")
        cat_total = len(cat_results)

        rows = ""
        for r in cat_results:
            detail_text = f'<div class="cell-detail">{_format_detail(r.details)}</div>' if r.details and r.status != "PASS" else ""
            badge_cls = {"PASS": "badge-pass", "FAIL": "badge-fail", "WARN": "badge-warn",
                         "SKIP": "badge-skip", "HUMAN_REVIEW": "badge-review"}.get(r.status, "")
            badge_label = r.status.replace("_", " ")
            rows += f"""
                <tr>
                    <td class="cell-status"><span class="status-badge {badge_cls}">{badge_label}</span></td>
                    <td class="cell-rule">{r.rule_id}</td>
                    <td class="cell-check">{_esc(r.check)}{detail_text}</td>
                </tr>"""

        details_html += f"""
        <div class="category-section">
            <div class="category-header">
                <h3>{label}</h3>
                <span class="category-count">{cat_passed}/{cat_total} passed</span>
            </div>
            <table class="results-table">
                <tbody>{rows}</tbody>
            </table>
        </div>"""

    # Logo markup - white logo for dark header, purple logo for white footer
    header_logo_src = logo_white_uri or logo_uri
    logo_html = f'<img src="{header_logo_src}" alt="PetDesk" class="header-logo">' if header_logo_src else '<span class="header-logo-text">PetDesk</span>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QA Report - {_esc(report.site_url)}</title>
    <style>
        :root {{
            --primary: #4f46e5;
            --primary-dark: #3730a3;
            --green: #16a34a;
            --red: #dc2626;
            --amber: #d97706;
            --indigo: #4f46e5;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-400: #9ca3af;
            --gray-500: #6b7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--gray-50);
            color: var(--gray-900);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }}
        .page {{ max-width: 960px; margin: 0 auto; padding: 32px 24px; }}

        /* Header */
        .report-header {{
            background: {header_bg_css};
            color: white;
            padding: 36px 44px;
            border-radius: 16px;
            margin-bottom: 28px;
            position: relative;
            overflow: hidden;
        }}
        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            position: relative;
            z-index: 1;
        }}
        .header-logo {{
            height: 36px;
        }}
        .header-logo-text {{
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }}
        .header-badge {{
            background: rgba(255,255,255,0.15);
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}
        .header-title {{
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin-bottom: 16px;
            position: relative;
            z-index: 1;
        }}
        .header-meta {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            position: relative;
            z-index: 1;
        }}
        .meta-item {{ font-size: 13px; }}
        .meta-label {{ opacity: 0.65; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }}
        .meta-value {{ font-weight: 600; }}

        /* Score card */
        .score-section {{
            display: grid;
            grid-template-columns: 220px 1fr;
            gap: 24px;
            margin-bottom: 32px;
        }}
        .score-card {{
            background: white;
            border-radius: 16px;
            padding: 32px 28px;
            text-align: center;
            box-shadow: 0 4px 16px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.04);
            border: 1px solid rgba(0,0,0,0.06);
        }}
        .score-assessment {{
            margin-top: 14px;
            padding: 10px 16px;
            background: {score_bg};
            border-radius: 10px;
            font-size: 11px;
            font-weight: 700;
            color: {score_color};
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }}
        .summary-card {{
            background: white;
            border-radius: 16px;
            padding: 28px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.04);
            border: 1px solid rgba(0,0,0,0.06);
        }}
        .summary-title {{ font-size: 14px; font-weight: 700; margin-bottom: 18px; color: var(--gray-700); text-transform: uppercase; letter-spacing: 0.5px; font-size: 12px; }}
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
        }}
        .stat-box {{
            padding: 18px 14px;
            border-radius: 12px;
            text-align: center;
        }}
        .stat-num {{ font-size: 30px; font-weight: 800; line-height: 1; margin-bottom: 6px; }}
        .stat-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700; }}
        .stat-pass {{ background: #dcfce7; color: #166534; }}
        .stat-fail {{ background: #fee2e2; color: #991b1b; }}
        .stat-warn {{ background: #fef3c7; color: #92400e; }}
        .stat-review {{ background: #e0e7ff; color: #3730a3; }}
        .stat-skip {{ background: var(--gray-100); color: var(--gray-500); }}
        .stat-pages {{ background: var(--gray-100); color: var(--gray-500); }}

        /* Section headings */
        .section {{ margin-bottom: 36px; }}
        .section-heading {{
            font-size: 15px;
            font-weight: 700;
            margin-bottom: 16px;
            padding: 12px 20px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .section-heading .dot {{
            width: 8px; height: 8px;
            border-radius: 50%;
            display: inline-block;
            flex-shrink: 0;
        }}
        .section-heading.heading-red {{
            background: linear-gradient(135deg, #fef2f2, #fee2e2);
            color: #991b1b;
            border: 1px solid #fecaca;
        }}
        .section-heading.heading-amber {{
            background: linear-gradient(135deg, #fffbeb, #fef3c7);
            color: #92400e;
            border: 1px solid #fde68a;
        }}
        .section-heading.heading-indigo {{
            background: linear-gradient(135deg, #eef2ff, #e0e7ff);
            color: #3730a3;
            border: 1px solid #c7d2fe;
        }}
        .dot-red {{ background: var(--red); }}
        .dot-amber {{ background: var(--amber); }}
        .dot-indigo {{ background: var(--indigo); }}

        /* Issue cards */
        .issue-card {{
            padding: 18px 22px;
            margin-bottom: 10px;
            border-radius: 12px;
            border-left: 4px solid;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.03);
            transition: box-shadow 0.2s ease, transform 0.2s ease;
        }}
        .issue-card:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04);
            transform: translateY(-1px);
        }}
        .fail-card {{ border-color: var(--red); background: #fffbfb; }}
        .warn-card {{ border-color: var(--amber); background: #fffdf7; }}
        .review-card {{ border-color: var(--indigo); background: #fafaff; }}
        .issue-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
        }}
        .issue-title {{ font-size: 14px; font-weight: 600; color: var(--gray-900); }}
        .rule-tag {{
            display: inline-block;
            background: #e0e7ff;
            color: #4338ca;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            font-family: 'Courier New', monospace;
            margin-right: 6px;
            letter-spacing: 0.3px;
        }}
        .points-badge {{
            white-space: nowrap;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 12px;
            border-radius: 20px;
        }}
        .fail-points {{ background: #fee2e2; color: #991b1b; }}
        .issue-detail {{
            font-size: 13px;
            color: var(--gray-500);
            margin-top: 8px;
            line-height: 1.5;
            word-break: break-word;
        }}
        .review-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 8px;
        }}
        .review-title {{
            font-size: 14px;
            font-weight: 600;
            color: var(--gray-900);
        }}
        .review-buttons {{
            display: flex;
            gap: 6px;
            flex-shrink: 0;
        }}
        .review-btn {{
            padding: 5px 16px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            border: 2px solid #d1d5db;
            background: white;
            cursor: pointer;
            color: #6b7280;
            transition: all 0.15s ease;
        }}
        .review-btn:hover {{ border-color: #9ca3af; background: #f9fafb; }}
        .review-btn-pass.active {{
            background: #dcfce7;
            border-color: #16a34a;
            color: #16a34a;
        }}
        .review-btn-fail.active {{
            background: #fee2e2;
            border-color: #dc2626;
            color: #dc2626;
        }}
        .review-btn-na.active {{
            background: #f3f4f6;
            border-color: #6b7280;
            color: #6b7280;
        }}
        .review-comments {{
            width: 100%;
            margin-top: 8px;
            padding: 8px 12px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-family: inherit;
            font-size: 13px;
            resize: vertical;
            color: var(--gray-900);
        }}
        .review-comments:focus {{
            outline: none;
            border-color: var(--indigo);
            box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.1);
        }}
        .review-card.reviewed-pass {{
            border-left: 4px solid #16a34a;
        }}
        .review-card.reviewed-fail {{
            border-left: 4px solid #dc2626;
        }}
        .empty-state {{
            text-align: center;
            padding: 20px;
            border-radius: 12px;
            background: var(--gray-100);
            color: var(--gray-500);
            font-size: 13px;
        }}
        .pass-state {{ background: #dcfce7; color: #166534; }}
        .review-hint {{
            background: #eef2ff;
            padding: 12px 18px;
            border-radius: 10px;
            font-size: 13px;
            color: var(--primary-dark);
            margin-bottom: 14px;
        }}

        /* Category tables */
        .category-section {{ margin-bottom: 28px; }}
        .category-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0;
            padding: 10px 16px;
            background: linear-gradient(135deg, #312e81, #4f46e5);
            border-radius: 12px 12px 0 0;
            color: white;
        }}
        .category-header h3 {{ font-size: 13px; font-weight: 700; color: white; text-transform: uppercase; letter-spacing: 0.5px; }}
        .category-count {{ font-size: 11px; color: rgba(255,255,255,0.7); font-weight: 600; }}
        .results-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            background: white;
            border-radius: 0 0 12px 12px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            border: 1px solid rgba(0,0,0,0.06);
            border-top: none;
        }}
        .results-table tr {{ border-bottom: 1px solid var(--gray-100); transition: background 0.15s ease; }}
        .results-table tr:last-child {{ border-bottom: none; }}
        .results-table tr:nth-child(even) {{ background: #fafaff; }}
        .results-table tr:hover {{ background: #f0f2ff; }}
        .cell-status {{ padding: 12px 14px; width: 110px; text-align: center; }}
        .cell-rule {{ padding: 12px 10px; width: 80px; font-size: 12px; font-weight: 700; color: var(--gray-500); font-family: 'Courier New', monospace; }}
        .cell-check {{ padding: 12px 14px; font-size: 13px; }}
        .cell-detail {{ font-size: 12px; color: var(--gray-500); margin-top: 4px; }}
        .detail-list {{
            margin: 6px 0 0 0;
            padding-left: 18px;
            list-style: disc;
        }}
        .detail-list li {{
            margin-bottom: 3px;
            line-height: 1.5;
        }}
        .collapse-content {{
            /* items hidden by default, toggled via JS */
        }}
        .collapse-toggle {{
            background: none;
            border: 1px solid var(--gray-300);
            color: var(--indigo);
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 6px;
        }}
        .collapse-toggle:hover {{
            background: var(--gray-100);
        }}
        .status-badge {{
            display: inline-block;
            padding: 4px 14px;
            border-radius: 20px;
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge-pass {{ background: #dcfce7; color: #166534; }}
        .badge-fail {{ background: #fee2e2; color: #991b1b; }}
        .badge-warn {{ background: #fef3c7; color: #92400e; }}
        .badge-skip {{ background: var(--gray-100); color: var(--gray-500); }}
        .badge-review {{ background: #e0e7ff; color: #3730a3; }}

        /* Section divider */
        .section-divider {{
            margin: 48px 0 28px;
            text-align: center;
            position: relative;
        }}
        .divider-line {{
            border: none;
            border-top: 3px solid var(--gray-200);
            margin: 0;
        }}
        .divider-label {{
            display: inline-block;
            position: relative;
            top: -14px;
            background: var(--gray-50);
            padding: 4px 20px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: var(--gray-400);
            border: 1px solid var(--gray-200);
            border-radius: 20px;
        }}
        .detail-section {{
            background: linear-gradient(180deg, #f3f4f6, #f9fafb);
            border-radius: 16px;
            padding: 28px;
            margin-bottom: 32px;
            border: 1px solid var(--gray-200);
        }}
        .detail-hint {{
            font-size: 12px;
            color: var(--gray-500);
            margin-bottom: 20px;
            font-style: italic;
        }}

        /* Footer */
        .report-footer {{
            text-align: center;
            padding: 28px 24px 12px;
            margin-top: 20px;
            font-size: 12px;
            color: var(--gray-400);
            border-top: 2px solid var(--gray-200);
        }}
        .report-footer img {{ height: 24px; opacity: 0.5; margin-bottom: 10px; }}

        /* Report toolbar (hidden in print) */
        .report-toolbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 24px;
            background: #fff;
            border-bottom: 1px solid #e5e7eb;
            font-size: 13px;
            color: #6b7280;
        }}
        .toolbar-left {{ display: flex; align-items: center; gap: 12px; }}
        .toolbar-left a {{
            color: #4f46e5; text-decoration: none; font-weight: 600; font-size: 13px;
        }}
        .toolbar-left a:hover {{ text-decoration: underline; }}
        .toolbar-right {{ display: flex; gap: 8px; }}
        .toolbar-btn {{
            display: inline-flex; align-items: center; gap: 6px;
            padding: 7px 16px; border-radius: 8px; font-size: 12px; font-weight: 600;
            border: 1.5px solid #e5e7eb; background: #fff; color: #374151; cursor: pointer;
            transition: all 0.15s;
        }}
        .toolbar-btn:hover {{ background: #f9fafb; border-color: #d1d5db; }}
        .toolbar-btn-primary {{
            background: #4f46e5; color: #fff; border-color: #4f46e5;
        }}
        .toolbar-btn-primary:hover {{ background: #4338ca; }}

        /* Print */
        @media print {{
            body {{ background: white; }}
            .report-toolbar {{ display: none !important; }}
            .page {{ max-width: 100%; padding: 16px; }}
            .report-header {{ break-inside: avoid; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .score-section {{ break-inside: avoid; }}
            .category-section {{ break-inside: avoid; }}
            .section-heading {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .category-header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .stat-box {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .status-badge {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .issue-card {{ break-inside: avoid; box-shadow: none; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .score-card, .summary-card {{ box-shadow: none; border: 1px solid #e5e7eb; }}
            .results-table {{ box-shadow: none; border: 1px solid #e5e7eb; }}
            .results-table tr:hover {{ background: inherit; }}
            .issue-card:hover {{ box-shadow: none; transform: none; }}
        }}
        @media (max-width: 700px) {{
            .score-section {{ grid-template-columns: 1fr; }}
            .header-meta {{ grid-template-columns: 1fr; gap: 10px; }}
            .stat-grid {{ grid-template-columns: repeat(3, 1fr); }}
        }}
    </style>
</head>
<body>
<div class="report-toolbar">
    <div class="toolbar-left">
        <a href="/" class="app-link" title="Back to Scanner">&larr; Scanner</a>
        <span style="color:#d1d5db;">|</span>
        <a href="/history" class="app-link">Scan History</a>
        <span style="color:#d1d5db;">|</span>
        <span>{_esc(report.scan_id) + ' &mdash; ' if report.scan_id else ''}{_esc(report.site_url)}</span>
    </div>
    <div class="toolbar-right">
        <button class="toolbar-btn" id="copyUrlBtn" onclick="copyReportUrl()" title="Copy this report's URL to clipboard">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            <span id="copyUrlLabel">Copy Report URL</span>
        </button>
        <button class="toolbar-btn toolbar-btn-primary" onclick="window.print()" title="Save as PDF using your browser's print dialog">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
            Save as PDF
        </button>
    </div>
</div>
<div class="page">

    <!-- Header -->
    <div class="report-header">
        <div class="header-top">
            {logo_html}
            <span class="header-badge">{_esc(report.scan_id) + ' · ' if report.scan_id else ''}QA Report</span>
        </div>
        <div class="header-title">Automated Quality Assurance Scan</div>
        <div class="header-meta" style="grid-template-columns: 2fr 1fr 1fr 1fr;">
            <div class="meta-item">
                <div class="meta-label">Site URL</div>
                <div class="meta-value">{_esc(report.site_url)}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Partner</div>
                <div class="meta-value">{report.partner.title()}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Build Phase</div>
                <div class="meta-value">{report.phase.title()}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Scan Date</div>
                <div class="meta-value">{report.scan_time[:10]}</div>
            </div>
        </div>
    </div>

    <!-- Score -->
    <div class="score-section">
        <div class="score-card">
            {score_ring_svg}
            <div class="score-assessment" id="score-assessment">{assessment}</div>
        </div>
        <div class="summary-card">
            <div class="summary-title">Scan Summary</div>
            <div class="stat-grid">
                <div class="stat-box stat-pass">
                    <div class="stat-num">{passed}</div>
                    <div class="stat-label">Passed</div>
                </div>
                <div class="stat-box stat-fail">
                    <div class="stat-num">{failed}</div>
                    <div class="stat-label">Failed</div>
                </div>
                <div class="stat-box stat-warn">
                    <div class="stat-num">{warnings}</div>
                    <div class="stat-label">Warnings</div>
                </div>
                <div class="stat-box stat-review">
                    <div class="stat-num">{human_review}</div>
                    <div class="stat-label">Human Review</div>
                </div>
                <div class="stat-box stat-pages">
                    <div class="stat-num">{report.pages_scanned}</div>
                    <div class="stat-label">Pages Scanned</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Failures -->
    <div class="section">
        <div class="section-heading heading-red"><span class="dot dot-red"></span> Failures &mdash; Action Required{f' <span style="font-size:12px;font-weight:500;margin-left:auto;opacity:0.7;">({failed} item{"s" if failed != 1 else ""})</span>' if failed else ''}</div>
        {failures_html}
    </div>

    <!-- Warnings -->
    <div class="section">
        <div class="section-heading heading-amber"><span class="dot dot-amber"></span> Warnings &mdash; Review Recommended{f' <span style="font-size:12px;font-weight:500;margin-left:auto;opacity:0.7;">({warnings} item{"s" if warnings != 1 else ""})</span>' if warnings else ''}</div>
        {warnings_html}
    </div>

    <!-- Human Review Checklist -->
    <div class="section">
        <div class="section-heading heading-indigo"><span class="dot dot-indigo"></span> Human Review Checklist{f' <span style="font-size:12px;font-weight:500;margin-left:auto;opacity:0.7;">({human_review} item{"s" if human_review != 1 else ""})</span>' if human_review else ''}</div>
        <div class="review-hint">These items require human judgment. Mark each as Pass, Fail, or N/A and add comments.</div>
        {human_html}
    </div>

    <!-- Detailed Category Breakdown -->
    <div class="section-divider">
        <hr class="divider-line" />
        <div class="divider-label">Full Breakdown</div>
    </div>
    <div class="section detail-section">
        <div class="section-heading">All Results by Category</div>
        <div class="detail-hint">Complete list of every automated check, organised by category. Items flagged above are repeated here for reference.</div>
        {details_html}
    </div>

    <!-- Footer -->
    <div class="report-footer">
        {"<img src='" + logo_uri + "' alt='PetDesk'><br>" if logo_uri else ""}
        {_esc(report.scan_id) + ' &bull; ' if report.scan_id else ''}Zero-Touch QA Scanner &bull; {report.scan_time[:16].replace("T", " ")} &bull; {report.pages_scanned} pages scanned
    </div>

</div>
<script>
var baseScore = {int(report.score)};
var circumference = 2 * Math.PI * 54;
var humanStatuses = {{}};

function setReview(idx, status) {{
    var card = document.getElementById('review-' + idx);
    var btns = card.querySelectorAll('.review-btn');
    btns.forEach(function(b) {{ b.classList.remove('active'); }});
    card.classList.remove('reviewed-pass', 'reviewed-fail');
    if (status === 'pass') {{
        card.querySelector('.review-btn-pass').classList.add('active');
        card.classList.add('reviewed-pass');
    }} else if (status === 'fail') {{
        card.querySelector('.review-btn-fail').classList.add('active');
        card.classList.add('reviewed-fail');
    }} else {{
        card.querySelector('.review-btn-na').classList.add('active');
    }}
    humanStatuses[idx] = status;
    recalcScore();
}}

function recalcScore() {{
    var lost = 0;
    for (var idx in humanStatuses) {{
        if (humanStatuses[idx] === 'fail') {{
            var card = document.getElementById('review-' + idx);
            var weight = parseInt(card.getAttribute('data-weight') || '1');
            lost += weight;
        }}
    }}
    var newScore = Math.max(0, baseScore - lost);
    // Update score number
    document.getElementById('score-value').textContent = newScore;
    // Update ring
    var offset = circumference * (1 - newScore / 100);
    document.getElementById('score-ring').setAttribute('stroke-dashoffset', offset);
    // Update ring color and assessment
    var assess = document.getElementById('score-assessment');
    var ring = document.getElementById('score-ring');
    var scoreText = document.getElementById('score-value');
    if (newScore >= 95) {{
        assess.textContent = 'Ready for Delivery';
        assess.style.background = '#dcfce7'; assess.style.color = '#16a34a';
        ring.setAttribute('stroke', '#22c55e'); scoreText.setAttribute('fill', '#16a34a');
    }} else if (newScore >= 85) {{
        assess.textContent = 'Minor Issues - Fix Before Delivery';
        assess.style.background = '#ecfccb'; assess.style.color = '#65a30d';
        ring.setAttribute('stroke', '#84cc16'); scoreText.setAttribute('fill', '#65a30d');
    }} else if (newScore >= 70) {{
        assess.textContent = 'Needs Work - Several Issues';
        assess.style.background = '#fef3c7'; assess.style.color = '#d97706';
        ring.setAttribute('stroke', '#f59e0b'); scoreText.setAttribute('fill', '#d97706');
    }} else {{
        assess.textContent = 'Significant Issues - Major Rework';
        assess.style.background = '#fecaca'; assess.style.color = '#dc2626';
        ring.setAttribute('stroke', '#ef4444'); scoreText.setAttribute('fill', '#dc2626');
    }}
    // Show score change indicator
    if (lost > 0) {{
        assess.textContent += ' (human review: -' + lost + ' pts)';
    }}
}}

// Hide app navigation links when viewing as a local file
(function() {{
    if (window.location.protocol === 'file:') {{
        document.querySelectorAll('.app-link').forEach(function(a) {{
            a.style.color = '#d1d5db';
            a.style.pointerEvents = 'none';
            a.style.cursor = 'default';
            a.title = 'Available when viewed through the web app';
        }});
    }}
}})();

function copyReportUrl() {{
    var url = window.location.href;
    if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(url).then(function() {{
            var label = document.getElementById('copyUrlLabel');
            label.textContent = 'Copied!';
            setTimeout(function() {{ label.textContent = 'Copy Report URL'; }}, 2000);
        }});
    }} else {{
        // Fallback for older browsers / file:// protocol
        var input = document.createElement('input');
        input.value = url;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
        var label = document.getElementById('copyUrlLabel');
        label.textContent = 'Copied!';
        setTimeout(function() {{ label.textContent = 'Copy Report URL'; }}, 2000);
    }}
}}
</script>
</body>
</html>"""

    return html


def generate_wrike_comment(report) -> str:
    """Generate a formatted comment suitable for posting to a Wrike task."""
    failures = [r for r in report.results if r.status == "FAIL"]
    warns = [r for r in report.results if r.status == "WARN"]
    humans = [r for r in report.results if r.status == "HUMAN_REVIEW"]

    lines = []
    lines.append(f"<b>Zero-Touch QA Scan Complete</b>")
    lines.append(f"Score: <b>{report.score}/100</b> | "
                 f"Passed: {report.passed} | Failed: {report.failed} | "
                 f"Warnings: {report.warnings} | Human Review: {report.human_review}")
    lines.append(f"Pages scanned: {report.pages_scanned}")
    lines.append("")

    if failures:
        lines.append("<b>FAILURES (must fix):</b>")
        for r in failures:
            lines.append(f"  &#x2717; [{r.rule_id}] {r.check}")
            if r.details:
                lines.append(f"    &rarr; {r.details}")
        lines.append("")

    if warns:
        lines.append("<b>WARNINGS (review):</b>")
        for r in warns:
            lines.append(f"  &#x26A0; [{r.rule_id}] {r.check}")
        lines.append("")

    if humans:
        lines.append("<b>HUMAN REVIEW NEEDED:</b>")
        for r in humans:
            lines.append(f"  &#x2610; {r.check}")

    return "<br>".join(lines)


def generate_json_report(report) -> dict:
    """Generate a machine-readable JSON report for audit trail."""
    return {
        "metadata": {
            "tool": "Zero-Touch QA Scanner",
            "version": "1.0.0",
            "scan_id": report.scan_id,
            "scan_time": report.scan_time,
            "site_url": report.site_url,
            "partner": report.partner,
            "phase": report.phase,
            "pages_scanned": report.pages_scanned,
        },
        "summary": {
            "score": report.score,
            "total_checks": report.total_checks,
            "passed": report.passed,
            "failed": report.failed,
            "warnings": report.warnings,
            "human_review": report.human_review,
        },
        "results": [
            {
                "rule_id": r.rule_id,
                "category": r.category,
                "check": r.check,
                "status": r.status,
                "weight": r.weight,
                "points_lost": r.points_lost,
                "details": r.details,
                "page_url": r.page_url,
            }
            for r in report.results
        ],
    }


def _esc(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_collapse_counter = 0

def _format_detail(text: str) -> str:
    """Format detail text: convert newline-separated items into a scannable HTML list.
    Long lists (>5 items) are collapsible — first 5 shown, rest behind a toggle."""
    global _collapse_counter
    if not text:
        return ""
    escaped = _esc(text)
    if "\n" not in escaped:
        return escaped
    lines = [line.strip() for line in escaped.split("\n") if line.strip()]
    if len(lines) <= 1:
        return escaped
    # First line is the summary, rest are list items
    summary = lines[0]
    items = lines[1:]
    max_visible = 5
    if len(items) <= max_visible:
        item_html = "".join(f"<li>{line}</li>" for line in items)
        return f'{summary}<ul class="detail-list">{item_html}</ul>'
    # Collapsible: show first N, hide rest behind toggle
    _collapse_counter += 1
    cid = f"collapse-{_collapse_counter}"
    visible_html = "".join(f"<li>{line}</li>" for line in items[:max_visible])
    hidden_html = "".join(f"<li>{line}</li>" for line in items[max_visible:])
    remaining = len(items) - max_visible
    return (
        f'{summary}<ul class="detail-list">{visible_html}'
        f'<div id="{cid}" class="collapse-content" style="display:none;">{hidden_html}</div>'
        f'</ul>'
        f'<button class="collapse-toggle" onclick="var el=document.getElementById(\'{cid}\');'
        f'if(el.style.display===\'none\'){{el.style.display=\'block\';this.textContent=\'Hide details\'}}'
        f'else{{el.style.display=\'none\';this.textContent=\'Show {remaining} more items\'}}">'
        f'Show {remaining} more items</button>'
    )
