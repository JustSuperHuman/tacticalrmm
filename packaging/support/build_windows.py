"""Generate the universal Windows MSI and its hash-addressed client payloads.

Run on Linux with wixl, msitools and g++-mingw-w64-i686 (or in the build container).
"""

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api/tacticalrmm"))

from support.installer.builder import Payload, build_msi  # noqa: E402
from support.installer.compatibility import verify  # noqa: E402


def build():
    base = os.environ["SUPPORT_INSTALLER_BASE_URL"].rstrip("/")
    version = os.environ["SUPPORT_VERSION"]
    legacy = ROOT / "payload/windows7/MeshAgent.exe"
    modern = ROOT / "payload/windows/MeshAgent.exe"
    verify(legacy)
    payload_root = ROOT / "release/payloads"
    payload_root.mkdir(parents=True, exist_ok=True)
    clients = []
    for source in (legacy, modern):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(source, payload_root / f"{digest}.exe")
        clients.append(Payload(f"{base}/support/payload/{digest}/", digest))
    config = legacy.with_suffix(".msh").read_bytes()
    entries = dict(
        line.split("=", 1)
        for line in config.decode("utf-8-sig").splitlines()
        if "=" in line
    )
    if (
        not all(entries.get(key) for key in ("MeshID", "MeshServer", "ServerID"))
        or entries.get("InstallFlags", "0") != "0"
    ):
        raise ValueError("Expected the support group's service configuration")
    output = build_msi(
        ROOT / "release/TacticalSupport-windows.msi",
        legacy=clients[0],
        modern=clients[1],
        mesh_settings=config,
        version=version,
        cache=ROOT / "payload/build-cache",
    )
    print(
        f"Generated {output.name}; Windows 7 and client architecture are selected during setup."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    build()
