"""The MeshCentral support group, reached through the server's own MeshCentral account.

Every device lookup is limited to the support group, so these helpers can't reach
computers in the regular Tactical RMM group.
"""

import asyncio
import json
import re
import time

import websockets
from websockets.exceptions import WebSocketException
from django.conf import settings

from core.utils import get_mesh_ws_url
from tacticalrmm.constants import TRMM_WS_MAX_SIZE

RESPONSE_ID = "trmm-support"
# MeshCentral agent ids for Windows builds (see MeshCentral's meshuser.js runcommands).
WINDOWS_AGENT_IDS = {1, 2, 3, 4, 42, 43}
# runcommands types
CMD, POWERSHELL, SHELL = 1, 2, 3
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
    # Resolve the URL outside the event loop: it reads settings from the database.
    uri = get_mesh_ws_url()
    try:
        return asyncio.run(_exchange(uri, payload, wanted, timeout))
    except asyncio.TimeoutError:
        return None
    except (OSError, WebSocketException, ValueError) as exc:
        raise MeshError(
            "MeshCentral isn’t responding. Check that it’s running."
        ) from exc


def _action(name):
    return lambda data: data.get("action") == name


def find_group():
    reply = _request({"action": "meshes", "responseid": RESPONSE_ID}, _action("meshes"))
    if reply is None:
        raise MeshError("MeshCentral didn’t answer in time.")
    wanted = group_name()
    for mesh in reply.get("meshes", []):
        short_id = mesh["_id"].split("/")[-1]
        if wanted in (mesh.get("name"), short_id):
            return {"id": mesh["_id"], "short_id": short_id, "name": mesh.get("name")}
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


def _device(node):
    agent_id = (node.get("agent") or {}).get("id", 0)
    return {
        "id": node["_id"].split("/")[-1],
        "node_id": node["_id"],
        "name": node.get("name") or node.get("host") or "Unnamed computer",
        "os": node.get("osdesc") or "",
        "windows": agent_id in WINDOWS_AGENT_IDS,
        "online": bool(node.get("conn", 0) & 1),
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
    devices = [_device(node) for node in nodes]
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
