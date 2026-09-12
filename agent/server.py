#!/usr/bin/env python3
"""Sunucu durumu ajanı — yalnız stdlib. Docker soketi + host /proc okur, /api/status JSON döner.
Formüller:
  CPU%            = Δ(meşgul jiffies) / Δ(toplam jiffies) × 100            (host /proc/stat, iki örnek arası)
  RAM%            = (MemTotal − MemAvailable) / MemTotal × 100             (host /proc/meminfo)
  Disk%           = (blocks − bfree) / blocks × 100                        (statvfs, host kökü)
  Konteyner CPU%  = Δcpu_usage / Δsystem_usage × online_cpus × 100         (Docker stats API)
  Konteyner RAM   = memory_stats.usage − inactive_file                     (Docker stats API)
"""
import json, os, sys, time, threading, socket, ssl, http.client, urllib.request, urllib.error
import hmac, hashlib, base64, struct, secrets, re, http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HOST_PROC = os.environ.get("HOST_PROC", "/host/proc")
HOST_ROOT = os.environ.get("HOST_ROOT", "/host/root")
SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
PORT = int(os.environ.get("PORT", "8080"))

# Uygulama → alan adı → konteyner adı eşleşmesi (Coolify uygulama kimliği önekleri; gizli değil)
APPS = [
    {"id": "home",     "name": "Ana sayfa + pano",   "domain": "triallumii.online",          "match": ["web-", "agent-"]},
    {"id": "finradar", "name": "FinRadar",           "domain": "finradar.triallumii.online", "match": ["itnvtia0iunrrl5ook9psymc"]},
    {"id": "planix",   "name": "PLANIX",             "domain": "planix.triallumii.online",   "match": ["hunkmgzsuvnypxnofpyvcsfr"]},
    {"id": "ges",      "name": "GES Stüdyo",         "domain": "ges.triallumii.online",      "match": ["p5p8mc7dlye2u6edvorrooas"]},
    {"id": "invoice",  "name": "Invoice AI",         "domain": "invoice.triallumii.online",  "match": ["fbgw65zccv4oaf59cshpasko"]},
    {"id": "derskocu", "name": "DersKoçu",           "domain": "derskocu.triallumii.online", "match": ["s6e4xgwxvr8p8nuny3ct0i20"]},
    {"id": "market",   "name": "GrossMarket",        "domain": "market.triallumii.online",   "match": ["gtjsfiqkffmr6uw9bsyunnvy"]},
    {"id": "rotaplan", "name": "RotaPlan",           "domain": "rotaplan.triallumii.online", "match": ["5bxjwsr156k3farugk3rkv0b"]},
    {"id": "vitrin",   "name": "TARCAN",             "domain": "vitrin.triallumii.online",   "match": ["teszcecvuwghaif17ry8cieb"]},
    {"id": "excel",    "name": "Excel Analyzer",     "domain": "excel.triallumii.online",    "match": ["tc3ls74qhspkrfdjaied7qyo"], "ok": [200, 401]},
    {"id": "supabase", "name": "Supabase (DersKoçu)","domain": "supabase.triallumii.online", "match": ["supabase-"], "path": "/auth/v1/health", "ok": [200, 401]},
    {"id": "shift",    "name": "ShiftTracker",       "domain": "shifttracker.online",        "match": ["z77nzo80cc1uzdgyd1lvcloq", "uz1amejxymtdldwqvlej8emv", "l31sovixufjbrdoqkypgbbub"]},
]
GROUPS = [
    ("coolify", "Coolify altyapısı", ["coolify"]),
    ("observe", "Gözlem (Loki/Grafana)", ["st-grafana", "st-loki", "st-promtail"]),
]


class UnixConn(http.client.HTTPConnection):
    def __init__(self, path, timeout=20):
        super().__init__("localhost", timeout=timeout)
        self.upath = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.upath)
        self.sock = s


def docker_get(path, timeout=20):
    c = UnixConn(SOCK, timeout)
    c.request("GET", path)
    r = c.getresponse()
    data = r.read()
    c.close()
    if r.status >= 300:
        raise RuntimeError(f"docker {path} -> {r.status}")
    return json.loads(data)


