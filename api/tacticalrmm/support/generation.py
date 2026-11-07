"""Generate the packages used by Support and Access. Raw clients are payloads only."""

import hashlib
import io
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.core import signing
from django.db import transaction
from django.http import FileResponse

from core.utils import get_core_settings

from . import mesh, meshapps
from .installer.builder import BuildError, Payload, build_msi
from .installer.compatibility import verify
from .models import SupportBranding, SupportInstaller, SupportPayload

MAX_BYTES = 256 * 1024 * 1024


def _fetch(url):
    if urlsplit(url).scheme != "https":
        raise BuildError("Installer clients must be downloaded over HTTPS.")
    try:
        with requests.get(url, stream=True, timeout=(15, 120)) as response:
            if response.status_code != 200:
                raise BuildError(
                    "The client download is unavailable. Try generating the installer again later."
                )
            chunks, size = [], 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise BuildError("The client download is larger than expected.")
                chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException as exc:
        raise BuildError(
            "The server could not download the client securely. Check its internet connection."
        ) from exc


def payload_directory():
    from .views import installer_directory

    directory = installer_directory() / "payloads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _payload_url(digest):
    base = (
        getattr(settings, "SUPPORT_INSTALLER_BASE_URL", "")
        or f"https://{settings.ALLOWED_HOSTS[0]}"
    )
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise BuildError(
            "Set SUPPORT_INSTALLER_BASE_URL to the server's public HTTPS address."
        )
    return f"{base.rstrip('/')}/support/payload/{digest}/"


def store_payload(data):
    if not data.startswith(b"MZ") or not 0 < len(data) <= MAX_BYTES:
        raise BuildError("The downloaded client is not a Windows executable.")
    digest = hashlib.sha256(data).hexdigest()
    root = payload_directory()
    target = root / f"{digest}.exe"
    if not target.is_file():
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as temporary:
            staged = Path(temporary.name)
            temporary.write(data)
        try:
            staged.chmod(0o640)
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)
    SupportPayload.objects.get_or_create(sha256=digest, defaults={"size": len(data)})
    return Payload(_payload_url(digest), digest)


def _cache_directory():
    from .views import installer_directory

    return installer_directory() / "build-cache"


def support_windows(output, group):
    base = get_core_settings().mesh_site.rstrip("/")
    if urlsplit(base).scheme != "https":
        raise BuildError(
            "Set MeshCentral's public HTTPS address before generating installers."
        )
    # Stage unconfigured clients; no privileged MeshCentral cookie reaches an MSI.
    legacy_data = meshapps._download(f"{base}/meshagents?id=3")
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "legacy.exe"
        candidate.write_bytes(legacy_data)
        try:
            verify(candidate)
        except ValueError as exc:
            raise BuildError(
                "The Windows 7 client failed its compatibility checks. Update the MeshCentral client before generating an installer."
            ) from exc
    legacy = store_payload(legacy_data)
    modern = store_payload(meshapps._download(f"{base}/meshagents?id=4"))
    config = meshapps._download(
        f"{base}/meshsettings?{urlencode({'id': group['short_id'], 'installflags': 1})}"
    )
    entries = dict(
        line.split("=", 1)
        for line in config.decode("utf-8-sig").splitlines()
        if "=" in line
    )
    if (
        not all(entries.get(key) for key in ("MeshID", "MeshServer", "ServerID"))
        or entries.get("InstallFlags") != "1"
    ):
        raise BuildError(
            "MeshCentral did not return a configured run-only support client."
        )
    brand = SupportBranding.load().name or getattr(
        settings, "SUPPORT_BRAND_NAME", "JustConnect"
    )
    return build_msi(
        output,
        legacy=legacy,
        modern=modern,
        mesh_settings=config,
        name=f"{brand} Support",
        publisher=brand,
        version=getattr(settings, "SUPPORT_INSTALLER_VERSION", "1.0.0"),
        cache=_cache_directory(),
    )


def publish(platforms=None, replace_signed=False):
    from .views import installer_directory

    selected = set(platforms or ("windows", "macos"))
    if "windows7" in selected:
        selected.remove("windows7")
        selected.add("windows")
    if not selected or selected - {"windows", "macos"}:
        raise BuildError("Choose Windows or Mac installers.")
    group = mesh.find_group()
    if not group:
        raise BuildError("Create the support group first.")
    root = installer_directory()
    root.mkdir(parents=True, exist_ok=True)
    records, result = {}, {}
    for platform in ("windows", "macos"):
        if platform not in selected:
            continue
        existing = SupportInstaller.objects.filter(
            platform=platform, generated=True, signed=True
        ).first()
        if existing and not replace_signed:
            result[platform] = "kept signed"
            continue
        with tempfile.TemporaryDirectory() as temporary:
            if platform == "windows":
                output = support_windows(Path(temporary) / "support.msi", group)
                data = output.read_bytes()
            else:
                # MeshCentral generates a configured, universal flat Mac package.
                data = meshapps.fetch(
                    get_core_settings().mesh_site, group["short_id"], ["macos"]
                )["macos"]
        installer = SupportInstaller(
            platform=platform,
            version=getattr(settings, "SUPPORT_INSTALLER_VERSION", "1.0.0"),
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            signer="",
            signed=False,
            generated=True,
        )
        meshapps._store(root, installer, data)
        records[platform] = installer
        result[platform] = "generated"
    with transaction.atomic():
        for platform, installer in records.items():
            SupportInstaller.objects.update_or_create(
                platform=platform,
                defaults={
                    "version": installer.version,
                    "sha256": installer.sha256,
                    "size": installer.size,
                    "signer": "",
                    "signed": False,
                    "generated": True,
                },
            )
    return result


