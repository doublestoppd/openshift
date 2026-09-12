"""Administrator-facing views (the /manage/ area)."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import csv_io, ratelimit, sms, tokens
from .forms import AccountForm, CsvUploadForm, ShiftForm, StaffForm, StaffGroupForm
from .models import Invitation, Shift, ShiftResponse, SmsLog, Staff, StaffGroup


# --- Access control ---------------------------------------------------------

def admin_required(view):
    """Require a logged-in user that has a portal administrator profile."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        profile = None
        if request.user.is_authenticated:
            profile = getattr(request.user, "portal_profile", None)
        if profile is None:
            return redirect_to_login(request.get_full_path(), reverse("manage_login"))
        request.admin_profile = profile
        return view(request, *args, **kwargs)

    return wrapper


def _get_managed_shift(request, pk):
    """Fetch a shift only if this administrator may manage its department.

    404 (not 403) so unauthorized administrators learn nothing about other
    departments' shifts.
    """
    shift = get_object_or_404(Shift, pk=pk)
    if shift.department not in request.admin_profile.allowed_departments:
        raise Http404
    return shift


# --- Authentication ---------------------------------------------------------

def manage_login(request):
    if request.user.is_authenticated and getattr(request.user, "portal_profile", None):
        return redirect("manage_dashboard")

    form = AuthenticationForm(request)
    blocked = False

    if request.method == "POST":
        ip = ratelimit.client_ip(request)
        username = request.POST.get("username", "").strip()[:150]
        if ratelimit.is_blocked(ratelimit.SCOPE_ADMIN_LOGIN_IP, ip) or ratelimit.is_blocked(
            ratelimit.SCOPE_ADMIN_LOGIN_USER, username.lower()
        ):
            blocked = True
        else:
            form = AuthenticationForm(request, data=request.POST)
            if form.is_valid():
                user = form.get_user()
                if getattr(user, "portal_profile", None) is None:
                    form.add_error(
                        None, "This account is not set up as a portal administrator."
                    )
                else:
                    ratelimit.clear_failures(ratelimit.SCOPE_ADMIN_LOGIN_IP, ip)
                    ratelimit.clear_failures(
                        ratelimit.SCOPE_ADMIN_LOGIN_USER, username.lower()
                    )
                    auth_login(request, user)
                    next_url = request.POST.get("next") or request.GET.get("next")
                    if next_url and url_has_allowed_host_and_scheme(
                        next_url, allowed_hosts={request.get_host()}
                    ):
                        return redirect(next_url)
                    return redirect("manage_dashboard")
            if not form.is_valid():
                ratelimit.record_failure(ratelimit.SCOPE_ADMIN_LOGIN_IP, ip)
                ratelimit.record_failure(
                    ratelimit.SCOPE_ADMIN_LOGIN_USER, username.lower()
                )

    return render(
        request,
        "portal/manage/login.html",
        {"form": form, "blocked": blocked, "next": request.GET.get("next", "")},
    )


@require_POST
def manage_logout(request):
    auth_logout(request)
    return redirect("manage_login")


# --- Dashboard and history --------------------------------------------------

def _shifts_with_counts(queryset):
    return queryset.annotate(
        invited_count=Count("invitations"),
        full_count=Count(
            "invitations",
            filter=Q(invitations__response__response_type=ShiftResponse.Type.ACCEPT_FULL),
        ),
        partial_count=Count(
            "invitations",
            filter=Q(invitations__response__response_type=ShiftResponse.Type.ACCEPT_PARTIAL),
        ),
        declined_count=Count(
            "invitations",
            filter=Q(invitations__response__response_type=ShiftResponse.Type.DECLINED),
        ),
    )


def _attach_no_response_counts(shifts):
    for shift in shifts:
        shift.no_response_count = (
            shift.invited_count - shift.full_count - shift.partial_count - shift.declined_count
        )
    return shifts


@admin_required
def dashboard(request):
    Shift.objects.expire_stale()
    shifts = _shifts_with_counts(
        Shift.objects.filter(
            status=Shift.Status.OPEN,
            department__in=request.admin_profile.allowed_departments,
        ).select_related("created_by").prefetch_related("requested_roles")
    ).order_by("shift_date", "department", "shift_type")
    shifts = _attach_no_response_counts(list(shifts))
    return render(request, "portal/manage/dashboard.html", {"shifts": shifts})