def read(path):
    with open(path) as f:
        return f.read()


# ---------- host ----------
_cpu_prev = None


def cpu_percent():
    global _cpu_prev
    vals = list(map(int, read(f"{HOST_PROC}/stat").splitlines()[0].split()[1:]))
    idle = vals[3] + vals[4]
    total = sum(vals)
    pct = None
    if _cpu_prev and total > _cpu_prev[1]:
        pct = round((1 - (idle - _cpu_prev[0]) / (total - _cpu_prev[1])) * 100, 1)
    _cpu_prev = (idle, total)
    return pct


def meminfo():
    m = {}
    for line in read(f"{HOST_PROC}/meminfo").splitlines():
        k, v = line.split(":")
        m[k] = int(v.strip().split()[0]) * 1024
    return m


def host_status():
    m = meminfo()
    st = os.statvfs(HOST_ROOT)
    up = float(read(f"{HOST_PROC}/uptime").split()[0])
    load = [float(x) for x in read(f"{HOST_PROC}/loadavg").split()[:3]]
    disk_total = st.f_blocks * st.f_frsize
    disk_free = st.f_bavail * st.f_frsize
    disk_used = (st.f_blocks - st.f_bfree) * st.f_frsize
    cores = os.cpu_count() or 1
    try:
        cores = sum(1 for l in read(f"{HOST_PROC}/cpuinfo").splitlines() if l.startswith("processor"))
    except Exception:
        pass
    swap_total = m.get("SwapTotal", 0)
    swap_used = swap_total - m.get("SwapFree", 0)
    return {
        "hostname": os.environ.get("HOST_NAME", socket.gethostname()),
        "uptime_s": int(up), "load": load, "cores": cores, "cpu_pct": cpu_percent(),
        "mem": {"total": m["MemTotal"], "available": m["MemAvailable"], "used": m["MemTotal"] - m["MemAvailable"],
                "pct": round((m["MemTotal"] - m["MemAvailable"]) / m["MemTotal"] * 100, 1)},
        "swap": {"total": swap_total, "used": swap_used, "pct": round(swap_used / swap_total * 100, 1) if swap_total else 0},
        "disk": {"total": disk_total, "used": disk_used, "free": disk_free, "pct": round(disk_used / disk_total * 100, 1)},
    }


# ---------- docker ----------
def container_stats(c):
    try:
        s = docker_get(f"/containers/{c['Id']}/stats?stream=false", timeout=15)
        cpu_d = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_d = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
        ncpu = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
        cpu = round(cpu_d / sys_d * ncpu * 100, 2) if sys_d > 0 else 0.0
        ms = s.get("memory_stats", {})
        mem = ms.get("usage", 0) - ms.get("stats", {}).get("inactive_file", 0)
        rx = tx = 0
        for v in (s.get("networks") or {}).values():
            rx += v.get("rx_bytes", 0)
            tx += v.get("tx_bytes", 0)
        return {"cpu_pct": cpu, "mem": mem, "mem_limit": ms.get("limit", 0), "rx": rx, "tx": tx}
    except Exception as e:
        return {"cpu_pct": None, "mem": None, "mem_limit": None, "rx": None, "tx": None, "err": str(e)[:80]}


def classify(name):
    for app in APPS:
        if any(m in name for m in app["match"]):
            return app["id"], app["name"]
    for gid, gname, pats in GROUPS:
        if any(name.startswith(p) for p in pats):
            return gid, gname
    return "other", "Diğer"


