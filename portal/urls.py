from django.urls import path
from django.views.generic import RedirectView

from . import manage_views, staff_views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="staff_login", permanent=False)),

    # Staff-facing
    path("i/<str:token>/", staff_views.invitation_by_token, name="staff_invitation"),
    path("staff/login/", staff_views.staff_login, name="staff_login"),
    path("staff/logout/", staff_views.staff_logout, name="staff_logout"),
    path("staff/invitations/", staff_views.invitation_list, name="staff_invitation_list"),
    path(
        "staff/invitations/<int:pk>/",
        staff_views.invitation_by_session,
        name="staff_invitation_detail",
    ),

    # Administrator-facing
    path("manage/login/", manage_views.manage_login, name="manage_login"),
    path("manage/logout/", manage_views.manage_logout, name="manage_logout"),
    path("manage/", manage_views.dashboard, name="manage_dashboard"),
    path("manage/history/", manage_views.history, name="manage_history"),
    path("manage/shifts/new/", manage_views.shift_create, name="shift_create"),
    path("manage/shifts/<int:pk>/edit/", manage_views.shift_edit, name="shift_edit"),
    path("manage/shifts/<int:pk>/close/", manage_views.shift_close, name="shift_close"),
    path(
        "manage/shifts/<int:pk>/responses/",
        manage_views.shift_responses,
        name="shift_responses",
    ),
    path(
        "manage/shifts/<int:pk>/recipients/",
        manage_views.shift_recipients,
        name="shift_recipients",
    ),
    path("manage/shifts/<int:pk>/send/", manage_views.shift_send, name="shift_send"),
    path("manage/staff/", manage_views.staff_list, name="staff_list"),
    path("manage/staff/new/", manage_views.staff_create, name="staff_create"),
    path("manage/staff/<int:pk>/edit/", manage_views.staff_edit, name="staff_edit"),
    path(
        "manage/staff/<int:pk>/toggle-active/",
        manage_views.staff_toggle_active,
        name="staff_toggle_active",
    ),
    path("manage/groups/", manage_views.group_list, name="group_list"),
    path("manage/groups/new/", manage_views.group_create, name="group_create"),
    path("manage/groups/<int:pk>/edit/", manage_views.group_edit, name="group_edit"),
    path("manage/groups/<int:pk>/delete/", manage_views.group_delete, name="group_delete"),
    path("manage/sms-log/", manage_views.sms_log, name="sms_log"),
    path("manage/account/", manage_views.account, name="manage_account"),
    path(
        "manage/account/password/",
        manage_views.password_change,
        name="manage_password_change",
    ),
]
