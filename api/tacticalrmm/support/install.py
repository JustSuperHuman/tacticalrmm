"""Commands that install the full RMM agent through the support app.

Both commands start the install in the background and return at once, because the
support app runs one command at a time. Progress is written to a log on the computer.
"""

import base64
import datetime as dt
import shlex
from pathlib import Path

from django.conf import settings
from knox.models import AuthToken

from accounts.models import User
from agents.utils import get_agent_url
from core.utils import token_is_valid
from tacticalrmm.constants import AgentPlat

WINDOWS_LOG = r"C:\ProgramData\TacticalRMM\support-install.log"
MAC_LOG = "/var/log/tacticalrmm-support-install.log"
AGENT_TYPES = ("workstation", "server")


class InstallUnavailable(Exception):
    """The full agent can't be installed on this computer from this server."""


def _install_token():
    installer = User.objects.filter(is_installer_user=True).first()
    if installer is None:
        raise InstallUnavailable(
            "This server has no installer account. Run "
            "“python manage.py create_installer_user” on the server, then try again."
        )
    _, token = AuthToken.objects.create(user=installer, expiry=dt.timedelta(hours=2))
    return token


def _check_server():
    if getattr(settings, "TRMM_INSECURE", False):
        raise InstallUnavailable(
            "Agents can’t be installed from here while the server runs in insecure mode."
        )


def windows_command(site, agent_type):
    """PowerShell for the support app. It asks Windows to run the official installer
    script as an administrator, so the person sees a Windows permission prompt."""
    _check_server()
    version = settings.LATEST_AGENT_VER
    codesign_token, _ = token_is_valid()
    script = Path(settings.BASE_DIR / "core" / "installer.ps1").read_text()
    for placeholder, value in {
        "innosetupchange": f"tacticalagent-v{version}-windows-amd64.exe",
        "clientchange": str(site.client_id),
        "sitechange": str(site.pk),
        "apichange": f"https://{settings.ALLOWED_HOSTS[0]}",
        "atypechange": agent_type,
        "powerchange": "0",
        "rdpchange": "0",
        "pingchange": "0",
        "downloadchange": get_agent_url(
            goarch="amd64", plat=AgentPlat.WINDOWS, token=codesign_token
        ),
        "tokenchange": _install_token(),
    }.items():
        script = script.replace(placeholder, value)
    elevated = (
        "$log = 'C:\\ProgramData\\TacticalRMM\\support-install.log'\n"
        "New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null\n"
        "Start-Transcript -Path $log -Force | Out-Null\n" + script
    )
    encoded = base64.b64encode(elevated.encode("utf-16-le")).decode()
    return (
        "try { Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden "
        "-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',"
        f"'{encoded}'; 'Install started.' }} "
        "catch { 'Install not started: ' + $_.Exception.Message }"
    )


def mac_command(site, agent_type):
    """Shell for the support app, which runs as root on a Mac."""
    _check_server()
    codesign_token, valid = token_is_valid()
    if not valid:
        raise InstallUnavailable(
            "Mac agents need a Tactical RMM code signing token. Add one in the "
            "dashboard under Settings, then try again."
        )
    urls = {
        arch: get_agent_url(goarch=arch, plat=AgentPlat.DARWIN, token=codesign_token)
        for arch in ("amd64", "arm64")
    }
    args = " ".join(
        shlex.quote(str(value))
        for value in (
            f"https://{settings.ALLOWED_HOSTS[0]}",
            site.client_id,
            site.pk,
            agent_type,
            _install_token(),
        )
    )
    installer = (
        'f=/tmp/tacticalagent-support; echo "Downloading the RMM agent"; '
        'curl -fsSL -o "$f" "$1" && chmod +x "$f" && '
        '"$f" -m install --api "$2" --client-id "$3" --site-id "$4" '
        '--agent-type "$5" --auth "$6"; echo "Finished with exit code $?"; rm -f "$f"'
    )
    # "rmm-install" names the background shell, so its log lines don't show the URL.
    return (
        f"url={shlex.quote(urls['amd64'])}\n"
        f"[ \"$(uname -m)\" = arm64 ] && url={shlex.quote(urls['arm64'])}\n"
        f'nohup sh -c {shlex.quote(installer)} rmm-install "$url" {args} '
        f"> {MAC_LOG} 2>&1 &\n"
        "echo 'Install started.'\n"
    )
