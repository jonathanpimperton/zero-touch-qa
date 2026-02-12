"""
WordPress REST API Client for Zero-Touch QA
Provides authenticated access to WordPress back-end for QA checks.

Uses WordPress Application Passwords for authentication.
See: https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/

Environment variables required:
- WP_USER: WordPress username with admin access
- WP_APP_PASSWORD: Application password (generated in WP admin > Users > Profile)

Or pass credentials per-scan via the scan request.
"""

import os
import re
import base64
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class WPAPIResult:
    """Result of a WordPress API check."""
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class WPSiteInfo:
    """WordPress site configuration from REST API."""
    timezone: str = ""
    site_title: str = ""
    admin_email: str = ""
    date_format: str = ""
    time_format: str = ""
    plugins: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    active_theme: str = ""
    media_count: int = 0
    old_media_files: list = field(default_factory=list)
    gravity_forms: list = field(default_factory=list)


# =============================================================================
# PETDESK QA PLUGIN CLIENT (RECOMMENDED)
# =============================================================================

# Default API key - must match the key in petdesk-qa-connector.php
PETDESK_QA_API_KEY = os.environ.get("PETDESK_QA_API_KEY", "petdesk-qa-2026-hackathon-key")


class PetDeskQAPluginClient:
    """
    Client for PetDesk QA Connector WordPress plugin.

    This is the RECOMMENDED approach - uses a single shared API key
    that works across all sites with the plugin installed.
    No per-site credentials needed.
    """

    def __init__(self, site_url: str, api_key: str = None):
        """
        Initialize PetDesk QA Plugin client.

        Args:
            site_url: Base URL of the WordPress site
            api_key: PetDesk QA API key (falls back to PETDESK_QA_API_KEY env var)
        """
        self.site_url = site_url.rstrip("/")
        self.api_key = api_key or PETDESK_QA_API_KEY
        self.endpoint = f"{self.site_url}/wp-json/petdesk-qa/v1/site-check"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ZeroTouchQA/1.0 (PetDesk QA Scanner)",
            "Accept": "application/json",
            "X-PetDesk-QA-Key": self.api_key,
        })
        self._site_data = None  # Cached response
        self._available = None  # Cached availability status

    def is_available(self) -> bool:
        """Check if the PetDesk QA plugin is installed and responding.

        Retries with backoff on 503 (hosting rate-limit) to avoid
        false negatives when the site is temporarily throttling requests.
        """
        if self._available is not None:
            return self._available

        import time
        RETRY_DELAYS = [2, 5]  # seconds between retries on 503

        try:
            resp = None
            for attempt in range(1 + len(RETRY_DELAYS)):
                resp = self.session.get(self.endpoint, timeout=10)
                if resp.status_code not in (429, 503):
                    break
                if attempt < len(RETRY_DELAYS):
                    retry_after = resp.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and retry_after.isdigit() else RETRY_DELAYS[attempt]
                    print(f"  [WPBE] Plugin endpoint returned {resp.status_code}, retrying in {delay}s...")
                    time.sleep(delay)

            self._available = resp.status_code == 200
            if self._available:
                self._site_data = resp.json().get("data", {})
            return self._available
        except requests.RequestException:
            self._available = False
            return False

    def get_site_data(self) -> dict:
        """Get all site data from the plugin (cached)."""
        if self._site_data is None:
            self.is_available()
        return self._site_data or {}

    def check_plugins_updated(self) -> tuple[bool, list, str]:
        """Check if plugins need updates."""
        data = self.get_site_data()
        plugins = data.get("plugins", [])

        if not plugins:
            return (True, [], "No plugin data available")

        outdated = []
        for p in plugins:
            if p.get("update_available") and p.get("active"):
                outdated.append({
                    "name": p.get("name", "Unknown"),
                    "current_version": p.get("version", "?"),
                    "new_version": p.get("new_version", "?"),
                })

        return (len(outdated) == 0, outdated, "")

    def check_themes_updated(self) -> tuple[bool, list, str]:
        """Check if themes need updates."""
        data = self.get_site_data()
        themes = data.get("themes", [])

        if not themes:
            return (True, [], "No theme data available")

        outdated = []
        for t in themes:
            if t.get("update_available"):
                outdated.append({
                    "name": t.get("name", "Unknown"),
                    "current_version": t.get("version", "?"),
                    "new_version": t.get("new_version", "?"),
                })

        return (len(outdated) == 0, outdated, "")

    def get_timezone(self) -> tuple[str, str]:
        """Get site timezone setting."""
        data = self.get_site_data()
        settings = data.get("settings", {})

        tz_string = settings.get("timezone_string", "")
        gmt_offset = settings.get("gmt_offset", 0)

        # Convert gmt_offset to float if it's a string
        try:
            gmt_offset = float(gmt_offset) if isinstance(gmt_offset, str) else gmt_offset
        except (ValueError, TypeError):
            gmt_offset = 0

        if tz_string:
            return (tz_string, "")
        elif gmt_offset:
            return (f"UTC{'+' if gmt_offset >= 0 else ''}{int(gmt_offset)}", "")
        else:
            return ("UTC", "")

    def check_old_media_files(self) -> tuple[bool, list, str]:
        """Check for old/template media files."""
        data = self.get_site_data()
        media = data.get("media", {})

        template_files = media.get("template_files", [])
        old_files = media.get("old_files", [])

        flagged = []
        for f in template_files:
            flagged.append({
                "filename": f.get("filename", ""),
                "url": f.get("url", ""),
                "uploaded": f.get("date", "")[:10] if f.get("date") else "unknown",
                "reason": f"template filename (contains '{f.get('pattern', '')}')",
            })

        return (len(flagged) == 0, flagged, "")

    def check_form_notifications(self, expected_email: str = None) -> tuple[bool, list, str]:
        """Check form notification settings for Gravity Forms or WPForms."""
        data = self.get_site_data()
        forms = data.get("forms", {})

        form_plugin = forms.get("form_plugin", "none")

        # No supported form plugin with notification data
        if form_plugin == "none":
            # Check if other form plugins are active (ones we can't inspect)
            plugins = data.get("plugins", [])
            other_form_plugins = set()
            form_plugin_names = {
                "contact-form-7": "Contact Form 7",
                "ninja-forms": "Ninja Forms",
                "formidable": "Formidable Forms",
                "everest-forms": "Everest Forms",
            }
            for p in plugins:
                if p.get("active"):
                    name_lower = p.get("name", "").lower()
                    for key, display in form_plugin_names.items():
                        if key in name_lower:
                            other_form_plugins.add(display)
                            break

            if other_form_plugins:
                return (True, [], f"Uses {', '.join(other_form_plugins)}. Verify notifications manually.")
            else:
                return (True, [], "No form plugin detected")

        # We have form data from Gravity Forms or WPForms
        plugin_name = "Gravity Forms" if form_plugin == "gravity_forms" else "WPForms"
        form_list = forms.get("forms", [])

        if not form_list:
            return (True, [], f"{plugin_name} active but no forms found")

        issues = []
        forms_checked = 0

        for form in form_list:
            if not form.get("is_active"):
                continue

            forms_checked += 1
            form_title = form.get("title", "Untitled")
            notifications = form.get("notifications", [])

            if not notifications:
                issues.append({
                    "form": form_title,
                    "issue": "No notifications configured",
                })
                continue

            active_notifications = [n for n in notifications if n.get("is_active")]
            if not active_notifications:
                issues.append({
                    "form": form_title,
                    "issue": "All notifications are disabled",
                })
                continue

            for notif in active_notifications:
                to_email = notif.get("to", "")

                # Check if notification has a recipient
                if not to_email:
                    issues.append({
                        "form": form_title,
                        "notification": notif.get("name", "Unnamed"),
                        "issue": "No recipient email configured",
                    })
                    continue

                # If expected email provided, validate it
                if expected_email and expected_email.lower() not in to_email.lower():
                    issues.append({
                        "form": form_title,
                        "notification": notif.get("name", "Unnamed"),
                        "issue": f"Sends to '{to_email}', expected '{expected_email}'",
                        "current_to": to_email,
                    })

        if forms_checked == 0:
            return (True, [], f"{plugin_name} active but no active forms found")

        return (len(issues) == 0, issues, "")


