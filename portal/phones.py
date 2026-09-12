"""US-centric phone number normalization to E.164."""

import re

from django.core.exceptions import ValidationError

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw):
    """Return the number in E.164 (+1XXXXXXXXXX) form.

    Accepts common US formats: (312) 555-0147, 312-555-0147, 3125550147,
    13125550147, +13125550147. Raises ValidationError otherwise.
    """
    raw = (raw or "").strip()
    digits = _NON_DIGIT.sub("", raw)
    if raw.startswith("+") and not raw.startswith("+1"):
        raise ValidationError("Only US (+1) mobile numbers are supported.")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) == 11 and digits.startswith("1") and digits[1] not in "01":
        return "+" + digits
    raise ValidationError("Enter a valid 10-digit US mobile number.")


def last4(phone):
    digits = _NON_DIGIT.sub("", phone or "")
    return digits[-4:]


def format_us_phone(phone):
    """'+13125550147' -> '(312) 555-0147' for display; anything else unchanged."""
    digits = _NON_DIGIT.sub("", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return phone or ""
