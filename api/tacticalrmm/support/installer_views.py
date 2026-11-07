"""The generated Windows installer form embedded in Access's existing modal."""

import datetime as dt

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from knox.models import AuthToken

from accounts.models import User
from clients.models import Site
from tacticalrmm.permissions import _has_perm

from .forms import MonitoringInstallerForm
from .generation import BuildError, monitoring_response
from .views import _brand, _headers


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def installer(request):
    user = request.user
    if (
        not user.is_authenticated
        or not user.is_active
        or user.agent_id
        or user.block_dashboard_login
        or user.is_installer_user
        or not _has_perm(request, "can_install_agents")
    ):
        return HttpResponseForbidden(
            "Sign in to Access with permission to install agents."
        )
    sites = list(
        Site.objects.filter_by_role(user)
        .select_related("client")
        .order_by("client__name", "name")
    )
    form = MonitoringInstallerForm(
        sites, request.POST if request.method == "POST" else None
    )
    error, status = "", 200
    if request.method == "POST" and form.is_valid():
        if getattr(settings, "TRMM_INSECURE", False):
            error, status = (
                "Generated installers need a server with verified HTTPS.",
                400,
            )
        elif getattr(settings, "DEMO", False):
            error, status = "Installers cannot be generated in the demo.", 403
        else:
            installer_user = User.objects.filter(is_installer_user=True).first()
            if not installer_user:
                error, status = (
                    "Create the server's installer account before generating installers.",
                    400,
                )
            else:
                data = form.cleaned_data
                site = next(s for s in sites if str(s.pk) == data["site"])
                authorization, token = AuthToken.objects.create(
                    user=installer_user, expiry=dt.timedelta(hours=data["expires"])
                )
                try:
                    return monitoring_response(
                        client=site.client_id,
                        site=site.pk,
                        agent_type=data["agent_type"],
                        token=token,
                        api=f"https://{settings.ALLOWED_HOSTS[0]}",
                        file_name=f"JustConnect-{site.client.name}-{site.name}.msi",
                        rdp=data["rdp"],
                        ping=data["ping"],
                        power=data["power"],
                    )
                except (BuildError, ValueError) as exc:
                    authorization.delete()
                    error, status = str(exc), 400
    elif request.method == "POST":
        status = 400
    response = _headers(
        render(
            request,
            "support/installer.html",
            {
                "brand": _brand(),
                "form": form,
                "error": error,
                "embedded": request.GET.get("embed") == "1",
            },
            status=status,
        )
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Content-Security-Policy"] = response["Content-Security-Policy"].replace(
        "frame-ancestors 'none'", "frame-ancestors 'self'"
    )
    return response
