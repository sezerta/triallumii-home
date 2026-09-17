# triallumii.online ana sayfa + /durum panosu — statik, nginx. Pano girişi: /giris + ajan çerez oturumu (auth_request).
FROM nginx:1.27-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/sec_headers.conf /etc/nginx/sec_headers.conf
COPY web/entrypoint.sh /docker-entrypoint.d/40-dash-auth.sh
RUN chmod +x /docker-entrypoint.d/40-dash-auth.sh
COPY index.html durum.html giris.html /usr/share/nginx/html/
COPY assets/ /usr/share/nginx/html/assets/
COPY moneyinmypocket/ /usr/share/nginx/html/moneyinmypocket/
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/health >/dev/null || exit 1
