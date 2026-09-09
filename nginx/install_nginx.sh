#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_SOURCE="$SCRIPT_DIR/reason.andrew.com.conf"
TARGET="/etc/nginx/sites-available/reason.andrew.com.conf"
ENABLED_LINK="/etc/nginx/sites-enabled/reason.andrew.com.conf"

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo: sudo ./nginx/install_nginx.sh"
    exit 1
fi

echo "1. Linking configuration from $CONF_SOURCE to $TARGET..."
ln -sf "$CONF_SOURCE" "$TARGET"
ln -sf "$TARGET" "$ENABLED_LINK"

# Disable conflicting default site if active
if [ -L /etc/nginx/sites-enabled/default ] || [ -f /etc/nginx/sites-enabled/default ]; then
    echo "2. Removing default Nginx site link..."
    rm -f /etc/nginx/sites-enabled/default
fi

echo "3. Testing Nginx configuration..."
nginx -t

echo "4. Reloading Nginx service..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl reload nginx || systemctl restart nginx
else
    nginx -s reload
fi

echo "✅ Nginx successfully configured and listening for reason.andrew.com on ports 80, 443, and 8000."
