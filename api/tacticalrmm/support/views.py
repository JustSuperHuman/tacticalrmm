import hashlib
import ipaddress
import re
import time
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from logs.models import AuditLog
from tacticalrmm.constants import AuditActionType, AuditObjType
from tacticalrmm.permissions import _has_perm

from .forms import AccessCodeForm
from .models import (
    SupportAccessCode,
    SupportBranding,
    SupportInstaller,
    code_digest,
    generate_code,
)

GRANT_SECONDS = 20 * 60
INVALID_CODE = (
    "That code didn’t work. It may have expired. Ask your technician for a new one."
)
PLATFORMS = {
    "windows": {"name": "Windows", "audience": "For Windows PCs"},
    "macos": {"name": "Mac", "audience": "For Macs with Apple silicon or Intel"},
}


def installer_directory():
    return Path(
        getattr(settings, "SUPPORT_INSTALLER_DIR", Path(settings.EXE_DIR) / "support")
    ).resolve()


def _headers(response):
    # Same-origin referrers preserve Django's CSRF checks for native HTML forms.
    response["Referrer-Policy"] = "same-origin"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
    )
    return response


def _grant(request):
    grant = request.session.get("support_download", {})
    try:
        if float(grant.get("until", 0)) > time.time():
            code = SupportAccessCode.objects.filter(pk=grant.get("id")).first()
            if code and code.available:
                return code
    except (ValueError, TypeError, ValidationError, AttributeError):
        pass
    request.session.pop("support_download", None)
    return None


def _brand():
    saved = SupportBranding.load()
    logo_url = static("support/logo.png")
    if saved.has_logo:
        logo_url = f"{reverse('support:logo')}?v={int(saved.updated_at.timestamp())}"
    default_name = getattr(settings, "SUPPORT_BRAND_NAME", "Tactical RMM")
    return {
        "name": saved.name or default_name,
        "default_name": default_name,
        "email": saved.contact_email or getattr(settings, "SUPPORT_CONTACT_EMAIL", ""),
        "phone": saved.contact_phone,
        "phone_href": re.sub(r"[^\d+]", "", saved.contact_phone),
        "logo_url": logo_url,
        "custom_logo": saved.has_logo,
        "saved": saved,
    }


def public_url(request):
    """Where people go for help: SUPPORT_PUBLIC_URL when the support page has its own
    address (support.example.com), otherwise this server's own support page."""
    configured = getattr(settings, "SUPPORT_PUBLIC_URL", "").strip()
    return configured or request.build_absolute_uri(reverse("support:portal"))


def _device(request):
    # Only orders the download buttons; every installer stays available.
    agent = request.headers.get("User-Agent", "")
    if re.search(r"iPhone|iPad|iPod|Android", agent):
        return "mobile"
    if "Windows" in agent:
        return "windows"
    if "Macintosh" in agent or "Mac OS X" in agent:
        return "macos"
    return ""


def _context(request, **extra):
    grant = _grant(request)
    installers = {i.platform: i for i in SupportInstaller.objects.all()}
    device = _device(request)
    order = ("macos", "windows") if device == "macos" else ("windows", "macos")
    return {
        "brand": _brand(),
        "device": device,
        "known_device": device in PLATFORMS,
        "platforms": [
            {
                "key": key,
                "installer": installers.get(key),
                "recommended": key == device,
                **PLATFORMS[key],
            }
            for key in order
        ],
        "grant": grant,
        "grant_expiry": (
            datetime.fromtimestamp(
                request.session["support_download"]["until"], datetime_timezone.utc
            )
            if grant
            else None
        ),
        **extra,
    }


def client_address(value):
    """A normalized IP address, or None. IPv4 addresses mapped into IPv6 become IPv4."""
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def _rate_limit(request):
    # Atomic counters work across web workers with the existing Redis cache.
    identity = salted_hmac(
        "support.throttle",
        getattr(request, "_client_ip", request.META.get("REMOTE_ADDR", "")),
    ).hexdigest()
    now = int(time.time())
    for seconds, maximum in ((60, 5), (86400, 100)):
        key = f"support:attempt:{identity}:{seconds}:{now // seconds}"
        cache.add(key, 0, timeout=seconds + 1)
        try:
            count = cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=seconds + 1)
            count = 1
        if count > maximum:
            return seconds - now % seconds
    return 0


def _portal_response(request, error="", status=200):
    if request.headers.get("Accept") == "application/json":
        response = JsonResponse(
            {"error": error} if error else {"redirect": reverse("support:portal")},
            status=status,
        )
    else:
        response = render(
            request,
            "support/portal.html",
            _context(request, error=error),
            status=status,
        )
    return _headers(response)


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def portal(request):
    if request.method == "POST":
        retry_after = _rate_limit(request)
        if retry_after:
            response = _portal_response(
                request, "Too many tries. Wait a minute, then try again.", 429
            )
            response["Retry-After"] = retry_after
            return response
        form = AccessCodeForm(request.POST)
        if not form.is_valid():
            return _portal_response(request, "Enter all 9 digits of your code.", 400)
        code = SupportAccessCode.objects.filter(
            digest=code_digest(form.cleaned_data["code"])
        ).first()
        if not code or not code.available:
            return _portal_response(request, INVALID_CODE, 400)
        request.session["support_download"] = {
            "id": str(code.pk),
            "until": min(time.time() + GRANT_SECONDS, code.expires_at.timestamp()),
        }
        if request.headers.get("Accept") == "application/json":
            return _portal_response(request)
        return _headers(HttpResponseRedirect(reverse("support:portal")))
    return _portal_response(request)


