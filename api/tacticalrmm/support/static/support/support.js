"use strict";

// Everything here is an enhancement. The pages work with plain links and form posts.
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const typing = (el) => el && (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) || el.isContentEditable);

  /* ---------- Public page ---------- */

  const code = document.getElementById("access-code");
  const codeError = document.getElementById("code-error");
  if (code) {
    const submit = code.form.querySelector("[type=submit]");
    const label = submit.querySelector("span");
    code.addEventListener("input", () => {
      // Show the code as 123 456 789 while typing. The server accepts any spacing.
      if (/^[\d\s-]*$/.test(code.value)) {
        const digits = code.value.replace(/\D/g, "").slice(0, 9);
        const atEnd = code.selectionStart === code.value.length;
        code.value = digits.replace(/(\d{3})(?=\d)/g, "$1 ");
        if (atEnd) code.setSelectionRange(code.value.length, code.value.length);
      }
      code.removeAttribute("aria-invalid");
      codeError.hidden = true;
    });
    code.form.addEventListener("submit", (event) => {
      if (!/^\d{9}$/.test(code.value.replace(/[\s-]/g, ""))) {
        event.preventDefault();
        codeError.textContent = "Enter all 9 digits of your code.";
        codeError.hidden = false;
        code.setAttribute("aria-invalid", "true");
        code.focus();
        return;
      }
      // Disable after the browser has captured the form data.
      setTimeout(() => { submit.disabled = true; label.textContent = "Checking your code…"; });
    });
    // Coming back with the Back button restores the page as it was left.
    window.addEventListener("pageshow", () => { submit.disabled = false; label.textContent = "Continue"; });
  }

  // iPads report themselves as Macs. Touch support gives them away.
  if (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1) {
    document.getElementById("mobile-note")?.removeAttribute("hidden");
  }

  const downloadStatus = document.getElementById("download-status");
  $$("form[data-download]").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button");
      // Each click counts toward the code's download limit, so block double clicks.
      setTimeout(() => { button.disabled = true; });
      setTimeout(() => { button.disabled = false; }, 5000);
      downloadStatus.textContent = "Your download is starting. Check your browser’s downloads.";
      $$("[data-steps]").forEach((list) => { list.hidden = list.dataset.steps !== form.dataset.platform; });
    });
  });
  window.addEventListener("pageshow", () => {
    $$("form[data-download] button").forEach((button) => { button.disabled = false; });
  });

  /* ---------- Shared ---------- */

  // Times in the visitor's own time zone.
  const localTimes = (root = document) => $$("time[data-local]", root).forEach((time) => {
    const date = new Date(time.getAttribute("datetime"));
    if (Number.isNaN(date.getTime())) return;
    const options = { hour: "numeric", minute: "2-digit" };
    if (date.toDateString() !== new Date().toDateString()) Object.assign(options, { month: "short", day: "numeric" });
    time.textContent = date.toLocaleString([], options);
    time.title = date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  });

  // "12 min", "2 h 5 min", kept current.
  const ago = (root = document) => $$("time[data-ago]", root).forEach((time) => {
    const minutes = Math.floor((Date.now() - new Date(time.getAttribute("datetime"))) / 60000);
    if (Number.isNaN(minutes)) return;
    const hours = Math.floor(minutes / 60);
    time.textContent = minutes < 1 ? "under a minute"
      : hours < 1 ? `${minutes} min`
      : hours < 24 ? `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`
      : `${Math.floor(hours / 24)} d`;
  });

  let toastTimer;
  const toast = (message, error) => {
    $(".sh-toast")?.remove();
    const el = document.createElement("div");
    el.className = `sh-toast${error ? " sh-toast--error" : ""}`;
    el.setAttribute("role", error ? "alert" : "status");
    el.textContent = message;
    document.body.append(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.remove(), error ? 6000 : 3200);
  };

  const announce = (text) => { const el = $("#announce"); if (el) el.textContent = text; };

  // Slow actions say what they're doing.
  const busyButtons = new Map();
  const setBusy = (button) => {
    busyButtons.set(button, button.innerHTML);
    button.disabled = true;
    button.textContent = button.dataset.busyLabel || "Working…";
  };
  const release = (button) => {
    if (!busyButtons.has(button)) return;
    button.innerHTML = busyButtons.get(button);
    button.disabled = false;
    busyButtons.delete(button);
  };
  window.addEventListener("pageshow", () => [...busyButtons.keys()].forEach(release));

  /* ---------- Commands ---------- */

  const PROMPTS = { 1: ">", 2: "PS>", 3: "$" };
  // Not form.action: these forms have buttons named "action", which shadow it.
  const target = (form) => form.getAttribute("action") || location.pathname;
  const scrollConsole = (root = document) => {
    const log = $("#console-log", root);
    if (log) log.scrollTop = log.scrollHeight;
  };

  const entry = (run) => {
    const article = document.createElement("article");
    article.className = `sp-entry${run.noReply ? " sp-entry--quiet" : ""}${run.pending ? " sp-entry--pending" : ""}`;
    const head = document.createElement("header");
    head.className = "sp-entry-head";
    const who = document.createElement("span");
    who.textContent = run.user || "You";
    const when = document.createElement("time");
    if (run.ranAt) { when.setAttribute("datetime", run.ranAt); when.dataset.local = ""; } else when.textContent = "now";
    head.append(who, when);
    const command = document.createElement("pre");
    command.className = "sp-entry-cmd";
    const prompt = document.createElement("span");
    prompt.className = "sp-prompt";
    prompt.setAttribute("aria-hidden", "true");
    prompt.textContent = run.prompt;
    command.append(prompt, run.command);
    const output = document.createElement("pre");
    output.className = "sp-entry-out";
    output.textContent = run.noReply ? run.message : run.output;
    article.append(head, command, output);
    localTimes(article);
    return article;
  };

  const bumpCount = (tabId) => {
    const tab = document.getElementById(tabId);
    if (!tab) return;
    let count = $(".sp-tab-count", tab);
    if (!count) { count = document.createElement("span"); count.className = "sp-tab-count"; count.textContent = "0"; tab.append(count); }
    count.textContent = String(Number(count.textContent) + 1);
  };

  const runCommand = async (form, button) => {
    const box = $("#command", form);
    const saved = $("#saved", form);
    const text = box.value.trim() ? box.value : saved?.selectedOptions[0]?.dataset.command || "";
    if (!text.trim()) { toast("Enter a command to run, or choose one from the toolbox.", true); box.focus(); return; }
    const log = $("#console-log");
    const shell = form.elements.shell?.value;
    const pending = entry({ command: text, prompt: PROMPTS[shell] || "$", output: "Running on their computer…", pending: true });
    $("[data-console-empty]", log)?.remove();
    log.append(pending);
    log.scrollTop = log.scrollHeight;
    const body = new FormData(form);
    body.set("action", "run");
    setBusy(button);
    try {
      const response = await fetch(target(form), { method: "POST", body, headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.run) throw new Error(data.error || "The command couldn’t be sent. Try again.");
      pending.replaceWith(entry(data.run));
      bumpCount("tab-commands");
      announce(data.run.noReply ? "No reply yet." : "Command finished.");
    } catch (error) {
      pending.remove();
      toast(error.message, true);
    } finally {
      release(button);
      log.scrollTop = log.scrollHeight;
    }
  };

  // Save to the toolbox without leaving the session.
  const saveCommand = async (form, button) => {
    const name = $("#save-name", form);
    const status = $("#save-status", form);
    const box = $("#command", form);
    const say = (text, error) => { status.textContent = text; status.toggleAttribute("data-error", Boolean(error)); };
    if (!name.value.trim()) { say("Give the command a name first.", true); name.focus(); return; }
    if (!box.value.trim()) { say("Enter or choose a command to save.", true); box.focus(); return; }
    const body = new FormData(form);
    body.set("action", "save");
    setBusy(button);
    try {
      const response = await fetch(target(form), { method: "POST", body, headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Couldn’t save the command. Try again.");
      say(`Saved to the toolbox as “${data.saved}”.`);
      const picker = $("#saved", form);
      if (picker) {
        const option = new Option(data.saved, data.id);
        option.dataset.command = box.value;
        option.dataset.shell = body.get("shell");
        picker.add(option);
      }
      name.value = "";
      name.closest("details").open = false;
    } catch (error) {
      say(error.message, true);
    } finally {
      release(button);
    }
  };

  /* ---------- Forms ---------- */

  document.addEventListener("submit", (event) => {
    const form = event.target;
    const button = event.submitter;
    if (event.defaultPrevented) return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
    if (form.id === "run-form" && button && ["run", "save"].includes(button.value)) {
      event.preventDefault();
      if (button.value === "run") runCommand(form, button);
      else saveCommand(form, button);
      return;
    }
    if (!form.matches("[data-busy]") || !button || button.hasAttribute("data-no-busy")) return;
    // Disable after the browser has captured the form data, including the button's value.
    setTimeout(() => {
      setBusy(button);
      // Forms that open a new tab leave this page as it was.
      if (form.matches("[data-busy-reset]")) setTimeout(() => release(button), 2500);
    });
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    // Choosing a saved command puts it in the box, ready to check and run.
    if (target.id === "saved") {
      const option = target.selectedOptions[0];
      const box = $("#command", target.form);
      if (!option?.value || !box) return;
      box.value = option.dataset.command;
      if (target.form.elements.shell?.tagName === "SELECT") target.form.elements.shell.value = option.dataset.shell;
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
    if (target.id === "logo" && target.files[0]) {
      const preview = $("#logo-preview");
      if (preview) {
        // A <source> for dark mode would keep showing the default logo.
        preview.closest("picture")?.querySelectorAll("source").forEach((s) => s.remove());
        preview.src = URL.createObjectURL(target.files[0]);
      }
    }
  });

  /* ---------- Workspace ---------- */

  const app = $("#app");
  const host = $(".sp-host");
  const narrow = window.matchMedia("(max-width: 900px)");
  let group = host?.dataset.group || "all";
  let shownVersion = "";

  const closeNav = () => app?.removeAttribute("data-nav-open");

  const withGroup = (path, extra = {}) => {
    const url = new URL(path, location.origin);
    url.search = "";
    if (group !== "all") url.searchParams.set("group", group);
    Object.entries(extra).forEach(([key, value]) => value && url.searchParams.set(key, value));
    return url.pathname + url.search;
  };

  const rowFor = (id) => (id ? $$(".sp-row").find((row) => row.dataset.id === id) : null);
  const selectedId = () => $("#detail")?.dataset.for || "";
  const markSelected = () => {
    const id = selectedId();
    $$(".sp-row").forEach((row) => {
      if (row.dataset.id === id) row.setAttribute("aria-current", "page");
      else row.removeAttribute("aria-current");
    });
  };

  const filter = () => {
    const list = $("#session-list");
    if (!list) return;
    const query = ($("#session-search")?.value || "").trim().toLowerCase();
    let inGroup = 0;
    let shown = 0;
    $$(".sp-rows > li", list).forEach((item) => {
      const member = item.dataset.groups.split(" ").includes(group);
      const match = !query || item.dataset.search.includes(query);
      item.hidden = !(member && match);
      if (member) inGroup += 1;
      if (!item.hidden) shown += 1;
    });
    $$("[data-empty-for]", list).forEach((empty) => {
      const key = empty.dataset.emptyFor;
      empty.hidden = !(inGroup === 0 ? key === group : shown === 0 && key === "search");
    });
  };

  const setGroup = (key, label) => {
    group = key;
    host.dataset.group = key;
    $$("#session-nav [data-group]").forEach((link) => {
      if (link.dataset.group === key) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    filter();
    closeNav();
    if (narrow.matches && host.hasAttribute("data-selected")) { navigate(withGroup(host.dataset.list)); return; }
    const url = new URL(location.href);
    if (key === "all") url.searchParams.delete("group"); else url.searchParams.set("group", key);
    history.replaceState(history.state, "", url);
    if (!selectedId()) { const crumb = $("#crumb"); if (crumb) crumb.textContent = label; }
    $$("[data-back]").forEach((back) => { back.href = withGroup(host.dataset.list); });
  };

  const selectTab = (tab) => {
    const list = tab.closest("[role=tablist]");
    $$("[role=tab]", list).forEach((other) => {
      const on = other === tab;
      other.setAttribute("aria-selected", String(on));
      other.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(other.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    const url = new URL(location.href);
    url.searchParams.set("tab", tab.id.replace("tab-", ""));
    history.replaceState(history.state, "", url);
    if (tab.id === "tab-commands") scrollConsole();
  };

  const enhance = (root) => {
    localTimes(root);
    ago(root);
    scrollConsole(root);
    $$("[role=tab]", root).forEach((tab) => { tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1; });
    shownVersion = rowFor(selectedId())?.dataset.version || "gone";
    // A new session's page is rendered by the form post, so give it its own address.
    const own = root.dataset?.url;
    if (own && own !== location.pathname) history.replaceState(history.state, "", withGroup(own));
  };

  // Don't swap the session out from under someone who's in the middle of something.
  const busy = () => {
    const detail = $("#detail");
    if (!detail || document.hidden || busyButtons.size || $("dialog[open]")) return true;
    if (detail.contains(document.activeElement) && typing(document.activeElement)) return true;
    if ($$("textarea, #save-name", detail).some((field) => field.value.trim())) return true;
    return Boolean($(".sp-output, details[open]:not(.sp-details)", detail));
  };

  let navigation = 0;
  const navigate = async (url, push = true) => {
    const ticket = ++navigation;
    const detail = $("#detail");
    detail?.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(url, { headers: { Accept: "text/html" } });
      if (ticket !== navigation) return;
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const next = doc.getElementById("detail");
      if (!next || !$(".sp-host", doc)) { location.href = url; return; }
      $("#detail").replaceWith(next);
      document.title = doc.title;
      const crumb = doc.getElementById("crumb");
      if (crumb) $("#crumb").textContent = crumb.textContent;
      host.toggleAttribute("data-selected", Boolean(next.dataset.for));
      if (push) history.pushState({ support: true }, "", url);
      markSelected();
      enhance(next);
      next.scrollTop = 0;
      announce(`Showing ${crumb?.textContent || "the session"}.`);
    } catch {
      location.href = url;
    }
  };

  // A newer picture of the open session's screen: swap it in once it has loaded.
  const swapScreen = () => {
    const version = rowFor(selectedId())?.dataset.screen;
    const image = $("#screen-preview");
    if (!version || !image) return;
    const current = (image.getAttribute("src").match(/[?&]v=(\d+)/) || [])[1];
    if (current === version) return;
    const next = new Image();
    next.onload = () => {
      image.src = next.src;
      const age = $("#screen-age time");
      if (age) { age.setAttribute("datetime", new Date(Number(version) * 1000).toISOString()); ago($("#screen-age")); }
    };
    next.src = image.getAttribute("src").replace(/([?&]v=)\d+/, `$1${version}`);
  };

  const refresh = async () => {
    if (!host || document.hidden) return;
    try {
      // Say which session is open, so its picture is kept fresher than the rest.
      const response = await fetch(withGroup(host.dataset.list, { open: selectedId() }), { headers: { Accept: "text/html" } });
      if (!response.ok) return;
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const fresh = doc.getElementById("session-list");
      const list = $("#session-list");
      if (!fresh || !list) return;
      const scroll = list.scrollTop;
      list.innerHTML = fresh.innerHTML;
      list.scrollTop = scroll;
      $$("#session-nav [data-group]").forEach((link) => {
        const count = doc.querySelector(`#session-nav [data-group="${link.dataset.group}"] .sh-count`);
        const mine = $(".sh-count", link);
        if (count && mine) mine.textContent = count.textContent;
      });
      markSelected();
      filter();
      localTimes(list);
      ago(list);
      swapScreen();
      // The open session changed: it connected, went offline, or its agent checked in.
      const id = selectedId();
      if (id && (rowFor(id)?.dataset.version || "gone") !== shownVersion && !busy()) navigate(location.href, false);
    } catch {
      // Try again on the next tick.
    }
  };

  if (host) {
    markSelected();
    filter();
    enhance($("#detail"));
    setInterval(refresh, Number($("#session-list")?.dataset.live || 10) * 1000);
    window.addEventListener("popstate", () => navigate(location.href, false));
    $("#session-search")?.addEventListener("input", filter);
    // On a phone the list is hidden while a session is open, so say what happened.
    if (narrow.matches) $$("[data-toast]").forEach((note) => toast(note.textContent));
  }

  const dialog = $("#new-session");
  if (dialog?.open && dialog.showModal) { dialog.close(); dialog.showModal(); }

  document.addEventListener("click", (event) => {
    const target = event.target;
    const plain = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

    const copy = target.closest("button[data-copy]");
    if (copy) {
      const label = $("span", copy);
      const original = label.textContent;
      const status = $("#copy-status");
      navigator.clipboard.writeText(copy.dataset.copy).then(() => {
        label.textContent = "Copied";
        if (status) status.textContent = `${original.replace("Copy ", "")} copied.`;
      }, () => {
        const range = document.createRange();
        range.selectNodeContents($("#new-code"));
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        label.textContent = "Press Ctrl+C";
        if (status) status.textContent = "The code is selected. Press Ctrl+C to copy it.";
      });
      setTimeout(() => { label.textContent = original; }, 2000);
      return;
    }

    // Remote control opens in its own window, like a desktop app.
    const app = target.closest("a[data-window]");
    if (app && plain) {
      const width = Math.min(1600, screen.availWidth), height = Math.min(960, screen.availHeight);
      const opened = window.open(app.href, app.dataset.window, `popup,width=${width},height=${height}`);
      if (opened) { event.preventDefault(); opened.focus(); }
      return;
    }

    const opener = target.closest("[data-open]");
    if (opener && plain) {
      const modal = document.getElementById(opener.dataset.open);
      if (modal?.showModal) {
        event.preventDefault();
        closeNav();
        if (!modal.open) modal.showModal();
        $("input:not([type=hidden]), select", modal)?.focus();
      }
      return;
    }
    const closer = target.closest("[data-close]");
    if (closer && closer.closest("dialog")) { event.preventDefault(); closer.closest("dialog").close(); return; }

    if (target.closest("[data-nav-toggle]")) { app?.toggleAttribute("data-nav-open"); return; }
    if (target.closest("[data-nav-close]")) { closeNav(); return; }

    const tab = target.closest(".sp-tabs [role=tab]");
    if (tab && plain) { event.preventDefault(); selectTab(tab); return; }

    if (!host || !plain) return;
    const groupLink = target.closest("#session-nav [data-group]");
    if (groupLink) { event.preventDefault(); setGroup(groupLink.dataset.group, $("span", groupLink).textContent); return; }
    const link = target.closest(".sp-row, [data-nav], [data-back]");
    if (link) {
      event.preventDefault();
      navigate(link.matches("[data-back]") ? withGroup(host.dataset.list) : withGroup(link.getAttribute("href")));
    }
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    // Ctrl+Enter (⌘ Return on a Mac) runs the command.
    if (target.id === "command" && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      const run = $(".sp-run", target.form);
      if (run && !run.disabled) { event.preventDefault(); target.form.requestSubmit(run); }
      return;
    }
    // Enter in the name box saves rather than running the command.
    if (target.id === "save-name" && event.key === "Enter") {
      event.preventDefault();
      target.form.requestSubmit($("[value=save]", target.form));
      return;
    }
    if (target.id === "session-search" && event.key === "Escape") { target.value = ""; filter(); target.blur(); return; }
    if (target.matches?.(".sp-tabs [role=tab]") && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      const tabs = $$("[role=tab]", target.closest("[role=tablist]"));
      const index = tabs.indexOf(target);
      const next = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: tabs.length - 1 }[event.key];
      const tab = tabs[(next + tabs.length) % tabs.length];
      event.preventDefault();
      tab.focus();
      selectTab(tab);
      return;
    }
    if (event.key === "/" && !typing(target) && $("#session-search") && !$("dialog[open]")) {
      event.preventDefault();
      if (narrow.matches && host?.hasAttribute("data-selected")) navigate(withGroup(host.dataset.list));
      $("#session-search").focus();
    }
    if (event.key === "Escape" && app?.hasAttribute("data-nav-open")) closeNav();
  });

  localTimes();
  ago();
  setInterval(() => ago(), 30000);
})();
