"""Logging filters. Kept free of Django model imports because logging is
configured before the app registry is ready."""

import logging
import re

# Invitation tokens are 43-character urlsafe strings; match anything long
# enough to be one so tampered/expired tokens are redacted too.
TOKEN_PATH = re.compile(r"/i/[A-Za-z0-9_\-]{16,}")


class RedactInvitationTokens(logging.Filter):
    """Replace invitation tokens in log messages with "[redacted]".

    Django logs request paths for 4xx/5xx responses ("Gone: /i/<token>/",
    "Forbidden (CSRF …): /i/<token>/"); those links are bearer credentials
    and must not land in the journal. The console SMS backend (portal.sms)
    is exempt: in local development the printed link is how you open an
    invitation, and that backend is never used in production.
    """

    def filter(self, record):
        if record.name.startswith("portal.sms"):
            return True
        try:
            message = record.getMessage()
        except Exception:  # malformed format args: leave the record alone
            return True
        if TOKEN_PATH.search(message):
            record.msg = TOKEN_PATH.sub("/i/[redacted]", message)
            record.args = ()
        return True
