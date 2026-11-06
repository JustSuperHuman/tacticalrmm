/* JustConnect in the RMM dashboard (Access).
   nginx adds this script to the dashboard's page (support-site.sh), so the dashboard's
   own release stays untouched. It puts the JustConnect logo where the dashboard says
   "Tactical RMM", and the Support | Access switch beside it. Support is the remote
   support console, served from this same address at /support/manage/.

   Colors come from the JustUI tokens (--sh-*) when the dashboard skin
   (dashboard-skin/) is installed; the fallbacks suit the stock dark grey header. */
(() => {
  "use strict";

  const here = document.currentScript?.src || location.origin + "/static/support/access.js";
  const asset = (path) => new URL(path, here).href;
  const OLD_NAME = "Tactical RMM";
  const NAME = "JustConnect";
  const SUPPORT_URL = "/support/manage/";
  const LIGHT = asset("brand/justconnect.webp");
  const DARK = asset("brand/justconnect-dark.webp");

  const icon = (body) => `<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  const HEADSET = icon('<path d="M3 11h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Zm0 0a9 9 0 1 1 18 0m0 0v5a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3Z"/><path d="M21 16v2a4 4 0 0 1-4 4h-5"/>');
  const ACCESS = icon('<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>');
  const wordmarks = (cls) =>
    `<img class="${cls} jc-on-light" src="${LIGHT}" alt="${NAME}" width="807" height="160">` +
    `<img class="${cls} jc-on-dark" src="${DARK}" alt="${NAME}" width="807" height="160">`;

  const style = document.createElement("style");
  style.textContent = `
    @font-face { font-family: "JC Geist"; src: url("${asset("superhuman/fonts/Geist-Variable.woff2")}") format("woff2"); font-weight: 100 900; font-display: swap; }
    .jc-brand { display: inline-flex; flex: none; align-items: center; gap: 16px; margin-right: 4px; vertical-align: middle; }
    .jc-wordmark { display: block; width: auto; height: 24px; }
    .jc-login { display: block; width: auto; height: 40px; max-width: 100%; margin: 0 auto; }

    /* Which copy of the logo shows. The stock header is dark grey in both themes; with
       the skin it follows the theme. The sign-in card always follows the theme. */
    .jc-wordmark.jc-on-light, .jc-login.jc-on-dark, .body--dark .jc-login.jc-on-light { display: none; }
    .body--dark .jc-login.jc-on-dark { display: block; }
    .jc-themed .body--light .jc-wordmark.jc-on-light { display: block; }
    .jc-themed .body--light .jc-wordmark.jc-on-dark { display: none; }

    .jc-switch { display: inline-flex; flex: none; gap: 2px; padding: 2px; border-radius: 8px;
      background: var(--sh-surface-3, rgba(0,0,0,.2)); box-shadow: inset 0 0 0 1px var(--sh-line, rgba(255,255,255,.12));
      font: 500 13px/1 var(--sh-font, "JC Geist", ui-sans-serif, system-ui, sans-serif); letter-spacing: 0; }
    /* Specific enough to keep its own colors over the skin's link color. */
    html body .jc-brand .jc-switch > a.jc-tab[href] { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 11px; border-radius: 6px;
      color: var(--sh-muted, rgba(255,255,255,.68)); text-decoration: none; transition: color .12s ease, background-color .12s ease; }
    html body .jc-brand .jc-switch > a.jc-tab[href]:hover { color: var(--sh-ink, #fff); }
    html body .jc-brand .jc-switch > a.jc-tab[href]:focus-visible { outline: 2px solid var(--sh-accent, #8fa4ff); outline-offset: 1px; }
    html body .jc-brand .jc-switch > a.jc-tab[aria-current="page"] { background: var(--sh-surface, #fff); color: var(--sh-ink, #0b0d10);
      box-shadow: var(--sh-shadow-sm, 0 1px 2px rgba(0,0,0,.3)), 0 0 0 1px var(--sh-line, transparent); }
    .jc-switch svg { width: 14px; height: 14px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

    @media (max-width: 599px) {
      .jc-brand { gap: 10px; }
      .jc-wordmark { height: 20px; }
      html body .jc-brand .jc-switch > a.jc-tab[href] { padding: 0 8px; }
      .jc-switch a span { display: none; }
    }
  `;
  document.head.appendChild(style);

  // Blank the old name in place: the dashboard keeps its reference to that text,
  // so it never notices the logo beside it.
  function takeOver(container) {
    const text = [...container.childNodes].find(
      (n) => n.nodeType === Node.TEXT_NODE && n.nodeValue.includes(OLD_NAME),
    );
    if (text) text.nodeValue = text.nodeValue.replace(OLD_NAME, "");
    return text;
  }

  function brandHeader() {
    const title = document.querySelector(".q-header .q-toolbar .q-toolbar__title");
    if (!title || title.querySelector(".jc-brand")) return;
    const text = takeOver(title);
    if (!text) return;
    const brand = document.createElement("span");
    brand.className = "jc-brand";
    brand.innerHTML =
      wordmarks("jc-wordmark") +
      `<nav class="jc-switch" aria-label="Support or Access">` +
      `<a class="jc-tab" href="${SUPPORT_URL}">${HEADSET}<span>Support</span></a>` +
      `<a class="jc-tab" href="/" aria-current="page">${ACCESS}<span>Access</span></a>` +
      `</nav>`;
    // You're already in Access; leave the dashboard where it is.
    brand.querySelector('[aria-current="page"]').addEventListener("click", (e) => e.preventDefault());
    title.insertBefore(brand, text);
  }

  function brandLogin() {
    for (const heading of document.querySelectorAll(".q-card .text-h4")) {
      if (heading.querySelector(".jc-login") || !heading.textContent.includes(OLD_NAME)) continue;
      const text = takeOver(heading);
      if (!text) continue;
      const logo = document.createElement("span");
      logo.innerHTML = wordmarks("jc-login");
      logo.lastElementChild.alt = "";
      heading.insertBefore(logo, text);
    }
  }

  function brandTitle() {
    if (document.title.includes(OLD_NAME)) document.title = document.title.replaceAll(OLD_NAME, NAME);
  }

  function brandIcon() {
    for (const link of document.querySelectorAll('link[rel~="icon"]')) link.remove();
    const link = document.createElement("link");
    link.rel = "icon";
    link.type = "image/png";
    link.href = asset("brand/favicon.png");
    document.head.appendChild(link);
  }

  // The skin sets the JustUI tokens on <body>; then the header follows the theme.
  function noteSkin() {
    const themed = !!document.body && getComputedStyle(document.body).getPropertyValue("--sh-ink").trim() !== "";
    document.documentElement.classList.toggle("jc-themed", themed);
  }

  let queued = false;
  function apply() {
    queued = false;
    noteSkin();
    brandHeader();
    brandLogin();
    brandTitle();
  }
  const soon = () => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(apply);
    }
  };

  brandIcon();
  apply();
  // The dashboard draws its pages after this runs and redraws them as you move
  // around, so check again whenever the page changes.
  new MutationObserver(soon).observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
})();
