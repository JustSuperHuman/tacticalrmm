#!/usr/bin/env bash
# Adds this fork's skin to the built Tactical RMM dashboard.
#
#   dashboard-skin/apply.sh [dist]            apply (default dist: /var/www/rmm/dist)
#   dashboard-skin/apply.sh --remove [dist]   go back to the stock upstream look
#
# update.sh and install.sh run it right after they unpack the upstream frontend, so the
# skin survives every update. Run it by hand after changing skin.css. Running it twice
# is harmless. It only adds dist/skin/ and two tags in dist/index.html; the upstream
# build and the nginx config are left as they are.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
kit="${repo}/api/tacticalrmm/support/static/support"

remove=0
if [[ "${1:-}" == "--remove" ]]; then
  remove=1
  shift
fi
dist="${1:-/var/www/rmm/dist}"
index="${dist}/index.html"

if [[ ! -f "$index" ]]; then
  echo "dashboard-skin: no dashboard build at ${dist}, skipping" >&2
  exit 0
fi

SUDO=''
if [[ $EUID -ne 0 ]]; then SUDO='sudo'; fi

# Clear any earlier copy first, so a re-run never doubles the tags.
$SUDO sed -i '/data-trmm-skin/d' "$index"
$SUDO rm -rf "${dist}/skin"

if [[ $remove -eq 1 ]]; then
  echo "dashboard-skin: removed from ${dist}"
  exit 0
fi

$SUDO mkdir -p "${dist}/skin/fonts"
$SUDO cp "${here}/skin.css" "${dist}/skin/skin.css"
$SUDO cp "${here}/mark.webp" "${dist}/skin/mark.webp"
# The fonts come from the support pages' copy of the JustUI kit, so the repo holds them once.
$SUDO cp "${kit}/superhuman/fonts/Geist-Variable.woff2" "${kit}/superhuman/fonts/GeistMono-Variable.woff2" "${dist}/skin/fonts/"

# Versioned link so browsers pick up a changed skin straight away.
ver="$(sha1sum "${here}/skin.css" | cut -c1-10)"
tags="<link rel=\"preload\" href=\"/skin/fonts/Geist-Variable.woff2\" as=\"font\" type=\"font/woff2\" crossorigin data-trmm-skin>\n<link rel=\"stylesheet\" href=\"/skin/skin.css?v=${ver}\" data-trmm-skin>\n"
$SUDO sed -i "s#</head>#${tags}</head>#" "$index"

if ! grep -q 'data-trmm-skin' "$index"; then
  echo "dashboard-skin: could not find </head> in ${index}" >&2
  exit 1
fi

if id www-data >/dev/null 2>&1; then
  $SUDO chown -R www-data:www-data "${dist}/skin" "$index"
fi

echo "dashboard-skin: applied to ${dist} (skin.css ${ver})"
