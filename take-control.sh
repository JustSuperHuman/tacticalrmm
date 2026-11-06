#!/usr/bin/env bash
#
# Open the dashboard's Take Control in this fork's remote control window instead of
# MeshCentral's page. Run on the RMM server after install.sh, as the user that
# installed Tactical RMM:
#
#   ./take-control.sh            turn it on
#   ./take-control.sh --off      go back to MeshCentral's page
#
# The dashboard opens Take Control at https://<dashboard>/takecontrol/<agent id>; this
# adds an nginx redirect for that address to /support/control/agents/<agent id>/ on
# the API host. Signing in to the dashboard signs in to the viewer too.

set -euo pipefail

LOCAL_SETTINGS='/rmm/api/tacticalrmm/tacticalrmm/local_settings.py'
FRONTEND='/etc/nginx/sites-available/frontend.conf'
SNIPPET='/etc/nginx/snippets/trmm-take-control.conf'
INCLUDE="include ${SNIPPET};"

if [[ ! -f "$LOCAL_SETTINGS" || ! -f "$FRONTEND" ]]; then
  echo "Tactical RMM isn't installed here. Run install.sh first." >&2
  exit 1
fi

if [[ "${1:-}" == "--off" ]]; then
  sudo sed -i "\\|${INCLUDE}|d" "$FRONTEND"
  sudo rm -f "$SNIPPET"
  sudo nginx -t && sudo systemctl reload nginx
  echo "Take Control opens MeshCentral's page again."
  exit 0
fi

api_host=$(grep -E "^ALLOWED_HOSTS" "$LOCAL_SETTINGS" | head -1 | sed -E "s/.*\[ *['\"]([^'\"]+)['\"].*/\1/")
if [[ ! "$api_host" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Couldn't read the API's address from $LOCAL_SETTINGS." >&2
  exit 1
fi

sudo mkdir -p /etc/nginx/snippets
sudo tee "$SNIPPET" >/dev/null <<EOF
# The dashboard's Take Control opens the remote control window (take-control.sh).
location ~ ^/takecontrol/([A-Za-z0-9_-]+)/?\$ {
    return 302 https://${api_host}/support/control/agents/\$1/;
}
EOF

# Inside the HTTPS server block: the first "charset utf-8;" line belongs to it.
if ! grep -qF "$INCLUDE" "$FRONTEND"; then
  sudo sed -i "0,/charset utf-8;/s||charset utf-8;\n    ${INCLUDE}|" "$FRONTEND"
fi

sudo nginx -t
sudo systemctl reload nginx
echo "Take Control in the dashboard now opens the remote control window."
