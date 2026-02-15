"""Base settings shared by all environments."""

from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

from apps.common.env import get_bool, get_env, get_int, get_list

BASE_DIR = Path(__file__).resolve().parents[2]


def _parse_openalex_security_level_ratios(raw: str) -> tuple[int, int, int]:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 3:
        raise ImproperlyConfigured(
            "OPENALEX_SECURITY_LEVEL_RATIOS must have 3 comma-separated integers "
            "(PUBLIC,INTERNAL,CONFIDENTIAL)."
        )

    try:
        public, internal, confidential = (int(part) for part in parts)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "OPENALEX_SECURITY_LEVEL_RATIOS values must be integers."
        ) from exc

    if any(value < 0 for value in (public, internal, confidential)):
        raise ImproperlyConfigured("OPENALEX_SECURITY_LEVEL_RATIOS values must be non-negative.")
    if (public + internal + confidential) != 100:
        raise ImproperlyConfigured("OPENALEX_SECURITY_LEVEL_RATIOS must sum to 100.")

    return (public, internal, confidential)


def _parse_database_url(raw_url: str) -> dict[str, str]:
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.lower()

    if scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        name = unquote(parsed.path.lstrip("/"))
        if not name:
            raise ImproperlyConfigured("DATABASE_URL is missing the database name.")

        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": name,
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
        }

    if scheme == "sqlite":
        if parsed.path in {"", "/"}:
            name = ":memory:"
        else:
            name = unquote(parsed.path[1:] if parsed.path.startswith("/") else parsed.path)

        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": name,
        }

    raise ImproperlyConfigured(
        "Unsupported DATABASE_URL scheme. Use postgresql:// or sqlite://."
    )


SECRET_KEY = get_env("DJANGO_SECRET_KEY", default="dev-insecure-secret-key")
DEBUG = get_bool("DEBUG", default=get_bool("DJANGO_DEBUG", default=False))
ALLOWED_HOSTS = get_list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "pgvector.django",
    "apps.api",
    "apps.documents",
    "apps.graphsync",
    "apps.health",
    "apps.ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASE_URL = get_env(
    "DATABASE_URL",
    default="postgresql://expert_user:expert_password@postgres:5432/expert_graph_rag",
)
DATABASES = {"default": _parse_database_url(DATABASE_URL)}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = get_env("REDIS_URL", default="redis://redis:6379/0")
CELERY_BROKER_URL = get_env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = get_env("CELERY_RESULT_BACKEND", default="redis://redis:6379/1")
CELERY_TASK_ALWAYS_EAGER = get_bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_TIME_LIMIT = get_int("CELERY_TASK_TIME_LIMIT", default=300)
CELERY_TASK_SOFT_TIME_LIMIT = get_int("CELERY_TASK_SOFT_TIME_LIMIT", default=240)

NEO4J_URI = get_env("NEO4J_URI", default="bolt://neo4j:7687")
NEO4J_USER = get_env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = get_env("NEO4J_PASSWORD", default="change-me")

EMBEDDING_DIM = get_int("EMBEDDING_DIM", default=8)
if EMBEDDING_DIM != 8:
    raise ImproperlyConfigured("EMBEDDING_DIM must be 8 in this demo (migration is fixed to 8).")
CHUNK_SIZE = get_int("CHUNK_SIZE", default=180)
CHUNK_OVERLAP = get_int("CHUNK_OVERLAP", default=40)
if CHUNK_SIZE <= 0:
    raise ImproperlyConfigured("CHUNK_SIZE must be greater than 0.")
if CHUNK_OVERLAP < 0:
    raise ImproperlyConfigured("CHUNK_OVERLAP must be zero or greater.")
if CHUNK_OVERLAP >= CHUNK_SIZE:
    raise ImproperlyConfigured("CHUNK_OVERLAP must be smaller than CHUNK_SIZE.")

EMBEDDING_BACKEND = get_env("EMBEDDING_BACKEND", default="auto").strip().lower()
if EMBEDDING_BACKEND not in {"auto", "local", "openai"}:
    raise ImproperlyConfigured("EMBEDDING_BACKEND must be one of: auto, local, openai.")
ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK = get_bool(
    "ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK",
    default=True,
)

LOCAL_EMBEDDING_MODEL = get_env(
    "LOCAL_EMBEDDING_MODEL",
    default="sentence-transformers/all-MiniLM-L6-v2",
)
OPENAI_API_KEY = get_env("OPENAI_API_KEY", default="")
OPENAI_EMBEDDING_MODEL = get_env("OPENAI_EMBEDDING_MODEL", default="text-embedding-3-small")
OPENAI_MODEL = get_env(
    "OPENAI_MODEL",
    default=get_env("OPENAI_ANSWER_MODEL", default="gpt-4o-mini"),
)
OPENAI_BASE_URL = get_env("OPENAI_BASE_URL", default="").strip()
try:
    OPENAI_TEMPERATURE = float(get_env("OPENAI_TEMPERATURE", default="0.1"))
