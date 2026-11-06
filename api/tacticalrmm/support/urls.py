from django.urls import path

from . import devices, views

app_name = "support"

urlpatterns = [
    path("", views.portal, name="portal"),
    path("reset/", views.reset, name="reset"),
    path("download/<str:platform>/", views.download, name="download"),
    path("manage/", views.manage, name="manage"),
    path("manage/devices/", devices.devices, name="devices"),
    path("manage/devices/<str:device_id>/", devices.device, name="device"),
    path("logo/", views.logo, name="logo"),
]
