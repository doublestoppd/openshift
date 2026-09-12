from django import forms
from django.utils import timezone

from .models import AdminProfile, Department, Role, Shift, ShiftType, Staff, StaffGroup
from .phones import normalize_phone


class StaffLoginForm(forms.Form):
    badge_id = forms.CharField(
        label="Badge ID",
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "off", "autofocus": True}),
    )
    phone_last4 = forms.CharField(
        label="Last 4 digits of your mobile number",
        min_length=4,
        max_length=4,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )

    def clean_phone_last4(self):
        value = self.cleaned_data["phone_last4"]
        if not value.isdigit():
            raise forms.ValidationError("Enter 4 digits.")
        return value


class PartialTimesForm(forms.Form):
    partial_start_time = forms.TimeField(
        label="Available from", widget=forms.TimeInput(attrs={"type": "time"})
    )
    partial_end_time = forms.TimeField(
        label="Available until", widget=forms.TimeInput(attrs={"type": "time"})
    )


class StaffForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=StaffGroup.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Group memberships",
    )

    class Meta:
        model = Staff
        fields = ["name", "badge_id", "mobile_phone", "role", "is_active"]
        labels = {"badge_id": "Badge ID", "mobile_phone": "Mobile phone", "is_active": "Active"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["groups"].initial = self.instance.groups.all()

    def clean_mobile_phone(self):
        return normalize_phone(self.cleaned_data["mobile_phone"])

    def clean_badge_id(self):
        return self.cleaned_data["badge_id"].strip()

    def save(self, commit=True):
        staff = super().save(commit=commit)
        if commit:
            staff.groups.set(self.cleaned_data["groups"])
        return staff


class StaffGroupForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(
        queryset=Staff.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = StaffGroup
        fields = ["name", "members"]


class ShiftForm(forms.ModelForm):
    roles = forms.MultipleChoiceField(
        choices=Role.choices,
        widget=forms.CheckboxSelectMultiple,
        label="Requested roles",
    )

    class Meta:
        model = Shift
        fields = ["department", "shift_date", "shift_type", "incentive_amount", "note"]
        widgets = {
            "shift_date": forms.DateInput(attrs={"type": "date"}),
            "shift_type": forms.RadioSelect,
            "note": forms.TextInput(
                attrs={"placeholder": "Staffing note only. Do not enter patient information."}
            ),
        }
        labels = {
            "shift_date": "Date",
            "shift_type": "Shift",
            "incentive_amount": "Incentive bonus (US $, optional)",
            "note": "Note (optional)",
        }
        help_texts = {
            "note": "Staffing note only. Do not enter patient information.",
        }

    def __init__(self, *args, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Server-side department authorization: the form only ever accepts
        # departments the logged-in administrator may manage.
        allowed = allowed_departments or []
        self.fields["department"].choices = [
            (value, label) for value, label in Department.choices if value in allowed
        ]
        self.fields["shift_type"].choices = ShiftType.choices  # drop empty choice
        if self.instance.pk:
            self.fields["roles"].initial = self.instance.roles_list

    def clean_incentive_amount(self):
        amount = self.cleaned_data.get("incentive_amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Enter a positive amount, or leave blank for none.")
        return amount

    def clean_shift_date(self):
        value = self.cleaned_data["shift_date"]
        if value < timezone.localdate():
            raise forms.ValidationError("The shift date cannot be in the past.")
        return value

    def save(self, commit=True):
        shift = super().save(commit=commit)
        if commit:
            shift.set_roles(self.cleaned_data["roles"])
        return shift


class AccountForm(forms.ModelForm):
    class Meta:
        model = AdminProfile
        fields = ["notification_phone"]
        labels = {"notification_phone": "SMS notification mobile number"}
        help_texts = {
            "notification_phone": (
                "Acceptance alerts for shifts you create are texted here. "
                "Leave blank to receive no texts."
            )
        }

    def clean_notification_phone(self):
        value = self.cleaned_data["notification_phone"].strip()
        if not value:
            return ""
        return normalize_phone(value)
