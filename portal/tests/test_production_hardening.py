"""Tests for the production-readiness and phone-UX fixes."""
import logging
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from portal import sms
from portal.logging_filters import RedactInvitationTokens
from portal.phones import format_us_phone

from .base import PortalTestCase

TOKEN = "kX9v2QpZ8mN4rT7wY1bC6dF3gH5jL0sA8uE2iO4pQ6z"


class TokenRedactionTests(PortalTestCase):
    def _record(self, name, msg, args=()):
        return logging.LogRecord(name, logging.WARNING, __file__, 1, msg, args, None)

    def test_request_log_paths_are_redacted(self):
        record = self._record("django.request", "%s: %s", ("Gone", f"/i/{TOKEN}/"))
        RedactInvitationTokens().filter(record)
        self.assertEqual(record.getMessage(), "Gone: /i/[redacted]/")
        self.assertNotIn(TOKEN, record.getMessage())

    def test_csrf_security_log_is_redacted(self):
        record = self._record(
            "django.security.csrf", "Forbidden (CSRF cookie not set.): /i/%s/", (TOKEN,)
        )
        RedactInvitationTokens().filter(record)
        self.assertNotIn(TOKEN, record.getMessage())

    def test_console_sms_backend_is_exempt(self):
        record = self._record(
            "portal.sms", "SMS (console backend) to %s: Respond here: http://x/i/%s/",
            ("+13125550111", TOKEN),
        )
        RedactInvitationTokens().filter(record)
        self.assertIn(TOKEN, record.getMessage())

    def test_filter_is_wired_into_console_handler(self):
        self.assertIn(
            "redact_invitation_tokens", settings.LOGGING["handlers"]["console"]["filters"]
        )
        self.assertEqual(
            settings.LOGGING["filters"]["redact_invitation_tokens"]["()"],
            "portal.logging_filters.RedactInvitationTokens",
        )


class TwilioFailureLoggingTests(PortalTestCase):
    @override_settings(
        SMS_BACKEND="twilio",
        TWILIO_ACCOUNT_SID="ACtest",
        TWILIO_AUTH_TOKEN="secret",
        TWILIO_FROM_NUMBER="+13125550000",
    )
    def test_failure_log_masks_destination_number(self):
        with patch("twilio.rest.Client") as client_cls:
            client_cls.return_value.messages.create.side_effect = RuntimeError("boom")
            with self.assertLogs("portal.sms", level="WARNING") as captured:
                sid, error = sms.deliver("+13125550111", "hello")
        self.assertEqual(sid, "")
        self.assertIn("boom", error)
        joined = "\n".join(captured.output)
        self.assertNotIn("+13125550111", joined)
        self.assertIn("***0111", joined)


class SessionAndRateLimitSettingsTests(PortalTestCase):
    def test_admin_sessions_default_to_twelve_hours(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 12 * 60 * 60)

    def test_ip_limit_is_looser_than_identity_limit(self):
        self.assertGreater(
            settings.LOGIN_RATE_LIMIT_IP_MAX_FAILURES, settings.LOGIN_RATE_LIMIT_MAX_FAILURES
        )

    @override_settings(LOGIN_RATE_LIMIT_MAX_FAILURES=100, LOGIN_RATE_LIMIT_IP_MAX_FAILURES=2)
    def test_ip_threshold_applies_across_different_badges(self):
        for badge in ("1001", "1002"):
            self.client.post("/staff/login/", {"badge_id": badge, "phone_last4": "0000"})
        response = self.client.post(
            "/staff/login/", {"badge_id": "1003", "phone_last4": "0000"}
        )
        self.assertContains(response, "Too many attempts")

    @override_settings(LOGIN_RATE_LIMIT_MAX_FAILURES=2, LOGIN_RATE_LIMIT_IP_MAX_FAILURES=100)
    def test_one_persons_typos_do_not_block_a_colleague(self):
        jane = self.make_staff(name="Jane Smith", badge_id="1001", mobile_phone="+13125550111")
        bob = self.make_staff(name="Bob Reyes", badge_id="1002", mobile_phone="+13125550122")
        for _ in range(2):
            self.staff_login(jane, last4="0000")
        # Jane is now blocked, but Bob (same IP) can still sign in.
        blocked = self.staff_login(jane)
        self.assertContains(blocked, "Too many attempts")
        ok = self.staff_login(bob)
        self.assertRedirects(ok, reverse("staff_invitation_list"))


class PhoneDisplayTests(PortalTestCase):
    def test_format_us_phone(self):
        self.assertEqual(format_us_phone("+13125550147"), "(312) 555-0147")
        self.assertEqual(format_us_phone(""), "")
        self.assertEqual(format_us_phone("+441234567890"), "+441234567890")

    def test_staff_list_shows_formatted_numbers(self):
        admin = self.make_admin()
        self.make_staff(name="Jane Smith", badge_id="1001", mobile_phone="+13125550111")
        self.client.force_login(admin)
        response = self.client.get(reverse("staff_list"))
        self.assertContains(response, "(312) 555-0111")

    def test_edit_forms_prefill_formatted_but_save_normalized(self):
        admin = self.make_admin(notification_phone="+13125550100")
        person = self.make_staff(name="Jane Smith", badge_id="1001", mobile_phone="+13125550111")
        self.client.force_login(admin)
        response = self.client.get(reverse("staff_edit", args=[person.pk]))
        self.assertContains(response, 'value="(312) 555-0111"')
        response = self.client.get(reverse("manage_account"))
        self.assertContains(response, 'value="(312) 555-0100"')
        # Submitting the formatted value round-trips to E.164.
        self.client.post(
            reverse("manage_account"), {"notification_phone": "(312) 555-0199"}
        )
        admin.portal_profile.refresh_from_db()
        self.assertEqual(admin.portal_profile.notification_phone, "+13125550199")


class CardCreatorTests(PortalTestCase):
    def test_dashboard_card_names_who_posted_the_shift(self):
        admin = self.make_admin(username="cno")
        admin.first_name, admin.last_name = "Pat", "Morgan"
        admin.save()
        self.make_shift(admin)
        self.client.force_login(admin)
        response = self.client.get(reverse("manage_dashboard"))
        self.assertContains(response, "Posted by Pat Morgan")


class PasswordPageTests(PortalTestCase):
    def test_password_page_renders_rules_after_input(self):
        admin = self.make_admin()
        self.client.force_login(admin)
        response = self.client.get(reverse("manage_password_change"))
        html = response.content.decode()
        self.assertIn('name="new_password1"', html)
        self.assertIn("at least 12 characters", html)
        self.assertLess(html.index('name="new_password1"'), html.index("at least 12 characters"))
