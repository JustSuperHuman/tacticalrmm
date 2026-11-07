/* JustConnect in the RMM dashboard (Access).
   nginx adds this script to the dashboard's page (support-site.sh), so the dashboard's
   own release stays untouched. It puts the JustConnect logo where the dashboard says
   "Tactical RMM", and the Support | Access switch at the top of the client sidebar, in
   the same place, size and style as the console's own switch, so it doesn't move when
   you go between them. Support is the remote support console, served from this same
   address at /support/manage/.

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
    .jc-brand { display: inline-flex; flex: none; align-items: center; margin-right: 4px; vertical-align: middle; }
    .jc-wordmark { display: block; width: auto; height: 26px; }
    .jc-login { display: block; width: auto; height: 40px; max-width: 100%; margin: 0 auto; }

    /* Which copy of the logo shows. The stock header is dark grey in both themes; with
       the skin it follows the theme. The sign-in card always follows the theme. */
    .jc-wordmark.jc-on-light, .jc-login.jc-on-dark, .body--dark .jc-login.jc-on-light { display: none; }
    .body--dark .jc-login.jc-on-dark { display: block; }
    .jc-themed .body--light .jc-wordmark.jc-on-light { display: block; }
    .jc-themed .body--light .jc-wordmark.jc-on-dark { display: none; }

    /* The switch copies the console's (.sh-seg.sp-switch in support.css): 12px from the
       top and sides of a 244px sidebar, 34px tall, the two halves splitting the width. */
    .jc-side { position: sticky; top: 0; z-index: 2; padding: 12px 10px; background: var(--sh-surface-2, #1d1d1d); }
    .jc-switch { display: flex; gap: 2px; padding: 2px; border-radius: 8px;
      background: var(--sh-surface-3, rgba(255,255,255,.06)); border: 1px solid var(--sh-line, rgba(255,255,255,.12));
      font: 500 13px/1 var(--sh-font, "JC Geist", ui-sans-serif, system-ui, sans-serif); letter-spacing: 0; }
    /* Specific enough to keep its own colors over any link color. */
    html body .jc-side .jc-switch > a.jc-tab[href] { flex: 1; min-width: 0; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      height: 28px; padding: 0 11px; border-radius: 6px; color: var(--sh-muted, rgba(255,255,255,.68)); text-decoration: none;
      transition: color .12s ease, background-color .12s ease; }
    /* Below 900px the sidebar can be dragged narrow; the labels give way before the icons. */
    .jc-switch a span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    html body .jc-side .jc-switch > a.jc-tab[href]:hover { color: var(--sh-ink, #fff); }
    html body .jc-side .jc-switch > a.jc-tab[href]:focus-visible { outline: none; box-shadow: var(--sh-focus, 0 0 0 2px #8fa4ff); }
    html body .jc-side .jc-switch > a.jc-tab[aria-current="page"] { background: var(--sh-surface, #fff); color: var(--sh-ink, #0b0d10);
      box-shadow: var(--sh-shadow-sm, 0 1px 2px rgba(0,0,0,.3)); }
    .jc-switch svg { width: 15px; height: 15px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

    /* Without the skin in light mode the sidebar is white, so the dark fallbacks above
       would vanish there. */
    html:not(.jc-themed) .body--light .jc-side { background: #fff; }
    html:not(.jc-themed) .body--light .jc-switch { background: rgba(0,0,0,.05); border-color: rgba(0,0,0,.12); }
    html:not(.jc-themed) body.body--light .jc-side .jc-switch > a.jc-tab[href]:not([aria-current]) { color: rgba(0,0,0,.6); }
    html:not(.jc-themed) body.body--light .jc-side .jc-switch > a.jc-tab[href]:not([aria-current]):hover { color: #000; }

    @media (max-width: 599px) {
      .jc-wordmark { height: 20px; }
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
    brand.innerHTML = wordmarks("jc-wordmark");
    title.insertBefore(brand, text);
  }

  // The switch heads the client sidebar, where the console has its own.
  function sideSwitch() {
    const sidebar = document.querySelector(".q-page > .q-splitter--vertical > .q-splitter__before");
    if (!sidebar || sidebar.firstElementChild?.classList.contains("jc-side")) return;
    sidebar.querySelector(":scope > .jc-side")?.remove();
    const side = document.createElement("div");
    side.className = "jc-side";
    side.innerHTML =
      `<nav class="jc-switch" aria-label="Support or Access">` +
      `<a class="jc-tab" href="${SUPPORT_URL}">${HEADSET}<span>Support</span></a>` +
      `<a class="jc-tab" href="/" aria-current="page">${ACCESS}<span>Access</span></a>` +
      `</nav>`;
    // You're already in Access; leave the dashboard where it is.
    side.querySelector('[aria-current="page"]').addEventListener("click", (e) => e.preventDefault());
    sidebar.prepend(side);
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
    sideSwitch();
    brandLogin();
    brandTitle();
  }
  const soon = () => {
    if (!queued) {
      queued = true;
      // A timer rather than requestAnimationFrame, which never runs in a background tab.
      setTimeout(apply, 16);
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
