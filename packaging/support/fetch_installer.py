"""Fetch fully configured installers from the operator's existing MeshCentral server."""

import argparse
import hashlib
import os
import shutil
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

import requests

MAX_SIZE = 256 * 1024 * 1024


def fetch(platform, destination):
    base = os.environ["SUPPORT_MESH_URL"].rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "SUPPORT_MESH_URL must be an HTTPS server URL without credentials or query parameters"
        )
    group = os.environ["SUPPORT_MESH_GROUP_ID"]
    if not group or any(c.isspace() for c in group) or "/" in group:
        raise ValueError(
            "Set SUPPORT_MESH_GROUP_ID to the group's final MeshCentral identifier"
        )
    # Keep Windows configuration beside the binary. Re-signing an EXE with an
    # embedded .msh can remove its enrollment data from the certificate table.
    params = {
        "id": 4 if platform == "windows" else 10005,
    }
    if platform == "macos":
        params.update(meshid=group, installflags=2)
    if os.environ.get("SUPPORT_MESH_DOWNLOAD_KEY"):
        params["key"] = os.environ["SUPPORT_MESH_DOWNLOAD_KEY"]
    headers = {}
    if os.environ.get("SUPPORT_MESH_DOWNLOAD_COOKIE"):
        headers["Cookie"] = os.environ["SUPPORT_MESH_DOWNLOAD_COOKIE"]
    endpoint = "meshagents" if platform == "windows" else "meshosxagent"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as stream:
        with requests.get(
            f"{base}/{endpoint}",
            params=params,
            headers=headers,
            stream=True,
            timeout=(15, 120),
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise ValueError(
                    f"MeshCentral returned HTTP {response.status_code}. Check server access and download configuration."
                )
            size = 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_SIZE:
                    raise ValueError("MeshCentral response is too large")
                stream.write(chunk)
        stream.seek(0)
        if platform == "windows":
            if stream.read(2) != b"MZ":
                raise ValueError("MeshCentral did not return a Windows executable")
            stream.seek(0)
            with destination.open("wb") as target:
                shutil.copyfileobj(stream, target)
        else:
            with zipfile.ZipFile(stream) as archive:
                packages = [
                    i
                    for i in archive.infolist()
                    if i.filename.endswith(".pkg") and not i.is_dir()
                ]
                if len(packages) != 1 or not 0 < packages[0].file_size <= MAX_SIZE:
                    raise ValueError(
                        "Expected one Mac installer in the MeshCentral archive"
                    )
                with archive.open(packages[0]) as source, destination.open(
                    "wb"
                ) as target:
                    shutil.copyfileobj(source, target)
            with destination.open("rb") as package:
                if package.read(4) != b"xar!":
                    raise ValueError(
                        "MeshCentral did not return a flat Mac installer package"
                    )
    with destination.open("rb") as stream:
        print(
            f"Fetched {platform} installer; SHA-256: {hashlib.file_digest(stream, 'sha256').hexdigest()}"
        )
    if platform == "windows":
        config_params = {"id": group, "installflags": 1}
        if os.environ.get("SUPPORT_MESH_DOWNLOAD_KEY"):
            config_params["key"] = os.environ["SUPPORT_MESH_DOWNLOAD_KEY"]
        with requests.get(
            f"{base}/meshsettings",
            params=config_params,
            headers=headers,
            stream=True,
            timeout=(15, 30),
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise ValueError("Could not download the support group's configuration")
            config = bytearray()
            for chunk in response.iter_content(4096):
                config.extend(chunk)
                if len(config) > 64 * 1024:
                    raise ValueError("Unexpected MeshCentral configuration size")
        text = config.decode("utf-8")
        entries = dict(
            line.strip().split("=", 1) for line in text.splitlines() if "=" in line
        )
        if (
            not all(entries.get(key) for key in ("MeshID", "MeshServer", "ServerID"))
            or entries.get("InstallFlags") != "1"
        ):
            raise ValueError(
                "MeshCentral did not return a configured run-only support client"
            )
        destination.with_suffix(".msh").write_bytes(config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["windows", "macos"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        fetch(args.platform, args.output)
    except (
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
    ) as exc:
        # requests exceptions can contain credential-bearing URLs. Never print them.
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            and not isinstance(exc, requests.RequestException)
            else "Download failed. Check MeshCentral connectivity and configuration."
        )
        raise SystemExit(message) from None