# =============================================================================
# WORDPRESS API CLIENT (FALLBACK - requires per-site credentials)
# =============================================================================

class WordPressAPIClient:
    """Client for WordPress REST API with Application Password authentication."""

    def __init__(self, site_url: str, username: str = None, app_password: str = None):
        """
        Initialize WordPress API client.

        Args:
            site_url: Base URL of the WordPress site (e.g., https://example.com)
            username: WP admin username (falls back to WP_USER env var)
            app_password: WP application password (falls back to WP_APP_PASSWORD env var)
        """
        self.site_url = site_url.rstrip("/")
        self.api_base = f"{self.site_url}/wp-json"
        self.username = username or os.environ.get("WP_USER", "")
        self.app_password = app_password or os.environ.get("WP_APP_PASSWORD", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ZeroTouchQA/1.0 (WordPress Back-End Checker)",
            "Accept": "application/json",
        })

        # Set up Basic Auth if credentials provided
        if self.username and self.app_password:
            credentials = f"{self.username}:{self.app_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            self.session.headers["Authorization"] = f"Basic {encoded}"

        self._authenticated = None  # Cached auth status

    def is_authenticated(self) -> bool:
        """Check if we have valid authentication to the WP REST API."""
        if self._authenticated is not None:
            return self._authenticated

        if not self.username or not self.app_password:
            self._authenticated = False
            return False

        try:
            # Try to access an authenticated endpoint
            resp = self.session.get(
                f"{self.api_base}/wp/v2/users/me",
                timeout=10
            )
            self._authenticated = resp.status_code == 200
            return self._authenticated
        except requests.RequestException:
            self._authenticated = False
            return False

    def _get(self, endpoint: str, params: dict = None) -> WPAPIResult:
        """Make authenticated GET request to WP REST API."""
        try:
            url = f"{self.api_base}/{endpoint}"
            resp = self.session.get(url, params=params, timeout=15)

            if resp.status_code == 401:
                return WPAPIResult(False, error="Authentication failed - check WP_USER and WP_APP_PASSWORD")
            elif resp.status_code == 403:
                return WPAPIResult(False, error="Permission denied - user lacks required capabilities")
            elif resp.status_code == 404:
                return WPAPIResult(False, error=f"Endpoint not found: {endpoint}")
            elif resp.status_code >= 400:
                return WPAPIResult(False, error=f"API error {resp.status_code}: {resp.text[:200]}")

            return WPAPIResult(True, data=resp.json())
        except requests.RequestException as e:
            return WPAPIResult(False, error=f"Request failed: {str(e)}")
        except ValueError as e:
            return WPAPIResult(False, error=f"Invalid JSON response: {str(e)}")

    # -------------------------------------------------------------------------
    # Site Settings
    # -------------------------------------------------------------------------

    def get_settings(self) -> WPAPIResult:
        """Get WordPress site settings (timezone, title, email, etc.)."""
        return self._get("wp/v2/settings")

    def get_timezone(self) -> tuple[str, str]:
        """
        Get site timezone setting.

        Returns:
            Tuple of (timezone_string, error_message)
        """
        result = self.get_settings()
        if not result.success:
            return ("", result.error)

        # WordPress stores timezone as either timezone_string (e.g., "America/Denver")
        # or as a UTC offset in gmt_offset
        tz_string = result.data.get("timezone_string", "")
        gmt_offset = result.data.get("gmt_offset", 0)

        if tz_string:
            return (tz_string, "")
        elif gmt_offset:
            return (f"UTC{'+' if gmt_offset >= 0 else ''}{gmt_offset}", "")
        else:
            return ("UTC", "")

    # -------------------------------------------------------------------------
    # Plugins
    # -------------------------------------------------------------------------

    def get_plugins(self) -> WPAPIResult:
        """Get list of installed plugins with version info."""
        return self._get("wp/v2/plugins")

    def check_plugins_updated(self) -> tuple[bool, list, str]:
        """
        Check if plugins need updates.

        Returns:
            Tuple of (all_updated: bool, outdated_plugins: list, error: str)
        """
        result = self.get_plugins()
        if not result.success:
            return (False, [], result.error)

        plugins = result.data if isinstance(result.data, list) else []
        outdated = []

        for plugin in plugins:
            # Check if plugin has update available
            # WordPress REST API includes 'update' field when update is available
            if plugin.get("update"):
                outdated.append({
                    "name": plugin.get("name", "Unknown"),
                    "current_version": plugin.get("version", "?"),
                    "new_version": plugin.get("update", {}).get("version", "?"),
                })

        return (len(outdated) == 0, outdated, "")

    # -------------------------------------------------------------------------
    # Themes
    # -------------------------------------------------------------------------

    def get_themes(self) -> WPAPIResult:
        """Get list of installed themes."""
        return self._get("wp/v2/themes")

    def check_themes_updated(self) -> tuple[bool, list, str]:
        """
        Check if themes need updates.

        Returns:
            Tuple of (all_updated: bool, outdated_themes: list, error: str)
        """
        result = self.get_themes()
        if not result.success:
            return (False, [], result.error)

        themes = result.data if isinstance(result.data, list) else []
        outdated = []

        for theme in themes:
            if theme.get("update"):
                outdated.append({
                    "name": theme.get("name", {}).get("rendered", "Unknown"),
                    "current_version": theme.get("version", "?"),
                    "new_version": theme.get("update", {}).get("version", "?"),
                })

        return (len(outdated) == 0, outdated, "")

    # -------------------------------------------------------------------------
    # Media Library
    # -------------------------------------------------------------------------

    def get_media(self, per_page: int = 100) -> WPAPIResult:
        """Get media library items."""
        return self._get("wp/v2/media", {"per_page": per_page})

    def check_old_media_files(self, days_threshold: int = 365) -> tuple[bool, list, str]:
        """
        Check for old/unused media files that should be cleaned up.

        This checks for media files uploaded before a threshold date that
        may be leftover from template or previous builds.

        Args:
            days_threshold: Flag files older than this many days

        Returns:
            Tuple of (clean: bool, old_files: list, error: str)
        """
        result = self.get_media(per_page=100)
        if not result.success:
            return (False, [], result.error)

        media = result.data if isinstance(result.data, list) else []
        threshold_date = datetime.now() - timedelta(days=days_threshold)
        old_files = []

        # Known template/placeholder filenames to flag
        template_patterns = [
            r"whiskerframe", r"placeholder", r"sample", r"demo",
            r"test[-_]", r"dummy", r"lorem", r"default[-_]"
        ]

        for item in media:
            filename = item.get("source_url", "").split("/")[-1].lower()
            upload_date_str = item.get("date", "")

            # Check for template filenames
            is_template = any(re.search(p, filename, re.I) for p in template_patterns)

            # Check upload date
            is_old = False
            if upload_date_str:
                try:
                    upload_date = datetime.fromisoformat(upload_date_str.replace("Z", "+00:00"))
                    is_old = upload_date.replace(tzinfo=None) < threshold_date
                except (ValueError, TypeError):
                    pass

            if is_template or is_old:
                old_files.append({
                    "filename": filename,
                    "url": item.get("source_url", ""),
                    "uploaded": upload_date_str[:10] if upload_date_str else "unknown",
                    "reason": "template filename" if is_template else "old file",
                })

        return (len(old_files) == 0, old_files, "")

    # -------------------------------------------------------------------------
    # Gravity Forms
    # -------------------------------------------------------------------------

    def get_gravity_forms(self) -> WPAPIResult:
        """Get Gravity Forms list (requires GF REST API add-on)."""
        return self._get("gf/v2/forms")

    def check_form_notifications(self, expected_email: str = None) -> tuple[bool, list, str]:
        """
        Check that form notifications are configured correctly.

        Args:
            expected_email: The clinic email that should receive notifications

        Returns:
            Tuple of (all_correct: bool, issues: list, error: str)
        """
        result = self.get_gravity_forms()
        if not result.success:
            # Gravity Forms API might not be available
            if "not found" in result.error.lower():
                return (True, [], "Gravity Forms REST API not available - skipping check")
            return (False, [], result.error)

        forms = result.data if isinstance(result.data, list) else []
        issues = []

        for form in forms:
            form_title = form.get("title", "Untitled Form")
            form_id = form.get("id", "?")

            # Get form details including notifications
            detail_result = self._get(f"gf/v2/forms/{form_id}")
            if not detail_result.success:
                continue

            notifications = detail_result.data.get("notifications", {})

            if not notifications:
                issues.append({
                    "form": form_title,
                    "issue": "No notifications configured",
                })
                continue

            # Check each notification
            for notif_id, notif in notifications.items():
                if not notif.get("isActive", True):
                    continue

                to_email = notif.get("to", "")

                # Check if notification goes to expected email
                if expected_email and expected_email.lower() not in to_email.lower():
                    issues.append({
                        "form": form_title,
                        "notification": notif.get("name", notif_id),
                        "issue": f"Notification not sent to clinic email ({expected_email})",
                        "current_to": to_email,
                    })

        return (len(issues) == 0, issues, "")

    # -------------------------------------------------------------------------
    # Divi Theme Settings (if using Divi)
    # -------------------------------------------------------------------------

    def get_divi_settings(self) -> WPAPIResult:
        """
        Get Divi theme customizer settings.

        Note: Divi stores settings in wp_options table.
        This requires custom endpoint or direct database access.
        Falls back to checking theme mods via standard API.
        """
        # Try Divi's custom endpoint first (if Divi API plugin installed)
        result = self._get("divi/v1/settings")
        if result.success:
            return result

        # Fall back to theme mods
        result = self._get("wp/v2/settings")
        if not result.success:
            return result

        # Extract Divi-related settings if present
        # Note: Full Divi settings require options API access
        return WPAPIResult(True, data={
            "note": "Full Divi settings require Divi REST API extension",
            "settings_available": list(result.data.keys()) if result.data else [],
        })


