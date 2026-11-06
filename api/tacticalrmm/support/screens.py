"""Screen previews: small, regularly updated pictures of each support computer's
screen, shown in the session list and on the session's page.

A preview is one frame of MeshCentral's own remote desktop. The server opens a
desktop tunnel to the support app the same way MeshCentral's web client does, asks
for small JPEG tiles, stitches the first full screen together, and closes the tunnel.
The support app already captures the screen for joining, so this needs no scripts
on the computer and no extra permissions. Previews are only taken while a technician
has the console open, and only kept in the cache.
"""

import asyncio
import io
import json
import secrets
import time

from django.core.cache import cache
from PIL import Image, UnidentifiedImageError

from tacticalrmm.constants import TRMM_WS_MAX_SIZE

from . import mesh

# How often previews are retaken while someone is looking: often for the session
# that's open, less for the rest of the list. Each one is a short remote desktop
# session in MeshCentral's own event log, so these stay modest.
OPEN_SECONDS = 20
LIST_SECONDS = 60
# How long the last preview stays available, for example after they go offline.
KEEP_SECONDS = 600
# After a failed capture (a locked Mac, say), wait this long before trying again.
RETRY_SECONDS = 60
# After the support app asks the person for permission, leave them alone this long.
CONSENT_PAUSE_SECONDS = 1800
WIDTH = 480
CAPTURE_SECONDS = 12
# MeshCentral consent flags that would show the person a notice, a prompt or a bar
# for every remote desktop session, previews included.
DESKTOP_CONSENT = 1 | 8 | 64
# MeshCentral desktop protocol (see MeshCentral's public/scripts/agent-desktop-0.0.2.js)
TILE, SCREEN, JUMBO, ERROR = 3, 7, 27, 65
JPEG = 1
QUALITY = 40
FRAME_MS = 1000


def _key(device_id):
    return f"support:screen:{device_id}"


def _wait_key(device_id):
    return f"support:screen-wait:{device_id}"


def enabled():
    from .models import SupportBranding

    return SupportBranding.load().screen_previews


def asks_consent(device, group_consent=0):
    return bool((device.get("consent", 0) | (group_consent or 0)) & DESKTOP_CONSENT)


def latest(device_id):
    """The newest preview as {"jpeg": bytes, "at": unix seconds}, or None."""
    return cache.get(_key(device_id))


def taken_at(device_id):
    preview = latest(device_id)
    return int(preview["at"]) if preview else None


def request(device, group_consent=0, every=LIST_SECONDS):
    """Ask for a new preview if the last one is older than `every` seconds.
    Never waits for it."""
    if not device.get("online") or asks_consent(device, group_consent):
        return False
    preview = latest(device["id"])
    if preview and time.time() - preview["at"] < every:
        return False
    # One capture at a time per computer, and a pause after a failed one.
    if not cache.add(_wait_key(device["id"]), 1, CAPTURE_SECONDS + 15):
        return False
    from .tasks import capture_screen

    try:
        capture_screen.delay({"id": device["id"], "node_id": device["node_id"]})
    except Exception:
        cache.delete(_wait_key(device["id"]))
        return False
    return True


def capture(device):
    """Take a preview now and keep it. Returns the JPEG, or None."""
    try:
        tiles, screen = asyncio.run(
            asyncio.wait_for(_tiles(device["node_id"]), CAPTURE_SECONDS + 5)
        )
    except Consent:
        cache.set(_wait_key(device["id"]), 1, CONSENT_PAUSE_SECONDS)
        return None
    except Exception:
        tiles, screen = [], None
    jpeg = stitch(tiles, screen)
    if jpeg:
        cache.set(_key(device["id"]), {"jpeg": jpeg, "at": time.time()}, KEEP_SECONDS)
        cache.delete(_wait_key(device["id"]))
    else:
        cache.set(_wait_key(device["id"]), 1, RETRY_SECONDS)
    return jpeg


class Consent(Exception):
    """The support app asked the person before sharing their screen."""


def stitch(tiles, screen=None):
    """One small JPEG from the screen's tiles, or None.

    `screen` is (width, height, scaling) as the support app reported and was asked.
    Tiles can run past the screen's edge, so the picture is cut to the screen.
    """
    canvas = None
    for x, y, data in tiles:
        try:
            tile = Image.open(io.BytesIO(data))
            tile.load()
        except (UnidentifiedImageError, OSError, ValueError):
            continue
        width, height = x + tile.width, y + tile.height
        if canvas is None:
            canvas = Image.new("RGB", (width, height), (11, 13, 16))
        elif width > canvas.width or height > canvas.height:
            grown = Image.new(
                "RGB",
                (max(width, canvas.width), max(height, canvas.height)),
                (11, 13, 16),
            )
            grown.paste(canvas, (0, 0))
            canvas = grown
        canvas.paste(tile.convert("RGB"), (x, y))
    if canvas is None:
        return None
    if screen:
        width, height, scaling = screen
        # Some systems ignore the requested scaling and send the full-size screen.
        factor = 1 if canvas.width >= width * 0.98 else scaling / 1024
        canvas = canvas.crop(
            (
                0,
                0,
                min(canvas.width, round(width * factor)),
                min(canvas.height, round(height * factor)),
            )
        )
    canvas.thumbnail((WIDTH, WIDTH * 2), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=70, optimize=True)
    return out.getvalue()