@never_cache
@require_POST
def reset(request):
    request.session.pop("support_download", None)
    return _headers(HttpResponseRedirect(reverse("support:portal")))


@require_GET
def logo(request):
    saved = SupportBranding.load(with_logo=True)
    if not saved.has_logo:
        raise Http404
    response = HttpResponse(bytes(saved.logo), content_type=saved.logo_type)
    # Versioned URLs (?v=) change whenever the logo does.
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    # An SVG opened directly can't run script or load anything.
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
    )
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


def _open_installer(installer):
    if not re.fullmatch(r"[a-f0-9]{64}", installer.sha256):
        raise OSError("Invalid installer digest")
    root = installer_directory()
    path = (root / installer.storage_name).resolve()
    if not path.is_relative_to(root):
        raise OSError("Invalid installer path")
    stream = path.open("rb")
    try:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if stream.tell() != installer.size or not constant_time_compare(
            digest, installer.sha256
        ):
            raise OSError("Installer integrity check failed")
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


def _audit(request, code, action, message):
    AuditLog.objects.create(
        username=(
            request.user.username if request.user.is_authenticated else "Public support"
        ),
        action=action,
        object_type=AuditObjType.CORE,
        message=message,
        after_value={"support_invitation": str(code.pk), "site": code.site_id},
    )


@never_cache
@require_POST
def download(request, platform):
    if platform not in SupportInstaller.Platform.values:
        return _portal_response(request, "Choose the Windows or Mac version.", 404)
    code = _grant(request)
    if not code:
        return _portal_response(
            request, "Your download time ran out. Enter your code again.", 403
        )
    installer = SupportInstaller.objects.filter(platform=platform).first()
    if not installer:
        return _portal_response(
            request,
            "This download isn’t ready yet. Let your technician know.",
            503,
        )
    try:
        stream = _open_installer(installer)
    except OSError:
        return _portal_response(
            request,
            "This download isn’t available right now. Let your technician know.",
            503,
        )
    # Reserve a download atomically: concurrent requests cannot exceed the limit.
    updated = SupportAccessCode.objects.filter(
        pk=code.pk,
        revoked_at=None,
        expires_at__gt=timezone.now(),
        download_count__lt=F("max_downloads"),
    ).update(
        download_count=F("download_count") + 1,
        downloaded_from=client_address(
            getattr(request, "_client_ip", "") or request.META.get("REMOTE_ADDR")
        ),
        downloaded_at=timezone.now(),
    )
    if not updated:
        stream.close()
        return _portal_response(request, INVALID_CODE, 403)
    try:
        _audit(
            request,
            code,
            AuditActionType.FILE_TRANSFER,
            f"Downloaded the {PLATFORMS[platform]['name']} support app "
            f"(version {installer.version}).",
        )
    except Exception:
        stream.close()
        raise
    return _headers(
        FileResponse(stream, as_attachment=True, filename=installer.filename)
    )


def _can_manage(request):
    user = request.user
    return (
        user.is_authenticated
        and user.is_active
        and not user.is_installer_user
        and not user.agent_id
        and not user.block_dashboard_login
        and _has_perm(request, "can_use_mesh")
        and _has_perm(request, "can_install_agents")
    )


def _save_branding(request, form):
    saved = SupportBranding.load()
    saved.name = form.cleaned_data["name"].strip()
    saved.contact_email = form.cleaned_data["contact_email"]
    saved.contact_phone = form.cleaned_data["contact_phone"].strip()
    if form.cleaned_data["logo"]:
        saved.logo, saved.logo_type = form.cleaned_data["logo"]
    elif form.cleaned_data["remove_logo"]:
        saved.logo, saved.logo_type = b"", ""
    saved.save()
    AuditLog.objects.create(
        username=request.user.username,
        action=AuditActionType.MODIFY,
        object_type=AuditObjType.CORE,
        message="Updated the support page branding.",
        after_value={
            "name": saved.name,
            "contact_email": saved.contact_email,
            "contact_phone": saved.contact_phone,
            "custom_logo": saved.has_logo,
        },
    )


def _create_code(request, sites, form):
    site = get_object_or_404(sites, pk=form.cleaned_data["site"])
    for attempt in range(5):
        raw_code = generate_code()
        try:
            with transaction.atomic():
                code = SupportAccessCode.objects.create(
                    digest=code_digest(raw_code),
                    last_three=raw_code[-3:],
                    site=site,
                    created_by=request.user,
                    label=form.cleaned_data["label"],
                    expires_at=timezone.now()
                    + timedelta(minutes=form.cleaned_data["lifetime"]),
                    max_downloads=form.cleaned_data["max_downloads"],
                )
                _audit(
                    request,
                    code,
                    AuditActionType.ADD,
                    "Created a support download code.",
                )
            return raw_code, code
        except IntegrityError:
            if attempt == 4:
                raise
