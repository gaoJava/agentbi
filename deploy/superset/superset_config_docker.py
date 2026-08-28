"""Local Superset settings for the AgentBI competition demo."""

from copy import deepcopy

from superset.config import (
    TALISMAN_CONFIG as DEFAULT_TALISMAN_CONFIG,
    TALISMAN_DEV_CONFIG as DEFAULT_TALISMAN_DEV_CONFIG,
)

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "DATASET_FOLDERS": True,
    "EMBEDDED_SUPERSET": True,
    "ENABLE_EXTENSIONS": True,
}

# Keep Superset's default CSP and only allow the loopback AgentBI workbench to
# frame dashboards. Production embedding should use Superset guest tokens and
# replace these origins with the exact HTTPS application origin.
def agentbi_talisman_config(default: dict) -> dict:
    """Preserve upstream CSP while allowing only the local AgentBI origin."""

    config = deepcopy(default)
    config["frame_options"] = None
    config["content_security_policy"]["frame-ancestors"] = [
        "'self'",
        "http://127.0.0.1:8090",
        "http://localhost:8090",
    ]
    return config


TALISMAN_CONFIG = agentbi_talisman_config(DEFAULT_TALISMAN_CONFIG)
TALISMAN_DEV_CONFIG = agentbi_talisman_config(DEFAULT_TALISMAN_DEV_CONFIG)

# Use Simplified Chinese for the competition demo while retaining an English
# fallback in the profile language selector. Superset's locale key is ``zh``.
BABEL_DEFAULT_LOCALE = "zh"
LANGUAGES = {
    "zh": {"flag": "cn", "name": "简体中文"},
    "en": {"flag": "us", "name": "English"},
}

# Mounted read-only by compose.agentbi.yml. Superset loads only the built dist tree.
LOCAL_EXTENSIONS = ["/app/extensions/agentbi-insight-pilot"]
