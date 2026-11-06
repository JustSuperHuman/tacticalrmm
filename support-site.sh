#!/usr/bin/env bash
#
# Serve the public support page on its own address, such as support.example.com.
# Run on the RMM server after install.sh, as the user that installed Tactical RMM:
#
#   ./support-site.sh support.example.com
#
# The certificate must cover that name. By default the script uses the certificate
# install.sh was given (CERT_FILE and KEY_FILE in local_settings.py); pass
# --cert /path/fullchain.pem --key /path/privkey.pem to use another one.
# Running it again with a new name moves the support page there.

set -euo pipefail

LOCAL_SETTINGS='/rmm/api/tacticalrmm/tacticalrmm/local_settings.py'
SITE='/etc/nginx/sites-available/support.conf'

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
if [[ ! -f "$LOCAL_SETTINGS" ]]; then
  echo "Tactical RMM isn't installed here ($LOCAL_SETTINGS is missing). Run install.sh first." >&2
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

# Only the support pages and their files are served here. The rest of the RMM stays
# on its own addresses.
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

# Let Django answer for the name, and send people there in invitations.
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
