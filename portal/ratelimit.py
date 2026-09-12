"""Small database-backed failure counter for login rate limiting.

Database-backed so the count is shared across gunicorn workers without
adding a cache service.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import RateLimitEvent

SCOPE_STAFF_LOGIN_IP = "staff_ip"
SCOPE_STAFF_LOGIN_BADGE = "staff_badge"
SCOPE_ADMIN_LOGIN_IP = "admin_ip"
SCOPE_ADMIN_LOGIN_USER = "admin_user"


def _window_start():
    return timezone.now() - timedelta(seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS)


def client_ip(request):
    # Caddy proxies from localhost and sets X-Forwarded-For to the real
    # client address; fall back to REMOTE_ADDR for direct connections.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.META.get("REMOTE_ADDR", "")[:64]


def is_blocked(scope, key):
    key = (key or "")[:190]
    # Prune expired events for this key so the table stays small.
    RateLimitEvent.objects.filter(scope=scope, key=key, created_at__lt=_window_start()).delete()
    count = RateLimitEvent.objects.filter(
        scope=scope, key=key, created_at__gte=_window_start()
    ).count()
    return count >= settings.LOGIN_RATE_LIMIT_MAX_FAILURES


def record_failure(scope, key):
    RateLimitEvent.objects.create(scope=scope, key=(key or "")[:190])


def clear_failures(scope, key):
    RateLimitEvent.objects.filter(scope=scope, key=(key or "")[:190]).delete()
