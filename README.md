# triallumii.online ana sayfa + sunucu paneli

`*.triallumii.online` altındaki tüm Lumii uygulamalarını proje kartlarıyla listeleyen statik vitrin (`/`) ve
şifre korumalı sunucu durum panosu (`/durum`).

- `index.html` — kartlar `PROJECTS` dizisinden üretilir; ekran görüntüleri `assets/<id>.jpg` (800px).
- `durum.html` — pano; `/api/status` JSON'unu 15 sn'de bir çeker (CPU/RAM/disk/takas, konteynerler, HTTP+sertifika probu, Docker disk).
- `agent/server.py` — stdlib Python ajan; Docker soketi (salt-okunur) + host `/proc` + kök (salt-okunur) okur. Formüller dosya başında.
- `web/` — nginx; `/durum` ve `/api/` basic auth (`DASH_USER`, `DASH_PASS` ortam değişkenleri, yoksa kapalı).

Canlı: https://triallumii.online — Coolify app `triallumii-home` (proje tools, compose build pack, servis `web` port 80).
Güncelleme: `main`'e push + `POST /deploy?uuid=<uuid>&force=true`.
