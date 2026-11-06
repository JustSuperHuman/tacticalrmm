"""The remote control window: a ScreenConnect-style viewer for a computer's screen.

It works for support computers and for monitored agents (the dashboard's Take
Control opens it too, see take-control.sh). The browser draws the screen with
MeshCentral's own desktop client, over a tunnel the server opens as the
technician's own MeshCentral account.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from agents.models import Agent
from core.utils import get_core_settings
from logs.models import AuditLog
from tacticalrmm.constants import AGENT_STATUS_ONLINE, AgentPlat, AuditActionType
from tacticalrmm.permissions import _has_perm, _has_perm_on_agent

from . import host, mesh
from .devices import _audit
from .views import _brand, _can_manage, _headers

PLATFORMS = {AgentPlat.WINDOWS: "windows", AgentPlat.DARWIN: "macos"}


def _allow_meshcentral(response):
    """The viewer talks to MeshCentral's relay, and Files/Terminal redirect there."""
    try:
        site = urlsplit(get_core_settings().mesh_site or "")
    except Exception:
        return response
    if site.scheme == "https" and site.netloc:
        policy = response["Content-Security-Policy"]
        policy = policy.replace(
            "connect-src 'self'", f"connect-src 'self' wss://{site.netloc}"
        ).replace("form-action 'self'", f"form-action 'self' https://{site.netloc}")
        response["Content-Security-Policy"] = policy
    return response


def _json(data, status=200):
    return _headers(JsonResponse(data, status=status))


def _demo():
    return getattr(settings, "DEMO", False)


# ---------- Support computers ----------


def _support_computer(request, device_id):
    if not mesh.DEVICE_ID.fullmatch(device_id):
        raise Http404
    found = host.support_computers()
    context = host.workspace(request, selected=device_id, found=found)
    computer = context["computers"].get(device_id)
    if computer is None:
        raise Http404
    return computer


@never_cache
@require_http_methods(["GET"])
def support_viewer(request, device_id):
    if not _can_manage(request):
        return _headers(HttpResponseRedirect(reverse("support:manage")))
    try:
        computer = _support_computer(request, device_id)
    except mesh.MeshError as exc:
        return _viewer(request, {"error": str(exc)}, status=503)
    detail = reverse("support:device", args=[device_id])
    return _viewer(
        request,
        {
            "title": computer["title"],
            "computer": computer["name"],
            "system": computer["os"],
            "signed_in": ", ".join(computer["users"]),
            "site": host.site_name(computer["site"]),
            "platform": computer["platform"],
            "online": computer["online"],
            "relay_url": reverse("support:control_relay", args=[device_id]),
            "open_url": detail,
            "open_action": "connect",
            "back_url": detail,
            "back_label": "Session",
        },
    )


@never_cache
@csrf_protect
@require_POST
def support_relay(request, device_id):
    if not _can_manage(request):
        return _json({"error": "Sign in to the dashboard first."}, 403)
    if _demo():
        return _json({"error": "Remote control isn’t available in the demo."}, 403)
    try:
        computer = _support_computer(request, device_id)
        if not computer["online"]:
            return _json(
                {
                    "error": "This computer is offline. Ask them to open the support app."
                },
                409,
            )
        relay = mesh.desktop_relay(computer["node_id"], request.user, grant=True)
    except mesh.MeshError as exc:
        return _json({"error": str(exc)}, 503)
    _audit(
        request,
        AuditActionType.REMOTE_SESSION,
        f"Took control of {computer['name']} through the support app.",
        computer,
        view="control",
    )
    return _json({"relay": relay})


# ---------- Monitored agents ----------


def _can_control(request, agent_id):
    user = request.user
    return (
        user.is_authenticated
        and user.is_active
        and not user.is_installer_user
        and not user.agent_id
        and not user.block_dashboard_login
        and _has_perm(request, "can_use_mesh")
        and _has_perm_on_agent(user, agent_id)
    )


def _agent(request, agent_id):
    if not _can_control(request, agent_id):
        return None
    agent = (
        Agent.objects.select_related("site__client").filter(agent_id=agent_id).first()
    )
    if agent is None:
        raise Http404
    return agent


@never_cache
@require_http_methods(["GET"])
def agent_viewer(request, agent_id):
    agent = _agent(request, agent_id)
    if agent is None:
        if request.user.is_authenticated:
            raise Http404
        return _viewer(request, {"signin": True}, status=401)
    return _viewer(
        request,
        {
            "title": agent.hostname,
            "computer": agent.hostname,
            "system": agent.operating_system or "",
            "signed_in": agent.logged_in_username or "",
            "site": f"{agent.site.client.name} / {agent.site.name}",
            "platform": PLATFORMS.get(agent.plat, "other"),
            "online": agent.status == AGENT_STATUS_ONLINE,
            "relay_url": reverse("support:agent_control_relay", args=[agent_id]),
            "open_url": reverse("support:agent_control_open", args=[agent_id]),
            "open_action": "",
            "back_url": settings.CORS_ORIGIN_WHITELIST[0].rstrip("/"),
            "back_label": "Dashboard",
        },
    )


@never_cache
@csrf_protect
@require_POST
def agent_relay(request, agent_id):
    agent = _agent(request, agent_id)
    if agent is None:
        return _json({"error": "You don’t have access to this agent."}, 403)
    if _demo():
        return _json({"error": "Remote control isn’t available in the demo."}, 403)
    if not agent.mesh_node_id:
        return _json(
            {"error": "This agent isn’t connected to MeshCentral yet. Try again soon."},
            409,
        )
    try:
        relay = mesh.desktop_relay(
            mesh.node_id(agent.mesh_node_id), request.user, grant=False
        )
    except mesh.MeshError as exc:
        return _json({"error": str(exc)}, 503)
    AuditLog.audit_mesh_session(
        username=request.user.username,
        agent=agent,
        debug_info={"ip": getattr(request, "_client_ip", "")},
    )
    return _json({"relay": relay})


@never_cache
@csrf_protect
@require_POST
def agent_open(request, agent_id):
    """Files or Terminal in MeshCentral, for a monitored agent."""
    agent = _agent(request, agent_id)
    view = request.POST.get("view")
    if agent is None or view not in mesh.SESSION_VIEWS or not agent.mesh_node_id:
        raise Http404
    try:
        url = mesh.session_url(
            {"node_id": agent.mesh_node_id}, request.user, view, grant=False
        )
    except mesh.MeshError as exc:
        return _json({"error": str(exc)}, 503)
    AuditLog.audit_mesh_session(
        username=request.user.username,
        agent=agent,
        debug_info={"ip": getattr(request, "_client_ip", ""), "view": view},
    )
    return _headers(HttpResponseRedirect(url))


def _viewer(request, data, status=200):
    context = {"brand": _brand(), "data": data, **data}
    response = render(request, "support/control.html", context, status=status)
    return _allow_meshcentral(_headers(response))