@admin_required
def history(request):
    Shift.objects.expire_stale()
    shifts = _shifts_with_counts(
        Shift.objects.filter(
            status__in=[Shift.Status.CLOSED, Shift.Status.EXPIRED],
            department__in=request.admin_profile.allowed_departments,
        ).select_related("created_by").prefetch_related("requested_roles")
    ).order_by("-shift_date", "department", "shift_type")
    page = Paginator(shifts, 25).get_page(request.GET.get("page"))
    _attach_no_response_counts(page.object_list)
    return render(request, "portal/manage/history.html", {"page": page})


# --- Shift CRUD ---------------------------------------------------------------

@admin_required
def shift_create(request):
    allowed = request.admin_profile.allowed_departments
    if request.method == "POST":
        form = ShiftForm(request.POST, allowed_departments=allowed)
        if form.is_valid():
            shift = form.save(commit=False)
            shift.created_by = request.user
            shift.save()
            shift.set_roles(form.cleaned_data["roles"])
            messages.success(request, "Open shift created. No SMS has been sent yet.")
            if "save_and_recipients" in request.POST:
                return redirect("shift_recipients", pk=shift.pk)
            return redirect("manage_dashboard")
    else:
        form = ShiftForm(allowed_departments=allowed)
    return render(
        request, "portal/manage/shift_form.html", {"form": form, "shift": None}
    )


@admin_required
def shift_edit(request, pk):
    shift = _get_managed_shift(request, pk)
    if not shift.is_open_for_responses:
        messages.error(request, "Closed or expired shifts can no longer be edited.")
        return redirect("shift_responses", pk=shift.pk)
    allowed = request.admin_profile.allowed_departments
    if request.method == "POST":
        form = ShiftForm(request.POST, instance=shift, allowed_departments=allowed)
        if form.is_valid():
            form.save()
            messages.success(request, "Shift updated.")
            if "save_and_recipients" in request.POST:
                return redirect("shift_recipients", pk=shift.pk)
            return redirect("manage_dashboard")
    else:
        form = ShiftForm(instance=shift, allowed_departments=allowed)
    return render(
        request, "portal/manage/shift_form.html", {"form": form, "shift": shift}
    )


@admin_required
@require_POST
def shift_close(request, pk):
    shift = _get_managed_shift(request, pk)
    if shift.status == Shift.Status.OPEN:
        shift.close()
        messages.success(
            request, f"{shift} is closed. Staff can no longer submit or change responses."
        )
    return redirect("manage_dashboard")


@admin_required
def shift_responses(request, pk):
    Shift.objects.expire_stale()
    shift = _get_managed_shift(request, pk)
    invitations = (
        shift.invitations.select_related("staff", "response").order_by("staff__name")
    )
    sections = {"full": [], "partial": [], "declined": [], "no_response": []}
    for invitation in invitations:
        response = invitation.current_response
        if response is None:
            sections["no_response"].append(invitation)
        elif response.response_type == ShiftResponse.Type.ACCEPT_FULL:
            sections["full"].append(invitation)
        elif response.response_type == ShiftResponse.Type.ACCEPT_PARTIAL:
            sections["partial"].append(invitation)
        else:
            sections["declined"].append(invitation)
    return render(
        request,
        "portal/manage/shift_responses.html",
        {"shift": shift, "sections": sections, "invited_count": len(invitations)},
    )


# --- Recipient selection and sending -----------------------------------------

