"""MeshCentral payload fetching and atomic storage for generated installers."""

import io
import os
import ssl
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .models import SupportInstaller

# Windows x64, interactive only: a small window with a Connect button, and no
# service. The person's own account runs it, like the signed Windows app.
WINDOWS = "/meshagents?id=4&meshid={group}&installflags=1"
# The native x86 client also runs on 64-bit Windows 7. Keep it separate from
# the x64 download so a 32-bit PC is never handed an incompatible executable.
WINDOWS7 = "/meshagents?id=3&meshid={group}&installflags=1"
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


def fetch(base, group_id, platforms=None):
    """{platform: installer bytes} for the support group, checked for format."""
    base = base.rstrip("/")
    selected = set(
        platforms if platforms is not None else SupportInstaller.Platform.values
    )
    if not selected or selected.difference(SupportInstaller.Platform.values):
        raise Unavailable("Choose a supported app to publish.")
    apps = {}
    for platform, endpoint in (("windows", WINDOWS), ("windows7", WINDOWS7)):
        if platform not in selected:
            continue
        data = _download(base + endpoint.format(group=group_id))
        if data[:2] != b"MZ":
            raise Unavailable("MeshCentral didn’t send a Windows program.")
        apps[platform] = data
    if "macos" not in selected:
        return apps
    archive = _download(base + MAC.format(group=group_id))
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            packages = [
                i
                for i in bundle.infolist()
                if i.filename.lower().endswith(".pkg") and not i.is_dir()
            ]
            if len(packages) != 1 or not 0 < packages[0].file_size <= MAX_BYTES:
                raise Unavailable(
                    "Expected one Mac installer package from MeshCentral."
                )
            package = bundle.read(packages[0])
    except (zipfile.BadZipFile, StopIteration, KeyError) as exc:
        raise Unavailable(
            "MeshCentral’s Mac download didn’t contain a package."
        ) from exc
    if package[:4] != b"xar!":
        raise Unavailable("MeshCentral didn’t send a Mac installer package.")
    apps[SupportInstaller.Platform.MACOS] = package
    return apps


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


def publish(replace_signed=False, platforms=None):
    """Compatibility command: generate packages; never publish raw Windows EXEs."""
    from .generation import publish as generate

    return generate(replace_signed=replace_signed, platforms=platforms)
