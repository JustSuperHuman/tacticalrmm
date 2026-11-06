"""The session page for a computer connected through the support app."""

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from agents.models import Agent
from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.permissions import _has_perm

from . import host, install, mesh, screens, toolbox
from .models import SupportCommand, SupportHandoff, SupportNote, SupportRun
from .views import _can_manage, _headers

MAX_OUTPUT = 100_000
MAX_NOTE = 4000
HISTORY = 25
NO_REPLY = (
    "No reply within 60 seconds. The command may still be running, "
    "or the support app is busy with an earlier command."
)
BUSY = (
    "No reply. The support app is busy, most likely because the Windows prompt "
    "is still waiting for them to choose Yes or No."
)
SESSION_NAMES = {"desktop": "remote desktop", "terminal": "terminal", "files": "files"}
TABS = ("general", "commands", "monitoring", "notes", "timeline")
PROMPTS = {mesh.POWERSHELL: "PS>", mesh.CMD: ">", mesh.SHELL: "$"}


def _audit(request, action, message, device, **details):
    AuditLog.objects.create(
        username=request.user.username,
        agent=device["name"][:255],
        action=action,
        object_type=AuditObjType.AGENT,
        message=message[:255],
        after_value={"support_device": device["id"], **details},
    )


def _wants_json(request):
    return request.headers.get("Accept") == "application/json"


def _back(request, device, **query):
    url = host.with_group(
        reverse("support:device", args=[device["id"]]),
        host.group_from(request),
        **query,
    )
    return _headers(HttpResponseRedirect(url))


def _run(request, context, device, command, shell, timeout=60, keep=True, note=""):
    if not _has_perm(request, "can_send_cmd"):
        context["error"] = "You need permission to send commands to run them here."
        return 403
    if not command.strip():
        context["error"] = "Enter a command to run, or choose one from the toolbox."
        return 400
    if len(command) > toolbox.MAX_COMMAND:
        context["error"] = "That command is too long."
        return 400
    _audit(
        request,
        AuditActionType.EXEC_COMMAND,
        note or f"Ran a command through the support app: {command}",
        device,
        command=command,
    )
    output = mesh.run_command(device, command, shell, timeout=timeout)
    context["output"] = output[:MAX_OUTPUT] if output is not None else None
    context["no_reply"] = output is None
    context["ran_at"] = timezone.now()
    if keep:
        context["run"] = SupportRun.objects.create(
            session=device["session"],
            user=request.user,
            shell=shell,
            command=command,
            output=context["output"] or "",
            no_reply=output is None,
            ran_at=context["ran_at"],
        )
    return 200


def _run_json(run):
    return {
        "command": run.command,
        "output": run.output or ("" if run.no_reply else "(no output)"),
        "noReply": run.no_reply,
        "message": NO_REPLY if run.no_reply else "",
        "ranAt": run.ran_at.isoformat(),
        "user": run.user.username if run.user else "",
        "prompt": PROMPTS.get(run.shell, "$"),
    }


def _install(request, context, device, sites):
    site = sites.filter(pk=request.POST.get("site") or 0).first()
    agent_type = request.POST.get("agent_type")
    if site is None or agent_type not in install.AGENT_TYPES:
        context["error"] = "Choose a client site and a type."
        return 400, None
    try:
        if device["windows"]:
            command, shell = install.windows_command(site, agent_type), mesh.POWERSHELL
        else:
            command, shell = install.mac_command(site, agent_type), mesh.SHELL
    except install.InstallUnavailable as exc:
        context["error"] = str(exc)
        return 409, None
    # On Windows this waits briefly for their answer to the permission prompt.
    reply = mesh.run_command(device, command, shell, timeout=20)
    answer = (reply or "").strip()
    if install.NOT_STARTED.strip() in answer:
        reason = answer.split(install.NOT_STARTED.strip(), 1)[1].strip()
        if "cancel" in reason.lower():
            context["error"] = (
                "They chose No on the Windows prompt. When they’re ready, install "
                "again and ask them to choose Yes."
            )
        else:
            context["error"] = f"The install didn’t start. {reason}"
        return 409, None
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
    return None, ("started" if install.STARTED in answer else "sent")


