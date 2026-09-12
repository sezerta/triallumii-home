# triallumii.online ana sayfa + sunucu paneli

`*.triallumii.online` altındaki tüm Lumii uygulamalarını proje kartlarıyla listeleyen statik vitrin (`/`) ve
şifre korumalı sunucu durum panosu (`/durum`).

- `index.html` — kartlar `PROJECTS` dizisinden üretilir; ekran görüntüleri `assets/<id>.jpg` (800px).
- `durum.html` — pano; `/api/status` JSON'unu 15 sn'de bir çeker (CPU/RAM/disk/takas, konteynerler, HTTP+sertifika probu, Docker disk).
- `agent/server.py` — stdlib Python ajan; Docker soketi (salt-okunur) + host `/proc` + kök (salt-okunur) okur. Formüller dosya başında.
- `giris.html` — `/giris`; şifre + Google Authenticator (TOTP) ile giriş, çerez oturumu açar.
- `web/` — nginx; basic auth yok. `/durum`, `/durum.html` ve `/api/` (auth/ ve public/ hariç) `auth_request` ile
  ajanın `lumii_sess` çerezine sorulur. Sayfa 401'de `/giris`'e yönlenir, API'de düz 401 JSON döner.

## Giriş (şifre + 2FA)

Ortam değişkenleri **yalnız `agent` servisinde** (Coolify → app ortam değişkenleri):

| Değişken | Zorunlu | Not |
|---|---|---|
| `DASH_USER` | evet | kullanıcı adı |
| `DASH_PASS_HASH` | evet | `pbkdf2_sha256:<iters>:<salt_b64url>:<hash_b64url>` — `python3 agent/server.py hash '<şifre>'`. Ayraç `:`, padding'siz base64url: değer `[A-Za-z0-9_:-]` kümesinde kalır, Coolify `$`'ı değişken referansı sanıp yutmaz. Eski `$` ayraçlı biçim hâlâ okunur. |
| `DASH_PASS` | hayır | geriye dönük uyum; `DASH_PASS_HASH` yoksa düz karşılaştırma |
| `DASH_TOTP_SECRET` | evet | base32 — `python3 server.py totp` yeni gizli anahtar + `otpauth://` URI basar |
| `DASH_SESSION_KEY` | önerilir | yoksa süreç başında üretilir → her yeniden başlatmada oturumlar düşer |
| `DASH_SESSION_TTL` | hayır | saniye, varsayılan 43200 (12 saat) |

Kilit: aynı IP için 5 başarısız denemede 15 dk (bellekte). nginx ayrıca `/api/auth/login`'e dakikada 10,
`/api/public/`'e dakikada 60 istek sınırı koyar. Girişler ajan stderr'inde `[auth]` ön ekiyle loglanır.

## Saldırı verisi

`GET /api/security` (oturum ister) host'taki `/home/deploy/security/attacks.json` dosyasını okur
(konteynerde `/host/security:ro`), 10 sn önbellekler, dosya yoksa `{"available":false,"reason":...}` döner.
Dosyayı bir host cron scripti üretir; ajan yalnız okur.

`GET /api/public/apps` (oturum istemez) vitrin için yalnız `{id: {up, ms}}` döner; iç veri sızdırmaz.

Canlı: https://triallumii.online — Coolify app `triallumii-home` (proje tools, compose build pack, servis `web` port 80).
Güncelleme: `main`'e push + `POST /deploy?uuid=<uuid>&force=true`.
