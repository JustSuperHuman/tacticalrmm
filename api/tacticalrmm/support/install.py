"""Commands that install the full RMM agent through the support app.

Both commands start the install in the background and return quickly, because the
support app runs one command at a time. Progress is written to a log on the computer.
"""

import datetime as dt
import shlex

from django.conf import settings
from knox.models import AuthToken

from accounts.models import User
from agents.utils import get_agent_url
from core.utils import token_is_valid
from tacticalrmm.constants import AgentPlat

WINDOWS_LOG = r"C:\ProgramData\TacticalRMM\support-install.log"
WINDOWS_STATUS = "tacticalrmm-support-install.txt"
MAC_LOG = "/var/log/tacticalrmm-support-install.log"
MAC_JOB = "com.tacticalrmm.support-install"
MAC_FOLDER = "/var/tmp/tacticalrmm-support"
AGENT_TYPES = ("workstation", "server")
# How the support app's reply to an install command starts.
STARTED = "Install started."
NOT_STARTED = "Install not started: "


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


def _api_url():
    return f"https://{settings.ALLOWED_HOSTS[0]}"


def _ps(value):
    """A PowerShell single-quoted string."""
    return "'" + str(value).replace("'", "''") + "'"


def windows_command(site, agent_type):
    """Download a generated MSI and ask Windows to elevate its native setup UI."""
    from .generation import BuildError, monitoring_ticket

    _check_server()
    try:
        url, ticket = monitoring_ticket(
            client=site.client_id,
            site=site.pk,
            agent_type=agent_type,
            rdp=False,
            ping=False,
            power=False,
            token=_install_token(),
            api=_api_url(),
            file_name="JustConnect-Monitoring.msi",
        )
    except (BuildError, ValueError) as exc:
        raise InstallUnavailable(str(exc)) from exc
    return "\n".join(
        [
            f"$status = Join-Path $env:TEMP {_ps(WINDOWS_STATUS)}",
            "$file = Join-Path $env:TEMP ('JustConnect-' + [guid]::NewGuid().ToString('N') + '.msi')",
            "$log = Join-Path $env:TEMP 'JustConnect-install.log'",
            "try {",
            "  $http = New-Object -ComObject WinHttp.WinHttpRequest.5.1",
            "  $http.Option(9) = 2048",
            f"  $http.Open('GET', {_ps(url)}, $false)",
            f"  $http.SetRequestHeader('Authorization', {_ps('Bearer ' + ticket)})",
            "  $http.Send()",
            "  if ($http.Status -ne 200) { throw 'The generated installer download expired. Start the installation again.' }",
            "  [IO.File]::WriteAllBytes($file, [byte[]]$http.ResponseBody)",
            "  Set-Content -LiteralPath $status -Value 'Waiting for them to choose Yes on the Windows prompt.'",
            "  $arguments = '/i ' + [char]34 + $file + [char]34 + ' /passive /norestart /l*v ' + [char]34 + $log + [char]34",
            "  $setup = Start-Process -FilePath 'msiexec.exe' -Verb RunAs -ArgumentList $arguments -PassThru",
            "  Set-Content -LiteralPath $status -Value 'They chose Yes. The MSI is installing the monitoring agent.'",
            # WMI is available in Windows 7's PowerShell 2; CIM cmdlets are not.
            "  $cleanup = 'Wait-Process -Id ' + $setup.Id + ' -ErrorAction SilentlyContinue; Remove-Item -LiteralPath ' + [char]39 + $file.Replace([string][char]39, ([string][char]39 + [char]39)) + [char]39 + ' -Force -ErrorAction SilentlyContinue'",
            "  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cleanup))",
            "  $startup = ([wmiclass]'Win32_ProcessStartup').CreateInstance()",
            "  $startup.ShowWindow = 0",
            "  $null = ([wmiclass]'Win32_Process').Create(('powershell.exe -NoProfile -WindowStyle Hidden -EncodedCommand ' + $encoded), $null, $startup)",
            f"  {_ps(STARTED)}",
            "} catch {",
            "  Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue",
            "  Set-Content -LiteralPath $status -Value ('The install did not start: ' + $_.Exception.Message)",
            f"  {_ps(NOT_STARTED)} + $_.Exception.Message",
            "}",
            "",
        ]
    )