def containers():
    lst = docker_get("/containers/json?all=1")
    running = [c for c in lst if c["State"] == "running"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        stats = dict(zip([c["Id"] for c in running], ex.map(container_stats, running)))
    out = []
    for c in lst:
        name = c["Names"][0].lstrip("/")
        status = c.get("Status", "")
        health = ("healthy" if "(healthy)" in status else "unhealthy" if "(unhealthy)" in status
                  else "starting" if "(health: starting)" in status else "none")
        gid, gname = classify(name)
        row = {"name": name, "image": c.get("Image", ""), "state": c["State"], "status": status, "health": health,
               "created": c.get("Created"), "group": gid, "group_name": gname}
        row.update(stats.get(c["Id"], {"cpu_pct": None, "mem": None, "mem_limit": None, "rx": None, "tx": None}))
        out.append(row)
    return out


def docker_df():
    d = docker_get("/system/df", timeout=120)
    imgs = d.get("Images") or []
    vols = d.get("Volumes") or []
    cts = d.get("Containers") or []
    cache = d.get("BuildCache") or []
    img_total = sum((i.get("Size") or 0) for i in imgs)
    img_active = sum((i.get("Size") or 0) for i in imgs if (i.get("Containers") or 0) > 0)
    vsize = lambda v: ((v.get("UsageData") or {}).get("Size") or 0)
    vref = lambda v: ((v.get("UsageData") or {}).get("RefCount") or 0)
    vol_total = sum(vsize(v) for v in vols)
    vol_active = sum(vsize(v) for v in vols if vref(v) > 0)
    cache_total = sum((c.get("Size") or 0) for c in cache)
    cache_active = sum((c.get("Size") or 0) for c in cache if c.get("InUse"))
    return {"images": {"count": len(imgs), "size": img_total, "reclaimable": img_total - img_active},
            "volumes": {"count": len(vols), "size": vol_total, "reclaimable": vol_total - vol_active},
            "containers": {"count": len(cts), "size": sum((c.get("SizeRw") or 0) for c in cts)},
            "build_cache": {"count": len(cache), "size": cache_total, "reclaimable": cache_total - cache_active}}


# ---------- probes ----------
def probe(app):
    url = f"https://{app['domain']}{app.get('path', '/')}"
    t0 = time.time()
    res = {"id": app["id"], "ok_codes": app.get("ok", [200])}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lumii-status/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            res["http"] = r.status
    except urllib.error.HTTPError as e:
        res["http"] = e.code
    except Exception as e:
        res["http"] = None
        res["err"] = str(e)[:100]
    res["ms"] = int((time.time() - t0) * 1000)
    return res


def cert_days(domain):
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=domain) as ss:
            cert = ss.getpeercert()
        exp = ssl.cert_time_to_seconds(cert["notAfter"])
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "")
        return {"days": round((exp - time.time()) / 86400, 1),
                "not_after": datetime.fromtimestamp(exp, timezone.utc).isoformat(), "issuer": issuer}
    except Exception as e:
        return {"days": None, "err": str(e)[:100]}


def apps_probe():
    with ThreadPoolExecutor(max_workers=6) as ex:
        probes = list(ex.map(probe, APPS))
        certs = list(ex.map(lambda a: cert_days(a["domain"]), APPS))
    return [{"id": a["id"], "name": a["name"], "domain": a["domain"], "path": a.get("path", "/"), "http": p.get("http"),
             "ok": p.get("http") in p["ok_codes"], "ms": p["ms"], "http_err": p.get("err"), "cert": c}
            for a, p, c in zip(APPS, probes, certs)]


# ---------- state / loops ----------
STATE = {"host": None, "containers": [], "apps": [], "docker_df": None, "updated": {}, "errors": {}}
LOCK = threading.Lock()


def loop(name, fn, every):
    while True:
        t0 = time.time()
        try:
            val = fn()
            with LOCK:
                STATE[name] = val
                STATE["updated"][name] = datetime.now(timezone.utc).isoformat()
                STATE["errors"].pop(name, None)
        except Exception as e:
            with LOCK:
                STATE["errors"][name] = str(e)[:200]
        time.sleep(max(1, every - (time.time() - t0)))