except ValueError as exc:
    raise ImproperlyConfigured("OPENAI_TEMPERATURE must be a valid float.") from exc
if OPENAI_TEMPERATURE < 0 or OPENAI_TEMPERATURE > 2:
    raise ImproperlyConfigured("OPENAI_TEMPERATURE must be between 0 and 2.")

SEARCH_PAGE_SIZE = get_int("SEARCH_PAGE_SIZE", default=10)
SEARCH_SCAN_BATCH_SIZE = get_int("SEARCH_SCAN_BATCH_SIZE", default=200)
SEARCH_MAX_CHUNK_SCAN = get_int("SEARCH_MAX_CHUNK_SCAN", default=2000)
SEARCH_SNIPPET_MAX_CHARS = get_int("SEARCH_SNIPPET_MAX_CHARS", default=220)
SEARCH_GRAPH_SEED_PAPERS = get_int("SEARCH_GRAPH_SEED_PAPERS", default=8)
SEARCH_GRAPH_EXPANSION_LIMIT = get_int("SEARCH_GRAPH_EXPANSION_LIMIT", default=40)
SEARCH_GRAPH_HOP_LIMIT = get_int("SEARCH_GRAPH_HOP_LIMIT", default=2)
if SEARCH_PAGE_SIZE <= 0:
    raise ImproperlyConfigured("SEARCH_PAGE_SIZE must be greater than 0.")
if SEARCH_SCAN_BATCH_SIZE <= 0:
    raise ImproperlyConfigured("SEARCH_SCAN_BATCH_SIZE must be greater than 0.")
if SEARCH_MAX_CHUNK_SCAN <= 0:
    raise ImproperlyConfigured("SEARCH_MAX_CHUNK_SCAN must be greater than 0.")
if SEARCH_SNIPPET_MAX_CHARS <= 0:
    raise ImproperlyConfigured("SEARCH_SNIPPET_MAX_CHARS must be greater than 0.")
if SEARCH_GRAPH_SEED_PAPERS <= 0:
    raise ImproperlyConfigured("SEARCH_GRAPH_SEED_PAPERS must be greater than 0.")
if SEARCH_GRAPH_EXPANSION_LIMIT < 0:
    raise ImproperlyConfigured("SEARCH_GRAPH_EXPANSION_LIMIT must be 0 or greater.")
if SEARCH_GRAPH_HOP_LIMIT not in {1, 2}:
    raise ImproperlyConfigured("SEARCH_GRAPH_HOP_LIMIT must be 1 or 2.")

EXPERTS_TOP_EXPERTS = get_int("EXPERTS_TOP_EXPERTS", default=10)
EXPERTS_TOP_PAPERS = get_int("EXPERTS_TOP_PAPERS", default=3)
EXPERTS_TOP_TOPICS = get_int("EXPERTS_TOP_TOPICS", default=3)
EXPERTS_MAX_CHUNK_SCAN = get_int("EXPERTS_MAX_CHUNK_SCAN", default=3000)
EXPERTS_TOPIC_DIVERSITY_TARGET = get_int("EXPERTS_TOPIC_DIVERSITY_TARGET", default=5)
EXPERTS_ENABLE_GRAPH_CENTRALITY = get_bool("EXPERTS_ENABLE_GRAPH_CENTRALITY", default=False)
if EXPERTS_TOP_EXPERTS <= 0:
    raise ImproperlyConfigured("EXPERTS_TOP_EXPERTS must be greater than 0.")
if EXPERTS_TOP_PAPERS <= 0:
    raise ImproperlyConfigured("EXPERTS_TOP_PAPERS must be greater than 0.")
if EXPERTS_TOP_TOPICS <= 0:
    raise ImproperlyConfigured("EXPERTS_TOP_TOPICS must be greater than 0.")
if EXPERTS_MAX_CHUNK_SCAN <= 0:
    raise ImproperlyConfigured("EXPERTS_MAX_CHUNK_SCAN must be greater than 0.")
if EXPERTS_TOPIC_DIVERSITY_TARGET <= 0:
    raise ImproperlyConfigured("EXPERTS_TOPIC_DIVERSITY_TARGET must be greater than 0.")

ASK_TOP_K = get_int("ASK_TOP_K", default=8)
ASK_MAX_CHUNK_SCAN = get_int("ASK_MAX_CHUNK_SCAN", default=2000)
ASK_FALLBACK_SENTENCE_COUNT = get_int("ASK_FALLBACK_SENTENCE_COUNT", default=3)
if ASK_TOP_K <= 0:
    raise ImproperlyConfigured("ASK_TOP_K must be greater than 0.")
if ASK_MAX_CHUNK_SCAN <= 0:
    raise ImproperlyConfigured("ASK_MAX_CHUNK_SCAN must be greater than 0.")
if ASK_FALLBACK_SENTENCE_COUNT <= 0:
    raise ImproperlyConfigured("ASK_FALLBACK_SENTENCE_COUNT must be greater than 0.")