# =============================================================================
# CHECK FUNCTIONS FOR QA SCANNER
# =============================================================================

# US State to timezone mapping for automatic validation
US_STATE_TIMEZONES = {
    # Eastern
    "new york": "America/New_York", "ny": "America/New_York",
    "new jersey": "America/New_York", "nj": "America/New_York",
    "connecticut": "America/New_York", "ct": "America/New_York",
    "massachusetts": "America/New_York", "ma": "America/New_York",
    "pennsylvania": "America/New_York", "pa": "America/New_York",
    "delaware": "America/New_York", "de": "America/New_York",
    "maryland": "America/New_York", "md": "America/New_York",
    "virginia": "America/New_York", "va": "America/New_York",
    "north carolina": "America/New_York", "nc": "America/New_York",
    "south carolina": "America/New_York", "sc": "America/New_York",
    "georgia": "America/New_York", "ga": "America/New_York",
    "florida": "America/New_York", "fl": "America/New_York",
    "ohio": "America/New_York", "oh": "America/New_York",
    "michigan": "America/Detroit", "mi": "America/Detroit",
    "indiana": "America/Indiana/Indianapolis", "in": "America/Indiana/Indianapolis",
    "maine": "America/New_York", "me": "America/New_York",
    "vermont": "America/New_York", "vt": "America/New_York",
    "new hampshire": "America/New_York", "nh": "America/New_York",
    "rhode island": "America/New_York", "ri": "America/New_York",
    "west virginia": "America/New_York", "wv": "America/New_York",
    # Central
    "illinois": "America/Chicago", "il": "America/Chicago",
    "wisconsin": "America/Chicago", "wi": "America/Chicago",
    "minnesota": "America/Chicago", "mn": "America/Chicago",
    "iowa": "America/Chicago", "ia": "America/Chicago",
    "missouri": "America/Chicago", "mo": "America/Chicago",
    "arkansas": "America/Chicago", "ar": "America/Chicago",
    "louisiana": "America/Chicago", "la": "America/Chicago",
    "mississippi": "America/Chicago", "ms": "America/Chicago",
    "alabama": "America/Chicago", "al": "America/Chicago",
    "tennessee": "America/Chicago", "tn": "America/Chicago",
    "kentucky": "America/Kentucky/Louisville", "ky": "America/Kentucky/Louisville",
    "texas": "America/Chicago", "tx": "America/Chicago",
    "oklahoma": "America/Chicago", "ok": "America/Chicago",
    "kansas": "America/Chicago", "ks": "America/Chicago",
    "nebraska": "America/Chicago", "ne": "America/Chicago",
    "south dakota": "America/Chicago", "sd": "America/Chicago",
    "north dakota": "America/Chicago", "nd": "America/Chicago",
    # Mountain
    "montana": "America/Denver", "mt": "America/Denver",
    "wyoming": "America/Denver", "wy": "America/Denver",
    "colorado": "America/Denver", "co": "America/Denver",
    "new mexico": "America/Denver", "nm": "America/Denver",
    "utah": "America/Denver", "ut": "America/Denver",
    "arizona": "America/Phoenix", "az": "America/Phoenix",
    "idaho": "America/Boise", "id": "America/Boise",
    # Pacific
    "washington": "America/Los_Angeles", "wa": "America/Los_Angeles",
    "oregon": "America/Los_Angeles", "or": "America/Los_Angeles",
    "california": "America/Los_Angeles", "ca": "America/Los_Angeles",
    "nevada": "America/Los_Angeles", "nv": "America/Los_Angeles",
    # Other
    "alaska": "America/Anchorage", "ak": "America/Anchorage",
    "hawaii": "Pacific/Honolulu", "hi": "Pacific/Honolulu",
}


