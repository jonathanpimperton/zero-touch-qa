"""
Zero-Touch QA - CLI Runner (for testing / CI use)
For everyday use, run app.py and use the web interface instead.

Usage:
    python run_qa.py <site_url> --partner <partner> --phase <phase>
"""

import argparse
import json
import os
import time
import webbrowser
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from qa_rules import get_rules_for_scan, get_automatable_rules, get_human_review_rules
from qa_scanner import SiteCrawler, ScanReport, CheckResult, CHECK_FUNCTIONS
from qa_report import generate_html_report, generate_json_report

_REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
_SCAN_COUNTER_FILE = os.path.join(_REPORTS_DIR, "scan_counter.json")


def _get_scan_id(site_url: str, phase: str) -> str:
    """Get or create a scan ID for a site+phase combination."""
    os.makedirs(_REPORTS_DIR, exist_ok=True)
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


def run_scan(site_url: str, partner: str, phase: str, max_pages: int = 50) -> ScanReport:
    """Run the full QA scan against a site."""

    print("=" * 70)
    print(f"  ZERO-TOUCH QA SCANNER")
    print(f"  Site:    {site_url}")
    print(f"  Partner: {partner}")
    print(f"  Phase:   {phase}")
    print("=" * 70)
    print()

    rules = get_rules_for_scan(partner, phase)
    auto_rules = get_automatable_rules(rules)
    human_rules = get_human_review_rules(rules)

    print(f"[1/3] Loaded {len(rules)} rules ({len(auto_rules)} automated, {len(human_rules)} human-review)")
    print()

    print(f"[2/3] Crawling site (max {max_pages} pages)...")
    crawler = SiteCrawler(site_url)
    pages = crawler.crawl(max_pages=max_pages)
    print(f"       Crawled {len(pages)} pages")
    print()

    print(f"[3/3] Running {len(auto_rules)} automated checks...")
    all_results = []

    for rule in auto_rules:
        fn_name = rule.get("check_fn")
        if fn_name and fn_name in CHECK_FUNCTIONS:
            fn = CHECK_FUNCTIONS[fn_name]
            try:
                results = fn(pages, rule)
                all_results.extend(results)
                for r in results:
                    icon = {"PASS": "+", "FAIL": "X", "WARN": "!", "SKIP": "-", "HUMAN_REVIEW": "?"}
                    print(f"  [{icon.get(r.status, '?')}] {r.rule_id}: {r.check} -> {r.status}")
            except Exception as e:
                all_results.append(CheckResult(
                    rule_id=rule["id"], category=rule["category"],
                    check=rule["check"], status="HUMAN_REVIEW", weight=rule["weight"],
                    details=f"Automated check encountered an error. Verify manually. ({str(e)})",
                ))
                print(f"  [E] {rule['id']}: {rule['check']} -> ERROR: {e}")
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
            details="Requires human judgment - flagged for manual review",
        ))

    total_points_lost = sum(r.points_lost for r in all_results)
    score = max(0, 100 - total_points_lost)

    return ScanReport(
        site_url=site_url, partner=partner, phase=phase,
        scan_time=datetime.now().isoformat(),
        pages_scanned=len(pages), total_checks=len(all_results),
        passed=sum(1 for r in all_results if r.status == "PASS"),
        failed=sum(1 for r in all_results if r.status == "FAIL"),
        warnings=sum(1 for r in all_results if r.status == "WARN"),
        human_review=sum(1 for r in all_results if r.status == "HUMAN_REVIEW"),
        score=score, results=all_results,
    )


def main():
    parser = argparse.ArgumentParser(description="Zero-Touch QA Scanner")
    parser.add_argument("site_url", help="The WordPress site URL to scan")
    parser.add_argument("--partner", default="independent",
                        choices=["independent", "western", "heartland", "united",
                                 "rarebreed", "evervet", "encore", "amerivet"],
                        help="Partner type for partner-specific rules")
    parser.add_argument("--phase", default="full",
                        choices=["prototype", "full", "final"],
                        help="Build phase (prototype, full, or final)")
    parser.add_argument("--max-pages", type=int, default=30,
                        help="Maximum pages to crawl (default: 30)")
    parser.add_argument("--output", default="qa_report",
                        help="Output filename prefix (without extension)")

    args = parser.parse_args()

    start_time = time.time()
    report = run_scan(args.site_url, args.partner, args.phase, args.max_pages)
    report.scan_id = _get_scan_id(args.site_url, args.phase)
    elapsed = time.time() - start_time

    print()
    print("=" * 70)
    print(f"  SCAN COMPLETE in {elapsed:.1f}s")
    print(f"  Scan ID: {report.scan_id}")
    print(f"  Score: {report.score}/100")
    print(f"  Passed: {report.passed} | Failed: {report.failed} | Warnings: {report.warnings} | Human Review: {report.human_review}")
    print("=" * 70)

    # Generate HTML report
    html_file = f"{args.output}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(generate_html_report(report))

    # Generate JSON for audit
    json_file = f"{args.output}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(generate_json_report(report), f, indent=2, default=str)

    print(f"\n  Reports saved:")
    print(f"    {html_file}")
    print(f"    {json_file}")

    # Auto-open the HTML report in the browser
    webbrowser.open(f"file://{os.path.abspath(html_file)}")


if __name__ == "__main__":
    main()
