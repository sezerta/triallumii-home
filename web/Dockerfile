# triallumii.online ana sayfa + /durum panosu — statik, nginx. Pano girişi için apache2-utils (htpasswd).
FROM nginx:1.27-alpine
RUN apk add --no-cache apache2-utils
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/entrypoint.sh /docker-entrypoint.d/40-dash-auth.sh
RUN chmod +x /docker-entrypoint.d/40-dash-auth.sh
COPY index.html durum.html /usr/share/nginx/html/
COPY assets/ /usr/share/nginx/html/assets/
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/health >/dev/null || exit 1
