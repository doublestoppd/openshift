from django.test import override_settings
from django.urls import reverse

from .base import PortalTestCase

GENERIC_FAILURE = "The information entered could not be verified."


class StaffLoginTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin()
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001", mobile_phone="+13125550111")
        self.bob = self.make_staff(name="Bob Reyes", badge_id="1002", mobile_phone="+13125550122")

    def test_rejects_wrong_phone_last4(self):
        response = self.staff_login(self.jane, last4="9999")
        self.assertContains(response, GENERIC_FAILURE)
        self.assertNotIn("staff_id", self.client.session)

    def test_unknown_badge_gets_same_generic_message(self):
        response = self.client.post(
            "/staff/login/", {"badge_id": "does-not-exist", "phone_last4": "0111"}
        )
        self.assertContains(response, GENERIC_FAILURE)
        self.assertNotIn("staff_id", self.client.session)

    def test_successful_login_creates_short_lived_session(self):
        response = self.staff_login(self.jane)
        self.assertRedirects(response, reverse("staff_invitation_list"))
        self.assertEqual(self.client.session["staff_id"], self.jane.pk)

    def test_inactive_staff_cannot_login(self):
        self.jane.is_active = False
        self.jane.save()
        response = self.staff_login(self.jane)
        self.assertContains(response, GENERIC_FAILURE)
        self.assertNotIn("staff_id", self.client.session)

    def test_login_shows_only_own_invitations(self):
        shift_a = self.make_shift(self.admin, days_from_now=2)
        shift_b = self.make_shift(self.admin, days_from_now=4, shift_type="DAY")
        self.make_invitation(shift_a, self.jane)
        self.make_invitation(shift_b, self.bob)

        self.staff_login(self.jane)
        response = self.client.get(reverse("staff_invitation_list"))
        invitations = list(response.context["invitations"])
        self.assertEqual([i.staff_id for i in invitations], [self.jane.pk])

    def test_cannot_open_another_staff_members_invitation(self):
        shift = self.make_shift(self.admin)
        invitation_bob, _ = self.make_invitation(shift, self.bob)

        self.staff_login(self.jane)
        response = self.client.get(
            reverse("staff_invitation_detail", args=[invitation_bob.pk])
        )
        self.assertEqual(response.status_code, 404)

    @override_settings(LOGIN_RATE_LIMIT_MAX_FAILURES=3)
    def test_rate_limit_blocks_repeated_failures(self):
        for _ in range(3):
            self.staff_login(self.jane, last4="0000")
        # Even correct credentials are refused while blocked.
        response = self.staff_login(self.jane)
        self.assertContains(response, "Too many attempts")
        self.assertNotIn("staff_id", self.client.session)


class AdminLoginTests(PortalTestCase):
    def test_login_and_redirect(self):
        from .base import PASSWORD

        self.make_admin(username="cno")
        response = self.client.post(
            reverse("manage_login"), {"username": "cno", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("manage_dashboard"))

    def test_account_without_profile_is_refused(self):
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(username="plain", password="x-1234567890")
        response = self.client.post(
            reverse("manage_login"), {"username": "plain", "password": "x-1234567890"}
        )
        self.assertContains(response, "not set up as a portal administrator")

    @override_settings(LOGIN_RATE_LIMIT_MAX_FAILURES=3)
    def test_admin_login_rate_limited(self):
        from .base import PASSWORD

        self.make_admin(username="cno")
        for _ in range(3):
            self.client.post(
                reverse("manage_login"), {"username": "cno", "password": "wrong"}
            )
        response = self.client.post(
            reverse("manage_login"), {"username": "cno", "password": PASSWORD}
        )
        self.assertContains(response, "Too many failed sign-in attempts")