# ---------- kimlik doğrulama: şifre + TOTP 2FA, çerez oturumu ----------
# Şifre : DASH_PASS_HASH = pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>   (yoksa düz DASH_PASS)
# 2FA   : DASH_TOTP_SECRET = base32 gizli anahtar (RFC 6238, SHA1, 6 hane, 30 sn adım, ±1 pencere)
# Oturum: lumii_sess çerezi = b64url("<exp>.<user>.<hmac_sha256(DASH_SESSION_KEY, 'exp.user')>")
# Kaba kuvvet: aynı IP 5 başarısız denemeden sonra 15 dk kilit (bellekte, süreç ömrü boyunca)
AUTH_USER = os.environ.get("DASH_USER", "").strip()
AUTH_PASS = os.environ.get("DASH_PASS", "")
AUTH_PASS_HASH = os.environ.get("DASH_PASS_HASH", "").strip()
AUTH_TOTP_SECRET = os.environ.get("DASH_TOTP_SECRET", "").strip()
SESSION_KEY = (os.environ.get("DASH_SESSION_KEY") or secrets.token_hex(32)).encode()
SESSION_TTL = int(os.environ.get("DASH_SESSION_TTL", "43200"))  # 12 saat
COOKIE_NAME = "lumii_sess"
FAIL_LIMIT = 5
FAIL_LOCK_S = 900
AUTH_LOCK = threading.Lock()
_fails = {}            # ip -> {"n": deneme, "until": kilit bitişi}
_totp_seen = 0         # kullanılmış en yüksek TOTP adımı — kod tekrar kullanımını engeller


def alog(msg):
    print(f"[auth] {datetime.now(timezone.utc).isoformat()} {msg}", file=sys.stderr, flush=True)


def _b64d(s):
    s = s.strip()
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64ud(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_pass_hash(pw, iterations=240000):
    """`python3 server.py hash <şifre>` ile DASH_PASS_HASH üretmek için.

    Biçim: pbkdf2_sha256:<iterations>:<salt_b64url>:<hash_b64url>
    Ayraç `:` ve padding'siz base64url — değer tamamen [A-Za-z0-9_:-] kümesinde kalır, böylece
    Coolify/compose/kabuk katmanları `$` veya `=` yüzünden değeri bozamaz.
    """
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{_b64u(salt)}:{_b64u(h)}"


def _parse_pass_hash(raw):
    """(iterations, salt, hash) döner. Yeni `:` biçimi ve eski `$` biçimi (b64) desteklenir."""
    raw = raw.strip()
    if raw.startswith("pbkdf2_sha256:"):
        algo, iters, salt_s, hash_s = raw.split(":")
        return int(iters), _b64ud(salt_s), _b64ud(hash_s)
    if raw.startswith("pbkdf2_sha256$"):                      # geriye dönük uyum
        algo, iters, salt_s, hash_s = raw.split("$")
        return int(iters), _b64d(salt_s), _b64d(hash_s)
    raise ValueError("bilinmeyen biçim (pbkdf2_sha256:... bekleniyor)")


def verify_password(pw):
    if AUTH_PASS_HASH:
        try:
            iters, salt, want = _parse_pass_hash(AUTH_PASS_HASH)
            got = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters, dklen=len(want))
        except Exception as e:
            alog(f"DASH_PASS_HASH okunamadı: {type(e).__name__}: {str(e)[:80]}")
            return False
        return hmac.compare_digest(got, want)
    if AUTH_PASS:  # geriye dönük uyum
        return hmac.compare_digest(pw.encode(), AUTH_PASS.encode())
    return False


def _totp_key():
    s = re.sub(r"[\s-]", "", AUTH_TOTP_SECRET).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8), casefold=True)


def totp_code(key, counter, digits=6):
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    val = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(val % (10 ** digits)).zfill(digits)


def verify_totp(code):
    """(ok, sebep) döner. ±1 pencere toleransı; kullanılan adım bir daha kabul edilmez."""
    global _totp_seen
    if not AUTH_TOTP_SECRET:
        return False, "2fa-yapılandırılmadı"
    code = re.sub(r"\s", "", code or "")
    if not (len(code) == 6 and code.isdigit()):
        return False, "kod-biçimi"
    try:
        key = _totp_key()
    except Exception:
        return False, "2fa-anahtarı-bozuk"
    now = int(time.time()) // 30
    for c in (now, now - 1, now + 1):
        if hmac.compare_digest(totp_code(key, c), code):
            with AUTH_LOCK:
                if c <= _totp_seen:
                    return False, "kod-tekrar"
                _totp_seen = c
            return True, "ok"
    return False, "kod-yanlış"


