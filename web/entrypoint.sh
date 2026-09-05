#!/bin/sh
# DASH_USER / DASH_PASS ortam değişkenlerinden pano giriş dosyasını üretir; yoksa /durum kapalı kalır.
set -e
if [ -n "$DASH_USER" ] && [ -n "$DASH_PASS" ]; then
  htpasswd -bcB /etc/nginx/.htpasswd "$DASH_USER" "$DASH_PASS" >/dev/null 2>&1
  printf 'auth_basic "Lumii sunucu paneli";\nauth_basic_user_file /etc/nginx/.htpasswd;\n' > /etc/nginx/dash_auth.conf
  echo "pano girişi: açık ($DASH_USER)"
else
  printf 'deny all;\n' > /etc/nginx/dash_auth.conf
  echo "pano girişi: DASH_USER/DASH_PASS tanımsız, /durum kapalı"
fi
