# Dashboard skin

A new look for the Tactical RMM dashboard and its sign-in page. It uses the JustUI style the
remote support pages already have: Geist type, neutral surfaces, hairline borders and one blue
accent, in light and dark mode.

## How it gets onto the server

The dashboard is a prebuilt Vue app from
[amidaware/tacticalrmm-web](https://github.com/amidaware/tacticalrmm-web). This repo doesn't
fork it. After `install.sh` and `update.sh` unpack the upstream build into
`/var/www/rmm/dist`, they run `apply.sh`, which:

- copies `skin.css`, `mark.webp` (the JustConnect mark) and the Geist fonts into `dist/skin/`
- adds a font preload and the stylesheet link to `dist/index.html`, both tagged `data-trmm-skin`

The upstream build and the nginx config stay untouched.

To apply a changed skin without a full update:

```bash
cd /rmm && git pull && bash dashboard-skin/apply.sh
```

To go back to the stock look, run `bash dashboard-skin/apply.sh --remove` and delete the
"dashboard skin" block from `update.sh` and `install.sh`.

## Merging upstream

Nothing in the skin touches upstream files, apart from the three-line hook in `update.sh` and
`install.sh` (the fork also bumps `SCRIPT_VERSION` in `update.sh` so servers fetch the hooked
script). Upstream changes to the dashboard arrive through `WEB_VERSION` in
`api/tacticalrmm/tacticalrmm/settings.py`.

`skin.css` restyles Quasar's own class names (`q-header`, `q-table`, `q-menu`, `q-field` and so
on), which stay stable across releases. A few rules target the dashboard's own layout: the menu
bar, the client tree, the agent table's toolbar and the detail tabs. After a `WEB_VERSION`
bump, check these in light and dark mode:

1. The sign-in page and the two-factor code dialog
2. The header, the File/View/Agents menu bar and one of its menus
3. The client tree and the Servers/Workstations/Mixed switch
4. The agent table with an agent selected, and the detail tabs under it
5. A dialog, such as Settings → Global Settings

If one of these breaks, the rest of the skin still works; that one area falls back to
upstream's look.

On screens 900px and wider the dashboard takes the support console's frame: a fixed 244px
client sidebar under a 52px header, so the brand and the Support | Access switch sit at the
same spot on both pages. The client tree's drag-to-resize is off for that reason. The menu bar,
toolbar and table footer heights add up to the same total upstream subtracts when it sizes
the agent table (header 50 + menu bar 40 + 78), so keep that sum if you change one of them.

## Working on it

Keep to the JustUI tokens (`--sh-*`) for every color. The token values are copied from
`api/tacticalrmm/support/static/support/superhuman/superhuman.css` and switch with Quasar's
`body--dark` class, not the system theme, because the dashboard has its own dark mode setting.
Prefix rules with `body` so they outrank Quasar's selectors and the route CSS that loads after
the skin.

`support-site.sh` adds `/static/support/access.js` to the dashboard, which brings the
JustConnect wordmark and the Support | Access switch into the header and the sign-in card. When
those are there, the skin hides its own mark (`:has(.jc-brand)`, `:has(.jc-login)`). Without
access.js the header shows the mark beside the dashboard's built-in "Tactical RMM" title.