def _command(cmd, *values):
    """A desktop protocol command: 2-byte type, 2-byte length, then the values."""
    body = bytes(values)
    return bytes([cmd >> 8, cmd & 0xFF, 0, 4 + len(body)]) + body


async def _tiles(node_id):
    """Open a desktop tunnel and collect the first full screen's tiles."""
    control_url = mesh.get_mesh_ws_url()
    base = control_url.split("/control.ashx")[0]
    async with mesh.websockets.connect(
        control_url, max_size=TRMM_WS_MAX_SIZE, open_timeout=10
    ) as control:
        await control.send(json.dumps({"action": "authcookie"}))
        while True:
            reply = json.loads(await control.recv())
            if reply.get("action") == "authcookie":
                break
        tunnel = secrets.token_hex(12)
        relay_url = (
            f"{base}/meshrelay.ashx?browser=1&p=2&nodeid={node_id}"
            f"&id={tunnel}&auth={reply['cookie']}"
        )
        async with mesh.websockets.connect(
            relay_url, max_size=TRMM_WS_MAX_SIZE, open_timeout=10
        ) as relay:
            await control.send(
                json.dumps(
                    {
                        "action": "msg",
                        "type": "tunnel",
                        "nodeid": node_id,
                        "usage": 2,
                        "value": f"*/meshrelay.ashx?p=2&nodeid={node_id}"
                        f"&id={tunnel}&rauth={reply['rcookie']}",
                    }
                )
            )
            return await _read_screen(relay)


async def _read_screen(relay):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CAPTURE_SECONDS
    tiles, covered, expected, asked, screen = [], 0, None, False, None
    pending, pending_size = b"", 0
    last_tile = None
    while loop.time() < deadline:
        # Done once the screen is covered, or the tiles have stopped coming.
        if expected and covered >= expected:
            break
        if last_tile and loop.time() - last_tile > 0.8:
            break
        try:
            message = await asyncio.wait_for(relay.recv(), 1)
        except asyncio.TimeoutError:
            continue
        if isinstance(message, str):
            if message in ("c", "cr"):
                await relay.send("2")  # the desktop protocol
            elif '"type":"console"' in message and '"msgid":1' in message:
                raise Consent  # "Waiting for user to grant access..."
            continue
        data = bytes(message)
        if pending:
            pending += data
            if len(pending) < pending_size:
                continue
            data, pending = pending, b""
        if len(data) < 4:
            continue
        cmd, size = (data[0] << 8) + data[1], (data[2] << 8) + data[3]
        if cmd == JUMBO and size == 8 and len(data) >= 12:
            size = (data[5] << 16) + (data[6] << 8) + data[7]
            data = data[8:]
            cmd = (data[0] << 8) + data[1]
        if size > len(data):
            pending, pending_size = data, size
            continue
        data = data[:size]
        if cmd == SCREEN and len(data) >= 8 and not asked:
            width, height = (data[4] << 8) + data[5], (data[6] << 8) + data[7]
            # Ask for a screen roughly twice the preview's width, as JPEG.
            scaling = max(128, min(1024, int(1024 * WIDTH * 2 / max(width, 1))))
            expected = int(width * height * (scaling / 1024) ** 2 * 0.98)
            screen = (width, height, scaling)
            await relay.send(
                _command(
                    5,
                    JPEG,
                    QUALITY,
                    scaling >> 8,
                    scaling & 0xFF,
                    FRAME_MS >> 8,
                    FRAME_MS & 0xFF,
                )
            )
            await relay.send(_command(8, 0))  # unpause
            await relay.send(_command(6))  # refresh
            asked = True
        elif cmd == TILE and len(data) > 8 and asked:
            x, y = (data[4] << 8) + data[5], (data[6] << 8) + data[7]
            tiles.append((x, y, data[8:]))
            try:
                tile_width, tile_height = Image.open(io.BytesIO(data[8:])).size
                covered += tile_width * tile_height
            except (UnidentifiedImageError, OSError, ValueError):
                pass
            last_tile = loop.time()
        elif cmd == ERROR:
            break
    return tiles, screen
