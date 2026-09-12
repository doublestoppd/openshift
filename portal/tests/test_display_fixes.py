"""Regression tests for display fixes found during end-user testing."""
from pathlib import Path

from django.conf import settings

from portal.models import Role

from .base import PortalTestCase


class RoleOrderingTests(PortalTestCase):
    def test_roles_display_in_canonical_order(self):
        admin = self.make_admin()
        # Add roles in a deliberately non-canonical order.
        shift = self.make_shift(admin, roles=["CNA", "RN", "LPN"])
        self.assertEqual(shift.roles_list, ["RN", "LPN", "CNA"])
        self.assertEqual(shift.roles_display, "RN / LPN / CNA")

    def test_partial_role_set_still_canonical(self):
        admin = self.make_admin()
        shift = self.make_shift(admin, roles=["CNA", "RN"])
        self.assertEqual(shift.roles_list, ["RN", "CNA"])

    def test_role_values_cover_all_choices(self):
        self.assertEqual(set(Role.values), {"RN", "LPN", "CNA"})


class HiddenAttributeCssTests(PortalTestCase):
    """The recipient filter hides rows via the `hidden` attribute; the
    stylesheet must force those rows to display:none, otherwise the flex
    .check-row rule keeps them visible."""

    def test_stylesheet_neutralizes_hidden_attribute(self):
        css = (Path(settings.BASE_DIR) / "portal" / "static" / "portal" / "style.css").read_text()
        # A rule targeting [hidden] with display:none !important must exist.
        self.assertRegex(
            css.replace(" ", "").replace("\n", ""),
            r"\[hidden\]\{display:none!important;?\}",
        )
