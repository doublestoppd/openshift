"""Tests for the simplified admin forms and dashboard wording."""
from django import forms
from django.urls import reverse

from portal.forms import ShiftForm, StaffGroupForm
from portal.models import Invitation, ShiftResponse

from .base import PortalTestCase


class ShiftFormDepartmentTests(PortalTestCase):
    def test_single_department_is_hidden_and_preset(self):
        form = ShiftForm(allowed_departments=["ER"])
        self.assertEqual(form.single_department, "ER")
        self.assertIsInstance(form.fields["department"].widget, forms.HiddenInput)
        self.assertEqual(form.fields["department"].initial, "ER")
        # Still validates: only ER is acceptable.
        self.assertEqual([c[0] for c in form.fields["department"].choices], ["ER"])

    def test_multiple_departments_require_an_explicit_choice(self):
        form = ShiftForm(allowed_departments=["ER", "MED_SURG"])
        self.assertIsNone(form.single_department)
        self.assertEqual(form.fields["department"].choices[0][0], "")
        self.assertEqual(
            [c[0] for c in form.fields["department"].choices[1:]], ["ER", "MED_SURG"]
        )

    def test_create_page_shows_department_as_text_for_single_department_admin(self):
        er_admin = self.make_admin(username="er_director", departments=("ER",))
        self.client.force_login(er_admin)
        response = self.client.get(reverse("shift_create"))
        self.assertContains(response, 'type="hidden" name="department" value="ER"')
        self.assertContains(response, 'class="static-value">ER<')
        self.assertNotContains(response, "<select")

    def test_labels_have_no_trailing_colon(self):
        admin = self.make_admin()
        self.client.force_login(admin)
        response = self.client.get(reverse("shift_create"))
        self.assertNotContains(response, "Date:")
        self.assertContains(response, ">Date<")


class GroupFormPickerTests(PortalTestCase):
    def test_selected_members_from_instance(self):
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        group = self.make_group(members=[jane])
        form = StaffGroupForm(instance=group)
        self.assertEqual(form.selected_member_ids, {jane.pk})

    def test_selected_members_preserved_from_submitted_data(self):
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        form = StaffGroupForm({"name": "", "members": [str(jane.pk)]})
        self.assertFalse(form.is_valid())  # name required -> re-render
        self.assertEqual(form.selected_member_ids, {jane.pk})

    def test_group_form_renders_searchable_picker(self):
        admin = self.make_admin()
        self.make_staff(name="Jane Smith", badge_id="1001")
        self.client.force_login(admin)
        response = self.client.get(reverse("group_create"))
        self.assertContains(response, 'class="picker-filter"')
        self.assertContains(response, 'name="members"')
        self.assertContains(response, "Jane Smith")


class StaffFormTests(PortalTestCase):
    def test_no_groups_shows_empty_state_instead_of_empty_box(self):
        admin = self.make_admin()
        self.client.force_login(admin)
        response = self.client.get(reverse("staff_create"))
        self.assertContains(response, "No groups yet")

    def test_groups_render_as_choices_when_present(self):
        admin = self.make_admin()
        self.make_group(name="Night Staff")
        self.client.force_login(admin)
        response = self.client.get(reverse("staff_create"))
        self.assertContains(response, "Night Staff")
        self.assertContains(response, 'name="groups"')


class DashboardWordingTests(PortalTestCase):
    def test_card_says_no_replies_yet_until_someone_responds(self):
        admin = self.make_admin()
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        shift = self.make_shift(admin)
        invitation, _ = self.make_invitation(shift, jane)
        self.client.force_login(admin)

        response = self.client.get(reverse("manage_dashboard"))
        self.assertContains(response, "1 invited · no replies yet")
        self.assertNotContains(response, "0 full")

        ShiftResponse.objects.create(
            invitation=invitation, response_type=ShiftResponse.Type.DECLINED
        )
        response = self.client.get(reverse("manage_dashboard"))
        self.assertContains(response, "1 declined")
        self.assertNotContains(response, "no replies yet")

    def test_card_before_any_send(self):
        admin = self.make_admin()
        self.make_shift(admin)
        self.client.force_login(admin)
        response = self.client.get(reverse("manage_dashboard"))
        self.assertContains(response, "No invitations sent yet")
        self.assertEqual(Invitation.objects.count(), 0)