OPENAI_ANSWER_MODEL = OPENAI_MODEL

LOG_LEVEL = get_env("LOG_LEVEL", default="INFO")

OPENALEX_BASE_URL = get_env("OPENALEX_BASE_URL", default="https://api.openalex.org")
OPENALEX_API_KEY = get_env("OPENALEX_API_KEY", default="")
OPENALEX_EMAIL = get_env("OPENALEX_EMAIL", default="")
OPENALEX_MAILTO = get_env("OPENALEX_MAILTO", default=OPENALEX_EMAIL)
OPENALEX_PAGE_SIZE = get_int("OPENALEX_PAGE_SIZE", default=200)
OPENALEX_HTTP_TIMEOUT_SECONDS = get_int("OPENALEX_HTTP_TIMEOUT_SECONDS", default=15)
OPENALEX_MAX_RETRIES = get_int("OPENALEX_MAX_RETRIES", default=3)
OPENALEX_BACKOFF_SECONDS = get_int("OPENALEX_BACKOFF_SECONDS", default=1)
OPENALEX_RATE_LIMIT_RPS = get_int(
    "OPENALEX_RATE_LIMIT_PER_SEC",
    default=get_int("OPENALEX_RATE_LIMIT_RPS", default=5),
)
OPENALEX_SECURITY_LEVEL_RATIOS = _parse_openalex_security_level_ratios(
    get_env("OPENALEX_SECURITY_LEVEL_RATIOS", default="70,20,10")
)
OPENALEX_LIVE_FETCH = get_bool("OPENALEX_LIVE_FETCH", default=True)
OPENALEX_LIVE_MIN_RESULTS = get_int("OPENALEX_LIVE_MIN_RESULTS", default=10)
OPENALEX_LIVE_FETCH_LIMIT = get_int("OPENALEX_LIVE_FETCH_LIMIT", default=40)
OPENALEX_LIVE_FETCH_COOLDOWN_SECONDS = get_int("OPENALEX_LIVE_FETCH_COOLDOWN_SECONDS", default=900)
OPENALEX_CACHE_ENABLED = get_bool("OPENALEX_CACHE_ENABLED", default=True)
OPENALEX_CACHE_TTL_SECONDS = get_int("OPENALEX_CACHE_TTL_SECONDS", default=900)
try:
    OPENALEX_MIN_QUERY_COVERAGE = float(get_env("OPENALEX_MIN_QUERY_COVERAGE", default="0.22"))
except ValueError as exc:
    raise ImproperlyConfigured("OPENALEX_MIN_QUERY_COVERAGE must be a valid float.") from exc
OPENALEX_MAX_TOPICS_PER_WORK = get_int("OPENALEX_MAX_TOPICS_PER_WORK", default=8)

if OPENALEX_PAGE_SIZE <= 0:
    raise ImproperlyConfigured("OPENALEX_PAGE_SIZE must be greater than 0.")
if OPENALEX_HTTP_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured("OPENALEX_HTTP_TIMEOUT_SECONDS must be greater than 0.")
if OPENALEX_MAX_RETRIES < 0:
    raise ImproperlyConfigured("OPENALEX_MAX_RETRIES must be 0 or greater.")
if OPENALEX_BACKOFF_SECONDS < 0:
    raise ImproperlyConfigured("OPENALEX_BACKOFF_SECONDS must be 0 or greater.")
if OPENALEX_RATE_LIMIT_RPS <= 0:
    raise ImproperlyConfigured("OPENALEX_RATE_LIMIT_RPS must be greater than 0.")
if OPENALEX_LIVE_MIN_RESULTS <= 0:
    raise ImproperlyConfigured("OPENALEX_LIVE_MIN_RESULTS must be greater than 0.")
if OPENALEX_LIVE_FETCH_LIMIT <= 0:
    raise ImproperlyConfigured("OPENALEX_LIVE_FETCH_LIMIT must be greater than 0.")
if OPENALEX_LIVE_FETCH_COOLDOWN_SECONDS < 0:
    raise ImproperlyConfigured("OPENALEX_LIVE_FETCH_COOLDOWN_SECONDS must be 0 or greater.")
if OPENALEX_CACHE_TTL_SECONDS < 0:
    raise ImproperlyConfigured("OPENALEX_CACHE_TTL_SECONDS must be 0 or greater.")
if OPENALEX_MIN_QUERY_COVERAGE < 0 or OPENALEX_MIN_QUERY_COVERAGE > 1:
    raise ImproperlyConfigured("OPENALEX_MIN_QUERY_COVERAGE must be between 0 and 1.")
if OPENALEX_MAX_TOPICS_PER_WORK <= 0:
    raise ImproperlyConfigured("OPENALEX_MAX_TOPICS_PER_WORK must be greater than 0.")

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s [%(name)s] %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "openai._base_client": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "neo4j.notifications": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
