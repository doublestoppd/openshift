from datetime import time
from unittest.mock import patch

from django.urls import reverse

from portal.models import Shift, ShiftResponse, SmsLog

from .base import PortalTestCase


class ResponseSubmissionTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin(notification_phone="+13125550100")
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.shift = self.make_shift(self.admin)
        self.invitation, self.token = self.make_invitation(self.shift, self.jane)
        self.url = reverse("staff_invitation", args=[self.token])

    def test_full_acceptance_saves(self):
        response = self.client.post(self.url, {"response_type": "ACCEPT_FULL"}, follow=True)
        self.assertContains(response, "you can work the full shift")
        saved = ShiftResponse.objects.get(invitation=self.invitation)
        self.assertEqual(saved.response_type, ShiftResponse.Type.ACCEPT_FULL)
        self.assertIsNone(saved.partial_start_time)

    def test_partial_requires_both_times(self):
        response = self.client.post(
            self.url,
            {"response_type": "ACCEPT_PARTIAL", "partial_start_time": "23:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShiftResponse.objects.exists())

        response = self.client.post(
            self.url,
            {"response_type": "ACCEPT_PARTIAL", "partial_end_time": "03:00"},
        )
        self.assertFalse(ShiftResponse.objects.exists())

    def test_partial_acceptance_saves_with_both_times(self):
        self.client.post(
            self.url,
            {
                "response_type": "ACCEPT_PARTIAL",
                "partial_start_time": "23:00",
                "partial_end_time": "03:00",
            },
        )
        saved = ShiftResponse.objects.get(invitation=self.invitation)
        self.assertEqual(saved.response_type, ShiftResponse.Type.ACCEPT_PARTIAL)
        # Overnight window: end before start is allowed and preserved as-is.
        self.assertEqual(saved.partial_start_time, time(23, 0))
        self.assertEqual(saved.partial_end_time, time(3, 0))

    def test_decline_saves(self):
        self.client.post(self.url, {"response_type": "DECLINED"})
        saved = ShiftResponse.objects.get(invitation=self.invitation)
        self.assertEqual(saved.response_type, ShiftResponse.Type.DECLINED)

    def test_changing_response_updates_the_same_row(self):
        self.client.post(self.url, {"response_type": "DECLINED"})
        first = ShiftResponse.objects.get(invitation=self.invitation)

        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        self.assertEqual(ShiftResponse.objects.count(), 1)
        second = ShiftResponse.objects.get(invitation=self.invitation)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.response_type, ShiftResponse.Type.ACCEPT_FULL)
        self.assertGreaterEqual(second.updated_at, first.updated_at)

    def test_never_says_scheduled_or_assigned(self):
        response = self.client.post(self.url, {"response_type": "ACCEPT_FULL"}, follow=True)
        content = response.content.decode().lower()
        for forbidden in ("the shift is yours", "you are scheduled", "has been assigned"):
            self.assertNotIn(forbidden, content)
        self.assertContains(response, "The department will contact you")


class ResponseAuthorizationTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin()
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001")

    def test_inactive_staff_cannot_respond(self):
        shift = self.make_shift(self.admin)
        invitation, token = self.make_invitation(shift, self.jane)
        self.jane.is_active = False
        self.jane.save()

        url = reverse("staff_invitation", args=[token])
        self.assertEqual(self.client.get(url).status_code, 410)
        self.client.post(url, {"response_type": "ACCEPT_FULL"})
        self.assertFalse(ShiftResponse.objects.exists())

    def test_closed_shift_rejects_new_and_changed_responses(self):
        shift = self.make_shift(self.admin)
        invitation, token = self.make_invitation(shift, self.jane)
        url = reverse("staff_invitation", args=[token])
        self.client.post(url, {"response_type": "DECLINED"})
        shift.close()

        response = self.client.post(url, {"response_type": "ACCEPT_FULL"}, follow=True)
        self.assertContains(response, "no longer accepting responses")
        saved = ShiftResponse.objects.get(invitation=invitation)
        self.assertEqual(saved.response_type, ShiftResponse.Type.DECLINED)

    def test_expired_shift_rejects_responses(self):
        shift = self.make_shift(self.admin, days_from_now=-1)
        invitation, token = self.make_invitation(shift, self.jane)
        url = reverse("staff_invitation", args=[token])

        # The link still shows the shift, but without response buttons.
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["accepting"])

        self.client.post(url, {"response_type": "ACCEPT_FULL"})
        self.assertFalse(ShiftResponse.objects.exists())

    def test_session_route_checks_ownership(self):
        bob = self.make_staff(name="Bob Reyes", badge_id="1002", mobile_phone="+13125550122")
        shift = self.make_shift(self.admin)
        invitation_bob, _ = self.make_invitation(shift, bob)

        self.staff_login(self.jane)
        url = reverse("staff_invitation_detail", args=[invitation_bob.pk])
        self.client.post(url, {"response_type": "ACCEPT_FULL"})
        self.assertFalse(ShiftResponse.objects.exists())


class AcceptanceNotificationTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin(notification_phone="+13125550100")
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.shift = self.make_shift(self.admin, department="MED_SURG")
        self.invitation, self.token = self.make_invitation(self.shift, self.jane)
        self.url = reverse("staff_invitation", args=[self.token])

    @patch("portal.sms.deliver", return_value=("SM-notify", ""))
    def test_full_acceptance_notifies_shift_creator(self, mock_deliver):
        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        mock_deliver.assert_called_once()
        to, body = mock_deliver.call_args[0]
        self.assertEqual(to, "+13125550100")
        self.assertIn("Jane Smith can work Med/Surg Night Shift", body)
        log = SmsLog.objects.get(
            message_type=SmsLog.MessageType.ADMIN_ACCEPTANCE_NOTIFICATION
        )
        self.assertEqual(log.admin_user, self.admin)
        self.assertEqual(log.result, SmsLog.Result.SENT_TO_TWILIO)

    @patch("portal.sms.deliver", return_value=("SM-notify", ""))
    def test_partial_acceptance_notification_includes_times(self, mock_deliver):
        self.client.post(
            self.url,
            {
                "response_type": "ACCEPT_PARTIAL",
                "partial_start_time": "23:00",
                "partial_end_time": "03:00",
            },
        )
        _, body = mock_deliver.call_args[0]
        self.assertIn("can work part of Med/Surg Night Shift", body)
        self.assertIn("11:00 PM-3:00 AM", body)

    @patch("portal.sms.deliver", return_value=("SM-notify", ""))
    def test_decline_sends_no_notification(self, mock_deliver):
        self.client.post(self.url, {"response_type": "DECLINED"})
        mock_deliver.assert_not_called()
        self.assertFalse(
            SmsLog.objects.filter(
                message_type=SmsLog.MessageType.ADMIN_ACCEPTANCE_NOTIFICATION
            ).exists()
        )

    @patch("portal.sms.deliver", return_value=("SM-notify", ""))
    def test_identical_resubmission_does_not_renotify(self, mock_deliver):
        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        self.assertEqual(mock_deliver.call_count, 1)

    @patch("portal.sms.deliver", return_value=("SM-notify", ""))
    def test_material_change_renotifies(self, mock_deliver):
        self.client.post(self.url, {"response_type": "DECLINED"})
        self.assertEqual(mock_deliver.call_count, 0)
        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        self.assertEqual(mock_deliver.call_count, 1)
        self.client.post(
            self.url,
            {
                "response_type": "ACCEPT_PARTIAL",
                "partial_start_time": "23:00",
                "partial_end_time": "03:00",
            },
        )
        self.assertEqual(mock_deliver.call_count, 2)
        # Changing back to declined is silent.
        self.client.post(self.url, {"response_type": "DECLINED"})
        self.assertEqual(mock_deliver.call_count, 2)

    @patch("portal.sms.deliver", return_value=("SM-notify", ""))
    def test_no_notification_phone_still_saves_response(self, mock_deliver):
        profile = self.admin.portal_profile
        profile.notification_phone = ""
        profile.save()
        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        self.assertTrue(ShiftResponse.objects.exists())
        mock_deliver.assert_not_called()

    @patch("portal.sms.deliver", side_effect=Exception("twilio exploded"))
    def test_notification_failure_still_saves_response(self, mock_deliver):
        self.client.post(self.url, {"response_type": "ACCEPT_FULL"})
        saved = ShiftResponse.objects.get(invitation=self.invitation)
        self.assertEqual(saved.response_type, ShiftResponse.Type.ACCEPT_FULL)


class ExpirationTests(PortalTestCase):
    def test_open_shifts_expire_after_their_date(self):
        admin = self.make_admin()
        past_shift = self.make_shift(admin, days_from_now=-1)
        future_shift = self.make_shift(admin, days_from_now=1, shift_type="DAY")
        self.client.force_login(admin)

        response = self.client.get(reverse("manage_dashboard"))
        shown = [s.pk for s in response.context["shifts"]]
        self.assertIn(future_shift.pk, shown)
        self.assertNotIn(past_shift.pk, shown)

        past_shift.refresh_from_db()
        self.assertEqual(past_shift.status, Shift.Status.EXPIRED)

        history = self.client.get(reverse("manage_history"))
        self.assertIn(past_shift.pk, [s.pk for s in history.context["page"]])
