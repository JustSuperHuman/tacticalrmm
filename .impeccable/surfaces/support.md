# Public support

Mode: Operate. White-label, built on the JustUI kit.

## Public page

One centered card (420px max) on the page background with a faint accent glow at the top. The card holds the brand (the default mark beside the name, or an uploaded logo by itself), one heading, and one task.

- **Enter code:** "Get remote support", a 56px access-code field that groups digits as 123 456 789 while typing, one primary "Continue" button, and a trust line ("Only use a code from a technician you trust"). Below the card: how to get a code (the branded phone and email, or "ask your technician"), and a faint "For technicians" link.
- **Download:** an ok pill ("Code accepted"), "Download the support app", and the visitor's platform as the one primary button (detected from the User-Agent). The other platform is a quiet "Need the Mac version?" button. A phone or tablet gets a warning to switch to their computer and sees both platforms as plain buttons. Three numbered steps for the chosen platform follow, then "Use a different code" and the code's end time in the visitor's own time zone.

The one moment of motion: the download step rises 6px and fades in, and the check in "Code accepted" pops. Reduced motion turns both off.

Downloads are plain form posts, so the browser's own download UI shows progress. The button is disabled for 5 seconds after a click because every click counts toward the code's download limit.

## Technician console

The JustUI 52px topbar (brand / Remote support, then Support page and Dashboard links), a page head, then two columns (360px and the rest), stacking below 960px:

- Left: the "Your code is ready" card after creating a code (the code at 32px, "Copy code" and "Copy invitation", which copies a ready-to-send message with the page URL), then the "New code" form with the page's only primary button.
- Right: "Recent codes" as a `.sh-table` (for, masked code, downloads, local expiry, status pill, Revoke). Below 720px the rows stack.
- Below: "Support apps" (publishing status per platform) and "Branding" (logo upload with live preview, company name, help phone, help email), shown only to roles that can edit global settings.

## Devices

"Codes" and "Devices" tabs (`.sh-tabs`, `aria-current="page"`) sit under the console's page head.

- **Devices list:** a `.sh-table` of computers in the MeshCentral support group: name and OS, an Online/Offline pill, and a monitoring state ("Support only", "Installing" in warn, "Monitored" in accent). Before the group exists, an empty state offers "Create support group" to admins. Admins also see the group ID to use when building the support apps.
- **Device page:** a back link, the device name as h1 with its online pill, then two cards. "Move to monitoring" moves through three states: the install form (the page's one primary button); a warn-tinted waiting row with a spinner that re-checks every 15 seconds, plus "Show install log" and a collapsed "Install again"; and "Monitored as HOST in Client / Site" with "Remove support app" as the primary. "Run a command" has a shell picker on Windows, a mono textarea, and output in the kit's always-dark code block. A danger-tinted "Done helping?" row ends support on any computer that isn't monitored yet. Removing asks for confirmation.

## Rules

- Colors come from `--sh-*` tokens only. The one literal is the white chip behind uploaded logos in dark mode.
- App classes use the `sp-` prefix. The kit copy in `static/support/superhuman/` is never edited.
- Times are rendered server-side and rewritten to the visitor's time zone in the browser.
- Every state is expressed in text: invalid code, expired grant, missing installer, phone visitor, busy buttons, copy results.