def _connect(request, device):
    view = request.POST.get("view")
    if view not in mesh.SESSION_VIEWS:
        raise Http404
    url = mesh.session_url(device, request.user, view)
    _audit(
        request,
        AuditActionType.REMOTE_SESSION,
        f"Opened {SESSION_NAMES[view]} on {device['name']} through the support app.",
        device,
        view=view,
    )
    return _headers(HttpResponseRedirect(url))


def _save_command(request, context, shells):
    if not toolbox.can_edit(request):
        context["error"] = "You need the Manage scripts permission to save commands."
        return 403
    name, shell, command, error = toolbox.clean(request.POST, dict(shells))
    if error:
        context["error"] = error
        return 400
    context["saved_command"] = toolbox.save(request, name, shell, command)
    return 200


def _existing_agents(request, device):
    short = device["name"].split(".")[0]
    return list(
        Agent.objects.filter_by_role(request.user)
        .filter(hostname__iexact=short)
        .select_related("site__client")
        .only("hostname", "site__name", "site__client__name")[:3]
    )


def _remove(request, device):
    mesh.remove_device(device)
    handoff = device["handoff"]
    if handoff:
        handoff.finished_at = timezone.now()
        handoff.save(update_fields=["finished_at"])
    _audit(
        request,
        AuditActionType.DELETE,
        f"Removed the support app from {device['name']}.",
        device,
    )
    url = host.with_group(
        reverse("support:manage"),
        host.group_from(request),
        removed="1" if device["online"] else "offline",
    )
    return _headers(HttpResponseRedirect(url))


def _rename(request, context, device):
    name = request.POST.get("name", "").strip()
    if len(name) > 100:
        context["error"] = "Use a session name of up to 100 characters."
        return 400
    session = device["session"]
    session.name = name
    session.save(update_fields=["name"])
    _audit(
        request,
        AuditActionType.MODIFY,
        (
            f"Named the session “{name}”."
            if name
            else "Cleared the session name, so it shows the computer’s name."
        ),
        device,
    )
    return None