def windows_progress_command():
    return "\n".join(
        [
            f"$status = Join-Path $env:TEMP {_ps(WINDOWS_STATUS)}",
            "$log = Join-Path $env:TEMP 'JustConnect-install.log'",
            # One line: the support app feeds PowerShell a line at a time, so an
            # "else" on its own line would be read as a new command.
            "if (Test-Path -LiteralPath $status) { Get-Content -LiteralPath $status } "
            "else { 'No install has been started through the support app yet.' }",
            "if (Test-Path -LiteralPath $log) "
            "{ ''; 'Install log:'; Get-Content -LiteralPath $log -Tail 40 }",
            "",
        ]
    )


def mac_command(site, agent_type):
    """Shell for the support app, which runs as root on a Mac.

    The RMM installer removes any MeshCentral agent it finds, including the support
    app that sends this command. So the install runs as its own launchd job, which
    keeps going after the support app is gone.
    """
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
    q = shlex.quote
    folder = MAC_FOLDER
    job = "\n".join(
        [
            "#!/bin/sh",
            f"d={q(folder)}",
            'echo "Downloading the RMM agent"',
            # hw.optional.arm64 is 1 on Apple silicon, even under Rosetta.
            f"url={q(urls['amd64'])}",
            '[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ] && '
            f"url={q(urls['arm64'])}",
            # curl reads the address from a file, so the token isn't in its arguments.
            'printf \'url = "%s"\\n\' "$url" > "$d/curl.conf"',
            'if curl -fsSL -o "$d/tacticalagent" -K "$d/curl.conf"; then',
            '  chmod 700 "$d/tacticalagent"',
            f'  "$d/tacticalagent" -m install --api {q(_api_url())} '
            f"--client-id {q(str(site.client_id))} --site-id {q(str(site.pk))} "
            f"--agent-type {q(agent_type)} --auth {q(_install_token())}",
            '  echo "Finished with exit code $?"',
            "else",
            '  echo "The download failed."',
            "fi",
            'rm -rf "$d"',
            "",
        ]
    )
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        f"<key>Label</key><string>{MAC_JOB}</string>"
        "<key>ProgramArguments</key><array><string>/bin/sh</string>"
        f"<string>{folder}/install.sh</string></array>"
        "<key>RunAtLoad</key><true/>"
        f"<key>StandardOutPath</key><string>{MAC_LOG}</string>"
        f"<key>StandardErrorPath</key><string>{MAC_LOG}</string>"
        "</dict></plist>\n"
    )
    end = "TRMM_SUPPORT_END"
    return "\n".join(
        [
            "umask 077",
            f"launchctl bootout system/{MAC_JOB} 2>/dev/null",
            f"rm -rf {q(folder)} && mkdir -p {q(folder)}",
            f"cat > {q(folder + '/install.sh')} <<'{end}'",
            job + end,
            f"cat > {q(folder + '/job.plist')} <<'{end}'",
            plist + end,
            f": > {MAC_LOG}",
            f"if launchctl bootstrap system {q(folder + '/job.plist')}; "
            f"then echo {q(STARTED)}; "
            f"else echo {q(NOT_STARTED + 'macOS refused to start the install.')}; fi",
            "",
        ]
    )


def mac_progress_command():
    return (
        f"if [ -s {MAC_LOG} ]; then tail -n 40 {MAC_LOG}; "
        f"elif [ -f {MAC_LOG} ]; then "
        "echo 'The install started, but nothing is logged yet.'; "
        "else echo 'No install has been started through the support app yet.'; fi\n"
    )
