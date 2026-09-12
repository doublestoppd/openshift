"""The portal identifies itself as Chicot Memorial everywhere users see it."""
from datetime import time

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from portal import sms
from portal.models import ShiftResponse

from .base import PortalTestCase


class BrandingTests(PortalTestCase):
    def test_default_names(self):
        self.assertEqual(settings.PORTAL_NAME, "Chicot Memorial Open Shifts")
        self.assertEqual(settings.PORTAL_ORG_NAME, "Chicot Memorial")

    def test_public_pages_carry_the_brand(self):
        for url in (reverse("staff_login"), reverse("manage_login")):
            response = self.client.get(url)
            self.assertContains(response, "<title>", msg_prefix=url)
            self.assertContains(response, "Chicot Memorial Open Shifts", msg_prefix=url)

    def test_admin_pages_carry_the_brand(self):
        admin = self.make_admin()
        self.client.force_login(admin)
        response = self.client.get(reverse("manage_dashboard"))
        self.assertContains(response, "Chicot Memorial Open Shifts")
        self.assertContains(response, "Open Shifts · Chicot Memorial Open Shifts")

    def test_invitation_page_names_the_hospital(self):
        admin = self.make_admin()
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        shift = self.make_shift(admin)
        _, token = self.make_invitation(shift, jane)
        response = self.client.get(reverse("staff_invitation", args=[token]))
        self.assertContains(response, "Chicot Memorial")

    def test_outgoing_texts_identify_the_sender(self):
        admin = self.make_admin()
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        shift = self.make_shift(admin, department="MED_SURG", shift_type="NIGHT")
        invitation, _ = self.make_invitation(shift, jane)

        body = sms.invitation_body(shift, "https://example.org/i/x/")
        self.assertTrue(body.startswith("Chicot Memorial open shift: Med/Surg Night Shift on "), body)

        full = ShiftResponse(invitation=invitation, response_type=ShiftResponse.Type.ACCEPT_FULL)
        self.assertTrue(
            sms.acceptance_body(full).startswith(
                "Chicot Memorial: Jane Smith can work Med/Surg Night Shift on "
            )
        )
        partial = ShiftResponse(
            invitation=invitation, response_type=ShiftResponse.Type.ACCEPT_PARTIAL,
            partial_start_time=time(23, 0), partial_end_time=time(3, 0),
        )
        self.assertTrue(
            sms.acceptance_body(partial).startswith(
                "Chicot Memorial: Jane Smith can work part of Med/Surg Night Shift on "
            )
        )

    @override_settings(PORTAL_ORG_NAME="Other Hospital")
    def test_organisation_name_is_configurable(self):
        admin = self.make_admin()
        shift = self.make_shift(admin)
        self.assertTrue(sms.invitation_body(shift, "https://x/i/y/").startswith("Other Hospital open shift:"))
