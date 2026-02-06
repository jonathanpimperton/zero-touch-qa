"""
Zero-Touch QA - Database Layer
PostgreSQL persistence for scan results and scan IDs.
Falls back gracefully when DATABASE_URL is not set (local dev).
"""

import os
import json
from datetime import datetime
from contextlib import contextmanager

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def is_db_available() -> bool:
    """Check if database is configured and psycopg2 is installed."""
    return bool(DATABASE_URL) and _HAS_PSYCOPG2


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables and sequence if they don't exist. Safe to call repeatedly."""
    if not is_db_available():
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE SEQUENCE IF NOT EXISTS scan_id_seq START WITH 1;

                    CREATE TABLE IF NOT EXISTS scan_id_map (
                        site_key    VARCHAR(500) PRIMARY KEY,
                        scan_id     VARCHAR(20) NOT NULL UNIQUE
                    );

                    CREATE TABLE IF NOT EXISTS scans (
                        id              SERIAL PRIMARY KEY,
                        scan_id         VARCHAR(20) NOT NULL,
                        site_url        TEXT NOT NULL,
                        partner         VARCHAR(50) NOT NULL,
                        phase           VARCHAR(20) NOT NULL,
                        score           INTEGER NOT NULL,
                        scan_time       TIMESTAMP NOT NULL,
                        pages_scanned   INTEGER DEFAULT 0,
                        total_checks    INTEGER DEFAULT 0,
                        passed          INTEGER DEFAULT 0,
                        failed          INTEGER DEFAULT 0,
                        warnings        INTEGER DEFAULT 0,
                        human_review    INTEGER DEFAULT 0,
                        report_html     TEXT NOT NULL,
                        report_json     TEXT NOT NULL,
                        report_filename VARCHAR(255) NOT NULL,
                        created_at      TIMESTAMP DEFAULT NOW()
                    );

                    CREATE INDEX IF NOT EXISTS idx_scans_scan_time
                        ON scans(scan_time DESC);
                    CREATE INDEX IF NOT EXISTS idx_scans_site_url
                        ON scans(site_url);

                    CREATE TABLE IF NOT EXISTS human_reviews (
                        id              SERIAL PRIMARY KEY,
                        report_filename VARCHAR(255) NOT NULL,
                        rule_id         VARCHAR(50) NOT NULL,
                        item_index      INTEGER NOT NULL,
                        decision        VARCHAR(10) NOT NULL,  -- 'pass', 'fail', 'na'
                        comments        TEXT DEFAULT '',
                        reviewed_at     TIMESTAMP DEFAULT NOW(),
                        UNIQUE(report_filename, item_index)
                    );

                    CREATE INDEX IF NOT EXISTS idx_human_reviews_filename
                        ON human_reviews(report_filename);
                """)
        print("[DB] Database tables initialized")
    except Exception as e:
        print(f"[DB] Error initializing database: {e}")


def db_clear_all():
    """Clear all scan data and reset the scan ID sequence to 1.

    Use for testing/demo resets. Irreversible.
    """
    if not is_db_available():
        return False

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE scans, scan_id_map, human_reviews RESTART IDENTITY CASCADE")
                cur.execute("ALTER SEQUENCE scan_id_seq RESTART WITH 1")
        print("[DB] Cleared all scan data and reset sequence to 1")
        return True
    except Exception as e:
        print(f"[DB] Error clearing database: {e}")
        return False


def db_get_scan_id(site_url: str, phase: str) -> str | None:
    """Get or create a scan ID for a site+phase combination.

    Returns scan_id string (e.g. "QA-0003"), or None if DB unavailable.
    """
    if not is_db_available():
        return None

    site_key = f"{site_url}|{phase}"

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check if mapping already exists
            cur.execute("SELECT scan_id FROM scan_id_map WHERE site_key = %s", (site_key,))
            row = cur.fetchone()
            if row:
                return row[0]

            # Generate new ID atomically
            cur.execute("SELECT nextval('scan_id_seq')")
            next_num = cur.fetchone()[0]
            scan_id = f"QA-{next_num:04d}"

            cur.execute(
                "INSERT INTO scan_id_map (site_key, scan_id) VALUES (%s, %s) "
                "ON CONFLICT (site_key) DO NOTHING RETURNING scan_id",
                (site_key, scan_id),
            )
            result = cur.fetchone()
            if result:
                return result[0]

            # Race condition: another process inserted first
            cur.execute("SELECT scan_id FROM scan_id_map WHERE site_key = %s", (site_key,))
            return cur.fetchone()[0]


def db_save_scan(scan_meta: dict, html_report: str, json_report: dict):
    """Save a complete scan to the database.

    scan_meta: dict with scan_id, site_url, partner, phase, score,
               scan_time, pages_scanned, total_checks, passed, failed,
               warnings, human_review, report_filename
    """
    if not is_db_available():
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO scans
                    (scan_id, site_url, partner, phase, score, scan_time,
                     pages_scanned, total_checks, passed, failed, warnings,
                     human_review, report_html, report_json, report_filename)
                VALUES
                    (%(scan_id)s, %(site_url)s, %(partner)s, %(phase)s,
                     %(score)s, %(scan_time)s, %(pages_scanned)s,
                     %(total_checks)s, %(passed)s, %(failed)s,
                     %(warnings)s, %(human_review)s,
                     %(report_html)s, %(report_json)s, %(report_filename)s)
            """, {
                **scan_meta,
                "report_html": html_report,
                "report_json": json.dumps(json_report, default=str),
            })