def _recipient_context(shift, preselected_staff_ids=None, preselected_group_ids=None):
    preselected_staff_ids = set(preselected_staff_ids or [])
    preselected_group_ids = set(preselected_group_ids or [])
    requested_roles = shift.roles_list

    groups = StaffGroup.objects.annotate(
        active_members=Count("members", filter=Q(members__is_active=True))
    ).order_by("name")

    staff = list(Staff.objects.filter(is_active=True).order_by("name"))
    for person in staff:
        person.matches_roles = person.role in requested_roles
        person.preselected = person.pk in preselected_staff_ids
    # Staff matching the requested roles first, but everyone stays selectable.
    staff.sort(key=lambda s: (not s.matches_roles, s.name.lower()))

    invitations = shift.invitations.select_related("staff", "response")
    no_response_ids = [
        i.staff_id for i in invitations if i.staff.is_active and i.current_response is None
    ]
    declined_ids = [
        i.staff_id
        for i in invitations
        if i.staff.is_active
        and i.current_response is not None
        and i.current_response.response_type == ShiftResponse.Type.DECLINED
    ]
    group_json = {
        str(g.pk): list(g.members.filter(is_active=True).values_list("pk", flat=True))
        for g in groups
    }
    return {
        "shift": shift,
        "groups": groups,
        "staff": staff,
        "preselected_group_ids": preselected_group_ids,
        "no_response_ids": no_response_ids,
        "declined_ids": declined_ids,
        "has_been_sent": shift.invitations.filter(last_sent_at__isnull=False).exists(),
        "group_members_json": group_json,
    }


def _resolve_recipients(shift, post):
    """Deduplicated active staff from groups + individuals + quick picks."""
    ids = set()
    group_ids = [v for v in post.getlist("group_ids") if v.isdigit()]
    if group_ids:
        ids.update(
            Staff.objects.filter(
                groups__pk__in=group_ids, is_active=True
            ).values_list("pk", flat=True)
        )
    staff_ids = [v for v in post.getlist("staff_ids") if v.isdigit()]
    if staff_ids:
        ids.update(
            Staff.objects.filter(pk__in=staff_ids, is_active=True).values_list(
                "pk", flat=True
            )
        )
    invitations = shift.invitations.select_related("staff", "response")
    if post.get("include_no_response"):
        ids.update(
            i.staff_id
            for i in invitations
            if i.staff.is_active and i.current_response is None
        )
    if post.get("include_declined"):
        ids.update(
            i.staff_id
            for i in invitations
            if i.staff.is_active
            and i.current_response is not None
            and i.current_response.response_type == ShiftResponse.Type.DECLINED
        )
    return list(Staff.objects.filter(pk__in=ids, is_active=True).order_by("name"))


@admin_required
def shift_recipients(request, pk):
    shift = _get_managed_shift(request, pk)
    if not shift.is_open_for_responses:
        messages.error(request, "This shift is closed or expired; invitations cannot be sent.")
        return redirect("shift_responses", pk=shift.pk)

    if request.method == "POST":
        if request.POST.get("stage") == "edit":
            # Back from the confirmation page: re-render with prior choices.
            context = _recipient_context(
                shift,
                preselected_staff_ids=[
                    int(v) for v in request.POST.getlist("staff_ids") if v.isdigit()
                ],
            )
            return render(request, "portal/manage/recipients.html", context)

        recipients = _resolve_recipients(shift, request.POST)
        if not recipients:
            messages.error(request, "Select at least one recipient.")
            context = _recipient_context(
                shift,
                preselected_staff_ids=[
                    int(v) for v in request.POST.getlist("staff_ids") if v.isdigit()
                ],
                preselected_group_ids=[
                    int(v) for v in request.POST.getlist("group_ids") if v.isdigit()
                ],
            )
            return render(request, "portal/manage/recipients.html", context)
        return render(
            request,
            "portal/manage/send_confirm.html",
            {"shift": shift, "recipients": recipients},
        )

    return render(request, "portal/manage/recipients.html", _recipient_context(shift))


