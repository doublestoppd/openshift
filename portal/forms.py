from django import forms
from django.utils import timezone

from .models import AdminProfile, Department, Role, Shift, ShiftType, Staff, StaffGroup
from .phones import format_us_phone, normalize_phone


class PortalForm(forms.Form):
    """Labels without the trailing colon Django adds by default."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class PortalModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class StaffLoginForm(PortalForm):
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


class PartialTimesForm(PortalForm):
    partial_start_time = forms.TimeField(
        label="Available from", widget=forms.TimeInput(attrs={"type": "time"})
    )
    partial_end_time = forms.TimeField(
        label="Available until", widget=forms.TimeInput(attrs={"type": "time"})
    )


class StaffForm(PortalModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=StaffGroup.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Groups",
    )

    class Meta:
        model = Staff
        fields = ["name", "badge_id", "mobile_phone", "role", "is_active"]
        labels = {"badge_id": "Badge ID", "mobile_phone": "Mobile phone", "is_active": "Active"}
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "off"}),
            "badge_id": forms.TextInput(attrs={"autocomplete": "off"}),
            "mobile_phone": forms.TextInput(
                attrs={"placeholder": "(312) 555-0147", "inputmode": "tel", "autocomplete": "off"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [("", "Choose a role…")] + list(Role.choices)
        if self.instance.pk:
            self.fields["groups"].initial = self.instance.groups.all()
            if not self.is_bound:
                self.initial["mobile_phone"] = format_us_phone(self.instance.mobile_phone)

    def clean_mobile_phone(self):
        return normalize_phone(self.cleaned_data["mobile_phone"])

    def clean_badge_id(self):
        return self.cleaned_data["badge_id"].strip()

    def save(self, commit=True):
        staff = super().save(commit=commit)
        if commit:
            staff.groups.set(self.cleaned_data["groups"])
        return staff


class StaffGroupForm(PortalModelForm):
    members = forms.ModelMultipleChoiceField(
        queryset=Staff.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = StaffGroup
        fields = ["name", "members"]
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": "e.g. ER RNs, Weekend Staff", "autocomplete": "off"}
            )
        }

    @property
    def staff_choices(self):
        """Everyone in the directory, for the searchable member picker."""
        return Staff.objects.order_by("name")

    @property
    def selected_member_ids(self):
        """Member ids to show as checked: the submitted values when re-rendering
        after a validation error, otherwise the group's current members."""
        if self.is_bound:
            data = self.data
            raw = data.getlist("members") if hasattr(data, "getlist") else data.get("members", [])
            return {int(v) for v in raw if str(v).isdigit()}
        if self.instance.pk:
            return set(self.instance.members.values_list("pk", flat=True))
        return set()


class ShiftForm(PortalModelForm):
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
            "incentive_amount": forms.NumberInput(
                attrs={"placeholder": "150", "inputmode": "decimal", "min": "0"}
            ),
            "note": forms.TextInput(
                attrs={"placeholder": "e.g. Census high, need coverage", "autocomplete": "off"}
            ),
        }
        labels = {
            "shift_date": "Date",
            "shift_type": "Shift",
            "incentive_amount": "Incentive bonus (US $)",
            "note": "Note",
        }

    def __init__(self, *args, allowed_departments=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Server-side department authorization: the form only ever accepts
        # departments the logged-in administrator may manage.
        allowed = list(allowed_departments or [])
        choices = [(value, label) for value, label in Department.choices if value in allowed]
        self.single_department = None
        if len(choices) == 1:
            # One permitted department: no choice to make, so don't show one.
            self.single_department = choices[0][1]
            self.fields["department"].widget = forms.HiddenInput()
            self.fields["department"].initial = choices[0][0]
        else:
            # Several: make the administrator choose explicitly rather than
            # defaulting to the first one and texting the wrong staff.
            choices = [("", "Choose a department…")] + choices
        self.fields["department"].choices = choices
        self.fields["shift_type"].choices = ShiftType.choices  # drop empty choice
        self.fields["shift_date"].widget.attrs["min"] = timezone.localdate().isoformat()
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


class AccountForm(PortalModelForm):
    class Meta:
        model = AdminProfile
        fields = ["notification_phone"]
        labels = {"notification_phone": "SMS notification mobile number"}
        widgets = {
            "notification_phone": forms.TextInput(
                attrs={"placeholder": "(312) 555-0147", "inputmode": "tel"}
            )
        }
        help_texts = {
            "notification_phone": (
                "Acceptance alerts for shifts you create are texted here. "
                "Leave blank to receive no texts."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance.notification_phone:
            self.initial["notification_phone"] = format_us_phone(
                self.instance.notification_phone
            )

    def clean_notification_phone(self):
        value = self.cleaned_data["notification_phone"].strip()
        if not value:
            return ""
        return normalize_phone(value)


class CsvUploadForm(PortalForm):
    file = forms.FileField(
        label="CSV file",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )
