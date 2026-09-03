"""Self-contained Superset configuration for the offline AgentBI image."""

from __future__ import annotations

import os


DATABASE_DIALECT = os.getenv("DATABASE_DIALECT", "postgresql")
DATABASE_USER = os.getenv("DATABASE_USER", "superset")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "superset")
DATABASE_HOST = os.getenv("DATABASE_HOST", "db")
DATABASE_PORT = os.getenv("DATABASE_PORT", "5432")
DATABASE_DB = os.getenv("DATABASE_DB", "superset")

SQLALCHEMY_DATABASE_URI = (
    f"{DATABASE_DIALECT}://{DATABASE_USER}:{DATABASE_PASSWORD}@"
    f"{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_DB}"
)

EXAMPLES_USER = os.getenv("EXAMPLES_USER", DATABASE_USER)
EXAMPLES_PASSWORD = os.getenv("EXAMPLES_PASSWORD", DATABASE_PASSWORD)
EXAMPLES_HOST = os.getenv("EXAMPLES_HOST", DATABASE_HOST)
EXAMPLES_PORT = os.getenv("EXAMPLES_PORT", DATABASE_PORT)
EXAMPLES_DB = os.getenv("EXAMPLES_DB", "examples")
SQLALCHEMY_EXAMPLES_URI = (
    f"{DATABASE_DIALECT}://{EXAMPLES_USER}:{EXAMPLES_PASSWORD}@"
    f"{EXAMPLES_HOST}:{EXAMPLES_PORT}/{EXAMPLES_DB}"
)

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_URL": f"redis://{REDIS_HOST}:{REDIS_PORT}/1",
}
DATA_CACHE_CONFIG = CACHE_CONFIG

# This module is embedded by AgentBI and contains CSP, locale and extension settings.
from superset_config_docker import *  # noqa: E402,F403
