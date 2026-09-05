# triallumii.online ana sayfa

`*.triallumii.online` altındaki tüm Lumii uygulamalarını proje kartlarıyla listeleyen statik vitrin.
Kartlar `index.html` içindeki `PROJECTS` dizisinden üretilir; ekran görüntüleri `assets/<id>.jpg` (800px, headless Chrome + sips).

Canlı: https://triallumii.online — Coolify app `triallumii-home` (proje tools, Dockerfile build pack, port 80).
Güncelleme: `main`'e push + `POST /deploy?uuid=<uuid>&force=true`.
