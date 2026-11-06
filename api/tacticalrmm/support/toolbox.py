"""The command toolbox: saved commands technicians run on support computers."""

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.permissions import _has_perm

from . import host
from .models import SupportCommand
from .views import _brand, _can_manage, _headers

MAX_COMMAND = 60_000
Shell = SupportCommand.Shell

# Read-only checks that work without administrator rights, so they also work through
# the Windows support app, which runs as the signed-in person.
STARTER = [
    (
        "System summary",
        Shell.POWERSHELL,
        "Get-CimInstance Win32_OperatingSystem | Select-Object CSName, Caption, "
        "Version, OSArchitecture, LastBootUpTime | Format-List",
    ),
    (
        "Disk space",
        Shell.POWERSHELL,
        "Get-PSDrive -PSProvider FileSystem | Where-Object Used | Select-Object Name, "
        "@{n='Used GB';e={[math]::Round($_.Used/1GB,1)}}, "
        "@{n='Free GB';e={[math]::Round($_.Free/1GB,1)}} | Format-Table -AutoSize",
    ),
    ("Network settings", Shell.CMD, "ipconfig /all"),
    (
        "Internet connection test",
        Shell.POWERSHELL,
        "Test-NetConnection www.microsoft.com -Port 443 | Select-Object "
        "ComputerName, RemoteAddress, TcpTestSucceeded | Format-List",
    ),
    (
        "Installed apps",
        Shell.POWERSHELL,
        "Get-ItemProperty "
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
        "-ErrorAction SilentlyContinue | Where-Object DisplayName | Sort-Object "
        "DisplayName -Unique | Format-Table DisplayName, DisplayVersion -AutoSize | "
        "Out-String -Width 200",
    ),
    (
        "Restart pending",
        Shell.POWERSHELL,
        "$keys = 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based "
        "Servicing\\RebootPending', 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\"
        "CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired'\n"
        "if ($keys | Where-Object { Test-Path $_ }) { 'A restart is pending.' } "
        "else { 'No restart pending.' }",
    ),
    (
        "Busiest apps",
        Shell.POWERSHELL,
        "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 "
        "Name, Id, @{n='Memory MB';e={[math]::Round($_.WorkingSet64/1MB)}} | "
        "Format-Table -AutoSize",
    ),
    (
        "System summary",
        Shell.SHELL,
        "sw_vers; uname -m; uptime\nsystem_profiler SPHardwareDataType | "
        "grep -E 'Model Name|Chip|Processor Name|Memory'",
    ),
    ("Disk space", Shell.SHELL, "df -h / /System/Volumes/Data"),
    ("Network settings", Shell.SHELL, "ifconfig | grep -E '^[a-z0-9]+:|inet '"),
    (
        "Internet connection test",
        Shell.SHELL,
        "ping -c 3 1.1.1.1\ncurl -sS -o /dev/null -w 'HTTPS: %{http_code} in "
        "%{time_total}s\\n' https://www.apple.com",
    ),
    ("Installed apps", Shell.SHELL, "ls /Applications"),
    ("Signed-in user", Shell.SHELL, "stat -f %Su /dev/console"),
    ("Busiest apps", Shell.SHELL, "ps -Ao pid,%mem,comm -m | head -11"),
]


def can_edit(request):
    return _has_perm(request, "can_manage_scripts")


def for_device(device):
    shells = (Shell.POWERSHELL, Shell.CMD) if device["windows"] else (Shell.SHELL,)
    return list(SupportCommand.objects.filter(shell__in=shells))


def clean(data, shells):
    """Return (name, shell, command, error) from posted data."""
    name = (data.get("name") or "").strip()
    command = data.get("command") or ""
    try:
        shell = int(data.get("shell", ""))
    except ValueError:
        shell = 0
    if not name or len(name) > 100:
        return name, shell, command, "Give the command a name of up to 100 characters."
    if shell not in shells:
        return name, shell, command, "Choose where the command runs."
    if not command.strip():
        return name, shell, command, "Enter the command to save."
    if len(command) > MAX_COMMAND:
        return name, shell, command, "That command is too long to save."
    return name, shell, command, ""


