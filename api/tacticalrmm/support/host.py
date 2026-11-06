"""The technician workspace: session groups on the left, the live session list, and
the selected session beside it.

A session is either a code that's waiting for someone to use it, or a computer
connected through the support app. A computer that connects from the address a code
was downloaded from, soon after the download, takes over that code's session.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from clients.models import Site
from core.utils import get_core_settings
from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.permissions import _has_perm

from . import mesh, meshapps, screens
from .forms import BrandingForm, CreateAccessCodeForm
from .models import (
    SupportAccessCode,
    SupportBranding,
    SupportHandoff,
    SupportInstaller,
    SupportSession,
)
from .views import (
    PLATFORMS,
    _audit,
    _brand,
    _can_manage,
    _create_code,
    _headers,
    _save_branding,
    client_address,
    public_url,
)

# (key, label, icon)
GROUPS = (
    ("all", "All sessions", "sessions"),
    ("mine", "My sessions", "user"),
    ("connected", "Connected", "connected"),
    ("waiting", "Waiting to connect", "waiting"),
    ("offline", "Offline", "offline"),
    ("monitoring", "Moving to monitoring", "activity"),
    ("past", "Past codes", "history"),
)
GROUP_KEYS = {key for key, _, _ in GROUPS}
# A computer belongs to a code's session if it first connects from the download's
# address within this long after the download.
MATCH_WITHIN = timedelta(hours=4)
# Allow for the two servers' clocks disagreeing a little.
CLOCK_SLACK = timedelta(minutes=2)
TIMELINE_LENGTH = 60
PUBLIC_USER = "Public support"


def dashboard_url():
    return settings.CORS_ORIGIN_WHITELIST[0].rstrip("/")


def allow_meshcentral(response):
    """Let Join, Terminal and Files redirect to MeshCentral.

    Browsers check a form's redirect against the page that sent the form, and the
    workspace swaps sessions in place, so every workspace page allows MeshCentral as
    a form target.
    """
    try:
        site = urlsplit(get_core_settings().mesh_site or "")
    except Exception:
        return response
    if site.scheme == "https" and site.netloc:
        response["Content-Security-Policy"] = response[
            "Content-Security-Policy"
        ].replace("form-action 'self'", f"form-action 'self' https://{site.netloc}")
    return response


def nav_groups():
    """Sidebar links for pages outside the session list, which skip the counts."""
    return [
        {
            "key": key,
            "label": label,
            "icon": icon,
            "count": None,
            "href": with_group(reverse("support:manage"), key),
        }
        for key, label, icon in GROUPS
    ]


def group_from(request):
    group = request.GET.get("group") or request.POST.get("group") or "all"
    return group if group in GROUP_KEYS else "all"


def with_group(url, group, **query):
    if group != "all":
        query = {"group": group, **query}
    query = {k: v for k, v in query.items() if v}
    return f"{url}?{urlencode(query)}" if query else url


def visible_codes(request, sites):
    codes = SupportAccessCode.objects.filter(site__in=sites).select_related(
        "site__client", "created_by"
    )
    if not _has_perm(request, "is_superuser"):
        codes = codes.filter(created_by=request.user)
    return codes


def site_name(site):
    return f"{site.client.name} / {site.name}" if site else ""


def _matching_code(device):
    address = client_address(device.get("ip"))
    first = device.get("first_seen")
    if not address or not first:
        return None
    return (
        SupportAccessCode.objects.filter(
            downloaded_from=address,
            downloaded_at__lte=first + CLOCK_SLACK,
            downloaded_at__gte=first - MATCH_WITHIN,
        )
        .order_by("-downloaded_at")
        .first()
    )


def sessions_for(devices):
    """Each computer's session, created the first time it's seen."""
    ids = [d["id"] for d in devices]
    found = {
        s.device_id: s
        for s in SupportSession.objects.filter(device_id__in=ids).select_related(
            "code__site__client", "code__created_by"
        )
    }
    for device in devices:
        if device["id"] not in found:
            code = _matching_code(device)
            found[device["id"]], _ = SupportSession.objects.get_or_create(
                device_id=device["id"],
                defaults={"code": code, "name": code.label if code else ""},
            )
    return found


def describe(device, session, handoff, user, visible):
    """What the list and the session page show about a computer.

    Session names and sites come from codes and installs in client sites, so
    technicians limited to other sites see only the computer's own name.
    """
    code = session.code if session else None
    agent = handoff.find_agent() if handoff else None
    site = handoff.site if handoff else (code.site if code else None)
    named = bool(session and session.name) and (code is None or code.site_id in visible)
    monitoring = ""
    if agent:
        monitoring = "monitored"
    elif handoff:
        monitoring = "stalled" if handoff.stalled else "installing"
    groups = ["all", "connected" if device["online"] else "offline"]
    if handoff:
        groups.append("monitoring")
    if (code and code.created_by_id == user.pk) or (
        handoff and handoff.requested_by_id == user.pk
    ):
        groups.append("mine")
    return dict(
        device,
        session=session,
        code=code if code and code.site_id in visible else None,
        handoff=handoff,
        agent=agent,
        title=session.name if named else device["name"],
        named=named,
        site=site if site and site.pk in visible else None,
        monitoring=monitoring,
        groups=groups,
    )


def _device_row(device):
    who = ", ".join(device["users"])
    details = [device["name"] if device["named"] else "", who, device["os"]]
    return {
        "kind": "device",
        "id": device["id"],
        "url": reverse("support:device", args=[device["id"]]),
        "title": device["title"],
        "detail": " · ".join(d for d in details if d) or "Unknown system",
        "platform": device["platform"],
        "online": device["online"],
        "since": device["connected_at"] if device["online"] else None,
        "monitoring": device["monitoring"],
        "screen_at": device["screen_at"],
        "site": site_name(device["site"]),
        "groups": device["groups"],
        "search": " ".join(
            [device["title"], device["name"], who, device["os"]]
            + [site_name(device["site"])]
        ).lower(),
        # Changes when anything shown in the session's header changes.
        # The first screen picture counts. Later ones are swapped in by the browser.
        "version": f"{device['online']}-{device['monitoring']}-{bool(device['screen_at'])}",
    }


def _code_row(code, user, waiting):
    groups = ["all", "waiting"] if waiting else ["past"]
    if waiting and code.created_by_id == user.pk:
        groups.append("mine")
    return {
        "kind": "code",
        "id": str(code.pk),
        "url": reverse("support:code", args=[code.pk]),
        "title": code.label or "Support session",
        "detail": f"Code ending {code.last_three} · {site_name(code.site)}",
        "platform": "code",
        "status": code.status,
        "waiting": waiting,
        "expires": code.expires_at,
        "created": code.created_at,
        "downloads": code.download_count,
        "groups": groups,
        "search": " ".join(
            [code.label, code.last_three, site_name(code.site), code.status]
        ).lower(),
        "version": f"{code.status}-{code.download_count}",
    }


def support_computers():
    """(the MeshCentral support group, its computers). Raises MeshError."""
    group = mesh.find_group()
    return group, (mesh.list_devices(group) if group else [])


def workspace(request, selected="", found=None):
    """The sidebar and session list. A MeshCentral problem becomes a message in the
    list, so codes keep working while it's down. `found` is support_computers()'s
    answer when the caller already has it."""
    user = request.user
    sites = Site.objects.filter_by_role(user).select_related("client")
    visible = set(sites.values_list("pk", flat=True))
    group = group_from(request)
    list_error = ""
    if found is None:
        try:
            found = support_computers()
        except mesh.MeshError as exc:
            found, list_error = (None, []), str(exc)
    mesh_group, devices = found
    sessions = sessions_for(devices) if devices else {}
    handoffs = {
        h.device_id: h
        for h in SupportHandoff.objects.filter(
            device_id__in=[d["id"] for d in devices], finished_at=None
        ).select_related("site__client")
    }
    computers = {
        d["id"]: describe(
            d, sessions.get(d["id"]), handoffs.get(d["id"]), user, visible
        )
        for d in devices
    }
    previews = screens.enabled()
    group_consent = (mesh_group or {}).get("consent", 0)
    for computer in computers.values():
        computer["previews"] = previews and not screens.asks_consent(
            computer, group_consent
        )
        if computer["previews"]:
            # The open session's picture is kept fresher than the rest of the list.
            watching = computer["id"] in (selected, request.GET.get("open"))
            screens.request(
                computer,
                group_consent,
                screens.OPEN_SECONDS if watching else screens.LIST_SECONDS,
            )
        computer["screen_at"] = (
            screens.taken_at(computer["id"]) if computer["previews"] else None
        )
        computer["screen_time"] = (
            datetime.fromtimestamp(computer["screen_at"], dt_timezone.utc)
            if computer["screen_at"]
            else None
        )
    rows = [_device_row(c) for c in computers.values()]
    # Newest connection first: that's usually the person waiting on the phone.
    rows.sort(
        key=lambda r: (not r["online"], -(r["since"].timestamp() if r["since"] else 0))
    )
    now = timezone.now()
    codes = visible_codes(request, sites)
    active = Q(
        revoked_at=None, expires_at__gt=now, download_count__lt=F("max_downloads")
    )
    waiting = list(codes.filter(active, sessions__isnull=True)[:100])
    online = [r for r in rows if r["online"]]
    rows = (
        online
        + [_code_row(c, user, True) for c in waiting]
        + [r for r in rows if not r["online"]]
        + [_code_row(c, user, False) for c in codes.exclude(active)[:50]]
    )
    counts = {key: 0 for key in GROUP_KEYS}
    for row in rows:
        row["selected"] = row["id"] == selected
        row["hidden"] = group not in row["groups"]
        row["href"] = with_group(row["url"], group)
        for key in row["groups"]:
            counts[key] += 1
    return {
        "brand": _brand(),
        "dashboard_url": dashboard_url(),
        "portal_url": public_url(request),
        "rows": rows,
        "computers": computers,
        "screen_previews": previews,
        "group": group,
        "groups": [
            {
                "key": key,
                "label": label,
                "icon": icon,
                "count": counts[key],
                "href": with_group(reverse("support:manage"), key),
            }
            for key, label, icon in GROUPS
        ],
        "group_label": dict((k, label) for k, label, _ in GROUPS)[group],
        "list_error": list_error,
        "mesh_group": mesh_group,
        "can_setup": _has_perm(request, "can_edit_core_settings"),
        "sites": sites,
        "visible_sites": visible,
        "create_form": CreateAccessCodeForm(
            initial={"lifetime": 30, "max_downloads": 5}
        ),
        "selected": selected,
        "workspace": True,
    }


def timeline(device_id=None, code=None, session=None, first_seen=None):
    """What happened in a session, newest first."""
    match = Q()
    if device_id:
        match |= Q(after_value__support_device=device_id)
    if code:
        match |= Q(after_value__support_invitation=str(code.pk))
    events = []
    if match:
        for entry in AuditLog.objects.filter(match).order_by("-entry_time")[
            :TIMELINE_LENGTH
        ]:
            events.append(
                {
                    "at": entry.entry_time,
                    "who": (
                        "Support page"
                        if entry.username == PUBLIC_USER
                        else entry.username
                    ),
                    "text": entry.message or "",
                    "kind": {
                        AuditActionType.REMOTE_SESSION: "connect",
                        AuditActionType.EXEC_COMMAND: "command",
                        AuditActionType.AGENT_INSTALL: "install",
                        AuditActionType.FILE_TRANSFER: "download",
                        AuditActionType.DELETE: "end",
                    }.get(entry.action, "event"),
                }
            )
    if session:
        for note in session.notes.select_related("author")[:TIMELINE_LENGTH]:
            events.append(
                {
                    "at": note.created_at,
                    "who": note.author.username if note.author else "",
                    "text": note.text,
                    "kind": "note",
                }
            )
    if first_seen:
        events.append(
            {
                "at": first_seen,
                "who": "",
                "text": "The support app connected for the first time.",
                "kind": "online",
            }
        )
    return sorted(events, key=lambda e: e["at"], reverse=True)[:TIMELINE_LENGTH]


def render_workspace(request, template, context, status=200):
    response = render(request, template, context, status=status)
    return allow_meshcentral(_headers(response))


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def sessions(request):
    if not _can_manage(request):
        signed_in = request.user.is_authenticated
        return _headers(
            render(
                request,
                "support/signin.html",
                {
                    "brand": _brand(),
                    "dashboard_url": dashboard_url(),
                    "portal_url": public_url(request),
                    "signed_in": signed_in,
                },
                status=403 if signed_in else 401,
            )
        )
    status = 200
    extra = {}
    if request.method == "POST":
        action = request.POST.get("action")
        sites = Site.objects.filter_by_role(request.user).select_related("client")
        if getattr(settings, "DEMO", False):
            extra["error"] = "Support codes can’t be made or changed in the demo."
            status = 403
        elif action == "revoke":
            try:
                code = get_object_or_404(
                    visible_codes(request, sites), pk=request.POST.get("invitation")
                )
            except ValidationError:
                raise Http404 from None
            if not code.revoked_at:
                code.revoked_at = timezone.now()
                code.save(update_fields=["revoked_at"])
                _audit(
                    request,
                    code,
                    AuditActionType.MODIFY,
                    "Revoked a support download code.",
                )
            return _headers(
                HttpResponseRedirect(
                    with_group(
                        reverse("support:code", args=[code.pk]), group_from(request)
                    )
                )
            )
        elif action == "create":
            form = CreateAccessCodeForm(request.POST)
            if form.is_valid():
                raw_code, code = _create_code(request, sites, form)
                context = workspace(request, selected=str(code.pk))
                context.update(
                    code_context(request, code),
                    new_code=" ".join(raw_code[i : i + 3] for i in range(0, 9, 3)),
                    new_lifetime=dict(form.fields["lifetime"].choices)[
                        form.cleaned_data["lifetime"]
                    ],
                )
                return render_workspace(request, "support/code.html", context)
            extra.update(
                error="Check the session details and try again.",
                create_form=form,
                show_new=True,
            )
            status = 400
        else:
            extra["error"] = "Choose what you’d like to do."
            status = 400
    context = workspace(request)
    context.update(
        extra,
        removed=request.GET.get("removed"),
        show_new=extra.get("show_new") or request.GET.get("new") == "1",
    )
    return render_workspace(request, "support/sessions.html", context, status)


def code_context(request, code):
    linked = list(code.sessions.all())
    return {
        "code": code,
        "code_site": site_name(code.site),
        "linked": [
            {"id": s.device_id, "url": reverse("support:device", args=[s.device_id])}
            for s in linked
        ],
        "events": timeline(code=code),
        "detail_url": reverse("support:code", args=[code.pk]),
    }


@never_cache
@csrf_protect
@require_http_methods(["GET"])
def code(request, code_id):
    if not _can_manage(request):
        return _headers(HttpResponseRedirect(reverse("support:manage")))
    sites = Site.objects.filter_by_role(request.user)
    try:
        found = get_object_or_404(visible_codes(request, sites), pk=code_id)
    except ValidationError:
        raise Http404 from None
    context = workspace(request, selected=str(found.pk))
    context.update(code_context(request, found))
    # The computers it brought in, by their own names.
    names = {key: c["name"] for key, c in context["computers"].items()}
    for item in context["linked"]:
        item["title"] = names.get(item["id"])
    context["linked"] = [i for i in context["linked"] if i["title"]]
    return render_workspace(request, "support/code.html", context)


@never_cache
def devices(request):
    """The old Devices page: its computers are the Connected group now."""
    return _headers(
        HttpResponseRedirect(with_group(reverse("support:manage"), "connected"))
    )


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def setup(request):
    """Support apps, the MeshCentral support group and the support page's branding."""
    if not _can_manage(request):
        return _headers(HttpResponseRedirect(reverse("support:manage")))
    can_brand = _has_perm(request, "can_edit_core_settings")
    brand = _brand()
    installers = {i.platform: i for i in SupportInstaller.objects.all()}
    context = {
        "brand": brand,
        "dashboard_url": dashboard_url(),
        "can_brand": can_brand,
        "platforms": [
            {"key": key, "installer": installers.get(key), **PLATFORMS[key]}
            for key in ("windows", "macos")
        ],
        "group_name": mesh.group_name(),
        "branding_saved": request.GET.get("saved") == "branding",
        "group_created": request.GET.get("saved") == "group",
        "previews_saved": request.GET.get("saved") == "previews",
        "apps_saved": request.GET.get("saved") == "apps",
        "page": "settings",
        "groups": nav_groups(),
        "portal_url": public_url(request),
        "screen_previews": brand["saved"].screen_previews,
        "previews_need_consent": False,
    }
    status = 200
    try:
        context["mesh_group"] = mesh.find_group()
    except mesh.MeshError as exc:
        context["group_error"] = str(exc)
    context["previews_need_consent"] = screens.asks_consent(
        {}, (context.get("mesh_group") or {}).get("consent", 0)
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if getattr(settings, "DEMO", False):
            context["error"] = "Support settings can’t be changed in the demo."
            status = 403
        elif not can_brand:
            context["error"] = (
                "You need permission to edit global settings to change these."
            )
            status = 403
        elif action == "branding":
            form = BrandingForm(request.POST, request.FILES)
            if form.is_valid():
                _save_branding(request, form)
                return _headers(
                    HttpResponseRedirect(
                        f"{reverse('support:settings')}?saved=branding"
                    )
                )
            context["branding_form"] = form
            status = 400
        elif action == "apps":
            try:
                results = meshapps.publish()
            except (meshapps.Unavailable, mesh.MeshError) as exc:
                context["error"] = f"Couldn’t get the support apps. {exc}"
                status = 503
            else:
                AuditLog.objects.create(
                    username=request.user.username,
                    action=AuditActionType.MODIFY,
                    object_type=AuditObjType.CORE,
                    message="Published unsigned support apps from MeshCentral.",
                    after_value=results,
                )
                return _headers(
                    HttpResponseRedirect(f"{reverse('support:settings')}?saved=apps")
                )
        elif action == "previews":
            saved = SupportBranding.load()
            saved.screen_previews = request.POST.get("screen_previews") == "on"
            # A new row on servers where branding was never saved; the logo stays put.
            saved.save()
            AuditLog.objects.create(
                username=request.user.username,
                action=AuditActionType.MODIFY,
                object_type=AuditObjType.CORE,
                message=(
                    "Turned screen previews "
                    f"{'on' if saved.screen_previews else 'off'} for remote support."
                ),
            )
            return _headers(
                HttpResponseRedirect(f"{reverse('support:settings')}?saved=previews")
            )
        elif action == "group":
            try:
                if not context.get("mesh_group"):
                    mesh.create_group()
                    AuditLog.objects.create(
                        username=request.user.username,
                        action=AuditActionType.ADD,
                        object_type=AuditObjType.CORE,
                        message="Created the MeshCentral support group.",
                        after_value={"group": mesh.group_name()},
                    )
                return _headers(
                    HttpResponseRedirect(f"{reverse('support:settings')}?saved=group")
                )
            except mesh.MeshError as exc:
                context["error"] = str(exc)
                status = 503
        else:
            context["error"] = "Choose what you’d like to do."
            status = 400
    if can_brand and "branding_form" not in context:
        saved = brand["saved"]
        context["branding_form"] = BrandingForm(
            initial={
                "name": saved.name,
                "contact_email": saved.contact_email,
                "contact_phone": saved.contact_phone,
            }
        )
    return _headers(render(request, "support/settings.html", context, status=status))
