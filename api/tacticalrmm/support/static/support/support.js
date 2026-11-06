"use strict";

// Everything here is an enhancement. The pages work with plain form posts.
(() => {
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

  // Show times in the visitor's own time zone.
  const today = new Date().toDateString();
  document.querySelectorAll("time[data-local]").forEach((time) => {
    const date = new Date(time.getAttribute("datetime"));
    if (Number.isNaN(date.getTime())) return;
    const options = { hour: "numeric", minute: "2-digit" };
    if (date.toDateString() !== today) Object.assign(options, { month: "short", day: "numeric" });
    time.textContent = date.toLocaleString([], options);
  });

  const status = document.getElementById("download-status");
  document.querySelectorAll("form[data-download]").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button");
      // Each click counts toward the code's download limit, so block double clicks.
      setTimeout(() => { button.disabled = true; });
      setTimeout(() => { button.disabled = false; }, 5000);
      status.textContent = "Your download is starting. Check your browser’s downloads.";
      document.querySelectorAll("[data-steps]").forEach((list) => {
        list.hidden = list.dataset.steps !== form.dataset.platform;
      });
    });
  });
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[data-download] button").forEach((button) => { button.disabled = false; });
  });

  const copyStatus = document.getElementById("copy-status");
  document.querySelectorAll("button[data-copy]").forEach((button) => {
    const label = button.querySelector("span");
    const original = label.textContent;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        label.textContent = "Copied";
        copyStatus.textContent = `${original.replace("Copy ", "")} copied.`;
      } catch {
        const range = document.createRange();
        range.selectNodeContents(document.getElementById("new-code"));
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        label.textContent = "Press Ctrl+C";
        copyStatus.textContent = "The code is selected. Press Ctrl+C to copy it.";
      }
      setTimeout(() => { label.textContent = original; }, 2000);
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  // While an agent install is pending, check again now and then. Never
  // interrupt someone typing a command or reading output.
  const waiting = document.querySelector("[data-refresh]");
  if (waiting) {
    setInterval(() => {
      const command = document.getElementById("command");
      const busy = document.querySelector(".sp-output") || (command && command.value.trim())
        || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
      if (!busy) window.location.replace(window.location.pathname);
    }, Number(waiting.dataset.refresh) * 1000);
  }

  const logoInput = document.getElementById("logo");
  const logoPreview = document.getElementById("logo-preview");
  logoInput?.addEventListener("change", () => {
    const file = logoInput.files[0];
    if (file) logoPreview.src = URL.createObjectURL(file);
  });
})();