def extract_state_from_address(address_text: str) -> str:
    """Extract US state from address text."""
    if not address_text:
        return ""

    # Common patterns: "City, ST 12345" or "City, State 12345"
    import re

    # Try to find 2-letter state code before zip
    match = re.search(r',\s*([A-Z]{2})\s+\d{5}', address_text)
    if match:
        return match.group(1).lower()

    # Try full state names
    address_lower = address_text.lower()
    for state_name in US_STATE_TIMEZONES.keys():
        if len(state_name) > 2 and state_name in address_lower:
            return state_name

    return ""


def get_expected_timezone_for_state(state: str) -> str:
    """Get expected timezone for a US state."""
    if not state:
        return ""
    return US_STATE_TIMEZONES.get(state.lower(), "")


def _is_client_available(wp_client) -> bool:
    """Check if WordPress client (either type) is available."""
    if not wp_client:
        return False
    # PetDeskQAPluginClient has is_available(), WordPressAPIClient has is_authenticated()
    if hasattr(wp_client, 'is_available'):
        return wp_client.is_available()
    elif hasattr(wp_client, 'is_authenticated'):
        return wp_client.is_authenticated()
    return False


def check_plugins_updated(pages: dict, rule: dict, wp_client=None) -> list:
    """Check that all WordPress plugins are up to date."""
    from qa_scanner import CheckResult

    if not _is_client_available(wp_client):
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details="PetDesk QA Plugin not detected on this site. Install the plugin or verify plugins manually in wp-admin > Plugins.",
        )]

    all_updated, outdated, error = wp_client.check_plugins_updated()

    if error:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details=f"Could not check plugins: {error}",
        )]

    if all_updated:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details="All plugins are up to date",
        )]
    else:
        details = "Plugins need updates:\n"
        for p in outdated:
            details += f"  - {p['name']}: {p['current_version']} -> {p['new_version']}\n"
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="FAIL",
            weight=rule["weight"],
            details=details,
            points_lost=rule["weight"],
        )]


