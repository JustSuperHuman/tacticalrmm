"""The MeshCentral support group, reached through the server's own MeshCentral account.

Every device lookup is limited to the support group, so these helpers can't reach
computers in the regular Tactical RMM group.
"""

import asyncio
import json
import re
import secrets
import time
from datetime import datetime, timezone

import websockets
from django.conf import settings
from meshctrl.utils import get_auth_token, get_login_token
from websockets.exceptions import WebSocketException

from core.utils import _b64_to_hex, get_core_settings, get_mesh_ws_url
from tacticalrmm.constants import TRMM_WS_MAX_SIZE

RESPONSE_ID = "trmm-support"
# MeshCentral agent ids for Windows builds (see MeshCentral's meshuser.js runcommands).
WINDOWS_AGENT_IDS = {1, 2, 3, 4, 42, 43}
# runcommands types
CMD, POWERSHELL, SHELL = 1, 2, 3
SHELL_NAMES = {POWERSHELL: "PowerShell", CMD: "Command Prompt", SHELL: "Mac shell"}
# MeshCentral web views for a device, and the device rights TRMM's own sync grants.
SESSION_VIEWS = {"desktop": 11, "terminal": 12, "files": 13}
DEVICE_RIGHTS = 4088024
DEVICE_ID = re.compile(r"[A-Za-z0-9@$]{16,128}")


class MeshError(Exception):
    """MeshCentral couldn't be reached or refused the request."""


def group_name():
    return getattr(settings, "SUPPORT_MESH_GROUP", "TacticalRMM Support")


async def _exchange(uri, payload, wanted, timeout):
    async with websockets.connect(
        uri, max_size=TRMM_WS_MAX_SIZE, open_timeout=10
    ) as ws:
        await ws.send(json.dumps(payload))
        if wanted is None:
            return None

        async def first_match():
            async for message in ws:
                data = json.loads(message)
                if wanted(data):
                    return data
            return None

        return await asyncio.wait_for(first_match(), timeout)


def _request(payload, wanted, timeout=15):
    """Send one request and return the first reply `wanted` accepts.

    Returns None on timeout, or straight away when `wanted` is None (send only).
    """
    try:
        # Resolve the URL outside the event loop: it reads settings from the database.
        uri = get_mesh_ws_url()
        return asyncio.run(_exchange(uri, payload, wanted, timeout))
    except asyncio.TimeoutError:
        return None
    except (OSError, WebSocketException, ValueError, KeyError, TypeError) as exc:
        raise MeshError(
            "MeshCentral isn’t responding. Check that it’s running and that "
            "Tactical RMM’s MeshCentral settings are right."
        ) from exc
    except Exception as exc:
        # Missing or broken MeshCentral settings in the database.
        raise MeshError("MeshCentral isn’t set up in Tactical RMM yet.") from exc


def _action(name):
    return lambda data: data.get("action") == name


def find_group():
    reply = _request({"action": "meshes", "responseid": RESPONSE_ID}, _action("meshes"))
    if reply is None:
        raise MeshError("MeshCentral didn’t answer in time.")
    wanted = group_name()
    for mesh in reply.get("meshes", []):
        mesh_id = str(mesh.get("_id", ""))
        short_id = mesh_id.split("/")[-1]
        if mesh_id and wanted in (mesh.get("name"), short_id):
            return {
                "id": mesh_id,
                "short_id": short_id,
                "name": mesh.get("name"),
                # MeshCentral's user consent flags for the whole group.
                "consent": int(mesh.get("consent") or 0),
            }
    return None


def create_group():
    _request(
        {
            "action": "createmesh",
            "meshname": group_name(),
            "meshtype": 2,
            "desc": "Computers connected through the public support page.",
            "responseid": RESPONSE_ID,
        },
        _action("createmesh"),
    )
    return find_group()


