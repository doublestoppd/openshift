from django.urls import reverse
from django.utils import timezone

from portal.models import Shift, Staff

from .base import PortalTestCase


class DepartmentPermissionTests(PortalTestCase):
    """An administrator may only manage shifts in their own departments."""

    def setUp(self):
        self.er_admin = self.make_admin(username="er_director", departments=("ER",))
        self.cno = self.make_admin(username="cno", departments=("MED_SURG", "ER"))
        self.medsurg_shift = self.make_shift(self.cno, department="MED_SURG")

    def test_cannot_open_other_department_shift_pages(self):
        self.client.force_login(self.er_admin)
        urls = [
            reverse("shift_responses", args=[self.medsurg_shift.pk]),
            reverse("shift_edit", args=[self.medsurg_shift.pk]),
            reverse("shift_recipients", args=[self.medsurg_shift.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_cannot_close_or_send_for_other_department(self):
        self.client.force_login(self.er_admin)
        response = self.client.post(reverse("shift_close", args=[self.medsurg_shift.pk]))
        self.assertEqual(response.status_code, 404)
        self.medsurg_shift.refresh_from_db()
        self.assertEqual(self.medsurg_shift.status, Shift.Status.OPEN)

        staff = self.make_staff()
        response = self.client.post(
            reverse("shift_send", args=[self.medsurg_shift.pk]),
            {"staff_ids": [staff.pk]},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.medsurg_shift.invitations.count(), 0)

    def test_cannot_create_shift_in_unpermitted_department(self):
        self.client.force_login(self.er_admin)
        response = self.client.post(
            reverse("shift_create"),
            {
                "department": "MED_SURG",
                "shift_date": timezone.localdate().isoformat(),
                "shift_type": "DAY",
                "roles": ["RN"],
            },
        )
        self.assertEqual(response.status_code, 200)  # form re-rendered with errors
        self.assertEqual(Shift.objects.filter(department="MED_SURG").count(), 1)

    def test_department_form_choices_are_limited(self):
        self.client.force_login(self.er_admin)
        response = self.client.get(reverse("shift_create"))
        form = response.context["form"]
        self.assertEqual([c[0] for c in form.fields["department"].choices], ["ER"])

    def test_dashboard_lists_only_permitted_departments(self):
        er_shift = self.make_shift(self.er_admin, department="ER")
        self.client.force_login(self.er_admin)
        response = self.client.get(reverse("manage_dashboard"))
        shifts = list(response.context["shifts"])
        self.assertIn(er_shift, shifts)
        self.assertNotIn(self.medsurg_shift, shifts)

    def test_manage_pages_require_admin_profile(self):
        from django.contrib.auth import get_user_model

        plain = get_user_model().objects.create_user(username="nobody", password="x-1234567890")
        self.client.force_login(plain)
        response = self.client.get(reverse("manage_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("manage_login"), response.url)


class StaffDirectoryTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin()
        self.client.force_login(self.admin)

    def test_badge_ids_are_unique(self):
        self.make_staff(badge_id="0042")
        response = self.client.post(
            reverse("staff_create"),
            {
                "name": "Other Person",
                "badge_id": "0042",
                "mobile_phone": "312-555-0199",
                "role": "CNA",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(Staff.objects.filter(badge_id="0042").count(), 1)

    def test_badge_id_preserves_leading_zeros(self):
        self.client.post(
            reverse("staff_create"),
            {
                "name": "Zero Lead",
                "badge_id": "00107",
                "mobile_phone": "(312) 555-0142",
                "role": "RN",
                "is_active": "on",
            },
        )
        person = Staff.objects.get(name="Zero Lead")
        self.assertEqual(person.badge_id, "00107")
        self.assertEqual(person.mobile_phone, "+13125550142")  # normalized to E.164
