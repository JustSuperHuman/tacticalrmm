"""Technician pages for computers connected through the support app."""

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from clients.models import Site
from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.permissions import _has_perm

from . import install, mesh
from .models import SupportHandoff
from .views import _brand, _can_manage, _headers

MAX_OUTPUT = 100_000
NO_REPLY = (
    "No reply within 60 seconds. The command may still be running, "
    "or the support app is busy with an earlier command."
)


def _context(request, **extra):
    return {
        "brand": _brand(),
        "dashboard_url": settings.CORS_ORIGIN_WHITELIST[0].rstrip("/"),
        "tab": "devices",
        **extra,
    }


def _audit(request, action, message, device, **details):
    AuditLog.objects.create(
        username=request.user.username,
        agent=device["name"][:255],
        action=action,
        object_type=AuditObjType.AGENT,
        message=message[:255],
        after_value={"support_device": device["id"], **details},
    )


def _redirect(name, query="", **kwargs):
    url = reverse(name, kwargs=kwargs)
    return _headers(HttpResponseRedirect(f"{url}?{query}" if query else url))


def _not_allowed():
    return _redirect("support:manage")


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def devices(request):
    if not _can_manage(request):
        return _not_allowed()
    context = _context(request, can_setup=_has_perm(request, "can_edit_core_settings"))
    status = 200
    try:
        group = mesh.find_group()
        if request.method == "POST":
            if getattr(settings, "DEMO", False):
                context["error"] = "The support group can’t be set up in the demo."
                status = 403
            elif not context["can_setup"]:
                context["error"] = (
                    "You need permission to edit global settings to set up the group."
                )
                status = 403
            elif group is None:
                group = mesh.create_group()
                AuditLog.objects.create(
                    username=request.user.username,
                    action=AuditActionType.ADD,
                    object_type=AuditObjType.CORE,
                    message="Created the MeshCentral support group.",
                    after_value={"group": mesh.group_name()},
                )
                return _redirect("support:devices")
        context["group"] = group
        context["group_name"] = mesh.group_name()
        if group:
            found = mesh.list_devices(group)
            open_handoffs = {
                h.device_id: h
                for h in SupportHandoff.objects.filter(
                    device_id__in=[d["id"] for d in found], finished_at=None
                ).select_related("site__client")
            }
            for device in found:
                device["handoff"] = open_handoffs.get(device["id"])
                if device["handoff"]:
                    device["agent"] = device["handoff"].find_agent()
            context["devices"] = found
    except mesh.MeshError as exc:
        context["error"] = str(exc)
        status = 503
    context["removed"] = request.GET.get("removed") == "1"
    return _headers(render(request, "support/devices.html", context, status=status))


def _run(request, context, device, command, shell):
    if not _has_perm(request, "can_send_cmd"):
        context["error"] = "You need permission to send commands to run them here."
        return 403
    if not command.strip():
        context["error"] = "Enter a command to run."
        return 400
    if len(command) > 60_000:
        context["error"] = "That command is too long."
        return 400
    _audit(
        request,
        AuditActionType.EXEC_COMMAND,
        f"Ran a command through the support app: {command}",
        device,
        command=command,
    )
    output = mesh.run_command(device, command, shell)
    context["output"] = NO_REPLY if output is None else output[:MAX_OUTPUT]
    context["no_reply"] = output is None
    context["command"] = command
    context["shell"] = shell
    return 200


def _install(request, context, device, sites):
    site = sites.filter(pk=request.POST.get("site") or 0).first()
    agent_type = request.POST.get("agent_type")
    if site is None or agent_type not in install.AGENT_TYPES:
        context["error"] = "Choose a client site and a type."
        return 400
    try:
        if device["windows"]:
            command, shell = install.windows_command(site, agent_type), mesh.POWERSHELL
        else:
            command, shell = install.mac_command(site, agent_type), mesh.SHELL
    except install.InstallUnavailable as exc:
        context["error"] = str(exc)
        return 409
    mesh.run_command(device, command, shell, reply=False, timeout=15)
    handoff = SupportHandoff.objects.filter(
        device_id=device["id"], finished_at=None
    ).first() or SupportHandoff(device_id=device["id"])
    handoff.device_name = device["name"][:255]
    handoff.site = site
    handoff.agent_type = agent_type
    handoff.agent = None
    handoff.requested_by = request.user
    handoff.requested_at = timezone.now()
    handoff.save()
    _audit(
        request,
        AuditActionType.AGENT_INSTALL,
        f"Started installing the RMM agent on {device['name']} through the support app.",
        device,
        site=site.pk,
        agent_type=agent_type,
    )
    return None


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def device(request, device_id):
    if not _can_manage(request):
        return _not_allowed()
    context = _context(request)
    try:
        group = mesh.find_group()
        found = mesh.get_device(group, device_id) if group else None
    except mesh.MeshError as exc:
        context["error"] = str(exc)
        return _headers(render(request, "support/device.html", context, status=503))
    if found is None:
        raise Http404
    sites = Site.objects.filter_by_role(request.user).select_related("client")
    handoff = (
        SupportHandoff.objects.filter(device_id=found["id"], finished_at=None)
        .select_related("site__client", "requested_by")
        .first()
    )
    shells = (
        [(mesh.POWERSHELL, "PowerShell"), (mesh.CMD, "Command Prompt")]
        if found["windows"]
        else [(mesh.SHELL, "Shell")]
    )
    context.update(
        device=found,
        sites=sites,
        shells=shells,
        can_send=_has_perm(request, "can_send_cmd"),
        install_log=(
            f"Get-Content '{install.WINDOWS_LOG}' -Tail 40"
            if found["windows"]
            else f"tail -n 40 {install.MAC_LOG}"
        ),
    )
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if getattr(settings, "DEMO", False):
                context["error"] = "Support computers can’t be changed in the demo."
                status = 403
            elif action == "remove":
                mesh.remove_device(found)
                if handoff:
                    handoff.finished_at = timezone.now()
                    handoff.save(update_fields=["finished_at"])
                _audit(
                    request,
                    AuditActionType.DELETE,
                    f"Removed the support app from {found['name']}.",
                    found,
                )
                return _redirect("support:devices", "removed=1")
            elif not found["online"]:
                context["error"] = (
                    "This computer is offline. Ask them to open the support app."
                )
                status = 409
            elif action == "run":
                try:
                    shell = int(request.POST.get("shell", ""))
                except ValueError:
                    shell = 0
                if shell not in dict(shells):
                    shell = shells[0][0]
                status = _run(
                    request, context, found, request.POST.get("command", ""), shell
                )
            elif action == "install":
                status = _install(request, context, found, sites)
                if status is None:
                    return _redirect(
                        "support:device", "sent=install", device_id=found["id"]
                    )
            else:
                context["error"] = "Choose what you’d like to do."
                status = 400
        except mesh.MeshError as exc:
            context["error"] = str(exc)
            status = 503
        handoff = (
            SupportHandoff.objects.filter(device_id=found["id"], finished_at=None)
            .select_related("site__client", "requested_by")
            .first()
        )
    context["handoff"] = handoff
    context["agent"] = handoff.find_agent() if handoff else None
    context["install_sent"] = request.GET.get("sent") == "install"
    return _headers(render(request, "support/device.html", context, status=status))