def db_load_scan_history() -> list | None:
    """Load all scan history from the database.

    Returns list of dicts matching the scan_history format, or None if DB unavailable.
    """
    if not is_db_available():
        return None

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT scan_id, site_url, partner, phase, score,
                       scan_time, report_filename
                FROM scans
                ORDER BY scan_time ASC
            """)
            rows = cur.fetchall()

    return [
        {
            "scan_id": row["scan_id"],
            "site_url": row["site_url"],
            "partner": row["partner"],
            "phase": row["phase"],
            "score": row["score"],
            "scan_time": row["scan_time"].isoformat()
                if isinstance(row["scan_time"], datetime)
                else str(row["scan_time"]),
            "report_file": row["report_filename"],
        }
        for row in rows
    ]


def db_get_report(filename: str, report_type: str = "html") -> str | None:
    """Fetch a report by filename from the database.

    report_type: "html" or "json"
    Returns the report content string, or None if not found / DB unavailable.
    """
    if not is_db_available():
        return None

    column = "report_html" if report_type == "html" else "report_json"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {column} FROM scans WHERE report_filename = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (filename,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def db_seed_from_filesystem(reports_dir: str):
    """One-time migration: import existing filesystem reports into DB.

    Reads JSON audit files and their paired HTML reports from the reports/
    directory and inserts them into the database. Skips files already imported.
    Also seeds scan_id_map from scan_counter.json.
    """
    if not is_db_available():
        return

    json_files = []
    try:
        json_files = sorted(
            f for f in os.listdir(reports_dir)
            if f.endswith(".json") and f != "scan_counter.json"
        )
    except FileNotFoundError:
        return

    imported = 0
    for jf in json_files:
        try:
            # Check if already in DB
            with get_connection() as conn:
                with conn.cursor() as cur:
                    html_filename = jf.replace(".json", ".html")
                    cur.execute(
                        "SELECT 1 FROM scans WHERE report_filename = %s",
                        (html_filename,),
                    )
                    if cur.fetchone():
                        continue

            # Read JSON
            with open(os.path.join(reports_dir, jf), "r") as f:
                data = json.load(f)

            meta = data.get("metadata", {})
            summary = data.get("summary", {})
            if not meta.get("site_url"):
                continue

            # Read HTML
            html_path = os.path.join(reports_dir, html_filename)
            html_content = ""
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()

            scan_meta = {
                "scan_id": meta.get("scan_id", ""),
                "site_url": meta.get("site_url", ""),
                "partner": meta.get("partner", ""),
                "phase": meta.get("phase", ""),
                "score": summary.get("score", 0),
                "scan_time": meta.get("scan_time", ""),
                "pages_scanned": meta.get("pages_scanned", 0),
                "total_checks": summary.get("total_checks", 0),
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "warnings": summary.get("warnings", 0),
                "human_review": summary.get("human_review", 0),
                "report_filename": html_filename,
            }
            db_save_scan(scan_meta, html_content, data)
            imported += 1
        except Exception as e:
            print(f"[DB] Skipping {jf}: {e}")

    if imported:
        print(f"[DB] Imported {imported} scan(s) from filesystem")

    # Seed scan_id_map from scan_counter.json
    counter_path = os.path.join(reports_dir, "scan_counter.json")
    if os.path.exists(counter_path):
        try:
            with open(counter_path, "r") as f:
                counter_data = json.load(f)
            mapping = counter_data.get("mapping", {})
            max_num = 0
            with get_connection() as conn:
                with conn.cursor() as cur:
                    for site_key, scan_id in mapping.items():
                        cur.execute(
                            "INSERT INTO scan_id_map (site_key, scan_id) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (site_key, scan_id),
                        )
                        num = int(scan_id.replace("QA-", ""))
                        if num > max_num:
                            max_num = num
                    # Advance sequence past existing IDs
                    if max_num > 0:
                        cur.execute("SELECT setval('scan_id_seq', %s, true)", (max_num,))
            if mapping:
                print(f"[DB] Seeded scan_id_map with {len(mapping)} entries, sequence at {max_num}")
        except Exception as e:
            print(f"[DB] Could not seed scan_id_map: {e}")


def db_save_human_review(report_filename: str, item_index: int, rule_id: str,
                          decision: str, comments: str = "") -> bool:
    """Save a human review decision for a specific item in a report.

    Args:
        report_filename: The report file (e.g., 'scan_QA-0001_20260206.html')
        item_index: The index of the human review item (0-based)
        rule_id: The rule ID (e.g., 'HUMAN-001')
        decision: 'pass', 'fail', or 'na'
        comments: Optional reviewer comments

    Returns:
        True if saved successfully, False otherwise.
    """
    if not is_db_available():
        return False

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO human_reviews
                        (report_filename, item_index, rule_id, decision, comments, reviewed_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (report_filename, item_index)
                    DO UPDATE SET
                        decision = EXCLUDED.decision,
                        comments = EXCLUDED.comments,
                        reviewed_at = NOW()
                """, (report_filename, item_index, rule_id, decision, comments))
        return True
    except Exception as e:
        print(f"[DB] Error saving human review: {e}")
        return False


def db_load_human_reviews(report_filename: str) -> list[dict] | None:
    """Load all human review decisions for a report.

    Returns:
        List of dicts with keys: item_index, rule_id, decision, comments, reviewed_at
        Returns None if DB unavailable, empty list if no reviews saved.
    """
    if not is_db_available():
        return None

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT item_index, rule_id, decision, comments, reviewed_at
                    FROM human_reviews
                    WHERE report_filename = %s
                    ORDER BY item_index
                """, (report_filename,))
                rows = cur.fetchall()

        return [
            {
                "item_index": row["item_index"],
                "rule_id": row["rule_id"],
                "decision": row["decision"],
                "comments": row["comments"] or "",
                "reviewed_at": row["reviewed_at"].isoformat()
                    if hasattr(row["reviewed_at"], 'isoformat')
                    else str(row["reviewed_at"]),
            }
            for row in rows
        ]
    except Exception as e:
        print(f"[DB] Error loading human reviews: {e}")
        return None
