#!/usr/bin/env bash
#
# Give remote support its two addresses. Run on the RMM server after install.sh, as
# the user that installed Tactical RMM:
#
#   ./support-site.sh support.example.com
#
# - The people you help use the address you give here. It serves only the public
#   support page and the support app downloads. Links to the technician console on
#   it are sent to the dashboard's address.
# - Technicians use the dashboard's address for everything: Access (the dashboard)
#   at /, and Support (the technician console) at /support/manage/. The dashboard's
#   pages get the JustConnect logo and a Support | Access switch.
#
# The certificate must cover the public name. By default the script uses the
# certificate install.sh was given (CERT_FILE and KEY_FILE in local_settings.py);
# pass --cert /path/fullchain.pem --key /path/privkey.pem to use another one.
# Running it again with a new name moves the public page there, for example to the
# bare domain once it points at this server.

set -euo pipefail

LOCAL_SETTINGS='/rmm/api/tacticalrmm/tacticalrmm/local_settings.py'
SITE='/etc/nginx/sites-available/support.conf'
FRONTEND='/etc/nginx/sites-available/frontend.conf'
CONSOLE_SNIPPET='/etc/nginx/snippets/trmm-support-console.conf'
CONSOLE_INCLUDE="include ${CONSOLE_SNIPPET};"
TAKE_CONTROL_SNIPPET='/etc/nginx/snippets/trmm-take-control.conf'

domain="${1:-}"
shift || true
cert=""
key=""
while [[ $# -gt 0 ]]; do
  case "$1" in
  --cert) cert="$2"; shift 2 ;;
  --key) key="$2"; shift 2 ;;
  *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ ! "$domain" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "Usage: $0 support.example.com [--cert fullchain.pem --key privkey.pem]" >&2
  exit 1
fi
if [[ ! -f "$LOCAL_SETTINGS" || ! -f "$FRONTEND" ]]; then
  echo "Tactical RMM isn't installed here ($LOCAL_SETTINGS or $FRONTEND is missing). Run install.sh first." >&2
  exit 1
fi

# The dashboard's address, from CORS_ORIGIN_WHITELIST.
dashboard=$(grep -A3 -E "^CORS_ORIGIN_WHITELIST" "$LOCAL_SETTINGS" | grep -oE "https://[A-Za-z0-9.-]+" | head -1 | sed 's|^https://||')
if [[ -z "$dashboard" ]]; then
  echo "Couldn't read the dashboard's address (CORS_ORIGIN_WHITELIST) from $LOCAL_SETTINGS." >&2
  exit 1
fi
if [[ "$dashboard" == "$domain" ]]; then
  echo "$domain is the dashboard's address. Give the public support page a name of its own." >&2
  exit 1
fi
if ! sudo nginx -V 2>&1 | grep -q -- '--with-http_sub_module'; then
  echo "This nginx can't add the Support | Access switch to the dashboard (it was built without http_sub_module)." >&2
  exit 1
fi

setting() { grep -E "^$1 *=" "$LOCAL_SETTINGS" | head -1 | sed -E 's/^[^=]+= *"?([^"]*)"?.*/\1/'; }
cert="${cert:-$(setting CERT_FILE)}"
key="${key:-$(setting KEY_FILE)}"
if [[ -z "$cert" || -z "$key" ]]; then
  cert="/etc/letsencrypt/live/${domain}/fullchain.pem"
  key="/etc/letsencrypt/live/${domain}/privkey.pem"
fi
if ! sudo test -f "$cert" || ! sudo test -f "$key"; then
  echo "Certificate files not found: $cert and $key" >&2
  exit 1
fi
if ! sudo openssl x509 -in "$cert" -noout -checkhost "$domain" | grep -q "does match"; then
  echo "The certificate in $cert doesn't cover $domain. Add the name to it first." >&2
  exit 1
fi

# The public address: only the support page and its files.
sudo tee "$SITE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${domain};
    client_max_body_size 2M;
    ssl_certificate ${cert};
    ssl_certificate_key ${key};

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers EECDH+AESGCM:EDH+AESGCM;
    ssl_ecdh_curve secp384r1;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location /static/ {
        root /rmm/api/tacticalrmm;
    }

    # Technicians work on the dashboard's address.
    location ~ ^/support/(manage|control)/ {
        return 302 https://${dashboard}\$request_uri;
    }

    location /support/ {
        uwsgi_pass  tacticalrmm;
        include     /etc/nginx/uwsgi_params;
        uwsgi_read_timeout 300s;
        uwsgi_ignore_client_abort on;
    }

    location / {
        return 302 /support/;
    }
}
EOF
sudo ln -sf "$SITE" /etc/nginx/sites-enabled/support.conf

# The dashboard's address: the technician console beside the dashboard, and the
# logo and Support | Access switch on the dashboard's own page.
sudo mkdir -p /etc/nginx/snippets
sudo tee "$CONSOLE_SNIPPET" >/dev/null <<'EOF'
# Remote support on the dashboard's address (support-site.sh).
location /support/ {
    uwsgi_pass  tacticalrmm;
    include     /etc/nginx/uwsgi_params;
    uwsgi_read_timeout 300s;
    uwsgi_ignore_client_abort on;
    client_max_body_size 2M;
}

location /static/support/ {
    root /rmm/api/tacticalrmm;
    add_header Cache-Control "no-cache";
    add_header X-Content-Type-Options nosniff;
}

# Every dashboard route ends up here; add the JustConnect logo and the switch.
location = /index.html {
    root /var/www/rmm/dist;
    add_header Cache-Control "no-store, no-cache, must-revalidate";
    add_header Pragma "no-cache";
    add_header X-Content-Type-Options nosniff;
    sub_filter '</head>' '<script src="/static/support/access.js" defer></script></head>';
    sub_filter_once on;
}
EOF
# Inside the HTTPS server block: the first "charset utf-8;" line belongs to it.
if ! grep -qF "$CONSOLE_INCLUDE" "$FRONTEND"; then
  sudo sed -i "0,/charset utf-8;/s||charset utf-8;\n    ${CONSOLE_INCLUDE}|" "$FRONTEND"
fi
# take-control.sh's redirect can stay on the dashboard's address now.
if sudo test -f "$TAKE_CONTROL_SNIPPET"; then
  sudo sed -i -E 's#return 302 https://[^/]+/support/control/#return 302 /support/control/#' "$TAKE_CONTROL_SNIPPET"
fi

# Let Django answer for the public name, and send people there in invitations.
sed -i '/^# Public support page (support-site.sh)$/,/^SUPPORT_PUBLIC_URL = /d' "$LOCAL_SETTINGS"
cat >>"$LOCAL_SETTINGS" <<EOF
# Public support page (support-site.sh)
ALLOWED_HOSTS.append("${domain}")
SUPPORT_PUBLIC_URL = "https://${domain}"
EOF

sudo nginx -t
sudo systemctl reload nginx
sudo systemctl restart rmm.service
echo "The public support page is at https://${domain}"
echo "Technicians: Access at https://${dashboard}, Support at https://${dashboard}/support/manage/"