@admin_required
@require_POST
def shift_send(request, pk):
    shift = _get_managed_shift(request, pk)
    if not shift.is_open_for_responses:
        messages.error(request, "This shift is closed or expired; invitations cannot be sent.")
        return redirect("shift_responses", pk=shift.pk)

    staff_ids = [v for v in request.POST.getlist("staff_ids") if v.isdigit()]
    recipients = list(
        Staff.objects.filter(pk__in=staff_ids, is_active=True).order_by("name")
    )
    if not recipients:
        messages.error(request, "No valid recipients were selected.")
        return redirect("shift_recipients", pk=shift.pk)

    sent, failed = [], []
    for person in recipients:
        raw_token = tokens.generate_token()
        new_hash = tokens.hash_token(raw_token)
        invitation = Invitation.objects.filter(shift=shift, staff=person).first()
        if invitation is None:
            invitation = Invitation.objects.create(
                shift=shift, staff=person, token_hash=new_hash
            )
            old_hash = None
        else:
            # Only the token's hash is stored, so a resend issues a fresh
            # link; the invitation row (and any response) is reused.
            old_hash = invitation.token_hash
            invitation.token_hash = new_hash
            invitation.save(update_fields=["token_hash"])
        if sms.send_invitation(invitation, raw_token):
            sent.append(person)
        else:
            failed.append(person)
            if old_hash:
                # The new link never went out; keep the previous one valid.
                invitation.token_hash = old_hash
                invitation.save(update_fields=["token_hash"])

    return render(
        request,
        "portal/manage/send_results.html",
        {"shift": shift, "sent": sent, "failed": failed},
    )


# --- Staff directory ----------------------------------------------------------

@admin_required
def staff_list(request):
    query = request.GET.get("q", "").strip()
    people = Staff.objects.order_by("name").prefetch_related("groups")
    if query:
        people = people.filter(Q(name__icontains=query) | Q(badge_id__icontains=query))
    return render(
        request, "portal/manage/staff_list.html", {"people": people, "query": query}
    )


@admin_required
def staff_create(request):
    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        person = form.save()
        messages.success(request, f"{person.name} added to the staff directory.")
        return redirect("staff_list")
    return render(
        request, "portal/manage/staff_form.html", {"form": form, "person": None}
    )


@admin_required
def staff_edit(request, pk):
    person = get_object_or_404(Staff, pk=pk)
    form = StaffForm(request.POST or None, instance=person)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{person.name} updated.")
        return redirect("staff_list")
    return render(
        request, "portal/manage/staff_form.html", {"form": form, "person": person}
    )


@admin_required
@require_POST
def staff_toggle_active(request, pk):
    person = get_object_or_404(Staff, pk=pk)
    person.is_active = not person.is_active
    person.save(update_fields=["is_active", "updated_at"])
    state = "reactivated" if person.is_active else "deactivated"
    messages.success(request, f"{person.name} {state}.")
    return redirect("staff_list")


# --- Groups -------------------------------------------------------------------

@admin_required
def group_list(request):
    groups = StaffGroup.objects.annotate(member_count=Count("members")).order_by("name")
    return render(request, "portal/manage/group_list.html", {"groups": groups})


@admin_required
def group_create(request):
    form = StaffGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = form.save()
        messages.success(request, f"Group “{group.name}” created.")
        return redirect("group_list")
    return render(
        request, "portal/manage/group_form.html", {"form": form, "group": None}
    )


@admin_required
def group_edit(request, pk):
    group = get_object_or_404(StaffGroup, pk=pk)
    form = StaffGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Group “{group.name}” updated.")
        return redirect("group_list")
    return render(
        request, "portal/manage/group_form.html", {"form": form, "group": group}
    )


@admin_required
def group_delete(request, pk):
    group = get_object_or_404(StaffGroup, pk=pk)
    if request.method == "POST":
        name = group.name
        group.delete()
        messages.success(request, f"Group “{name}” deleted. Staff records are unaffected.")
        return redirect("group_list")
    return render(request, "portal/manage/group_confirm_delete.html", {"group": group})


# --- SMS log ------------------------------------------------------------------

@admin_required
def sms_log(request):
    logs = SmsLog.objects.select_related("shift", "staff", "admin_user")
    shift_id = request.GET.get("shift", "")
    if shift_id.isdigit():
        logs = logs.filter(shift_id=int(shift_id))
    page = Paginator(logs, 50).get_page(request.GET.get("page"))
    return render(
        request, "portal/manage/sms_log.html", {"page": page, "shift_id": shift_id}
    )


# --- Account ------------------------------------------------------------------

@admin_required
def account(request):
    profile = request.admin_profile
    form = AccountForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Account settings saved.")
        return redirect("manage_account")
    return render(
        request,
        "portal/manage/account.html",
        {"form": form, "profile": profile},
    )


@admin_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None, label_suffix="")
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password changed.")
        return redirect("manage_account")
    return render(request, "portal/manage/password_change.html", {"form": form})