def _when(milliseconds):
    try:
        return datetime.fromtimestamp(int(milliseconds) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _device(node):
    agent_id = (node.get("agent") or {}).get("id", 0)
    users = node.get("users") if isinstance(node.get("users"), list) else []
    system = str(node.get("osdesc") or "")
    windows = agent_id in WINDOWS_AGENT_IDS
    return {
        "id": node["_id"].split("/")[-1],
        "node_id": node["_id"],
        "name": str(node.get("name") or node.get("host") or "Unnamed computer"),
        "os": system,
        "windows": windows,
        "platform": (
            "windows"
            if windows
            else (
                "macos" if re.search(r"mac ?os|os ?x|darwin", system, re.I) else "other"
            )
        ),
        "online": bool(node.get("conn", 0) & 1),
        "connected_at": _when(node.get("agct")),
        "first_seen": _when(node.get("firstconnect")),
        # The address MeshCentral sees the computer connect from.
        "ip": str(node.get("ip") or ""),
        "consent": int(node.get("consent") or 0),
        # "DESKTOP-1\dana" -> "dana": who is signed in, to tell computers apart.
        "users": [str(u).split("\\")[-1] for u in users if u][:3],
    }


def list_devices(group, device_id=None):
    payload = {"action": "nodes", "meshid": group["id"], "responseid": RESPONSE_ID}
    if device_id:
        payload["id"] = device_id
    reply = _request(payload, _action("nodes"))
    if reply is None:
        raise MeshError("MeshCentral didn’t answer in time.")
    if reply.get("result"):
        raise MeshError(f"MeshCentral said: {reply['result']}")
    nodes = [node for nodes in reply.get("nodes", {}).values() for node in nodes]
    devices = [
        _device(node) for node in nodes if isinstance(node, dict) and node.get("_id")
    ]
    return sorted(devices, key=lambda d: (not d["online"], d["name"].lower()))


def get_device(group, device_id):
    if not DEVICE_ID.fullmatch(device_id):
        return None
    matches = [d for d in list_devices(group, device_id) if d["id"] == device_id]
    return matches[0] if matches else None


def run_command(device, command, shell, *, reply=True, timeout=60):
    """Run a command. Returns its output, or None if no reply arrived in time."""
    payload = {
        "action": "runcommands",
        "nodeids": [device["node_id"]],
        "type": shell,
        "cmds": command,
        "runAsUser": 0,
        "reply": reply,
        "responseid": RESPONSE_ID,
    }

    def wanted(data):
        if data.get("responseid") != RESPONSE_ID:
            return False
        if data.get("action") == "msg" and data.get("type") == "runcommands":
            return True
        return data.get("action") == "runcommands"

    answer = _request(payload, wanted, timeout)
    if answer is None:
        return None
    if answer.get("action") == "runcommands":
        # The server's own answer: "OK" when it didn't wait for output, otherwise an error.
        if answer.get("result") == "OK" and not reply:
            return ""
        raise MeshError(answer.get("result") or "The command couldn’t be sent.")
    return answer.get("result") or ""


# Run-only agents (the Windows support app) ignore MeshCentral's uninstall request and
# reconnect after removal, so they're stopped from the inside. These run as the agent's
# child, so $PPID / ParentProcessId is the agent itself.
STOP_UNIX = 'p=$PPID; nohup sh -c "sleep 1; kill $p" >/dev/null 2>&1 & echo Stopping.'
# WMI starts the follow-up outside the agent's process tree, so it outlives the agent
# and can run the support app's own uninstaller once the files are no longer in use.
STOP_WINDOWS = (
    '$agent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId\n'
    "$steps = 'Start-Sleep -Seconds 1; Stop-Process -Id ' + $agent + ' -Force "
    "-ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; $u = Join-Path "
    "$env:LOCALAPPDATA ''TacticalRMMSupport\\unins000.exe''; if (Test-Path $u) "
    "{ Start-Process $u -ArgumentList ''/VERYSILENT'',''/SUPPRESSMSGBOXES'',''/NORESTART'' }'\n"
    "$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($steps))\n"
    "$null = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
    '@{ CommandLine = "powershell.exe -NoProfile -WindowStyle Hidden -EncodedCommand $encoded" }\n'
    "'Stopping.'\n"
)


def remove_device(device):
    """Remove the support app from a computer and drop it from the group."""
    nodeids = [device["node_id"]]
    # Installed as a service (the Mac support app), the agent uninstalls itself.
    # The agent never answers this request, so don't wait for one.
    _request({"action": "uninstallagent", "nodeids": nodeids}, None)
    time.sleep(2)
    try:
        if device["windows"]:
            run_command(device, STOP_WINDOWS, POWERSHELL, timeout=15)
        else:
            run_command(device, STOP_UNIX, SHELL, timeout=15)
    except MeshError:
        pass  # Already disconnected, usually because the uninstall worked.
    # Give it a moment to exit so it can't reconnect and re-add itself.
    time.sleep(3)
    _request(
        {"action": "removedevices", "nodeids": nodeids, "responseid": RESPONSE_ID},
        _action("removedevices"),
        timeout=10,
    )


def node_id(value):
    """MeshCentral's own form of a computer's id ("node//" and base64).

    Tactical RMM stores agents' ids as 96 hex characters. MeshCentral's web page
    accepts those, but its tunnel requests only accept its own form.
    """
    value = (value or "").strip()
    if re.fullmatch(r"[0-9A-Fa-f]{96}", value):
        return "node//" + _b64_to_hex(value)
    return value


def identity(user, node_id, grant=True):
    """(core settings, the MeshCentral user a technician acts as) for one computer.

    With MeshCentral sync on (Tactical RMM's default), technicians use their own
    MeshCentral account. `grant` gives that account rights to this one computer
    first, which support computers need; monitored agents get their rights from
    Tactical RMM's sync. With sync off, the server's account is used, as in the
    dashboard.
    """
    core = get_core_settings()
    if not core.mesh_site or not core.mesh_token:
        raise MeshError("MeshCentral isn’t set up in Tactical RMM’s settings yet.")
    if not core.sync_mesh_with_trmm:
        return core, f"user//{core.mesh_api_superuser}"
    mesh_user = user.mesh_user_id
    reply = _request({"action": "users", "responseid": RESPONSE_ID}, _action("users"))
    known = {u.get("_id") for u in (reply or {}).get("users", [])}
    if mesh_user not in known:
        raise MeshError(
            "Your MeshCentral account isn’t ready yet. Tactical RMM creates it "
            "within a few minutes of getting MeshCentral access. Try again soon."
        )
    if grant:
        reply = _request(
            {
                "action": "adddeviceuser",
                "nodeid": node_id,
                "userids": [mesh_user],
                "rights": DEVICE_RIGHTS,
                "remove": False,
                "responseid": RESPONSE_ID,
            },
            _action("adddeviceuser"),
        )
        if reply is None or reply.get("result") not in ("ok", None):
            raise MeshError("MeshCentral didn’t give you access to this computer.")
    return core, mesh_user


def session_url(device, user, view, grant=True):
    """A one-time MeshCentral link that opens the remote desktop, terminal or files."""
    core, mesh_user = identity(user, device["node_id"], grant)
    token = get_login_token(key=core.mesh_token, user=mesh_user)
    return (
        f"{core.mesh_site.rstrip('/')}/?login={token}&gotonode={device['node_id']}"
        f"&viewmode={SESSION_VIEWS[view]}&hide=31"
    )


async def _open_desktop(control_url, node_id, tunnel):
    """Ask the computer to open a desktop tunnel. Returns the relay cookie."""
    async with websockets.connect(
        control_url, max_size=TRMM_WS_MAX_SIZE, open_timeout=10
    ) as control:
        await control.send(json.dumps({"action": "authcookie"}))

        async def cookies():
            async for message in control:
                reply = json.loads(message)
                if reply.get("action") == "authcookie":
                    return reply
            return None

        reply = await asyncio.wait_for(cookies(), 15)
        if not reply or not reply.get("cookie"):
            raise MeshError("MeshCentral didn’t open a remote desktop connection.")
        await control.send(
            json.dumps(
                {
                    "action": "msg",
                    "type": "tunnel",
                    "nodeid": node_id,
                    "usage": 2,
                    "value": f"*/meshrelay.ashx?p=2&nodeid={node_id}&id={tunnel}"
                    f"&rauth={reply['rcookie']}",
                }
            )
        )
        return reply["cookie"]


def desktop_relay(node_id, user, grant=True):
    """The address a browser connects to for this computer's remote desktop.

    The server asks the computer to open the tunnel as the technician's own
    MeshCentral account; the browser then joins it directly on MeshCentral, so
    MeshCentral's own consent settings and session log apply.
    """
    core, mesh_user = identity(user, node_id, grant)
    base = get_mesh_ws_url().split("/control.ashx")[0]
    control_url = (
        f"{base}/control.ashx?auth={get_auth_token(mesh_user, core.mesh_token)}"
    )
    tunnel = secrets.token_hex(12)
    try:
        cookie = asyncio.run(_open_desktop(control_url, node_id, tunnel))
    except MeshError:
        raise
    except asyncio.TimeoutError as exc:
        raise MeshError("MeshCentral didn’t answer in time.") from exc
    except (OSError, WebSocketException, ValueError, KeyError, TypeError) as exc:
        raise MeshError(
            "MeshCentral isn’t responding. Check that it’s running and that "
            "Tactical RMM’s MeshCentral settings are right."
        ) from exc
    public = re.sub(r"^http", "ws", core.mesh_site.rstrip("/"))
    return (
        f"{public}/meshrelay.ashx?browser=1&p=2&nodeid={node_id}"
        f"&id={tunnel}&auth={cookie}"
    )
