"""
Django settings for the Open Shift Response Portal.

Configuration comes from environment variables (optionally loaded from a
.env file in the project root or the path named by ENV_FILE). See
.env.example for the full list.
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(os.environ.get("ENV_FILE", BASE_DIR / ".env"))


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-dev-only-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# Branding: shown in page titles/headers, and used to identify outgoing
# texts ("Chicot Memorial open shift: ...", "Chicot Memorial: Jane can work ...").
PORTAL_NAME = os.environ.get("PORTAL_NAME", "Chicot Memorial Open Shifts")
PORTAL_ORG_NAME = os.environ.get("PORTAL_ORG_NAME", "Chicot Memorial")

# Public base URL used to build invitation links in SMS messages,
# e.g. https://shifts.example.org (no trailing slash).
PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "http://localhost:8000").rstrip("/")

if PORTAL_BASE_URL.startswith("https://"):
    CSRF_TRUSTED_ORIGINS = [PORTAL_BASE_URL]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
                "portal.context_processors.branding",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH", BASE_DIR / "db.sqlite3"),
        "OPTIONS": {
            # Reduce "database is locked" errors under concurrent gunicorn workers.
            "timeout": 20,
            "init_command": "PRAGMA journal_mode=WAL;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "America/Chicago")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# The manifest storage requires collectstatic; use plain storage for local
# development and the test runner.
if DEBUG or "test" in sys.argv[:2]:
    STORAGES["staticfiles"] = {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "manage_login"
LOGIN_REDIRECT_URL = "manage_dashboard"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# How long an administrator stays signed in, in seconds (default 12 hours —
# administrators often use shared nurse-station computers).
SESSION_COOKIE_AGE = int(os.environ.get("ADMIN_SESSION_AGE", 12 * 60 * 60))

# How long a manual staff (badge ID) session lasts, in seconds.
STAFF_SESSION_AGE = int(os.environ.get("STAFF_SESSION_AGE", 30 * 60))

# TLS terminates at Caddy, which proxies to Django over localhost, sets
# X-Forwarded-Proto, and handles the HTTP->HTTPS redirect. Trusted in every
# mode so the HTTPS dev/testing setup (README section 4) behaves like
# production; plain localhost runserver is unaffected.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"

# --- SMS / Twilio -----------------------------------------------------------
# SMS_BACKEND: "twilio" (default) sends real SMS; "console" prints messages to
# stdout for local development and never contacts Twilio.
SMS_BACKEND = os.environ.get("SMS_BACKEND", "twilio")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")

# --- Login rate limiting ----------------------------------------------------
# Per badge ID / username: tight, because it protects one account.
LOGIN_RATE_LIMIT_MAX_FAILURES = int(os.environ.get("LOGIN_RATE_LIMIT_MAX_FAILURES", 8))
# Per client IP: looser, because a whole hospital typically shares one
# public address and one person's typos must not lock everyone out.
LOGIN_RATE_LIMIT_IP_MAX_FAILURES = int(os.environ.get("LOGIN_RATE_LIMIT_IP_MAX_FAILURES", 50))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 15 * 60))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        # Invitation links are bearer credentials; Django logs request paths
        # for 4xx/5xx responses, so strip the token before anything is written.
        "redact_invitation_tokens": {
            "()": "portal.logging_filters.RedactInvitationTokens",
        },
    },
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["redact_invitation_tokens"],
        },
    },
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