def _rmm_payload(goarch, codesign_token):
    from agents.utils import get_agent_url
    from tacticalrmm.constants import AgentPlat

    url = get_agent_url(goarch=goarch, plat=AgentPlat.WINDOWS, token=codesign_token)
    key = "generated-msi-client:" + hashlib.sha256(url.encode()).hexdigest()
    digest = cache.get(key)
    if (
        digest
        and SupportPayload.objects.filter(pk=digest).exists()
        and (payload_directory() / f"{digest}.exe").is_file()
    ):
        return Payload(_payload_url(digest), digest)
    # Keep upstream's service setup and cleanup contract. The native MSI runs
    # this verified, unconfigured payload hidden, then enrolls it without a shell.
    # Old innoextract versions cannot parse current upstream installer headers.
    data = _fetch(url)
    if goarch == "386":
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "client.exe"
            candidate.write_bytes(data)
            try:
                verify(candidate)
            except ValueError as exc:
                raise BuildError(
                    "The Windows 7 monitoring setup failed its binary compatibility checks."
                ) from exc
    payload = store_payload(data)
    cache.set(key, payload.sha256, 3600)
    return payload


def monitoring_response(
    *, client, site, agent_type, rdp, ping, power, token, api, file_name, goarch=None
):
    from core.utils import token_is_valid

    parsed = urlsplit(api)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise BuildError("Use the server's HTTPS API address for the installer.")
    if agent_type not in ("server", "workstation"):
        raise BuildError("Choose Workstation or Server.")
    client, site = int(client), int(site)
    codesign_token, _ = token_is_valid()
    legacy, modern = _rmm_payload("386", codesign_token), _rmm_payload(
        "amd64", codesign_token
    )
    arguments = [
        "-m",
        "install",
        "--api",
        api,
        "--client-id",
        str(client),
        "--site-id",
        str(site),
        "--agent-type",
        agent_type,
        "--auth",
        token,
    ]
    for enabled, flag in ((rdp, "--rdp"), (ping, "--ping"), (power, "--power")):
        if str(enabled) in ("1", "True", "true"):
            arguments.append(flag)
    name = re.sub(
        r"\.(exe|msi|ps1)$",
        "",
        Path(str(file_name).replace("\\", "/")).name,
        flags=re.I,
    )
    name = re.sub(r"[^A-Za-z0-9._ -]", "-", name)[:120] or "JustConnect-Monitoring"
    with tempfile.TemporaryDirectory(prefix="justconnect-monitoring-") as temporary:
        output = build_msi(
            Path(temporary) / "monitoring.msi",
            legacy=legacy,
            modern=modern,
            mode="monitoring",
            arguments=subprocess.list2cmdline(arguments),
            name="JustConnect Monitoring Agent",
            version=settings.LATEST_AGENT_VER,
            cache=_cache_directory(),
        )
        data = output.read_bytes()
    response = FileResponse(
        io.BytesIO(data),
        as_attachment=True,
        filename=name + ".msi",
        content_type="application/x-msi",
    )
    response["Cache-Control"] = "no-store"
    return response


def monitoring_ticket(**kwargs):
    """A short-lived bearer download for a hidden support-to-monitoring handoff."""
    from .views import installer_directory

    response = monitoring_response(**kwargs)
    try:
        data = b"".join(response.streaming_content)
    finally:
        # This is an internal response, not the end of the enclosing request.
        # HttpResponse.close() sends request_finished and closes its DB connection.
        response.file_to_stream.close()
    digest = hashlib.sha256(data).hexdigest()
    directory = installer_directory() / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        path = Path(temporary.name)
        temporary.write(data)
    try:
        path.chmod(0o640)
        path.replace(directory / f"{digest}.msi")
    finally:
        path.unlink(missing_ok=True)
    ticket = signing.dumps(
        {"sha256": digest, "size": len(data)}, salt="generated-installers"
    )
    return f"https://{settings.ALLOWED_HOSTS[0]}/support/generated/", ticket
