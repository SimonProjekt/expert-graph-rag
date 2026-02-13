from django.core.exceptions import ImproperlyConfigured

from apps.common.env import get_bool, get_env, get_list
from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, SECRET_KEY

DEBUG = get_bool("DEBUG", default=False)

if SECRET_KEY in {"change-me-in-production", "dev-insecure-secret-key"}:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a secure value in production.")

if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to explicit hostnames in production.")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

STATIC_ROOT = get_env("DJANGO_STATIC_ROOT", default="/app/staticfiles")
CSRF_TRUSTED_ORIGINS = get_list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
