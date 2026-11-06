"use strict";

// What MeshCentral's desktop client (vendor/meshcentral/agent-desktop.js) expects
// to find on the page.
window.Q = window.Q || ((id) => document.getElementById(id));
window.urlargs = window.urlargs || {};
// From MeshCentral's common.js: two bytes, big-endian, as a binary string.
window.ShortToStr = window.ShortToStr || ((v) => String.fromCharCode((v >> 8) & 0xFF, v & 0xFF));
window.rstr2hex = window.rstr2hex || ((s) => [...s].map((c) => c.charCodeAt(0).toString(16).padStart(2, "0")).join(""));

// The remote control window. MeshCentral's client draws their screen and turns our
// mouse and keyboard into input; this file connects it and runs the toolbar.
document.addEventListener("DOMContentLoaded", () => {
  const viewer = document.getElementById("viewer");
  if (!viewer || typeof CreateAgentRemoteDesktop !== "function") return;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const data = JSON.parse($("#control-data").textContent);
  const canvas = $("#screen");
  const stage = $("#stage");

  const ZOOMS = [25, 33, 50, 67, 75, 90, 100, 110, 125, 150, 175, 200, 300];
  const QUALITY = {
    best: { level: 85, scaling: 1024, frame: 50, label: "Best quality" },
    balanced: { level: 55, scaling: 1024, frame: 100, label: "Balanced" },
    fast: { level: 30, scaling: 768, frame: 150, label: "Fastest" },
  };
  const KEYS = {
    win: [[5, 0x5b], [4, 0x5b]],
    alttab: [[1, 18], [1, 9], [2, 9], [2, 18]],
    esc: [[1, 27], [2, 27]],
    taskmgr: [[1, 17], [1, 16], [1, 27], [2, 27], [2, 16], [2, 17]],
    run: [[5, 0x5b], [1, 82], [2, 82], [4, 0x5b]],
    lock: [[5, 0x5b], [1, 76], [2, 76], [4, 0x5b]],
    prtsc: [[1, 44], [2, 44]],
  };
  const remember = {
    get: (key, fallback) => { try { return localStorage.getItem(`sp-control-${key}`) || fallback; } catch { return fallback; } },
    set: (key, value) => { try { localStorage.setItem(`sp-control-${key}`, value); } catch { /* private mode */ } },
  };

  let zoom = remember.get("zoom", "fit");
  let quality = QUALITY[remember.get("quality", "balanced")] ? remember.get("quality", "balanced") : "balanced";
  let desk = null;
  let socket = null;
  let pending = null;
  let connectedAt = 0;
  let displays = {};
  let display = null;
  let rtt = null;
  let clock = null;
  let ended = false;

  /* ---------- State and overlay ---------- */

  const overlay = {
    title: $("#overlay-title"), text: $("#overlay-text"),
    retry: $("#overlay-retry"), close: $("#overlay-close"), back: $("#overlay-back"),
  };
  const STATES = {
    connecting: () => [`Connecting to ${data.computer}…`, "This usually takes a few seconds.", "Connecting"],
    waiting: () => ["Waiting for them to allow it", `${data.computer} is asking whoever’s there to allow remote control.`, "Waiting"],
    offline: () => [`${data.computer} is offline`, data.open_action ? "Ask them to open the support app, then try again." : "Its agent isn’t checking in right now. Try again when it’s back online.", "Offline"],
    ended: (text) => ["Disconnected", text || `The connection to ${data.computer} ended.`, "Disconnected"],
    error: (text) => ["Couldn’t connect", text || "Something went wrong. Try again.", "Not connected"],
    live: () => ["", "", "Connected"],
  };

  const setState = (state, text) => {
    viewer.dataset.state = state;
    const [title, body, label] = STATES[state](text);
    overlay.title.textContent = title;
    overlay.text.textContent = body;
    $("#state-text").textContent = label;
    const done = ["ended", "error", "offline"].includes(state);
    overlay.retry.hidden = !done;
    overlay.retry.textContent = state === "ended" ? "Reconnect" : "Try again";
    overlay.close.hidden = !done || !window.opener;
    overlay.back.hidden = !done || Boolean(window.opener);
    if (state === "live") {
      if (!connectedAt) connectedAt = Date.now();
      clock = clock || setInterval(tick, 1000);
      grabInput();
      canvas.focus();
    } else {
      releaseInput();
      clearInterval(clock); clock = null;
      if (done) connectedAt = 0;
      $("#timer").textContent = "";
    }
    document.title = `${state === "live" ? "" : `${label} · `}${data.title} · Remote control`;
  };

  const tick = () => {
    const seconds = Math.floor((Date.now() - connectedAt) / 1000);
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    $("#timer").textContent = `${h ? `${h}:` : ""}${String(m).padStart(h ? 2 : 1, "0")}:${String(s).padStart(2, "0")}`;
  };

  /* ---------- Input ---------- */

  let menuOpen = false;
  const grabInput = () => {
    if (!desk || viewer.dataset.state !== "live" || menuOpen || $("#type-dialog").open) return;
    desk.GrabMouseInput();
    desk.GrabKeyInput();
  };
  const releaseInput = () => {
    if (!desk) return;
    desk.UnGrabKeyInput();
  };
  const keys = (sequence) => sequence.forEach(([action, code]) => desk && desk.SendKeyMsgKC(action, code));

  /* ---------- Zoom ---------- */

  const scale = () => {
    if (zoom !== "fit") return Number(zoom) / 100;
    return Math.min(stage.clientWidth / canvas.width, stage.clientHeight / canvas.height);
  };
  const fit = () => {
    if (!canvas.width || !canvas.height) return;
    const factor = scale();
    canvas.style.width = `${Math.max(1, Math.round(canvas.width * factor))}px`;
    canvas.style.height = `${Math.max(1, Math.round(canvas.height * factor))}px`;
    $("#zoom-label").textContent = zoom === "fit" ? "Fit" : `${zoom}%`;
    $$("[data-zoom]").forEach((item) => item.setAttribute("aria-checked", String(item.dataset.zoom === zoom)));
    $("#st-zoom").textContent = `Zoom ${Math.round(factor * 100)}%${zoom === "fit" ? " (fit)" : ""}`;
  };
  const setZoom = (value) => {
    zoom = String(value);
    remember.set("zoom", zoom);
    fit();
  };
  const stepZoom = (direction) => {
    const current = Math.round(scale() * 100);
    const next = direction > 0 ? ZOOMS.find((z) => z > current) : [...ZOOMS].reverse().find((z) => z < current);
    if (next) setZoom(next);
  };

  /* ---------- Quality and displays ---------- */

  const setQuality = (key, send = true) => {
    quality = key;
    remember.set("quality", key);
    const q = QUALITY[key];
    if (desk && send) desk.SendCompressionLevel(1, q.level, q.scaling, q.frame);
    $("#quality-label").textContent = q.label;
    $("#st-quality").textContent = q.label;
    $$("[data-quality]").forEach((item) => item.setAttribute("aria-checked", String(item.dataset.quality === key)));
  };

  const renderDisplays = () => {
    const list = $("#display-list");
    list.textContent = "";
    const numbers = Object.keys(displays).map(Number).sort((a, b) => (a === 65535) - (b === 65535) || a - b);
    const shown = numbers.filter((n) => n !== 0);
    if (!shown.length) {
      // One display: show it, so the menu still says what you're looking at.
      shown.push(1);
      display = 1;
      const note = document.createElement("p");
      note.className = "cv-displays-note";
      note.textContent = "Their computer has one display.";
      list.append(note);
    }
    shown.forEach((number) => {
      const all = number === 65535;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `cv-display${all ? " cv-display--all" : ""}`;
      button.setAttribute("role", "menuitemradio");
      button.setAttribute("aria-checked", String(number === display));
      button.dataset.display = String(number);
      const monitor = document.createElement("span");
      monitor.className = "cv-monitor";
      monitor.textContent = all ? "All" : String(number);
      const label = document.createElement("span");
      label.textContent = all ? "All displays" : `Display ${number}`;
      button.append(monitor, label);
      list.insertBefore(button, list.querySelector(".cv-displays-note"));
    });
    const count = shown.filter((n) => n !== 65535).length;
    const current = display === 65535 ? "All displays" : display ? `Display ${display}` : "Display";
    $("#display-label").textContent = current;
    $("#st-display").textContent = count > 1 ? `${current} of ${count}` : "";
  };

  /* ---------- Connection ---------- */

  const send = (x) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (typeof x === "string") {
      const bytes = new Uint8Array(x.length);
      for (let i = 0; i < x.length; i++) bytes[i] = x.charCodeAt(i);
      socket.send(bytes.buffer);
    } else socket.send(x);
  };
  const control = (message) => socket && socket.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ ctrlChannel: "102938", ...message }));

  const onControl = (text) => {
    let message;
    try { message = JSON.parse(text); } catch { return; }
    if (String(message.ctrlChannel) !== "102938") return;
    if (message.type === "ping") control({ type: "pong" });
    else if (message.type === "rtt" && typeof message.time === "number") $("#st-latency").textContent = `${Date.now() - message.time} ms`;
    else if (message.type === "console") {
      // MeshCentral's consent prompt: 1 is "waiting for the user to allow it".
      if (message.msgid === 1) setState("waiting");
      else if (message.msg && viewer.dataset.state !== "live") $("#overlay-text").textContent = message.msg;
    }
  };

  const onBinary = (buffer) => {
    let view = new Uint8Array(buffer);
    if (pending) {
      pending.parts.push(view);
      pending.length += view.byteLength;
      if (pending.length < pending.size) return;
      const all = new Uint8Array(pending.length);
      let offset = 0;
      pending.parts.forEach((part) => { all.set(part, offset); offset += part.byteLength; });
      const { cmd, size } = pending;
      pending = null;
      desk.ProcessBinaryCommand(cmd, size, all);
      return;
    }
    if (view.byteLength < 4) return;
    let cmd = (view[0] << 8) + view[1];
    let size = (view[2] << 8) + view[3];
    if (cmd === 27 && size === 8) {
      // A large message: the real header follows, with a 24-bit length.
      cmd = (view[8] << 8) + view[9];
      size = (view[5] << 16) + (view[6] << 8) + view[7];
      view = view.slice(8);
    }
    if (size > view.byteLength) { pending = { cmd, size, parts: [view], length: view.byteLength }; return; }
    desk.ProcessBinaryCommand(cmd, size, size < view.byteLength ? view.slice(0, size) : view);
  };

  const disconnect = () => {
    clearInterval(rtt); rtt = null;
    if (socket) { socket.onclose = null; try { socket.close(); } catch { /* already closed */ } socket = null; }
    if (desk) { desk.UnGrabMouseInput(); desk.UnGrabKeyInput(); }
  };

  const connect = async () => {
    disconnect();
    ended = false;
    pending = null;
    setState("connecting");
    let relay;
    try {
      const token = $("input[name=csrfmiddlewaretoken]")?.value || "";
      const response = await fetch(data.relay_url, { method: "POST", headers: { "X-CSRFToken": token, Accept: "application/json" }, credentials: "same-origin" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) { setState(response.status === 409 ? "offline" : "error", body.error); return; }
      relay = body.relay;
    } catch {
      setState("error", "Couldn’t reach the server. Check your connection, then try again.");
      return;
    }

    desk = CreateAgentRemoteDesktop(canvas, stage);
    const q = QUALITY[quality];
    Object.assign(desk, { CompressionLevel: q.level, ScalingLevel: q.scaling, FrameRateTimer: q.frame });
    // Measure the canvas where it actually is, so clicks land right when zoomed and scrolled.
    desk.GetPositionOfControl = (element) => {
      const box = element.getBoundingClientRect();
      return [box.left + window.scrollX, box.top + window.scrollY];
    };
    desk.onScreenSizeChange = () => {
      fit();
      $("#st-size").textContent = `${canvas.width} × ${canvas.height}`;
      if (viewer.dataset.state !== "live" && canvas.width > 16) setState("live");
    };
    desk.onDisplayinfo = (_, list, selected) => { displays = list || {}; display = selected; renderDisplays(); };
    desk.onRemoteInputLockChanged = (_, locked) => $("[data-act=block]").setAttribute("aria-pressed", String(Boolean(locked)));
    desk.parent = {
      send,
      setConsoleMessage: (text) => { if (text) $("#st-note").textContent = text; },
    };
    desk.Start();

    socket = new WebSocket(relay);
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        if ((event.data === "c" || event.data === "cr") && desk.State !== 3) {
          socket.send("2"); // MeshCentral's desktop protocol
          desk.xxStateChange(3);
          control({ type: "rtt", time: Date.now() });
          rtt = setInterval(() => control({ type: "rtt", time: Date.now() }), 10000);
        } else onControl(event.data);
        return;
      }
      onBinary(event.data);
    };
    socket.onclose = () => {
      clearInterval(rtt); rtt = null;
      if (!ended) setState("ended", viewer.dataset.state === "live" ? null : "The computer didn’t accept the connection. It may have gone offline.");
    };
  };

  /* ---------- Toolbar ---------- */

  const closeMenus = () => {
    $$(".cv-menu").forEach((menu) => { menu.hidden = true; });
    $$("[data-menu]").forEach((button) => button.setAttribute("aria-expanded", "false"));
    menuOpen = false;
    grabInput();
  };

  document.addEventListener("click", (event) => {
    const target = event.target;
    const menuButton = target.closest("[data-menu]");
    if (menuButton) {
      const menu = $(`#menu-${menuButton.dataset.menu}`);
      const opening = menu.hidden;
      closeMenus();
      if (opening) {
        menu.hidden = false;
        menuButton.setAttribute("aria-expanded", "true");
        menuOpen = true;
        releaseInput();
        if (menuButton.dataset.menu === "display" && desk) desk.GetDisplayNumbers();
      }
      return;
    }
    const keyItem = target.closest("[data-keys]");
    const zoomItem = target.closest("[data-zoom]");
    const qualityItem = target.closest("[data-quality]");
    const displayItem = target.closest("[data-display]");
    const act = target.closest("[data-act]")?.dataset.act;
    const step = target.closest("[data-zoom-step]");
    if (keyItem) keys(KEYS[keyItem.dataset.keys]);
    else if (zoomItem) setZoom(zoomItem.dataset.zoom);
    else if (qualityItem) setQuality(qualityItem.dataset.quality);
    else if (displayItem && desk) { desk.SetDisplay(Number(displayItem.dataset.display)); display = Number(displayItem.dataset.display); renderDisplays(); }
    else if (step) { stepZoom(Number(step.dataset.zoomStep)); return; }
    else if (act === "cad" && desk) desk.SendCtrlAltDelMsg();
    else if (act === "block" && desk) {
      const locked = $("[data-act=block]").getAttribute("aria-pressed") === "true";
      desk.SendRemoteInputLock(locked ? 0 : 1);
    } else if (act === "fullscreen") {
      if (document.fullscreenElement) document.exitFullscreen(); else viewer.requestFullscreen?.();
    } else if (act === "hidebar") { viewer.setAttribute("data-bar-hidden", ""); requestAnimationFrame(fit); }
    else if (act === "showbar") { viewer.removeAttribute("data-bar-hidden"); requestAnimationFrame(fit); }
    else if (act === "disconnect") { ended = true; disconnect(); setState("ended", "You disconnected."); }
    else if (act === "reconnect") connect();
    else if (act === "close") window.close();
    else if (act === "type") { closeMenus(); releaseInput(); $("#type-dialog").showModal(); $("#type-text").focus(); return; }
    else if (act === "clipboard") {
      closeMenus();
      navigator.clipboard?.readText().then((text) => { if (text && desk) desk.SendStringUnicode(text); }, () => {
        $("#st-note").textContent = "Your browser didn’t share the clipboard. Use Type text instead.";
      });
      return;
    }
    if (!target.closest(".cv-menu-wrap")) closeMenus();
    else if (keyItem || zoomItem || qualityItem || displayItem) closeMenus();
  });

  $("#type-dialog").addEventListener("close", () => {
    const dialog = $("#type-dialog");
    if (dialog.returnValue === "type" && desk) desk.SendStringUnicode($("#type-text").value);
    $("#type-text").value = "";
    grabInput();
    canvas.focus();
  });

  document.addEventListener("fullscreenchange", () => {
    const full = Boolean(document.fullscreenElement);
    $("#fullscreen-label").textContent = full ? "Exit full screen" : "Full screen";
    $("#fullscreen-icon").setAttribute("href", full ? "#i-minimize" : "#i-maximize");
    requestAnimationFrame(fit);
  });
  window.addEventListener("resize", () => { if (zoom === "fit") fit(); });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  // Escape closes our menus, rather than reaching their computer, while one is open.
  document.addEventListener("keydown", (event) => {
    if (!menuOpen || event.key !== "Escape") return;
    closeMenus();
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  window.addEventListener("beforeunload", () => { ended = true; disconnect(); });

  setQuality(quality, false);
  $$("[data-zoom]").forEach((item) => item.setAttribute("aria-checked", String(item.dataset.zoom === zoom)));
  $("#zoom-label").textContent = zoom === "fit" ? "Fit" : `${zoom}%`;
  renderDisplays();
  if (data.online) connect(); else setState("offline");
});
