"""Local Superset settings for the AgentBI competition demo."""

from copy import deepcopy

from superset.config import TALISMAN_CONFIG as DEFAULT_TALISMAN_CONFIG

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "DATASET_FOLDERS": True,
    "EMBEDDED_SUPERSET": True,
    "ENABLE_EXTENSIONS": True,
}

# Keep Superset's default CSP and only allow the loopback AgentBI workbench to
# frame dashboards. Production embedding should use Superset guest tokens and
# replace these origins with the exact HTTPS application origin.
TALISMAN_CONFIG = deepcopy(DEFAULT_TALISMAN_CONFIG)
TALISMAN_CONFIG["frame_options"] = None
TALISMAN_CONFIG["content_security_policy"]["frame-ancestors"] = [
    "'self'",
    "http://127.0.0.1:8090",
    "http://localhost:8090",
]

# Use Simplified Chinese for the competition demo while retaining an English
# fallback in the profile language selector. Superset's locale key is ``zh``.
BABEL_DEFAULT_LOCALE = "zh"
LANGUAGES = {
    "zh": {"flag": "cn", "name": "简体中文"},
    "en": {"flag": "us", "name": "English"},
}

# Mounted read-only by compose.agentbi.yml. Superset loads only the built dist tree.
LOCAL_EXTENSIONS = ["/app/extensions/agentbi-insight-pilot"]
