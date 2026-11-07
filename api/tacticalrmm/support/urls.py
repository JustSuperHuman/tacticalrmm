from django.urls import path

from . import devices, host, installer_views, remote, toolbox, views

app_name = "support"

urlpatterns = [
    path("", views.portal, name="portal"),
    path("reset/", views.reset, name="reset"),
    path("download/<str:platform>/", views.download, name="download"),
    path("payload/<str:sha256>/", views.payload, name="payload"),
    path("generated/", views.generated, name="generated"),
    path("manage/", host.sessions, name="manage"),
    path("manage/installer/", installer_views.installer, name="installer"),
    path("manage/codes/<str:code_id>/", host.code, name="code"),
    path("manage/devices/", host.devices, name="devices"),
    path("manage/devices/<str:device_id>/", devices.device, name="device"),
    path("manage/devices/<str:device_id>/screen/", devices.screen, name="screen"),
    path(
        "manage/devices/<str:device_id>/control/",
        remote.support_viewer,
        name="control",
    ),
    path(
        "manage/devices/<str:device_id>/control/relay/",
        remote.support_relay,
        name="control_relay",
    ),
    path("control/agents/<str:agent_id>/", remote.agent_viewer, name="agent_control"),
    path(
        "control/agents/<str:agent_id>/relay/",
        remote.agent_relay,
        name="agent_control_relay",
    ),
    path(
        "control/agents/<str:agent_id>/open/",
        remote.agent_open,
        name="agent_control_open",
    ),
    path("manage/toolbox/", toolbox.toolbox, name="toolbox"),
    path("manage/settings/", host.setup, name="settings"),
    path("logo/", views.logo, name="logo"),
]
