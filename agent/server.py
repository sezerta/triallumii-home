#!/usr/bin/env python3
"""Sunucu durumu ajanı — yalnız stdlib. Docker soketi + host /proc okur, /api/status JSON döner.
Formüller:
  CPU%            = Δ(meşgul jiffies) / Δ(toplam jiffies) × 100            (host /proc/stat, iki örnek arası)
  RAM%            = (MemTotal − MemAvailable) / MemTotal × 100             (host /proc/meminfo)
  Disk%           = (blocks − bfree) / blocks × 100                        (statvfs, host kökü)
  Konteyner CPU%  = Δcpu_usage / Δsystem_usage × online_cpus × 100         (Docker stats API)
  Konteyner RAM   = memory_stats.usage − inactive_file                     (Docker stats API)
"""
import json, os, time, threading, socket, ssl, http.client, urllib.request, urllib.error
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


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/health"):
            return self.send_json({"ok": True})
        if self.path.startswith("/api/status"):
            with LOCK:
                snap = json.loads(json.dumps(STATE, ensure_ascii=False))
            snap["now"] = datetime.now(timezone.utc).isoformat()
            return self.send_json(snap)
        self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    cpu_percent()  # ilk örnek
    for name, fn, every in [("host", host_status, 5), ("containers", containers, 15),
                            ("apps", apps_probe, 60), ("docker_df", docker_df, 600)]:
        threading.Thread(target=loop, args=(name, fn, every), daemon=True).start()
    print(f"status agent :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
