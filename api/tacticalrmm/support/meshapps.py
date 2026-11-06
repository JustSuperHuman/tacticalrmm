"""Support apps straight from MeshCentral, for use until signed ones are published.

MeshCentral builds them for the support group with its server and group settings
inside. You haven't code-signed them, so Windows SmartScreen and macOS Gatekeeper
warn people before they run. Publishing signed apps with publish_support_installers
replaces them.
"""

import hashlib
import io
import os
import ssl
import tempfile
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

from django.db import transaction

from core.utils import get_core_settings

from . import mesh
from .models import SupportInstaller

# Windows x64, interactive only: a small window with a Connect button, and no
# service. The person's own account runs it, like the signed Windows app.
WINDOWS = "/meshagents?id=4&meshid={group}&installflags=1"
# The universal Mac package, which MeshCentral sends inside a zip.
MAC = "/meshosxagent?id=10005&meshid={group}&installflags=0"
MAX_BYTES = 256 * 1024 * 1024
# Replaced in development labs whose MeshCentral uses a self-signed certificate.
SSL_CONTEXT = None


class Unavailable(Exception):
    """MeshCentral didn't hand over a usable support app."""


def _download(url):
    try:
        with urllib.request.urlopen(url, timeout=120, context=SSL_CONTEXT) as response:
            data = response.read(MAX_BYTES + 1)
    except (OSError, ValueError, ssl.SSLError) as exc:
        raise Unavailable(f"MeshCentral didn’t send the app ({exc}).") from exc
    if len(data) > MAX_BYTES:
        raise Unavailable("MeshCentral sent a file that’s too large to be the app.")
    return data


def fetch(base, group_id):
    """{platform: installer bytes} for the support group, checked for format."""
    base = base.rstrip("/")
    windows = _download(base + WINDOWS.format(group=group_id))
    if windows[:2] != b"MZ":
        raise Unavailable("MeshCentral didn’t send a Windows program.")
    archive = _download(base + MAC.format(group=group_id))
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            name = next(n for n in bundle.namelist() if n.lower().endswith(".pkg"))
            package = bundle.read(name)
    except (zipfile.BadZipFile, StopIteration, KeyError) as exc:
        raise Unavailable(
            "MeshCentral’s Mac download didn’t contain a package."
        ) from exc
    if package[:4] != b"xar!":
        raise Unavailable("MeshCentral didn’t send a Mac installer package.")
    return {
        SupportInstaller.Platform.WINDOWS: windows,
        SupportInstaller.Platform.MACOS: package,
    }


def _store(root, installer, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as target:
            temporary = Path(target.name)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, root / installer.storage_name)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def publish(replace_signed=False):
    """Fetch and publish both apps. Signed apps stay unless `replace_signed`.

    Returns {platform: "published" | "kept signed"}.
    """
    from .views import installer_directory

    group = mesh.find_group()
    if group is None:
        raise Unavailable("Create the support group first.")
    core = get_core_settings()
    if not core.mesh_site:
        raise Unavailable("MeshCentral’s address isn’t set in Tactical RMM yet.")
    apps = fetch(core.mesh_site, group["short_id"])
    signed = set(
        SupportInstaller.objects.filter(signed=True).values_list("platform", flat=True)
    )
    root = installer_directory()
    root.mkdir(parents=True, exist_ok=True)
    version = f"meshcentral-{date.today().isoformat()}"
    results, records = {}, {}
    for platform, data in apps.items():
        if platform in signed and not replace_signed:
            results[platform] = "kept signed"
            continue
        installer = SupportInstaller(
            platform=platform,
            version=version,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            signer="",
            signed=False,
        )
        _store(root, installer, data)
        records[platform] = installer
        results[platform] = "published"
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
                },
            )
    return results
