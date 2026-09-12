"""Saving staff responses and deciding when to notify the shift creator."""

from .models import ShiftResponse
from . import sms


def _is_material_acceptance(previous, response_type, start, end):
    """True when the new answer is an acceptance the creator hasn't seen yet:
    a first acceptance, a change of acceptance type, or a changed partial
    time range. Declines and identical resubmissions are not material.
    """
    if response_type not in (
        ShiftResponse.Type.ACCEPT_FULL,
        ShiftResponse.Type.ACCEPT_PARTIAL,
    ):
        return False
    if previous is None or previous.response_type != response_type:
        return True
    if response_type == ShiftResponse.Type.ACCEPT_PARTIAL:
        return previous.partial_start_time != start or previous.partial_end_time != end
    return False


def submit_response(invitation, response_type, start=None, end=None):
    """Create or replace the invitation's current response.

    Only the current answer is kept (updated in place). Sends the creator
    an acceptance notification when the answer materially changes into or
    within an acceptance. Returns the saved ShiftResponse.
    """
    if response_type != ShiftResponse.Type.ACCEPT_PARTIAL:
        start = end = None

    previous = invitation.current_response
    material = _is_material_acceptance(previous, response_type, start, end)

    if previous is None:
        response = ShiftResponse.objects.create(
            invitation=invitation,
            response_type=response_type,
            partial_start_time=start,
            partial_end_time=end,
        )
    else:
        previous.response_type = response_type
        previous.partial_start_time = start
        previous.partial_end_time = end
        previous.save()
        response = previous

    if material:
        sms.notify_acceptance(response)
    return response
