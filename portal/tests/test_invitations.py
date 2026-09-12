import re
from unittest.mock import patch

from django.urls import reverse

from portal.models import Invitation, SmsLog

from .base import PortalTestCase


class TokenResolutionTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin()
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.bob = self.make_staff(
            name="Bob Reyes", badge_id="1002", mobile_phone="+13125550122"
        )
        self.shift_a = self.make_shift(self.admin, days_from_now=2, department="MED_SURG")
        self.shift_b = self.make_shift(
            self.admin, days_from_now=5, department="MED_SURG", shift_type="DAY"
        )
        self.inv_jane, self.token_jane = self.make_invitation(self.shift_a, self.jane)
        self.inv_bob, self.token_bob = self.make_invitation(self.shift_b, self.bob)

    def test_token_resolves_only_to_its_own_invitation(self):
        response = self.client.get(reverse("staff_invitation", args=[self.token_jane]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["invitation"].pk, self.inv_jane.pk)
        self.assertEqual(response.context["staff"].pk, self.jane.pk)
        self.assertEqual(response.context["shift"].pk, self.shift_a.pk)

        response = self.client.get(reverse("staff_invitation", args=[self.token_bob]))
        self.assertEqual(response.context["invitation"].pk, self.inv_bob.pk)

    def test_tampered_or_unknown_token_is_404(self):
        tampered = self.token_jane[:-2] + "zz"
        self.assertEqual(
            self.client.get(reverse("staff_invitation", args=[tampered])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("staff_invitation", args=["nonsense"])).status_code, 404
        )

    def test_raw_token_is_not_stored(self):
        self.assertNotEqual(self.inv_jane.token_hash, self.token_jane)
        self.assertEqual(len(self.inv_jane.token_hash), 64)  # SHA-256 hex


class SendingTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin()
        self.client.force_login(self.admin)
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.bob = self.make_staff(
            name="Bob Reyes", badge_id="1002", mobile_phone="+13125550122"
        )
        self.shift = self.make_shift(self.admin)

    def test_group_plus_individual_selection_deduplicates(self):
        group = self.make_group(members=[self.jane, self.bob])
        response = self.client.post(
            reverse("shift_recipients", args=[self.shift.pk]),
            {"group_ids": [group.pk], "staff_ids": [self.jane.pk]},
        )
        recipients = response.context["recipients"]
        self.assertEqual(sorted(p.pk for p in recipients), sorted([self.jane.pk, self.bob.pk]))

    @patch("portal.sms.deliver", return_value=("SM123", ""))
    def test_duplicate_selection_creates_one_invitation_per_staff(self, mock_deliver):
        self.client.post(
            reverse("shift_send", args=[self.shift.pk]),
            {"staff_ids": [self.jane.pk, self.jane.pk, self.bob.pk]},
        )
        self.assertEqual(Invitation.objects.filter(shift=self.shift).count(), 2)
        self.assertEqual(
            Invitation.objects.filter(shift=self.shift, staff=self.jane).count(), 1
        )

    @patch("portal.sms.deliver", return_value=("SM123", ""))
    def test_resend_reuses_invitation_row(self, mock_deliver):
        for _ in range(2):
            self.client.post(
                reverse("shift_send", args=[self.shift.pk]), {"staff_ids": [self.jane.pk]}
            )
        self.assertEqual(
            Invitation.objects.filter(shift=self.shift, staff=self.jane).count(), 1
        )
        self.assertEqual(
            SmsLog.objects.filter(
                shift=self.shift,
                staff=self.jane,
                message_type=SmsLog.MessageType.SHIFT_INVITATION,
            ).count(),
            2,
        )

    @patch("portal.sms.deliver")
    def test_one_failure_does_not_stop_other_sends(self, mock_deliver):
        def deliver_side_effect(to, body):
            if to == self.bob.mobile_phone:
                return "", "Simulated Twilio outage"
            return "SM-ok", ""

        mock_deliver.side_effect = deliver_side_effect
        response = self.client.post(
            reverse("shift_send", args=[self.shift.pk]),
            {"staff_ids": [self.bob.pk, self.jane.pk]},
        )
        self.assertEqual(mock_deliver.call_count, 2)
        self.assertEqual([p.pk for p in response.context["sent"]], [self.jane.pk])
        self.assertEqual([p.pk for p in response.context["failed"]], [self.bob.pk])
        self.assertEqual(
            SmsLog.objects.filter(result=SmsLog.Result.FAILED, staff=self.bob).count(), 1
        )
        jane_invitation = Invitation.objects.get(shift=self.shift, staff=self.jane)
        self.assertIsNotNone(jane_invitation.last_sent_at)
        bob_invitation = Invitation.objects.get(shift=self.shift, staff=self.bob)
        self.assertIsNone(bob_invitation.last_sent_at)

    @patch("portal.sms.deliver", return_value=("SM123", ""))
    def test_inactive_staff_are_never_sent(self, mock_deliver):
        self.bob.is_active = False
        self.bob.save()
        self.client.post(
            reverse("shift_send", args=[self.shift.pk]),
            {"staff_ids": [self.jane.pk, self.bob.pk]},
        )
        self.assertEqual(Invitation.objects.filter(shift=self.shift).count(), 1)
        self.assertEqual(mock_deliver.call_count, 1)

    @patch("portal.sms.deliver", return_value=("SM123", ""))
    def test_sms_message_contains_link_and_details_and_no_pii(self, mock_deliver):
        shift = self.make_shift(
            self.admin, days_from_now=4, incentive_amount="150.00", department="MED_SURG"
        )
        self.client.post(
            reverse("shift_send", args=[shift.pk]), {"staff_ids": [self.jane.pk]}
        )
        to, body = mock_deliver.call_args[0]
        self.assertEqual(to, self.jane.mobile_phone)
        self.assertIn("Med/Surg Night Shift", body)
        self.assertIn("$150 bonus", body)
        match = re.search(r"/i/([A-Za-z0-9_\-]+)", body)
        self.assertIsNotNone(match, body)
        token = match.group(1)
        # The URL must not leak employee information.
        self.assertNotIn(self.jane.badge_id, token)
        self.assertNotIn("Jane", body.split("/i/")[1])
        # The link actually works.
        page = self.client.get(reverse("staff_invitation", args=[token]))
        self.assertEqual(page.status_code, 200)

    @patch("portal.sms.deliver", return_value=("SM123", ""))
    def test_no_incentive_means_no_bonus_text(self, mock_deliver):
        self.client.post(
            reverse("shift_send", args=[self.shift.pk]), {"staff_ids": [self.jane.pk]}
        )
        _, body = mock_deliver.call_args[0]
        self.assertNotIn("bonus", body)
        self.assertNotIn("$", body)

    def test_cannot_send_for_closed_shift(self):
        self.shift.close()
        response = self.client.post(
            reverse("shift_send", args=[self.shift.pk]), {"staff_ids": [self.jane.pk]}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invitation.objects.count(), 0)