def save(request, name, shell, command, existing=None):
    item = existing or SupportCommand(created_by=request.user)
    item.name, item.shell, item.command = name, shell, command
    item.save()
    AuditLog.objects.create(
        username=request.user.username,
        action=AuditActionType.MODIFY if existing else AuditActionType.ADD,
        object_type=AuditObjType.CORE,
        message=f"{'Updated' if existing else 'Saved'} “{name}” in the support toolbox.",
        after_value={"name": name, "shell": shell, "command": command},
    )
    return item


def _redirect(query):
    return _headers(HttpResponseRedirect(f"{reverse('support:toolbox')}?{query}"))


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def toolbox(request):
    if not _can_manage(request):
        return _headers(HttpResponseRedirect(reverse("support:manage")))
    editable = can_edit(request)
    items = SupportCommand.objects.all()
    context = {
        "brand": _brand(),
        "dashboard_url": settings.CORS_ORIGIN_WHITELIST[0].rstrip("/"),
        "page": "toolbox",
        "groups": host.nav_groups(),
        "portal_url": host.public_url(request),
        "can_edit": editable,
        "shells": Shell.choices,
        "form": {"shell": Shell.POWERSHELL},
    }
    status = 200
    editing = None
    edit_id = request.POST.get("id") or request.GET.get("edit")
    if edit_id:
        editing = items.filter(pk=edit_id if str(edit_id).isdigit() else 0).first()
        if editing is None:
            raise Http404
        context["form"] = {
            "id": editing.pk,
            "name": editing.name,
            "shell": editing.shell,
            "command": editing.command,
        }
    if request.method == "POST":
        action = request.POST.get("action")
        if getattr(settings, "DEMO", False):
            context["error"] = "The toolbox can’t be changed in the demo."
            status = 403
        elif not editable:
            context["error"] = (
                "You need the Manage scripts permission to change the toolbox."
            )
            status = 403
        elif action == "save":
            name, shell, command, error = clean(request.POST, Shell.values)
            if error:
                context["error"] = error
                context["form"] = {
                    "id": editing.pk if editing else None,
                    "name": name,
                    "shell": shell,
                    "command": command,
                }
                status = 400
            else:
                save(request, name, shell, command, editing)
                return _redirect("saved=1")
        elif action == "delete" and editing:
            AuditLog.objects.create(
                username=request.user.username,
                action=AuditActionType.DELETE,
                object_type=AuditObjType.CORE,
                message=f"Deleted “{editing.name}” from the support toolbox.",
                after_value={"name": editing.name, "command": editing.command},
            )
            editing.delete()
            return _redirect("deleted=1")
        elif action == "starter":
            have = set(items.values_list("name", "shell"))
            added = [
                SupportCommand(name=n, shell=s, command=c, created_by=request.user)
                for n, s, c in STARTER
                if (n, s) not in have
            ]
            SupportCommand.objects.bulk_create(added)
            AuditLog.objects.create(
                username=request.user.username,
                action=AuditActionType.ADD,
                object_type=AuditObjType.CORE,
                message=f"Added {len(added)} starter commands to the support toolbox.",
            )
            return _redirect("starter=1")
        else:
            context["error"] = "Choose what you’d like to do."
            status = 400
    context.update(
        editing=editing,
        windows=[i for i in items if i.windows],
        mac=[i for i in items if not i.windows],
        notice={
            "saved": "Saved. It’s in the toolbox on every session’s Commands tab.",
            "deleted": "Command deleted.",
            "starter": "Starter commands added. Edit them to suit your clients.",
        },
    )
    for key, text in context.pop("notice").items():
        if request.GET.get(key) == "1":
            context["notice"] = text
    return _headers(render(request, "support/toolbox.html", context, status=status))