def check_themes_updated(pages: dict, rule: dict, wp_client=None) -> list:
    """Check that WordPress themes are up to date."""
    from qa_scanner import CheckResult

    if not _is_client_available(wp_client):
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details="PetDesk QA Plugin not detected on this site. Install the plugin or verify themes manually in wp-admin > Appearance > Themes.",
        )]

    all_updated, outdated, error = wp_client.check_themes_updated()

    if error:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details=f"Could not check themes: {error}",
        )]

    if all_updated:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details="All themes are up to date",
        )]
    else:
        details = "Themes need updates:\n"
        for t in outdated:
            details += f"  - {t['name']}: {t['current_version']} -> {t['new_version']}\n"
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="FAIL",
            weight=rule["weight"],
            details=details,
            points_lost=rule["weight"],
        )]


def check_timezone(pages: dict, rule: dict, wp_client=None,
                   expected_timezone: str = None) -> list:
    """Check that WordPress timezone matches clinic location extracted from the site."""
    from qa_scanner import CheckResult
    import re

    if not _is_client_available(wp_client):
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details="PetDesk QA Plugin not detected on this site. Install the plugin or verify timezone manually in wp-admin > Settings > General.",
        )]

    timezone, error = wp_client.get_timezone()

    if error:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details=f"Could not check timezone: {error}",
        )]

    # Try to extract expected timezone from the site's address
    if not expected_timezone and pages:
        # Look for address in footer or contact page
        address_text = ""
        for url, page_data in pages.items():
            # Check footer content
            if hasattr(page_data, 'footer_text'):
                address_text += " " + (page_data.footer_text or "")
            # Check contact pages
            if "contact" in url.lower():
                if hasattr(page_data, 'body_text'):
                    address_text += " " + (page_data.body_text or "")
            # Also check homepage
            if url.endswith('/') or url.endswith('.com') or url.endswith('.com/'):
                if hasattr(page_data, 'body_text'):
                    address_text += " " + (page_data.body_text or "")[:2000]

        # Extract state from address
        state = extract_state_from_address(address_text)
        if state:
            expected_timezone = get_expected_timezone_for_state(state)

    # If we have an expected timezone, validate against it
    if expected_timezone:
        # Check if timezones match (handle variations like America/New_York vs America/Indiana/Indianapolis)
        tz_base = timezone.split("/")[-1].lower() if "/" in timezone else timezone.lower()
        expected_base = expected_timezone.split("/")[-1].lower() if "/" in expected_timezone else expected_timezone.lower()

        # Also check if both are in the same region (America/New_York matches America/Detroit for practical purposes)
        tz_region = timezone.split("/")[0] if "/" in timezone else ""
        expected_region = expected_timezone.split("/")[0] if "/" in expected_timezone else ""

        if timezone.lower() == expected_timezone.lower():
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="PASS",
                weight=rule["weight"],
                details=f"Timezone correctly set to: {timezone} (matches clinic location)",
            )]
        elif tz_region == expected_region and tz_region == "America":
            # Same general region - probably OK (e.g., America/New_York vs America/Detroit)
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="PASS",
                weight=rule["weight"],
                details=f"Timezone set to: {timezone} (expected {expected_timezone} based on address, but both are valid US timezones)",
            )]
        elif timezone in ("UTC", "UTC+0", "UTC-0", ""):
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="FAIL",
                weight=rule["weight"],
                details=f"Timezone is UTC but clinic appears to be in {expected_timezone}. Update in wp-admin > Settings > General.",
                points_lost=rule["weight"],
            )]
        else:
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="WARN",
                weight=rule["weight"],
                details=f"Timezone is '{timezone}' but clinic address suggests '{expected_timezone}'. Verify this is correct.",
            )]
    else:
        # No expected timezone found - use smart defaults
        if timezone in ("UTC", "UTC+0", "UTC-0", ""):
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="FAIL",
                weight=rule["weight"],
                details=f"Timezone is set to UTC - this is incorrect for a veterinary clinic. Set to the clinic's local timezone in wp-admin > Settings > General.",
                points_lost=rule["weight"],
            )]
        elif "/" in timezone:
            # Named timezone like "America/New_York" - this is valid
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="PASS",
                weight=rule["weight"],
                details=f"Timezone set to: {timezone}",
            )]
        else:
            # UTC offset like "UTC-5" - less ideal but acceptable
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="PASS",
                weight=rule["weight"],
                details=f"Timezone set to: {timezone}",
            )]