def _note(request, context, device):
    text = request.POST.get("note", "").strip()
    if not text:
        context["error"] = "Write the note first."
        return 400
    if len(text) > MAX_NOTE:
        context["error"] = f"Keep notes under {MAX_NOTE:,} characters."
        return 400
    SupportNote.objects.create(
        session=device["session"], author=request.user, text=text
    )
    return None


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def device(request, device_id):
    if not _can_manage(request):
        return _headers(HttpResponseRedirect(reverse("support:manage")))
    if not mesh.DEVICE_ID.fullmatch(device_id):
        raise Http404
    try:
        found = host.support_computers()
    except mesh.MeshError as exc:
        context = host.workspace(request, selected=device_id, found=(None, []))
        context.update(list_error=str(exc), error=str(exc))
        return host.render_workspace(request, "support/device.html", context, 503)
    context = host.workspace(request, selected=device_id, found=found)
    computer = context["computers"].get(device_id)
    if computer is None:
        raise Http404
    sites = context["sites"]
    shells = (
        [(mesh.POWERSHELL, "PowerShell"), (mesh.CMD, "Command Prompt")]
        if computer["windows"]
        else [(mesh.SHELL, "Mac shell")]
    )
    tab = request.POST.get("tab") or request.GET.get("tab")
    context.update(
        device=computer,
        shells=shells,
        shell=shells[0][0],
        can_send=_has_perm(request, "can_send_cmd"),
        can_save=toolbox.can_edit(request),
        toolbox=toolbox.for_device(computer),
        tab=tab if tab in TABS else "general",
        detail_url=reverse("support:device", args=[device_id]),
    )
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        command = request.POST.get("command", "")
        try:
            shell = int(request.POST.get("shell", ""))
        except ValueError:
            shell = 0
        if shell not in dict(shells):
            shell = shells[0][0]
        context.update(command=command, shell=shell)
        try:
            if getattr(settings, "DEMO", False):
                context["error"] = "Support computers can’t be changed in the demo."
                status = 403
            elif action == "remove":
                return _remove(request, computer)
            elif action == "rename":
                status = _rename(request, context, computer)
                if status is None:
                    return _back(request, computer, tab="general")
            elif action == "note":
                context["tab"] = "notes"
                status = _note(request, context, computer)
                if status is None:
                    return _back(request, computer, tab="notes")
            elif action == "save":
                status = _save_command(request, context, shells)
                if _wants_json(request):
                    saved = context.get("saved_command")
                    return _headers(
                        JsonResponse(
                            (
                                {"saved": saved.name, "id": saved.pk}
                                if saved
                                else {"error": context["error"]}
                            ),
                            status=status,
                        )
                    )
                context["tab"] = "commands"
            elif not computer["online"]:
                context["error"] = (
                    "This computer is offline. Ask them to open the support app."
                )
                status = 409
            elif action == "connect":
                return _connect(request, computer)
            elif action == "run":
                context["tab"] = "commands"
                if not command.strip() and request.POST.get("saved", "").isdigit():
                    saved = SupportCommand.objects.filter(
                        pk=request.POST["saved"], shell__in=dict(shells)
                    ).first()
                    if saved:
                        command, shell = saved.command, saved.shell
                        context.update(command=command, shell=shell)
                status = _run(request, context, computer, command, shell)
            elif action == "progress":
                context["tab"] = "monitoring"
                progress = (
                    install.windows_progress_command()
                    if computer["windows"]
                    else install.mac_progress_command()
                )
                status = _run(
                    request,
                    context,
                    computer,
                    progress,
                    shells[0][0],
                    timeout=15,
                    keep=False,
                    note="Checked the RMM agent install’s progress.",
                )
                context["progress"] = True
                if context.get("no_reply") and computer["windows"]:
                    context["output"] = BUSY
                elif context.get("no_reply"):
                    context["output"] = NO_REPLY
            elif action == "install":
                context["tab"] = "monitoring"
                status, result = _install(request, context, computer, sites)
                if status is None:
                    return _back(request, computer, sent=result, tab="monitoring")
            else:
                context["error"] = "Choose what you’d like to do."
                status = 400
        except mesh.MeshError as exc:
            context["error"] = str(exc)
            status = 503
        if _wants_json(request) and action == "run":
            run = context.get("run")
            return _headers(
                JsonResponse(
                    {"run": _run_json(run)} if run else {"error": context["error"]},
                    status=status,
                )
            )
    session = computer["session"]
    runs = list(reversed(session.runs.select_related("user")[:HISTORY]))
    for run in runs:
        run.prompt = PROMPTS.get(run.shell, "$")
    context.update(
        runs=runs,
        notes=list(session.notes.select_related("author")[:50]),
        events=host.timeline(
            device_id=computer["id"],
            code=computer["code"],
            session=session,
            first_seen=computer["first_seen"],
        ),
        sent=request.GET.get("sent"),
        existing=[] if computer["handoff"] else _existing_agents(request, computer),
        prompts=PROMPTS,
        no_reply_text=NO_REPLY,
    )
    return host.render_workspace(request, "support/device.html", context, status)


@never_cache
@require_http_methods(["GET"])
def screen(request, device_id):
    """The latest screen preview of a support computer."""
    if not _can_manage(request):
        raise Http404
    preview = screens.latest(device_id) if mesh.DEVICE_ID.fullmatch(device_id) else None
    if not preview or not screens.enabled():
        raise Http404
    response = HttpResponse(preview["jpeg"], content_type="image/jpeg")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response
