#!/bin/sh
# Basic auth kaldırıldı: /durum ve /api/ artık ajanın çerez oturumuna auth_request ile sorulur.
# Kimlik bilgileri yalnız `agent` servisinin ortam değişkenlerinde (DASH_USER / DASH_PASS_HASH / DASH_TOTP_SECRET).
set -e
echo "pano girişi: /giris (şifre + TOTP 2FA) — korumalı yollar: /durum, /durum.html, /api/ (auth/ ve public/ hariç)"
