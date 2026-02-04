"""
QA Rules Engine for Zero-Touch QA
Loads checklist rules from rules.json with partner-specific overlays.
Rules can be edited via the web UI at /rules/edit -- no coding required.
"""

import json
import os

_RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")


def _load_rules() -> dict:
    """Load all rules from the JSON file."""
    try:
        with open(_RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"universal": [], "western": [], "independent": [],
                "heartland": [], "evervet": [], "encore": [],
                "amerivet": [], "rarebreed": [], "united": []}


def _save_rules(data: dict):
    """Save rules back to the JSON file."""
    with open(_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_all_rules() -> dict:
    """Return the full rules dict keyed by partner group."""
    return _load_rules()


def get_rules_for_scan(partner: str, phase: str) -> list:
    """
    Returns the combined list of rules applicable to a given partner and build phase.
    """
    partner = partner.lower().strip()
    phase = phase.lower().strip()
    data = _load_rules()

    applicable = []

    # Add universal rules for this phase
    for rule in data.get("universal", []):
        if phase in rule.get("phase", []):
            applicable.append(rule)

    # Add partner-specific rules for this phase
    for rule in data.get(partner, []):
        if phase in rule.get("phase", []):
            applicable.append(rule)

    return applicable


def get_automatable_rules(rules: list) -> list:
    """Filter to only rules that can be run automatically."""
    return [r for r in rules if r.get("automated", False)]


def get_human_review_rules(rules: list) -> list:
    """Filter to rules that require human review."""
    return [r for r in rules if not r.get("automated", False)]


# For backwards compatibility - expose loaded data as module-level variables
def _get_rules_list(key):
    return _load_rules().get(key, [])


# These are properties that load from JSON on access
class _RulesProxy:
    """Lazy loader that reads from rules.json each time, so edits are reflected immediately."""
    def __getattr__(self, name):
        mapping = {
            "UNIVERSAL_RULES": "universal",
            "WESTERN_RULES": "western",
            "INDEPENDENT_RULES": "independent",
            "HEARTLAND_RULES": "heartland",
            "EVERVET_RULES": "evervet",
            "ENCORE_RULES": "encore",
            "AMERIVET_RULES": "amerivet",
            "RAREBREED_RULES": "rarebreed",
            "UNITED_RULES": "united",
        }
        if name in mapping:
            return _load_rules().get(mapping[name], [])
        raise AttributeError(name)


_proxy = _RulesProxy()

# Module-level names for import compatibility
UNIVERSAL_RULES = property(lambda self: _proxy.UNIVERSAL_RULES)
PARTNER_RULE_MAP = {
    "independent": "independent",
    "western": "western",
    "heartland": "heartland",
    "evervet": "evervet",
    "encore": "encore",
    "amerivet": "amerivet",
    "rarebreed": "rarebreed",
    "united": "united",
}


def get_partner_rule_map() -> dict:
    """Return a dict mapping partner name -> list of partner-specific rules."""
    data = _load_rules()
    result = {}
    for key in ["independent", "western", "heartland", "evervet",
                 "encore", "amerivet", "rarebreed", "united"]:
        result[key] = data.get(key, [])
    return result
