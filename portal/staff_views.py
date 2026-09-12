"""Staff-facing views: invitation response pages and manual badge login.

Staff have no passwords. They are identified either by an opaque invitation
token from their SMS link, or by a short-lived session established with
badge ID + phone last-four. Every request re-checks authorization
server-side; nothing relies on hidden fields or client-side logic.
"""

import secrets

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import ratelimit
from .forms import PartialTimesForm, StaffLoginForm
from .models import Invitation, Shift, ShiftResponse, Staff
from .responses import submit_response
from .tokens import hash_token

SESSION_STAFF_KEY = "staff_id"


def _current_staff(request):
    staff_id = request.session.get(SESSION_STAFF_KEY)
    if not staff_id:
        return None
    return Staff.objects.filter(pk=staff_id, is_active=True).first()


# --- Manual login -------------------------------------------------------------

def staff_login(request):
    if _current_staff(request):
        return redirect("staff_invitation_list")

    form = StaffLoginForm()
    blocked = False

    if request.method == "POST":
        ip = ratelimit.client_ip(request)
        badge = request.POST.get("badge_id", "").strip()[:32]
        if ratelimit.is_blocked(ratelimit.SCOPE_STAFF_LOGIN_IP, ip) or ratelimit.is_blocked(
            ratelimit.SCOPE_STAFF_LOGIN_BADGE, badge
        ):
            blocked = True
        else:
            form = StaffLoginForm(request.POST)
            verified = None
            if form.is_valid():
                candidate = Staff.objects.filter(
                    badge_id=form.cleaned_data["badge_id"].strip(), is_active=True
                ).first()
                if candidate is not None and secrets.compare_digest(
                    candidate.phone_last4, form.cleaned_data["phone_last4"]
                ):
                    verified = candidate
            if verified is not None:
                ratelimit.clear_failures(ratelimit.SCOPE_STAFF_LOGIN_IP, ip)
                ratelimit.clear_failures(ratelimit.SCOPE_STAFF_LOGIN_BADGE, badge)
                request.session.cycle_key()
                request.session[SESSION_STAFF_KEY] = verified.pk
                request.session.set_expiry(settings.STAFF_SESSION_AGE)
                return redirect("staff_invitation_list")
            ratelimit.record_failure(ratelimit.SCOPE_STAFF_LOGIN_IP, ip)
            ratelimit.record_failure(ratelimit.SCOPE_STAFF_LOGIN_BADGE, badge)
            # One generic message; never reveal which field was wrong or
            # whether the badge ID exists.
            form.add_error(None, "The information entered could not be verified.")

    return render(
        request, "portal/staff/login.html", {"form": form, "blocked": blocked}
    )


@require_POST
def staff_logout(request):
    request.session.pop(SESSION_STAFF_KEY, None)
    return redirect("staff_login")


def invitation_list(request):
    staff = _current_staff(request)
    if staff is None:
        return redirect("staff_login")
    Shift.objects.expire_stale()
    invitations = (
        staff.invitations.filter(shift__status=Shift.Status.OPEN)
        .select_related("shift", "response")
        .order_by("shift__shift_date")
    )
    return render(
        request,
        "portal/staff/invitation_list.html",
        {"staff": staff, "invitations": invitations},
    )


# --- Responding ---------------------------------------------------------------

def invitation_by_token(request, token):
    invitation = (
        Invitation.objects.filter(token_hash=hash_token(token))
        .select_related("shift", "staff")
        .first()
    )
    if invitation is None:
        raise Http404
    if not invitation.staff.is_active:
        # Same page as an unknown link: reveal nothing about the record.
        return render(request, "portal/staff/link_inactive.html", status=410)
    return _respond(request, invitation, reverse("staff_invitation", args=[token]))


def invitation_by_session(request, pk):
    staff = _current_staff(request)
    if staff is None:
        return redirect("staff_login")
    invitation = get_object_or_404(
        Invitation.objects.select_related("shift", "staff"), pk=pk
    )
    if invitation.staff_id != staff.pk:
        # 404, not 403: another employee's invitation should look nonexistent.
        raise Http404
    return _respond(
        request, invitation, reverse("staff_invitation_detail", args=[pk])
    )


def _respond(request, invitation, post_url):
    """Shared GET/POST handler for both the token and session entry points.

    Authorization has already been established by the caller (valid token,
    or session staff matching the invitation).
    """
    shift = invitation.shift
    accepting = shift.is_open_for_responses
    current = invitation.current_response

    partial_form = PartialTimesForm(
        initial=(
            {
                "partial_start_time": current.partial_start_time,
                "partial_end_time": current.partial_end_time,
            }
            if current and current.response_type == ShiftResponse.Type.ACCEPT_PARTIAL
            else None
        )
    )
    partial_open = False

    if request.method == "POST":
        if not accepting:
            messages.error(
                request, "This shift is no longer accepting responses."
            )
            return redirect(post_url)
        choice = request.POST.get("response_type", "")
        if choice == ShiftResponse.Type.ACCEPT_FULL:
            submit_response(invitation, ShiftResponse.Type.ACCEPT_FULL)
            messages.success(
                request,
                "Your response has been recorded. You indicated that you can "
                "work the full shift.",
            )
            return redirect(post_url)
        if choice == ShiftResponse.Type.DECLINED:
            submit_response(invitation, ShiftResponse.Type.DECLINED)
            messages.success(
                request,
                "Your response has been recorded. You indicated that you "
                "cannot work this shift.",
            )
            return redirect(post_url)
        if choice == ShiftResponse.Type.ACCEPT_PARTIAL:
            partial_form = PartialTimesForm(request.POST)
            partial_open = True
            if partial_form.is_valid():
                response = submit_response(
                    invitation,
                    ShiftResponse.Type.ACCEPT_PARTIAL,
                    start=partial_form.cleaned_data["partial_start_time"],
                    end=partial_form.cleaned_data["partial_end_time"],
                )
                messages.success(
                    request,
                    "Your response has been recorded. You indicated that you "
                    f"can work part of the shift, {response.partial_window_display}.",
                )
                return redirect(post_url)
        else:
            messages.error(request, "Please choose one of the response options.")

    return render(
        request,
        "portal/staff/invitation.html",
        {
            "invitation": invitation,
            "shift": shift,
            "staff": invitation.staff,
            "current": current,
            "accepting": accepting,
            "partial_form": partial_form,
            "partial_open": partial_open,
            "post_url": post_url,
            "logged_in_staff": _current_staff(request),
        },
    )
