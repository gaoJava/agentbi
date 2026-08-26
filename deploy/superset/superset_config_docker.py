"""Local Superset settings for the AgentBI competition demo."""

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "DATASET_FOLDERS": True,
    "ENABLE_EXTENSIONS": True,
}

# Use Simplified Chinese for the competition demo while retaining an English
# fallback in the profile language selector. Superset's locale key is ``zh``.
BABEL_DEFAULT_LOCALE = "zh"
LANGUAGES = {
    "zh": {"flag": "cn", "name": "简体中文"},
    "en": {"flag": "us", "name": "English"},
}

# Mounted read-only by compose.agentbi.yml. Superset loads only the built dist tree.
LOCAL_EXTENSIONS = ["/app/extensions/agentbi-insight-pilot"]
