from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Department(models.TextChoices):
    ER = "ER", "ER"
    MED_SURG = "MED_SURG", "Med/Surg"


class Role(models.TextChoices):
    RN = "RN", "RN"
    LPN = "LPN", "LPN"
    CNA = "CNA", "CNA"


class ShiftType(models.TextChoices):
    DAY = "DAY", "Day Shift"
    NIGHT = "NIGHT", "Night Shift"


class AdminProfile(models.Model):
    """Portal administrator (e.g. CNO, ER Director) on top of Django auth."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portal_profile"
    )
    # SMS acceptance notifications for shifts this administrator created go
    # here. Blank means: save responses normally, send no notification.
    notification_phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.user.get_username()

    @property
    def allowed_departments(self):
        return list(self.department_access.values_list("department", flat=True))

    def can_manage(self, department):
        return self.department_access.filter(department=department).exists()


class AdminDepartmentAccess(models.Model):
    profile = models.ForeignKey(
        AdminProfile, on_delete=models.CASCADE, related_name="department_access"
    )
    department = models.CharField(max_length=16, choices=Department.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "department"], name="unique_admin_department"
            )
        ]

    def __str__(self):
        return f"{self.profile} → {self.get_department_display()}"


class Staff(models.Model):
    name = models.CharField(max_length=100)
    # String so leading zeros survive.
    badge_id = models.CharField(max_length=32, unique=True)
    # Stored in E.164 (+1XXXXXXXXXX) form.
    mobile_phone = models.CharField(max_length=20)
    role = models.CharField(max_length=8, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "staff"

    def __str__(self):
        return f"{self.name} ({self.badge_id})"

    @property
    def phone_last4(self):
        digits = [c for c in self.mobile_phone if c.isdigit()]
        return "".join(digits[-4:])


class StaffGroup(models.Model):
    name = models.CharField(max_length=100, unique=True)
    members = models.ManyToManyField(Staff, related_name="groups", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShiftQuerySet(models.QuerySet):
    def expire_stale(self):
        """Mark OPEN shifts whose calendar date has passed as EXPIRED.

        Runs synchronously from the views that list or serve shifts, so no
        scheduler is needed.
        """
        return self.filter(
            status=Shift.Status.OPEN, shift_date__lt=timezone.localdate()
        ).update(status=Shift.Status.EXPIRED, updated_at=timezone.now())


class Shift(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        EXPIRED = "EXPIRED", "Expired"

    department = models.CharField(max_length=16, choices=Department.choices)
    shift_date = models.DateField()
    shift_type = models.CharField(max_length=8, choices=ShiftType.choices)
    incentive_amount = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    objects = ShiftQuerySet.as_manager()

    class Meta:
        ordering = ["shift_date", "department", "shift_type"]

    def __str__(self):
        return f"{self.get_department_display()} {self.get_shift_type_display()} {self.shift_date}"

    @property
    def is_past_date(self):
        return self.shift_date < timezone.localdate()

    @property
    def is_open_for_responses(self):
        """Authoritative check; does not trust a possibly stale stored status."""
        return self.status == self.Status.OPEN and not self.is_past_date

    @property
    def effective_status(self):
        if self.status == self.Status.OPEN and self.is_past_date:
            return self.Status.EXPIRED
        return self.Status(self.status)

    def close(self):
        self.status = self.Status.CLOSED
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_at", "updated_at"])

    @property
    def roles_list(self):
        # Canonical clinical order (RN, LPN, CNA) regardless of insertion order,
        # so displays read consistently everywhere.
        order = {role: i for i, role in enumerate(Role.values)}
        return sorted(
            (r.role for r in self.requested_roles.all()),
            key=lambda role: order.get(role, len(order)),
        )

    @property
    def roles_display(self):
        return " / ".join(self.roles_list)

    def set_roles(self, roles):
        current = set(self.roles_list)
        wanted = set(roles)
        self.requested_roles.filter(role__in=current - wanted).delete()
        for role in wanted - current:
            ShiftRequestedRole.objects.create(shift=self, role=role)

    @property
    def incentive_display(self):
        """'150' or '150.50' — no trailing .00, no currency symbol."""
        if self.incentive_amount is None:
            return ""
        amount = Decimal(self.incentive_amount).normalize()
        text = f"{amount:f}"
        return text[:-2] if text.endswith(".0") else text

    def response_counts(self):
        from django.db.models import Count, Q

        agg = self.invitations.aggregate(
            invited=Count("id"),
            full=Count("id", filter=Q(response__response_type=ShiftResponse.Type.ACCEPT_FULL)),
            partial=Count(
                "id", filter=Q(response__response_type=ShiftResponse.Type.ACCEPT_PARTIAL)
            ),
            declined=Count("id", filter=Q(response__response_type=ShiftResponse.Type.DECLINED)),
        )
        agg["no_response"] = agg["invited"] - agg["full"] - agg["partial"] - agg["declined"]
        return agg


class ShiftRequestedRole(models.Model):
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="requested_roles")
    role = models.CharField(max_length=8, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shift", "role"], name="unique_shift_role")
        ]

    def __str__(self):
        return f"{self.shift_id}: {self.role}"


class Invitation(models.Model):
    """One row per (shift, staff) pair; resends reuse the row."""

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="invitations")
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="invitations")
    # SHA-256 hex digest of the URL token. The raw token is never stored.
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shift", "staff"], name="unique_shift_staff")
        ]

    def __str__(self):
        return f"{self.staff} ← {self.shift}"

    @property
    def current_response(self):
        try:
            return self.response
        except ShiftResponse.DoesNotExist:
            return None


class ShiftResponse(models.Model):
    class Type(models.TextChoices):
        ACCEPT_FULL = "ACCEPT_FULL", "Full shift"
        ACCEPT_PARTIAL = "ACCEPT_PARTIAL", "Partial shift"
        DECLINED = "DECLINED", "Declined"

    invitation = models.OneToOneField(
        Invitation, on_delete=models.CASCADE, related_name="response"
    )
    response_type = models.CharField(max_length=16, choices=Type.choices)
    # Clock times offered by the employee. The shift has no start/end of its
    # own; an end earlier than the start simply means "past midnight".
    partial_start_time = models.TimeField(null=True, blank=True)
    partial_end_time = models.TimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.invitation}: {self.response_type}"

    @property
    def partial_window_display(self):
        if self.partial_start_time and self.partial_end_time:
            fmt = lambda t: t.strftime("%-I:%M %p")  # noqa: E731
            return f"{fmt(self.partial_start_time)}–{fmt(self.partial_end_time)}"
        return ""


class SmsLog(models.Model):
    class MessageType(models.TextChoices):
        SHIFT_INVITATION = "SHIFT_INVITATION", "Shift invitation"
        ADMIN_ACCEPTANCE_NOTIFICATION = (
            "ADMIN_ACCEPTANCE_NOTIFICATION",
            "Admin acceptance notification",
        )

    class Result(models.TextChoices):
        SENT_TO_TWILIO = "SENT_TO_TWILIO", "Sent to Twilio"
        FAILED = "FAILED", "Failed"

    message_type = models.CharField(max_length=40, choices=MessageType.choices)
    shift = models.ForeignKey(
        Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_logs"
    )
    staff = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_logs"
    )
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_logs",
    )
    destination_phone = models.CharField(max_length=20)
    twilio_sid = models.CharField(max_length=64, blank=True)
    result = models.CharField(max_length=16, choices=Result.choices)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.message_type} → {self.masked_phone} [{self.result}]"

    @property
    def masked_phone(self):
        digits = [c for c in self.destination_phone if c.isdigit()]
        last4 = "".join(digits[-4:]) if len(digits) >= 4 else "".join(digits)
        return f"***-***-{last4}"


class RateLimitEvent(models.Model):
    """A single failed login attempt, counted within a rolling window."""

    scope = models.CharField(max_length=32)
    key = models.CharField(max_length=190)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["scope", "key", "created_at"])]

    def __str__(self):
        return f"{self.scope}:{self.key}"