def session_issue(user, ttl=None):
    exp = int(time.time()) + int(ttl or SESSION_TTL)
    body = f"{exp}.{user}"
    sig = hmac.new(SESSION_KEY, body.encode(), hashlib.sha256).hexdigest()
    return _b64u(f"{body}.{sig}".encode()), exp


def session_verify(token):
    if not token:
        return None
    try:
        parts = _b64ud(token).decode().split(".")
        if len(parts) < 3:
            return None
        exp_s, sig = parts[0], parts[-1]
        user = ".".join(parts[1:-1])
        want = hmac.new(SESSION_KEY, f"{exp_s}.{user}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        exp = int(exp_s)
    except Exception:
        return None
    if exp < time.time():
        return None
    return {"user": user, "exp": exp}


def lock_left(ip):
    with AUTH_LOCK:
        f = _fails.get(ip)
        if not f or not f["until"]:
            return 0
        if f["until"] <= time.time():
            _fails.pop(ip, None)
            return 0
        return int(f["until"] - time.time())


def note_fail(ip):
    with AUTH_LOCK:
        f = _fails.setdefault(ip, {"n": 0, "until": 0})
        f["n"] += 1
        if f["n"] >= FAIL_LIMIT:
            f["until"] = time.time() + FAIL_LOCK_S
        if len(_fails) > 5000:  # bellek sızıntısı olmasın
            for k in [k for k, v in _fails.items() if not v["until"] or v["until"] < time.time()][:2000]:
                _fails.pop(k, None)


def note_ok(ip):
    with AUTH_LOCK:
        _fails.pop(ip, None)


# ---------- saldırı verisi (host cron'u yazar, biz sadece okuruz) ----------
SECURITY_FILE = os.environ.get("SECURITY_FILE", "/host/security/attacks.json")
SECURITY_TTL = 10
_sec_cache = {"t": 0.0, "v": None}


def security_data():
    now = time.time()
    with AUTH_LOCK:
        if _sec_cache["v"] is not None and now - _sec_cache["t"] < SECURITY_TTL:
            return _sec_cache["v"]
    try:
        st = os.stat(SECURITY_FILE)
        with open(SECURITY_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("JSON nesnesi bekleniyor")
        d = dict(d)
        d["available"] = True
        d["file"] = SECURITY_FILE
        d["file_mtime"] = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        d = {"available": False, "reason": f"dosya yok: {SECURITY_FILE}"}
    except Exception as e:
        d = {"available": False, "reason": f"{type(e).__name__}: {str(e)[:160]}"}
    with AUTH_LOCK:
        _sec_cache["t"] = now
        _sec_cache["v"] = d
    return d


class H(BaseHTTPRequestHandler):
    server_version = "lumii-status"
    sys_version = ""

    def log_message(self, *a):
        pass

    # ---- yardımcılar ----
    def client_ip(self):
        fwd = self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For") or ""
        return fwd.split(",")[0].strip() or self.client_address[0]

    def cookie(self, name):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            m = http.cookies.SimpleCookie(raw).get(name)
            return m.value if m else None
        except Exception:
            return None

    def session(self):
        return session_verify(self.cookie(COOKIE_NAME))

    def send_json(self, obj, code=200, headers=None):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def send_empty(self, code=204, headers=None):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()

    def read_json(self, limit=8192):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n <= 0:
            return None
        body = self.rfile.read(min(n, limit))
        if n > limit:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    def need_session(self):
        s = self.session()
        if not s:
            self.send_json({"error": "yetkisiz"}, 401)
            return None
        return s

    # ---- yönlendirme ----
    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/api/health"):
            return self.send_json({"ok": True})
        if p == "/api/auth/check":                      # nginx auth_request; gövdesiz de çalışır
            s = self.session()
            if not s:
                return self.send_json({"error": "yetkisiz"}, 401)
            return self.send_json({"user": s["user"], "exp": s["exp"]})
        if p == "/api/public/apps":                     # vitrin için — kimlik gerektirmez, sadece id/up/ms
            with LOCK:
                apps = STATE.get("apps") or []
                stamp = STATE["updated"].get("apps")
            return self.send_json({
                "generated_at": stamp or datetime.now(timezone.utc).isoformat(),
                "apps": {a["id"]: {"up": bool(a.get("ok")), "ms": a.get("ms")} for a in apps},
            })
        if p.startswith("/api/security"):
            if not self.need_session():
                return
            return self.send_json(security_data())
        if p.startswith("/api/status"):
            if not self.need_session():
                return
            with LOCK:
                snap = json.loads(json.dumps(STATE, ensure_ascii=False))
            snap["now"] = datetime.now(timezone.utc).isoformat()
            return self.send_json(snap)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        body = self.read_json() or {}                   # gövde her zaman okunsun (keep-alive)
        ip = self.client_ip()

        if p == "/api/auth/logout":
            alog(f"ip={ip} sonuç=çıkış")
            return self.send_empty(204, [("Set-Cookie", f"{COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0")])

        if p == "/api/auth/login":
            left = lock_left(ip)
            if left:
                alog(f"ip={ip} sonuç=kilitli kalan={left}s")
                return self.send_json({"error": f"çok fazla deneme — {left // 60 + 1} dk sonra tekrar deneyin"}, 429)
            if not AUTH_USER or not (AUTH_PASS_HASH or AUTH_PASS) or not AUTH_TOTP_SECRET:
                alog(f"ip={ip} sonuç=yapılandırma-eksik")
                return self.send_json({"error": "giriş yapılandırılmadı"}, 503)
            user = str(body.get("user") or "")
            pw = str(body.get("pass") or "")
            code = str(body.get("code") or "")
            ok_user = hmac.compare_digest(user.encode(), AUTH_USER.encode())
            ok_pass = verify_password(pw)               # her durumda çalışsın (zamanlama sızıntısı olmasın)
            if not (ok_user and ok_pass):
                note_fail(ip)
                alog(f"ip={ip} user={user[:32]!r} sonuç=başarısız sebep=kimlik")
                return self.send_json({"error": "kullanıcı adı veya şifre hatalı"}, 401)
            ok_totp, why = verify_totp(code)
            if not ok_totp:
                note_fail(ip)
                alog(f"ip={ip} user={user[:32]!r} sonuç=başarısız sebep={why}")
                return self.send_json({"error": "doğrulama kodu geçersiz"}, 401)
            note_ok(ip)
            token, exp = session_issue(AUTH_USER)
            alog(f"ip={ip} user={AUTH_USER!r} sonuç=başarılı exp={exp}")
            ck = f"{COOKIE_NAME}={token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age={SESSION_TTL}"
            return self.send_json({"ok": True, "user": AUTH_USER, "exp": exp}, 200, [("Set-Cookie", ck)])

        self.send_json({"error": "not found"}, 404)


def _cli():
    """Kurulum yardımcıları: `python3 server.py hash <şifre>` ve `python3 server.py totp`."""
    cmd = sys.argv[1]
    if cmd == "hash":
        pw = sys.argv[2] if len(sys.argv) > 2 else input("şifre: ")
        print(make_pass_hash(pw))
        return True
    if cmd == "totp":
        s = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        label = AUTH_USER or "admin"
        print(s)
        print(f"otpauth://totp/Lumii%20Pano:{label}?secret={s}&issuer=Lumii&algorithm=SHA1&digits=6&period=30")
        return True
    return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and _cli():
        raise SystemExit(0)
    _missing = [k for k, v in (("DASH_USER", AUTH_USER),
                               ("DASH_PASS_HASH|DASH_PASS", AUTH_PASS_HASH or AUTH_PASS),
                               ("DASH_TOTP_SECRET", AUTH_TOTP_SECRET)) if not v]
    if _missing:
        alog("eksik ortam değişkeni: " + ", ".join(_missing) + " → giriş kapalı, /durum açılmaz")
    elif not os.environ.get("DASH_SESSION_KEY"):
        alog("DASH_SESSION_KEY yok → geçici anahtar üretildi; yeniden başlatınca oturumlar düşer")
    cpu_percent()  # ilk örnek
    for name, fn, every in [("host", host_status, 5), ("containers", containers, 15),
                            ("apps", apps_probe, 60), ("docker_df", docker_df, 600)]:
        threading.Thread(target=loop, args=(name, fn, every), daemon=True).start()
    print(f"status agent :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