def check_old_media_deleted(pages: dict, rule: dict, wp_client=None) -> list:
    """Check that old/template media files have been cleaned up."""
    from qa_scanner import CheckResult

    if not _is_client_available(wp_client):
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details="PetDesk QA Plugin not detected on this site. Install the plugin or verify media library manually in wp-admin > Media.",
        )]

    clean, old_files, error = wp_client.check_old_media_files()

    if error:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details=f"Could not check media library: {error}",
        )]

    if clean:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details="No old/template media files detected in library",
        )]
    else:
        details = f"{len(old_files)} potential leftover media file(s) found:\n"
        for f in old_files[:10]:
            details += f"  - {f['filename']} ({f['reason']}, uploaded {f['uploaded']})\n"
        if len(old_files) > 10:
            details += f"  ... and {len(old_files) - 10} more\n"
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="WARN",
            weight=rule["weight"],
            details=details,
        )]


def check_form_notifications(pages: dict, rule: dict, wp_client=None,
                             expected_email: str = None) -> list:
    """Check that form notifications are correctly configured."""
    from qa_scanner import CheckResult

    if not _is_client_available(wp_client):
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details="PetDesk QA Plugin not detected on this site. Install the plugin or verify form notifications manually in wp-admin > Forms.",
        )]

    all_correct, issues, error = wp_client.check_form_notifications(expected_email)

    if error:
        # Provide targeted guidance based on what we found
        if "Uses " in error and "Verify notifications manually" in error:
            # Other form plugin detected (CF7, Ninja, etc.) - can't inspect, pass with note
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="HUMAN_REVIEW",
                weight=rule["weight"],
                details=error,
            )]
        elif "No form plugin detected" in error:
            # No form plugin at all - likely a problem
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="WARN",
                weight=rule["weight"],
                details="No form plugin detected. Verify a contact form exists and notifications are configured.",
            )]
        elif "no forms found" in error.lower() or "no active forms" in error.lower():
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="WARN",
                weight=rule["weight"],
                details=error,
            )]
        elif "not available" in error.lower():
            return [CheckResult(
                rule_id=rule["id"],
                category=rule["category"],
                check=rule["check"],
                status="HUMAN_REVIEW",
                weight=rule["weight"],
                details="Form plugin REST API not available. Verify form notifications manually.",
            )]
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="HUMAN_REVIEW",
            weight=rule["weight"],
            details=f"Could not check form notifications: {error}",
        )]

    if all_correct:
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="PASS",
            weight=rule["weight"],
            details="All form notifications are correctly configured",
        )]
    else:
        details = "Form notification issues found:\n"
        for issue in issues:
            details += f"  - {issue['form']}: {issue['issue']}\n"
        return [CheckResult(
            rule_id=rule["id"],
            category=rule["category"],
            check=rule["check"],
            status="FAIL",
            weight=rule["weight"],
            details=details,
            points_lost=rule["weight"],
        )]


# =============================================================================
# REGISTRY FOR QA SCANNER INTEGRATION
# =============================================================================

WP_CHECK_FUNCTIONS = {
    "check_plugins_updated": check_plugins_updated,
    "check_themes_updated": check_themes_updated,
    "check_timezone": check_timezone,
    "check_old_media_deleted": check_old_media_deleted,
    "check_form_notifications": check_form_notifications,
}
