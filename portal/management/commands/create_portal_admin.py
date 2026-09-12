"""Create or update a portal administrator account.

Examples:
    PORTAL_ADMIN_PASSWORD='...' python manage.py create_portal_admin cno \
        --departments MED_SURG ER --notification-phone "312-555-0147" --name "Chris Ortiz"

    python manage.py create_portal_admin er_director --departments ER
    (prompts for a password when PORTAL_ADMIN_PASSWORD is not set)
"""

import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import AdminDepartmentAccess, AdminProfile, Department
from portal.phones import normalize_phone


class Command(BaseCommand):
    help = "Create or update a portal administrator (user + profile + department access)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--departments",
            nargs="+",
            required=True,
            choices=[value for value, _ in Department.choices],
            help="Departments this administrator may manage (ER, MED_SURG).",
        )
        parser.add_argument(
            "--notification-phone",
            default="",
            help="Mobile number for SMS acceptance notifications (optional).",
        )
        parser.add_argument("--name", default="", help="Display name (optional).")
        parser.add_argument(
            "--password-env",
            default="PORTAL_ADMIN_PASSWORD",
            help="Environment variable to read the password from "
            "(default: PORTAL_ADMIN_PASSWORD). Prompts if unset.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]

        password = os.environ.get(options["password_env"], "")
        user = User.objects.filter(username=username).first()
        if not password and user is None:
            password = getpass.getpass("Password for new administrator: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise CommandError("Passwords did not match.")

        phone = options["notification_phone"].strip()
        if phone:
            try:
                phone = normalize_phone(phone)
            except ValidationError as exc:
                raise CommandError(f"Invalid notification phone: {'; '.join(exc.messages)}")

        created = user is None
        if created:
            user = User(username=username)
        if options["name"]:
            parts = options["name"].split(None, 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ""
        if password:
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                raise CommandError("Weak password: " + "; ".join(exc.messages))
            user.set_password(password)
        user.save()

        profile, _ = AdminProfile.objects.get_or_create(user=user)
        if phone:
            profile.notification_phone = phone
            profile.save(update_fields=["notification_phone"])

        wanted = set(options["departments"])
        profile.department_access.exclude(department__in=wanted).delete()
        for department in wanted:
            AdminDepartmentAccess.objects.get_or_create(
                profile=profile, department=department
            )

        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} administrator '{username}' "
                f"(departments: {', '.join(sorted(wanted))}; "
                f"notification phone: {profile.notification_phone or 'none'})"
            )
        )
        if not created and not password:
            self.stdout.write(
                "Password unchanged (set the password environment variable to change it)."
            )
