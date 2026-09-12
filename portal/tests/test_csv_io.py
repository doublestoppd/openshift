"""Tests for CSV import/export of staff and groups."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from portal import csv_io
from portal.models import Staff, StaffGroup

from .base import PortalTestCase

STAFF_CSV = (
    "Name,Badge ID,Mobile Phone,Role,Active,Groups\n"
    "Jane Smith,0007,(312) 555-0111,rn,yes,ER RNs; Night Staff\n"
    "Bob Reyes,1002,312-555-0122,LPN,no,\n"
)


class StaffCsvParseTests(PortalTestCase):
    def test_parses_and_normalizes_values(self):
        rows, errors = csv_io.parse_staff_csv(STAFF_CSV)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["badge_id"], "0007")  # leading zero kept
        self.assertEqual(rows[0]["mobile_phone"], "+13125550111")
        self.assertEqual(rows[0]["role"], "RN")
        self.assertTrue(rows[0]["is_active"])
        self.assertEqual(rows[0]["groups"], ["ER RNs", "Night Staff"])
        self.assertFalse(rows[1]["is_active"])
        self.assertEqual(rows[1]["groups"], [])

    def test_loose_headers_and_semicolon_delimiter(self):
        text = "Employee;Badge;Cell;Title\nAna Diaz;1003;3125550133;CNA\n"
        rows, errors = csv_io.parse_staff_csv(text)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["name"], "Ana Diaz")
        self.assertIsNone(rows[0]["groups"])  # no Groups column -> untouched

    def test_missing_columns_reported(self):
        rows, errors = csv_io.parse_staff_csv("Name,Badge ID\nJane,1\n")
        self.assertEqual(rows, [])
        self.assertIn("Missing required column(s): Mobile Phone, Role", errors[0])

    def test_row_errors_are_numbered_and_nothing_is_returned_as_valid(self):
        text = (
            "Name,Badge ID,Mobile Phone,Role,Active\n"
            "Jane Smith,1001,555,RN,yes\n"
            "Bob Reyes,1001,3125550122,XX,maybe\n"
            ",,,,\n"
        )
        rows, errors = csv_io.parse_staff_csv(text)
        joined = "\n".join(errors)
        self.assertIn("Row 2: Enter a valid 10-digit US mobile number", joined)
        self.assertIn("Row 3: Badge ID 1001 also appears on row 2", joined)
        self.assertIn("Row 3: Role must be RN, LPN or CNA", joined)
        self.assertIn("Row 3: Active must be yes or no", joined)
        self.assertEqual(len(rows), 2)  # blank row skipped

    def test_empty_file(self):
        rows, errors = csv_io.parse_staff_csv("Name,Badge ID,Mobile Phone,Role\n")
        self.assertIn("no staff rows", errors[0])


class StaffCsvApplyTests(PortalTestCase):
    def test_creates_updates_and_syncs_groups(self):
        existing = self.make_staff(
            name="Old Name", badge_id="0007", mobile_phone="+13125550999", role="CNA"
        )
        self.make_group(name="Night Staff", members=[existing])
        self.make_group(name="Weekend", members=[existing])
        rows, _ = csv_io.parse_staff_csv(STAFF_CSV)
        result = csv_io.apply_staff_rows(rows)
        self.assertEqual(result, {"created": 1, "updated": 1, "groups_created": 1})

        existing.refresh_from_db()
        self.assertEqual(existing.name, "Jane Smith")
        self.assertEqual(existing.mobile_phone, "+13125550111")
        self.assertEqual(existing.role, "RN")
        self.assertEqual(
            set(existing.groups.values_list("name", flat=True)), {"ER RNs", "Night Staff"}
        )
        bob = Staff.objects.get(badge_id="1002")
        self.assertFalse(bob.is_active)
        self.assertEqual(bob.groups.count(), 0)
        self.assertEqual(Staff.objects.count(), 2)

    def test_no_groups_column_leaves_memberships_alone(self):
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.make_group(name="Night Staff", members=[jane])
        rows, _ = csv_io.parse_staff_csv("Name,Badge ID,Phone,Role\nJane S.,1001,3125550111,RN\n")
        csv_io.apply_staff_rows(rows)
        jane.refresh_from_db()
        self.assertEqual(jane.name, "Jane S.")
        self.assertEqual(list(jane.groups.values_list("name", flat=True)), ["Night Staff"])

    def test_preview_counts(self):
        self.make_staff(name="Jane Smith", badge_id="0007")
        self.make_group(name="Night Staff")
        rows, _ = csv_io.parse_staff_csv(STAFF_CSV)
        preview = csv_io.preview_staff_rows(rows)
        self.assertEqual((preview["new"], preview["updated"]), (1, 1))
        self.assertEqual(preview["new_groups"], ["ER RNs"])


class GroupCsvTests(PortalTestCase):
    def setUp(self):
        self.jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.bob = self.make_staff(name="Bob Reyes", badge_id="1002", mobile_phone="+13125550122")

    def test_parse_and_apply(self):
        text = (
            "Group,Badge ID,Name\n"
            "ER RNs,1001,Jane\n"
            "er rns,1002,Bob\n"
            "Weekend Staff,,\n"
        )
        mapping, errors = csv_io.parse_groups_csv(text)
        self.assertEqual(errors, [])
        self.assertEqual(list(mapping), ["ER RNs", "Weekend Staff"])  # case-insensitive merge
        self.assertEqual(mapping["ER RNs"], ["1001", "1002"])

        old = self.make_group(name="Weekend Staff", members=[self.jane])
        result = csv_io.apply_group_rows(mapping)
        self.assertEqual(result, {"created": 1, "updated": 1, "memberships": 2})
        self.assertEqual(
            set(StaffGroup.objects.get(name="ER RNs").members.all()), {self.jane, self.bob}
        )
        old.refresh_from_db()
        self.assertEqual(old.members.count(), 0)  # membership replaced by the file

    def test_unknown_badge_is_an_error(self):
        mapping, errors = csv_io.parse_groups_csv("Group,Badge ID\nER RNs,9999\n")
        self.assertIn("No staff member has badge ID 9999", errors[0])

    def test_missing_group_column(self):
        mapping, errors = csv_io.parse_groups_csv("Badge ID\n1001\n")
        self.assertIn("Missing required column: Group", errors[0])


class ExportTests(PortalTestCase):
    def test_staff_export_round_trips(self):
        jane = self.make_staff(name="Jane Smith", badge_id="0007", mobile_phone="+13125550111")
        self.make_group(name="Night Staff", members=[jane])
        text = csv_io.export_staff_csv()
        self.assertTrue(text.startswith("﻿"))
        self.assertIn("Name,Badge ID,Mobile Phone,Role,Active,Groups", text)
        self.assertIn("Jane Smith,0007,(312) 555-0111,RN,yes,Night Staff", text)

        rows, errors = csv_io.parse_staff_csv(text)
        self.assertEqual(errors, [])
        csv_io.apply_staff_rows(rows)
        self.assertEqual(Staff.objects.count(), 1)
        jane.refresh_from_db()
        self.assertEqual(jane.mobile_phone, "+13125550111")

    def test_formula_like_values_are_escaped_and_unescaped(self):
        self.make_staff(name="=SUM(A1)", badge_id="-1")
        text = csv_io.export_staff_csv()
        self.assertIn("'=SUM(A1)", text)
        self.assertIn("'-1", text)
        rows, _ = csv_io.parse_staff_csv(text)
        self.assertEqual(rows[0]["name"], "=SUM(A1)")
        self.assertEqual(rows[0]["badge_id"], "-1")

    def test_groups_export(self):
        jane = self.make_staff(name="Jane Smith", badge_id="1001")
        self.make_group(name="ER RNs", members=[jane])
        self.make_group(name="Empty Group")
        text = csv_io.export_groups_csv()
        self.assertIn("Group,Badge ID,Name,Role", text)
        self.assertIn("ER RNs,1001,Jane Smith,RN", text)
        self.assertIn("Empty Group,,,", text)


class CsvViewTests(PortalTestCase):
    def setUp(self):
        self.admin = self.make_admin()

    def upload(self, url, content, name="staff.csv"):
        return self.client.post(url, {"file": SimpleUploadedFile(name, content)})

    def test_export_requires_admin(self):
        response = self.client.get(reverse("staff_export"))
        self.assertEqual(response.status_code, 302)

    def test_export_is_a_csv_attachment(self):
        self.make_staff(name="Jane Smith", badge_id="1001")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_export"))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('attachment; filename="staff-', response["Content-Disposition"])
        self.assertIn("Jane Smith", response.content.decode("utf-8-sig"))
        response = self.client.get(reverse("group_export"))
        self.assertIn('filename="groups-', response["Content-Disposition"])

    def test_import_page_explains_columns(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_import"))
        self.assertContains(response, "Expected columns")
        self.assertContains(response, "Badge ID")
        self.assertContains(response, reverse("staff_export"))

    def test_upload_previews_then_confirm_applies(self):
        self.client.force_login(self.admin)
        response = self.upload(reverse("staff_import"), STAFF_CSV.encode())
        self.assertContains(response, "Ready to import")
        self.assertContains(response, "<strong>2</strong> new staff")
        self.assertContains(response, 'name="csv_text"')
        self.assertEqual(Staff.objects.count(), 0)  # nothing written yet

        response = self.client.post(
            reverse("staff_import"), {"confirm": "1", "csv_text": STAFF_CSV}, follow=True
        )
        self.assertRedirects(response, reverse("staff_list"))
        self.assertContains(response, "Imported 2 new staff")
        self.assertEqual(Staff.objects.count(), 2)
        self.assertEqual(StaffGroup.objects.count(), 2)

    def test_bad_file_shows_errors_and_writes_nothing(self):
        self.client.force_login(self.admin)
        bad = b"Name,Badge ID,Mobile Phone,Role\nJane,1001,not-a-phone,RN\n"
        response = self.upload(reverse("staff_import"), bad)
        self.assertContains(response, "Nothing was imported")
        self.assertContains(response, "Row 2:")
        self.assertEqual(Staff.objects.count(), 0)

    def test_windows_1252_file_decodes(self):
        self.client.force_login(self.admin)
        content = "Name,Badge ID,Mobile Phone,Role\nJosé Núñez,1001,3125550111,RN\n".encode("cp1252")
        response = self.upload(reverse("staff_import"), content)
        self.assertContains(response, "José Núñez")

    def test_oversized_upload_rejected(self):
        self.client.force_login(self.admin)
        big = b"Name,Badge ID,Mobile Phone,Role\n" + b"x" * (csv_io.MAX_UPLOAD_BYTES + 1)
        response = self.upload(reverse("staff_import"), big)
        self.assertContains(response, "larger than 2 MB")
        self.assertEqual(Staff.objects.count(), 0)

    def test_group_import_flow(self):
        self.make_staff(name="Jane Smith", badge_id="1001")
        self.client.force_login(self.admin)
        text = "Group,Badge ID\nER RNs,1001\n"
        response = self.upload(reverse("group_import"), text.encode(), name="groups.csv")
        self.assertContains(response, "Ready to import")
        response = self.client.post(
            reverse("group_import"), {"confirm": "1", "csv_text": text}, follow=True
        )
        self.assertRedirects(response, reverse("group_list"))
        self.assertEqual(StaffGroup.objects.get(name="ER RNs").members.count(), 1)