# --- CSV import / export ------------------------------------------------------

def _csv_response(text, stem):
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{stem}-{timezone.localdate()}.csv"'
    return response


@admin_required
def staff_export(request):
    return _csv_response(csv_io.export_staff_csv(), "staff")


@admin_required
def group_export(request):
    return _csv_response(csv_io.export_groups_csv(), "groups")


CSV_KINDS = {
    "staff": {
        "title": "Import staff from CSV",
        "noun": "staff",
        "headers": csv_io.STAFF_HEADERS,
        "example": [
            ["Jane Smith", "1001", "(312) 555-0147", "RN", "yes", "ER RNs; Night Staff"],
            ["Bob Reyes", "0042", "312-555-0122", "CNA", "no", ""],
        ],
        "notes": [
            "Column names are matched loosely (“Phone”, “Mobile” or “Cell” all work) and order does not matter.",
            "Staff are matched by Badge ID: known badges are updated, new ones are added. Nothing is ever deleted.",
            "Active is yes or no (blank means yes). Groups: separate several with semicolons; groups that do not exist yet are created. Leave the Groups column out to leave memberships alone.",
            "Excel tip: badge IDs with leading zeros (0007) — format that column as Text before saving, or Excel turns 0007 into 7.",
            "Any problem in any row stops the whole import, so you never end up with half a file.",
        ],
        "parse": csv_io.parse_staff_csv,
        "preview": csv_io.preview_staff_rows,
        "apply": csv_io.apply_staff_rows,
        "list_url": "staff_list",
        "export_url": "staff_export",
        "success": lambda r: (
            f"Imported {r['created']} new staff and updated {r['updated']} existing"
            + (f", creating {r['groups_created']} new group(s)" if r["groups_created"] else "")
            + "."
        ),
    },
    "groups": {
        "title": "Import groups from CSV",
        "noun": "groups",
        "headers": csv_io.GROUP_HEADERS,
        "example": [
            ["ER RNs", "1001", "Jane Smith", "RN"],
            ["ER RNs", "1002", "Bob Reyes", "RN"],
            ["Weekend Staff", "0042", "Ana Diaz", "CNA"],
        ],
        "notes": [
            "One row per member: the group name and that member’s Badge ID. Name and Role are optional and ignored.",
            "Staff must already exist — add or import them under Staff first.",
            "Groups that do not exist yet are created. A listed group’s members are replaced by the rows in the file; groups not in the file are left alone.",
        ],
        "parse": csv_io.parse_groups_csv,
        "preview": csv_io.preview_group_rows,
        "apply": csv_io.apply_group_rows,
        "list_url": "group_list",
        "export_url": "group_export",
        "success": lambda r: (
            f"Imported {r['created'] + r['updated']} group(s) ({r['created']} new) "
            f"with {r['memberships']} membership(s)."
        ),
    },
}


def _csv_import(request, kind):
    spec = CSV_KINDS[kind]
    context = {
        "kind": kind, "spec": spec, "form": CsvUploadForm(),
        "errors": None, "preview": None, "csv_text": "",
    }
    if request.method == "POST":
        if request.POST.get("confirm"):
            # Second step: the validated file contents come back in a hidden
            # field; re-validate before writing anything.
            text = request.POST.get("csv_text", "")
            parsed, errors = spec["parse"](text)
            if errors:
                context["errors"] = errors
            else:
                result = spec["apply"](parsed)
                messages.success(request, spec["success"](result))
                return redirect(spec["list_url"])
        else:
            form = CsvUploadForm(request.POST, request.FILES)
            context["form"] = form
            if form.is_valid():
                try:
                    text = csv_io.decode_upload(form.cleaned_data["file"])
                except ValidationError as exc:
                    form.add_error("file", exc)
                else:
                    parsed, errors = spec["parse"](text)
                    if errors:
                        context["errors"] = errors
                    else:
                        context["preview"] = spec["preview"](parsed)
                        context["csv_text"] = text
    return render(request, "portal/manage/csv_import.html", context)


@admin_required
def staff_import(request):
    return _csv_import(request, "staff")


@admin_required
def group_import(request):
    return _csv_import(request, "groups")
