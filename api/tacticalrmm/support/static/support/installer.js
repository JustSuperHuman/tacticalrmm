"use strict";
(() => {
  const form = document.getElementById("installer-form");
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  const status = document.getElementById("installer-status");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "Generating your installer…";
    status.textContent = "Preparing the Windows installer. This can take a moment.";
    try {
      const response = await fetch(form.action || location.href, { method: "POST", body: new FormData(form) });
      if (!response.ok || !response.headers.get("Content-Disposition")?.includes("attachment")) {
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        throw new Error(page.querySelector('[role="alert"], .sp-error')?.textContent || "The installer could not be generated. Try again or contact your administrator.");
      }
      const file = await response.blob();
      const name = response.headers.get("Content-Disposition").match(/filename="?([^";]+)"?/i)?.[1] || "JustConnect-Monitoring.msi";
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url; link.download = name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = "Your MSI is downloading. Open it on the computer you want to monitor.";
    } catch (error) {
      status.textContent = error.message || "The download did not finish. Check your connection and try again.";
    } finally {
      button.disabled = false;
      button.textContent = "Generate and download MSI";
    }
  });
})();
