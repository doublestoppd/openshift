"""Outbound SMS: message building, Twilio transport, and the SMS log.

Sending is synchronous and per-recipient; one failure never stops the rest
of a batch. Every attempt is recorded in SmsLog. The console backend
(SMS_BACKEND=console) prints messages instead of contacting Twilio, for
local development.
"""

import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import SmsLog

logger = logging.getLogger(__name__)


def deliver(to, body):
    """Submit one message. Returns (twilio_sid, error_message); exactly one is set."""
    backend = getattr(settings, "SMS_BACKEND", "twilio")
    if backend == "console":
        logger.info("SMS (console backend) to %s: %s", to, body)
        return f"console-{timezone.now().timestamp()}", ""
    if not (
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_FROM_NUMBER
    ):
        return "", "Twilio is not configured (missing TWILIO_* environment variables)."
    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            to=to, from_=settings.TWILIO_FROM_NUMBER, body=body
        )
        return message.sid or "", ""
    except Exception as exc:  # Twilio raises many exception types; log and report all.
        logger.warning("Twilio send to ***%s failed: %s", to[-4:], exc)
        return "", str(exc)[:500]


def _short_date(shift):
    return shift.shift_date.strftime("%-m/%-d")


def _shift_label(shift):
    return f"{shift.get_department_display()} {shift.get_shift_type_display()}"


def invitation_body(shift, respond_url):
    day = shift.shift_date.strftime("%a")
    text = f"{settings.PORTAL_ORG_NAME} open shift: {_shift_label(shift)} on {day} {_short_date(shift)}."
    if shift.incentive_amount:
        text += f" ${shift.incentive_display} bonus."
    text += f" Respond here: {respond_url}"
    return text


def invitation_url(raw_token):
    return settings.PORTAL_BASE_URL + reverse("staff_invitation", args=[raw_token])


def send_invitation(invitation, raw_token):
    """Send one invitation SMS and log the attempt. Returns True on success."""
    body = invitation_body(invitation.shift, invitation_url(raw_token))
    sid, error = deliver(invitation.staff.mobile_phone, body)
    ok = not error
    SmsLog.objects.create(
        message_type=SmsLog.MessageType.SHIFT_INVITATION,
        shift=invitation.shift,
        staff=invitation.staff,
        destination_phone=invitation.staff.mobile_phone,
        twilio_sid=sid,
        result=SmsLog.Result.SENT_TO_TWILIO if ok else SmsLog.Result.FAILED,
        error_message=error,
    )
    if ok:
        invitation.last_sent_at = timezone.now()
        invitation.save(update_fields=["last_sent_at"])
    return ok


def acceptance_body(response):
    shift = response.invitation.shift
    staff = response.invitation.staff
    if response.response_type == response.Type.ACCEPT_PARTIAL:
        window = response.partial_window_display.replace("–", "-")
        return (
            f"{settings.PORTAL_ORG_NAME}: {staff.name} can work part of {_shift_label(shift)} "
            f"on {_short_date(shift)}, available {window}."
        )
    return f"{settings.PORTAL_ORG_NAME}: {staff.name} can work {_shift_label(shift)} on {_short_date(shift)}."


def notify_acceptance(response):
    """SMS the shift creator about a full/partial acceptance.

    Never raises: the employee response must save even if notification
    fails. Skips silently when the creator has no notification number.
    """
    shift = response.invitation.shift
    try:
        creator = shift.created_by
        profile = getattr(creator, "portal_profile", None)
        phone = profile.notification_phone if profile else ""
        if not phone:
            return
        body = acceptance_body(response)
        sid, error = deliver(phone, body)
        SmsLog.objects.create(
            message_type=SmsLog.MessageType.ADMIN_ACCEPTANCE_NOTIFICATION,
            shift=shift,
            staff=response.invitation.staff,
            admin_user=creator,
            destination_phone=phone,
            twilio_sid=sid,
            result=SmsLog.Result.SENT_TO_TWILIO if not error else SmsLog.Result.FAILED,
            error_message=error,
        )
    except Exception:
        logger.exception("Acceptance notification failed for shift %s", shift.pk)
