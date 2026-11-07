# Public support

Mode: Operate. White-label, built on the JustUI kit.

## Public page

One centered card (420px max) on the page background with a faint accent glow at the top. The card holds the brand, one heading, and one task. The brand is the JustConnect wordmark at 40px (a `<picture>` swaps in the dark-mode copy), the JustConnect mark beside any other company name, or an uploaded logo by itself.

- **Enter code:** "Get remote support", a 56px access-code field that groups digits as 123 456 789 while typing, one primary "Continue" button, and a trust line ("Only use a code from a technician you trust"). Below the card: how to get a code (the branded phone and email, or "ask your technician"), and a faint "For technicians" link.
- **Download:** an ok pill ("Code accepted"), "Download the support app", and the visitor's platform as the one primary button (detected from the User-Agent). The other platform is a quiet "Need the Mac version?" button. A phone or tablet gets a warning to switch to their computer and sees both platforms as plain buttons. Three numbered steps for the chosen platform follow, then "Use a different code" and the code's end time in the visitor's own time zone.

The one moment of motion: the download step rises 6px and fades in, and the check in "Code accepted" pops. Reduced motion turns both off.

Downloads are plain form posts, so the browser's own download UI shows progress. The button is disabled for 5 seconds after a click because every click counts toward the code's download limit.

The Windows MSI installs the JustConnect Support service with administrator permission. Its instructions explain that remote access remains available after sign-out and restart until removal. Finish does not launch an agent window. The native wizard states the same persistence before Install, and setup verifies service startup. This provides remote access without installing monitoring.

## Technician console

A host workspace in the spirit of a remote support host console, on the JustUI app shell (`.sh-app`, 244px sidebar, 52px header row).

- **Sidebar:** the brand (the wordmark at 26px), the Support | Access switch (the kit's `.sh-seg`, full width, Support current), **New session** (the shell's one primary button), the session groups as `.sh-nav-item`s with mono counts (All sessions, My sessions, Connected, Waiting to connect, Offline, Moving to monitoring, Past codes), then Manage (Toolbox, Settings) and the signed-in technician. Below 860px it's the kit's drawer, with a close button in its head.
- **Session list** (380px, surface): a search field (`/` focuses it), then quiet rows of a 64×40 picture of the screen (the system icon on a tint when there's no picture) with a presence dot, the session name over the computer and user, and on the right how long it's been connected (ok), or Waiting/Downloaded (warn) for codes, plus a small monitoring pill. The selected row is tinted with a full accent ring, not an edge stripe. Each group has its own dashed empty state, and search has one too. "Updates on its own" with a slow ok dot sits at the foot. Every 10 seconds the list is re-fetched and swapped in place, keeping the scroll position and the search.
- **Selected session** (scrolls on its own, `--sh-bg`): a 44px glyph, the session name as h1, a status line (Connected pill and "for 12 min") and a details line (computer · user · client). On the right are Files, Terminal and **Join**, the page's one primary button. Tabs follow: General ("Their screen", a 16:10 picture on black that joins when clicked and shows a Join chip on hover, beside a one-column facts card; then the session name and a danger-ringed "Done helping?" row), Commands (an always-dark console holding the shared history, with the input below it), Monitoring (the four-state install card), Notes, and Timeline (icon dots on a hairline). Opening a session swaps this pane in place and pushes the URL. Tabs switch without reloading.
- **A code's session:** a key glyph and a Waiting pill, the code at 34px once with Copy code, Copy invitation and Email invitation, a green link row when a computer connects with it, "How they connect" steps with a waiting row, facts, and its timeline. Cancel code is a danger button on the right.
- **New session:** a `.sh-dialog` with the session name, client site, expiry and download limit.
- **Below 900px:** one pane at a time. A session shows a back link to its group, and Join takes the most width.
- **Screen pictures** refresh in place: the browser swaps a newer one in after it has loaded, with "Updated … ago" beside it. Offline pictures go grey.
- **Toolbox and Settings** are plain pages in the same shell. Toolbox: add/edit on the left, Windows and Mac cards on the right. Settings: Support apps and Support group side by side, Screen previews (a switch), then Branding (logo with preview, company name, help phone, help email).

## Remote control window

A full-window viewer in the spirit of ScreenConnect's host window, always dark (`data-theme="dark"` on the kit). From top to bottom:

- **Toolbar** (60px, surface): the session's identity on the left (a 32px system glyph, the name, then computer · system · user), centred tool groups, and the status on the right (a dot, Connected, a mono timer, a solid danger Disconnect, and a chevron that hides the toolbar).
  - The tools are icon-over-label buttons (50px), in groups divided by hairlines: Essentials (Ctrl+Alt+Del, Keys menu, Block input), View (Display menu with monitor tiles, a zoom stepper around a zoom menu, Quality menu with descriptions, Full screen) and Tools (Files, Terminal).
  - Menus are kit popovers, and the checked choice gets a soft accent tint.
  - Below 1280px only icons show (zoom keeps its label).
- **Their screen** sits on black, centred when it's smaller than the window and scrollable when it's larger.
- **Overlay card** (spinner, title, line, actions) for each state: connecting, waiting for their consent, offline, disconnected and error.
- **Status bar** (28px, mono numbers): screen size, display, zoom, quality, round-trip time, and a faint note that input goes to their computer.

While not live, the tool groups dim. Windows-only tools hide for other systems. A hidden toolbar leaves a small tab at the top centre to bring it back.

## Access (the RMM dashboard)

The dashboard is upstream's build, never forked. `static/support/access.js`, added to its page by nginx, blanks the "Tactical RMM" text in the header and the sign-in card in place and puts the JustConnect lockup there (24px in the header, 40px on sign-in), with the Support | Access switch after it (Access current). The switch matches the dashboard skin's segmented control: a 2px track on `--sh-surface-3` with a hairline ring, 28px items, the current one on `--sh-surface` with a shadow and ring. Without the skin, fallbacks suit the stock dark grey header, and the header always uses the dark-mode lockup.

## Rules

- Colors come from `--sh-*` tokens only. The one literal is the white chip behind uploaded logos in dark mode.
- App classes use the `sp-` prefix. The kit copy in `static/support/superhuman/` is never edited.
- Times are rendered server-side and rewritten to the visitor's time zone in the browser.
- Every state is expressed in text: invalid code, expired grant, missing installer, phone visitor, busy buttons, copy results, empty groups, no search matches, MeshCentral being down.
