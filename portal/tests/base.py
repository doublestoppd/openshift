from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from portal.models import (
    AdminDepartmentAccess,
    AdminProfile,
    Invitation,
    Shift,
    Staff,
    StaffGroup,
)
from portal.tokens import generate_token, hash_token

PASSWORD = "portal-test-passw0rd"


class PortalTestCase(TestCase):
    def make_admin(
        self,
        username="cno",
        departments=("MED_SURG",),
        notification_phone="+13125550100",
    ):
        user = get_user_model().objects.create_user(username=username, password=PASSWORD)
        profile = AdminProfile.objects.create(
            user=user, notification_phone=notification_phone
        )
        for department in departments:
            AdminDepartmentAccess.objects.create(profile=profile, department=department)
        return user

    def make_staff(
        self,
        name="Jane Smith",
        badge_id="1001",
        mobile_phone="+13125550111",
        role="RN",
        is_active=True,
    ):
        return Staff.objects.create(
            name=name,
            badge_id=badge_id,
            mobile_phone=mobile_phone,
            role=role,
            is_active=is_active,
        )

    def make_shift(
        self,
        created_by,
        department="MED_SURG",
        days_from_now=3,
        shift_type="NIGHT",
        roles=("RN", "LPN"),
        **kwargs,
    ):
        shift = Shift.objects.create(
            department=department,
            shift_date=timezone.localdate() + timedelta(days=days_from_now),
            shift_type=shift_type,
            created_by=created_by,
            **kwargs,
        )
        shift.set_roles(roles)
        return shift

    def make_invitation(self, shift, staff):
        """Returns (invitation, raw_token)."""
        raw = generate_token()
        invitation = Invitation.objects.create(
            shift=shift, staff=staff, token_hash=hash_token(raw)
        )
        return invitation, raw

    def make_group(self, name="ER RNs", members=()):
        group = StaffGroup.objects.create(name=name)
        group.members.set(members)
        return group

    def staff_login(self, staff, last4=None):
        return self.client.post(
            "/staff/login/",
            {"badge_id": staff.badge_id, "phone_last4": last4 or staff.phone_last4},
        )
