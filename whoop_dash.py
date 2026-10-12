#!/usr/bin/env python3
"""whoop_dash.py: WHOOP -> dash.json + menu-bar sparkline + dropdown charts (and the data file the WhoopWidget
desktop widget reads). Lives next to whoop_bee.py (state in data/) and shares its WHOOP app, refresh token and lock.

  python3 whoop_dash.py fetch      pull recent sleeps/recoveries/workouts (first run: 90 days), recompute, render
  python3 whoop_dash.py render     recompute + render from the local cache, no network
  python3 whoop_dash.py log TAG    toggle a behavior tag on the relevant night (before 15:00 = last night, after = tonight)
  python3 whoop_dash.py menubar    print the SwiftBar plugin body (menubar/whoop.10m.py execs this)
  python3 whoop_dash.py schedule   install the launchd job com.whoopdash.fetch (hourly + at login)
  python3 whoop_dash.py status     one-line summary for a terminal

Design (inputs first): the always-visible mark is bedtime regularity, an input he controls. HRV and resting heart rate
appear only as 7-night averages against a 60-night personal band (10th-90th percentile); the number shows only when
the average leaves the band. No recovery score, strain, sleep stages or efficiency anywhere. Stdlib only.

WHOOP scopes: read:sleep is enough for the regularity view. HRV/RHR need read:recovery, the late-workout auto-tag
needs read:workout. Add them to the app at developer-dashboard.whoop.com, then run: python3 whoop_bee.py auth
"""
import base64, fcntl, json, math, os, plistlib, statistics, struct, subprocess, sys, time, zlib
import urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # the repo; private state goes in data/
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))
import whoop_bee as wb  # noqa: E402  (load/save/access_token/LOCK/NOON_CUTOFF)

SELF = ROOT / "whoop_dash.py"
CACHE, DASH, JOURNAL, DCFG = DATA / "dash_cache.json", DATA / "dash.json", DATA / "journal.json", DATA / "dash_config.json"
IMG = DATA / "img"
DLOG = DATA / "dash_log.txt"
PLIST = Path.home() / "Library/LaunchAgents/com.whoopdash.fetch.plist"
API = "https://api.prod.whoop.com/developer/v2"
PAGE = 25
FIRST_DAYS, RECENT_DAYS = 90, 12
NOON = getattr(wb, "NOON_CUTOFF", 15)  # a sleep starting before 15:00 local belongs to the previous night

# (key, label, SF Symbol). Manual tags; late_workout is also auto-derived from WHOOP workouts when read:workout is granted.
TAGS = [("alcohol", "Alcohol", "wineglass"),
        ("caffeine", "Caffeine after 2 pm", "cup.and.saucer"),
        ("late_meal", "Ate after 8 pm", "fork.knife"),
        ("late_workout", "Hard workout <2 h before bed", "figure.run")]
TAG_KEYS = [t[0] for t in TAGS]

DEFAULT_DCFG = {"grace_min": 30, "nights_shown": 14, "baseline_days": 60, "band_lo_pct": 10, "band_hi_pct": 90,
                "min_baseline": 14, "late_workout_strain": 10.0, "late_workout_window_min": 120}


# ----------------------------------------------------------------------------------------------------- small utils
def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj, mode=0o600):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.chmod(tmp, mode)
    tmp.replace(path)


def log(msg):
    with DLOG.open("a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")


def dcfg():
    c = dict(DEFAULT_DCFG)
    c.update(read_json(DCFG, {}))
    try:
        c.setdefault("target", wb.load().get("target", "23:00"))
    except Exception:
        c.setdefault("target", "23:00")
    return c


def parse_offset(tz):  # WHOOP timezone_offset: '+hh:mm', '-hh:mm' or 'Z'
    if not tz or tz == "Z":
        return timedelta(0)
    sign = -1 if tz[0] == "-" else 1
    hh, mm = tz.lstrip("+-").split(":")
    return sign * timedelta(hours=int(hh), minutes=int(mm))


def local_dt(iso_utc, tz):
    s = iso_utc.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:  # odd fraction length: drop the fraction
        head, _, tail = s.partition(".")
        dt = datetime.fromisoformat(head + tail[tail.find("+"):] if "+" in tail else head)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) + parse_offset(tz)).replace(tzinfo=None)


def night_of(start_local):
    return (start_local - timedelta(hours=NOON)).date()


def hm(t):  # "23:00" -> time
    h, m = t.split(":")
    return dtime(int(h), int(m))


def mins_after_18(dt):  # time of day as minutes after 18:00, so 00:47 -> 407 (no midnight wrap in medians/charts)
    m = dt.hour * 60 + dt.minute - 18 * 60
    return m + 1440 if m < 0 else m


def percentile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    return xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f)


def fmt_dur(mins):
    if mins is None:
        return "–"
    h, m = divmod(int(round(mins)), 60)
    return f"{h} h {m:02d} min"


def relevant_night(now=None):
    """Which night a tag logged now refers to: before 15:00 it's last night, after it's tonight."""
    now = now or datetime.now()
    return (now - timedelta(hours=NOON)).date()


# ------------------------------------------------------------------------------------------------------- lock + API
class Lock:
    """Same lock file whoop_bee.py uses, so the two scripts never refresh the (rotating) WHOOP token at the same time."""

    def __init__(self, wait=90):
        self.wait = wait
        self.fh = None

    def __enter__(self):
        self.fh = open(wb.LOCK, "a")
        deadline = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() > deadline:
                    raise SystemExit("whoop_bee.py holds the WHOOP lock; try again in a minute")
                time.sleep(1)

    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


class ScopeMissing(Exception):
    pass


def api_get(url, token, params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{url}?{q}", headers={"Authorization": f"Bearer {token}", "User-Agent": "whoop-dash/1.0"})  # Cloudflare 403s the default UA
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403):
                raise ScopeMissing(f"{e.code} {body}")
            if e.code == 429 and attempt < 3:
                time.sleep(int(e.headers.get("Retry-After", "20")))
                continue
            raise SystemExit(f"WHOOP {e.code} on {url}: {body}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise SystemExit(f"WHOOP unreachable: {e.reason}")


def api_pages(url, token, start, end):
    out, tok = [], None
    while True:
        page = api_get(url, token, {"start": start, "end": end, "limit": PAGE, "nextToken": tok})
        out += page.get("records", [])
        tok = page.get("next_token")
        if not tok:
            return out


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_token(cfg):
    tok = wb.access_token(cfg)
    if isinstance(tok, dict):
        tok = tok.get("access_token")
    try:
        wb.save(cfg)  # whoop_bee rotates the refresh token on every refresh; make sure the new one is on disk
    except Exception as e:  # pragma: no cover
        log(f"save after refresh failed: {e}")
    return tok


def fetch():
    cache = read_json(CACHE, {"sleep": {}, "recovery": {}, "workout": {}, "scopes": {}, "errors": {}})
    now = datetime.now(timezone.utc)
    end = iso_z(now + timedelta(days=1))
    with Lock():
        cfg = wb.load()
        token = get_token(cfg)
        errors = {}
        for kind, url, key in (("sleep", f"{API}/activity/sleep", "id"),
                               ("recovery", f"{API}/recovery", "sleep_id"),
                               ("workout", f"{API}/activity/workout", "id")):
            try:  # a kind with nothing cached yet (first run, or a scope granted later) backfills the full window
                start = iso_z(now - timedelta(days=FIRST_DAYS if not cache[kind] else RECENT_DAYS))
                recs = api_pages(url, token, start, end)
                for r in recs:
                    old = cache[kind].get(str(r[key]))
                    if not old or r.get("updated_at", "") >= old.get("updated_at", ""):
                        cache[kind][str(r[key])] = r
                cache["scopes"][kind] = True
            except ScopeMissing as e:
                if kind == "sleep":  # the token itself was refused (read:sleep is the one scope the app always has)
                    log(f"fetch failed: WHOOP refused the token: {e}")
                    raise SystemExit(f"WHOOP refused the token ({e}). Run: python3 {ROOT / 'whoop_bee.py'} auth")
                cache["scopes"][kind] = False  # same token worked for sleep, so this is a missing scope
                errors[kind] = str(e)
    cache["errors"] = errors
    cache["fetched"] = datetime.now().isoformat(timespec="seconds")
    write_json(CACHE, cache)
    log(f"fetch ok: {len(cache['sleep'])} sleeps, {len(cache['recovery'])} recoveries, {len(cache['workout'])} workouts; "
        f"scopes {cache['scopes']}")
    return cache


# ---------------------------------------------------------------------------------------------------------- compute
def sleep_minutes(rec):
    sc = rec.get("score") or {}
    st = sc.get("stage_summary") or {}
    if rec.get("score_state") == "SCORED" and st:
        tst = (st.get("total_light_sleep_time_milli", 0) + st.get("total_slow_wave_sleep_time_milli", 0)
               + st.get("total_rem_sleep_time_milli", 0)) / 60000
        return tst, st.get("total_in_bed_time_milli", 0) / 60000
    s, e = local_dt(rec["start"], rec.get("timezone_offset")), local_dt(rec["end"], rec.get("timezone_offset"))
    return None, (e - s).total_seconds() / 60


def journal():
    return read_json(JOURNAL, {})


def effective_tags(night_iso, auto, manual):
    tags = set(auto)
    for k, v in (manual.get(night_iso) or {}).items():
        if k in TAG_KEYS:
            (tags.add if v else tags.discard)(k)
    return sorted(tags)


def build_nights(cache, cfg):
    target = hm(cfg["target"])
    by_night = {}
    for rec in cache["sleep"].values():
        if rec.get("nap"):
            continue
        s, e = local_dt(rec["start"], rec.get("timezone_offset")), local_dt(rec["end"], rec.get("timezone_offset"))
        by_night.setdefault(night_of(s), []).append((s, e, rec))
    rec_by_sleep = cache.get("recovery", {})
    workouts = []
    for w in cache.get("workout", {}).values():
        try:
            workouts.append((local_dt(w["start"], w.get("timezone_offset")), local_dt(w["end"], w.get("timezone_offset")),
                             ((w.get("score") or {}).get("strain"))))
        except (KeyError, ValueError):
            pass
    manual = journal()
    nights = []
    for night, items in sorted(by_night.items()):
        items.sort()
        asleep, wake = items[0][0], max(i[1] for i in items)
        main = items[0][2]
        tst = sum((sleep_minutes(r)[0] or 0) for _, _, r in items) or None
        in_bed = sum(sleep_minutes(r)[1] for _, _, r in items)
        target_dt = datetime.combine(night, target)
        offset = round((asleep - target_dt).total_seconds() / 60)
        rec = rec_by_sleep.get(str(main.get("id"))) or {}
        rs = rec.get("score") or {}
        scored = rec.get("score_state") == "SCORED" and rs
        auto = []
        win = timedelta(minutes=cfg["late_workout_window_min"])
        for ws, we, strain in workouts:
            if asleep - win <= we <= asleep and (strain is None or strain >= cfg["late_workout_strain"]):
                auto.append("late_workout")
        ms = main.get("score") or {}
        nights.append({
            "night": night.isoformat(), "asleep": asleep.strftime("%Y-%m-%dT%H:%M"), "wake": wake.strftime("%Y-%m-%dT%H:%M"),
            "offset_min": offset, "asleep_m18": mins_after_18(asleep), "wake_m18": mins_after_18(wake),
            "tst_min": None if tst is None else round(tst), "in_bed_min": round(in_bed),
            "sleeps": len(items),
            "hrv": round(rs["hrv_rmssd_milli"], 1) if scored and rs.get("hrv_rmssd_milli") is not None else None,
            "rhr": round(rs["resting_heart_rate"]) if scored and rs.get("resting_heart_rate") is not None else None,
            "calibrating": bool(rs.get("user_calibrating")) if scored else None,
            "respiratory_rate": ms.get("respiratory_rate"),
            "sleep_consistency": ms.get("sleep_consistency_percentage"),
            "tags": effective_tags(night.isoformat(), auto, manual), "auto_tags": sorted(set(auto)),
        })
    return nights


def sri(cache, days=14, today=None):
    """Sleep Regularity Index (Phillips et al. 2017): 200 x P(same sleep/wake state at the same minute on consecutive
    days) - 100. Uses all sleeps incl. naps over the last `days` calendar days; days without any recorded sleep are
    treated as missing and skipped. Returns (sri, pairs) or (None, pairs)."""
    today = today or date.today()
    first = today - timedelta(days=days)
    slots = {first + timedelta(days=i): bytearray(1440) for i in range(days + 1)}
    covered = set()
    for rec in cache["sleep"].values():
        s, e = local_dt(rec["start"], rec.get("timezone_offset")), local_dt(rec["end"], rec.get("timezone_offset"))
        t = s.replace(second=0, microsecond=0)
        while t < e:
            d = t.date()
            if d in slots:
                slots[d][t.hour * 60 + t.minute] = 1
                covered.add(d)
            t += timedelta(minutes=1)
    # only days whose evening has already been slept through count: up to the day the latest sleep started
    starts = [local_dt(r["start"], r.get("timezone_offset")).date() for r in cache["sleep"].values()]
    last_full = max(starts) if starts else today
    match = pairs = 0
    for d in sorted(slots)[:-1]:
        n = d + timedelta(days=1)
        if d in covered and n in covered and n <= min(today, last_full):
            match += sum(1 for a, b in zip(slots[d], slots[n]) if a == b)
            pairs += 1
    if pairs < 5:
        return None, pairs
    return round(-100 + 200 * match / (1440 * pairs), 1), pairs


def band_series(nights, key, cfg, days_out=30):
    """For each of the last `days_out` nights: value, trailing 7-night mean, and the 10th/90th percentile band of the
    previous `baseline_days` nights (needs min_baseline values). Also the current status of the 7-night mean."""
    vals = [(date.fromisoformat(n["night"]), n[key]) for n in nights if n.get(key) is not None]
    if not vals:
        return {"status": "none", "series": [], "n_baseline": 0}
    last = vals[-1][0]
    out = []
    for d, v in vals:
        if (last - d).days >= days_out:
            continue
        base = [x for dd, x in vals if 0 < (d - dd).days <= cfg["baseline_days"]]
        recent = [x for dd, x in vals if 0 <= (d - dd).days < 7]
        row = {"night": d.isoformat(), "v": v, "avg7": round(statistics.fmean(recent), 1) if len(recent) >= 3 else None}
        if len(base) >= cfg["min_baseline"]:
            row.update(lo=round(percentile(base, cfg["band_lo_pct"]), 1), hi=round(percentile(base, cfg["band_hi_pct"]), 1),
                       med=round(percentile(base, 50), 1))
        out.append(row)
    cur = out[-1]
    status = "collecting"
    if cur.get("lo") is not None and cur.get("avg7") is not None:
        status = "below" if cur["avg7"] < cur["lo"] else "above" if cur["avg7"] > cur["hi"] else "in"
    elif cur.get("avg7") is None:
        status = "collecting"
    n_base = len([x for dd, x in vals if 0 < (last - dd).days <= cfg["baseline_days"]])
    return {"status": status, "avg7": cur.get("avg7"), "lo": cur.get("lo"), "hi": cur.get("hi"), "med": cur.get("med"),
            "n_baseline": n_base, "last_night": cur["night"], "series": out}


def compute(cache=None):
    cfg = dcfg()
    cache = cache or read_json(CACHE, {"sleep": {}, "recovery": {}, "workout": {}, "scopes": {}})
    nights = build_nights(cache, cfg)
    recent = nights[-cfg["nights_shown"]:]
    last10 = nights[-10:]
    hits = sum(1 for n in last10 if n["offset_min"] <= cfg["grace_min"])
    last7 = nights[-7:]
    med_asleep = statistics.median(n["asleep_m18"] for n in last7) if last7 else None
    spread = (percentile([n["asleep_m18"] for n in last7], 75) - percentile([n["asleep_m18"] for n in last7], 25)) if len(last7) >= 4 else None
    sri_val, pairs = sri(cache)
    dash = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fetched_at": cache.get("fetched"),
        "target": cfg["target"], "grace_min": cfg["grace_min"], "nights_shown": cfg["nights_shown"],
        "scopes": cache.get("scopes", {}), "errors": cache.get("errors", {}),
        "tags_catalog": [{"key": k, "label": l, "symbol": s} for k, l, s in TAGS],
        "log_night": relevant_night().isoformat(),
        "nights": recent,
        "regularity": {
            "sri_14": sri_val, "sri_pairs": pairs,
            "hits_10": hits, "n_10": len(last10),
            "median_asleep_7": None if med_asleep is None else f"{((med_asleep + 18 * 60) // 60) % 24:02.0f}:{med_asleep % 60:02.0f}",
            "asleep_iqr_7_min": None if spread is None else round(spread),
            "avg_tst_7_min": round(statistics.fmean(n["tst_min"] for n in last7 if n["tst_min"])) if any(n["tst_min"] for n in last7) else None,
        },
        "hrv": band_series(nights, "hrv", cfg), "rhr": band_series(nights, "rhr", cfg),
        "last": recent[-1] if recent else None,
    }
    return dash, nights


# -------------------------------------------------------------------------------------------- tiny raster + PNG writer
# Stdlib-only renderer: supersampled RGBA canvas with 'over' compositing, boxed down to the output size, written as PNG
# with a 144-dpi pHYs chunk so macOS treats 2x pixels as points (SwiftBar reads images at their point size).
PALETTE = {  # dataviz reference palette; light/dark pairs chosen for each surface
    "light": {"blue": "#2a78d6", "aqua": "#1baf7a", "orange": "#eb6834", "ink": "#0b0b0b", "ink2": "#52514e",
              "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7"},
    # dark values are luminous on purpose: read at night through f.lux / blue-blocking glasses, dim hues vanish
    "dark": {"blue": "#9cc8ff", "aqua": "#6fe3b4", "orange": "#ff9a62", "ink": "#ffffff", "ink2": "#f4f2ec",
             "muted": "#c4c1b8", "grid": "#45443f", "axis": "#5a5954"},
}
FONT = {'0': "111101101101111", '1': "010110010010111", '2': "111001111100111", '3': "111001111001111",
        '4': "101101111001001", '5': "111100111001111", '6': "111100111101111", '7': "111001001001001",
        '8': "111101111101111", '9': "111101111001111", ':': "000010000010000", '-': "000000111000000",
        '+': "000010111010000", '.': "000000000000010", ' ': "000000000000000"}


def rgba(hexcol, a=1.0):
    h = hexcol.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


class Canvas:
    def __init__(self, w, h, ss=2, scale=2):
        """w, h in points; the PNG is w*scale x h*scale pixels (2x = retina) tagged 144 dpi; ss = supersampling."""
        self.ss, self.k = ss, ss * scale
        self.w, self.h = w * scale, h * scale
        self.W, self.H = self.w * ss, self.h * ss
        self.buf = [0.0] * (self.W * self.H * 4)

    def _blend(self, i, col):
        r, g, b, a = col
        buf = self.buf
        da = buf[i + 3]
        oa = a + da * (1 - a)
        if oa <= 0:
            return
        k = da * (1 - a)
        buf[i] = (r * a + buf[i] * k) / oa
        buf[i + 1] = (g * a + buf[i + 1] * k) / oa
        buf[i + 2] = (b * a + buf[i + 2] * k) / oa
        buf[i + 3] = oa

    def fill(self, x0, y0, x1, y1, pred, col):
        k = self.k
        X0, Y0 = max(0, int(math.floor(x0 * k))), max(0, int(math.floor(y0 * k)))
        X1, Y1 = min(self.W, int(math.ceil(x1 * k))), min(self.H, int(math.ceil(y1 * k)))
        for Y in range(Y0, Y1):
            py = (Y + 0.5) / k
            row = Y * self.W
            for X in range(X0, X1):
                if pred((X + 0.5) / k, py):
                    self._blend((row + X) * 4, col)

    def rect(self, x0, y0, x1, y1, col):
        self.fill(x0, y0, x1, y1, lambda x, y: True, col)

    def rrect(self, x0, y0, x1, y1, r, col):
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)

        def pred(x, y):
            cx = min(max(x, x0 + r), x1 - r)
            cy = min(max(y, y0 + r), y1 - r)
            return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
        self.fill(x0, y0, x1, y1, pred, col)

    def circle(self, cx, cy, r, col):
        self.fill(cx - r, cy - r, cx + r, cy + r, lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r, col)

    def ring(self, cx, cy, r, w, col):
        ri = r - w
        self.fill(cx - r, cy - r, cx + r, cy + r,
                  lambda x, y: ri * ri <= (x - cx) ** 2 + (y - cy) ** 2 <= r * r, col)

    def line(self, x0, y0, x1, y1, w, col):
        hw = w / 2
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy or 1e-9

        def pred(x, y):
            t = max(0, min(1, ((x - x0) * dx + (y - y0) * dy) / L2))
            return (x - (x0 + t * dx)) ** 2 + (y - (y0 + t * dy)) ** 2 <= hw * hw
        self.fill(min(x0, x1) - hw, min(y0, y1) - hw, max(x0, x1) + hw, max(y0, y1) + hw, pred, col)

    def polyline(self, pts, w, col):
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            self.line(ax, ay, bx, by, w, col)

    def band(self, xs, los, his, col):
        """Fill between two polylines sharing xs (lo above hi or vice versa, either order)."""
        if len(xs) < 2:
            return

        def pred(x, y):
            for i in range(len(xs) - 1):
                if xs[i] <= x <= xs[i + 1]:
                    t = (x - xs[i]) / ((xs[i + 1] - xs[i]) or 1e-9)
                    a = los[i] + t * (los[i + 1] - los[i])
                    b = his[i] + t * (his[i + 1] - his[i])
                    return min(a, b) <= y <= max(a, b)
            return False
        self.fill(min(xs), min(min(los), min(his)), max(xs), max(max(los), max(his)), pred, col)

    def text(self, x, y, s, scale, col, align="left"):
        """3x5 bitmap digits; scale = pixel size of one font cell. Returns text width."""
        adv = 4 * scale
        width = len(s) * adv - scale
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        for ch in s:
            bits = FONT.get(ch, FONT[' '])
            for i, b in enumerate(bits):
                if b == "1":
                    cx, cy = x + (i % 3) * scale, y + (i // 3) * scale
                    self.rect(cx, cy, cx + scale, cy + scale, col)
            x += adv
        return width

    def png(self, dpi=144):
        ss, W, H, w, h = self.ss, self.W, self.H, self.w, self.h
        n = ss * ss
        raw = bytearray()
        buf = self.buf
        for y in range(h):
            raw.append(0)
            for x in range(w):
                r = g = b = a = 0.0
                for sy in range(ss):
                    base = ((y * ss + sy) * W + x * ss) * 4
                    for sx in range(ss):
                        i = base + sx * 4
                        pa = buf[i + 3]
                        r += buf[i] * pa
                        g += buf[i + 1] * pa
                        b += buf[i + 2] * pa
                        a += pa
                if a > 0:
                    raw += bytes((int(r / a * 255 + 0.5), int(g / a * 255 + 0.5), int(b / a * 255 + 0.5),
                                  int(a / n * 255 + 0.5)))
                else:
                    raw += b"\0\0\0\0"

        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
        ppm = int(dpi / 0.0254 + 0.5)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1)) + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                + chunk(b"IEND", b""))


# ----------------------------------------------------------------------------------------------------------- charts
def night_slots(nights, n):
    """Last n nights as fixed calendar slots ending at the most recent night (gaps stay empty)."""
    if not nights:
        return []
    last = date.fromisoformat(nights[-1]["night"])
    by = {x["night"]: x for x in nights}
    return [(last - timedelta(days=n - 1 - i), by.get((last - timedelta(days=n - 1 - i)).isoformat())) for i in range(n)]


def render_sparkline(dash, w=72, h=18):
    """Menu-bar template image: 14 nights of fall-asleep time vs the target line; later = lower. Filled dot = plain
    night, hollow = a behavior was logged, bigger = last night, gap = no data. Band = the grace window."""
    c = Canvas(w, h, ss=3)
    black = rgba("#000000")
    slots = night_slots(dash["nights"], dash["nights_shown"])
    lo, hi = -60, 180  # minutes vs target shown; clipped beyond
    top, bot = 2.5, h - 2.5

    def ypos(off):
        off = max(lo, min(hi, off))
        return top + (off - lo) / (hi - lo) * (bot - top)
    y0, yg = ypos(0), ypos(dash["grace_min"])
    c.rect(1, y0, w - 1, yg, rgba("#000000", 0.14))
    c.rect(1, y0 - 0.4, w - 1, y0 + 0.4, rgba("#000000", 0.5))
    step = (w - 6) / max(1, len(slots) - 1)
    for i, (d, n) in enumerate(slots):
        if not n:
            continue
        x = 3 + i * step
        y = ypos(n["offset_min"])
        last = i == len(slots) - 1
        r = 2.1 if last else 1.6
        if n["tags"]:
            c.ring(x, y, r + 0.2, 0.9, black)
        else:
            c.circle(x, y, r, black)
    return c.png()


def _hour_to_y(m18, top, bot, span=16 * 60):  # 18:00 -> top ... 10:00 next day -> bot
    return top + (m18 / span) * (bot - top)


def render_timeline(dash, mode, w=400, h=230):
    """Dropdown chart: one column per night from asleep to wake on a 20:00 -> 10:00 clock (later = lower), the target
    line, hour gridlines with labels, day-of-month under each column, and a dot above nights with a logged behavior."""
    P = PALETTE[mode]
    c = Canvas(w, h, ss=2)
    L, R, T, B = 26, w - 8, 14, h - 16
    span_lo, span_hi = 2 * 60, 17 * 60  # 20:00 .. 11:00 as minutes after 18:00

    def ypos(m18):
        m18 = max(span_lo, min(span_hi, m18))
        return T + (m18 - span_lo) / (span_hi - span_lo) * (B - T)
    for hr in range(20, 36, 2):  # 20,22,0,2,4,6,8,10
        m18 = hr * 60 - 18 * 60
        y = ypos(m18)
        c.rect(L, y - 0.5, R, y + 0.5, rgba(P["grid"]))
        c.text(L - 4, y - 3.5, f"{hr % 24:02d}", 1.4, rgba(P["muted"]), align="right")
    tgt = hm(dash["target"])
    ty = ypos(mins_after_18(datetime.combine(date.today(), tgt)))
    c.rect(L, ty - 0.9, R, ty + 0.9, rgba(P["ink2"], 0.9))
    slots = night_slots(dash["nights"], dash["nights_shown"])
    n_slots = len(slots) or 1
    slot_w = (R - L) / n_slots
    bar_w = min(14, slot_w * 0.55)
    col = rgba(P["blue"])
    for i, (d, n) in enumerate(slots):
        cx = L + slot_w * (i + 0.5)
        c.text(cx, B + 4, f"{d.day:02d}", 1.3, rgba(P["muted"]), align="center")
        if not n:
            continue
        a, wk = n["asleep_m18"], n["wake_m18"]
        if wk < a:
            wk += 1440
        y_a, y_w = ypos(a), ypos(wk)
        c.rrect(cx - bar_w / 2, y_a, cx + bar_w / 2, max(y_w, y_a + 3), 3, col)
        if n["tags"]:
            c.circle(cx, T - 7, 2.6, rgba(P["ink2"]))
    return c.png()


def render_band(series_block, mode, unit_digits=0, w=400, h=84):
    """7-night mean (line) inside the personal band (10th-90th percentile of the previous 60 nights, wash).
    Nightly values are deliberately not drawn."""
    P = PALETTE[mode]
    c = Canvas(w, h, ss=2)
    rows = series_block.get("series") or []
    hue = rgba(P["aqua"]) if series_block.get("_hue") == "aqua" else rgba(P["orange"])
    L, R, T, B = 30, w - 8, 8, h - 8
    if len(rows) < 2:
        return c.png()
    vals = [r["avg7"] for r in rows if r.get("avg7") is not None] + \
           [r[k] for r in rows for k in ("lo", "hi") if r.get(k) is not None]
    if not vals:
        return c.png()
    vmin, vmax = min(vals), max(vals)
    pad = (vmax - vmin) * 0.15 or 1
    vmin, vmax = vmin - pad, vmax + pad
    d0 = date.fromisoformat(rows[0]["night"])
    d1 = date.fromisoformat(rows[-1]["night"])
    days = max(1, (d1 - d0).days)

    def xp(night):
        return L + (date.fromisoformat(night) - d0).days / days * (R - L)

    def yp(v):
        return B - (v - vmin) / (vmax - vmin) * (B - T)
    band_rows = [r for r in rows if r.get("lo") is not None]
    if len(band_rows) >= 2:
        c.band([xp(r["night"]) for r in band_rows], [yp(r["lo"]) for r in band_rows], [yp(r["hi"]) for r in band_rows],
               (hue[0], hue[1], hue[2], 0.16))
    pts = [(xp(r["night"]), yp(r["avg7"])) for r in rows if r.get("avg7") is not None]
    if len(pts) >= 2:
        c.polyline(pts, 2, hue)
        c.circle(*pts[-1], 3.6, rgba("#fcfcfb" if mode == "light" else "#1a1a19"))
        c.circle(*pts[-1], 2.6, hue)
    c.rect(L, B, R, B + 0.6, rgba(P["axis"]))
    fmt = f"{{:.{unit_digits}f}}"
    c.text(L - 4, yp(vmax - pad) - 3, fmt.format(vmax - pad), 1.3, rgba(P["muted"]), align="right")
    c.text(L - 4, yp(vmin + pad) - 3, fmt.format(vmin + pad), 1.3, rgba(P["muted"]), align="right")
    return c.png()


def render_all(dash):
    IMG.mkdir(exist_ok=True)
    (IMG / "spark.png").write_bytes(render_sparkline(dash))
    for mode in ("light", "dark"):
        (IMG / f"timeline_{mode}.png").write_bytes(render_timeline(dash, mode))
        for key, hue in (("hrv", "aqua"), ("rhr", "orange")):
            blk = dict(dash[key])
            blk["_hue"] = hue
            (IMG / f"{key}_{mode}.png").write_bytes(render_band(blk, mode, 1 if key == "hrv" else 0))


# --------------------------------------------------------------------------------------------------------- SwiftBar
TEXT = "#1d1d1f,#f4f2ec"  # SwiftBar greys out a line with no action unless it has an explicit color
NOTE = "#5f5d57,#c4c1b8"  # captions


def appearance():
    """'dark' or 'light'. SwiftBar 2.1.1 swaps the two halves of image=light,dark in dark mode, so send one image."""
    r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
    return "dark" if "Dark" in r.stdout else "light"


def b64(path):
    return base64.b64encode(path.read_bytes()).decode()


def band_line(name, blk, unit, digits):
    st = blk.get("status")
    if st == "none":
        return f"{name}: no data yet"
    if st == "collecting":
        return f"{name}: collecting your baseline ({blk.get('n_baseline', 0)}/{DEFAULT_DCFG['min_baseline']} nights)"
    f = f"{{:.{digits}f}}"
    if st == "in":
        return f"{name} 7-night avg: in your range ({f.format(blk['lo'])}–{f.format(blk['hi'])} {unit})"
    word = "below" if st == "below" else "above"
    return f"{name} 7-night avg {f.format(blk['avg7'])} {unit}, {word} your range {f.format(blk['lo'])}–{f.format(blk['hi'])}"


def legacy_bedtime_lines():
    """If the older Beeminder plugin is still around, fold its dropdown into a submenu so it can be disabled in SwiftBar."""
    p = ROOT / "menubar" / "bedtime.10m.py"
    if not p.exists():
        return []
    try:
        out = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    lines = out.splitlines()
    if "---" not in lines:
        return []
    cut = lines.index("---")
    head = []  # keep the icon (bed, or airplane on travel nights); drop the menu-bar font and colors
    for l in lines[:cut]:
        if l.strip():
            text, _, params = l.partition("|")
            sf = next((x for x in params.split() if x.startswith("sfimage=")), "")
            head.append(f"{text.strip()} | {sf} color={TEXT}")
    body = [l for l in lines[cut + 1:] if l.strip() and l.strip() != "---"]
    sub = ["Beeminder bedtime | sfimage=bell"]
    for l in head:
        sub.append(f"--{l}")
    for l in body:
        if not any(k in l for k in ("color=", "href=", "bash=")):
            l += (" " if "|" in l else " | ") + f"color={TEXT}"
        sub.append(f"--{l}")
    return sub


def menubar():
    dash = read_json(DASH, None)
    if dash is None or not (IMG / "spark.png").exists():
        print("WHOOP | sfimage=moon.zzz")
        print("---")
        print(f"No data yet. Run: python3 {SELF} fetch | bash={sys.executable} param1={SELF} param2=fetch terminal=false refresh=true")
        return
    # keep data fresh without hitting WHOOP every 10 minutes: refetch in the background when the cache is >60 min old
    try:
        age = (datetime.now() - datetime.fromisoformat(dash.get("fetched_at") or "2000-01-01")).total_seconds()
    except ValueError:
        age = 1e9
    if age > 3600 and not (DATA / "dash_fetch.pid").exists():
        subprocess.Popen([sys.executable, str(SELF), "fetch-bg"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    reg, last = dash["regularity"], dash.get("last")
    tgt = dash["target"]
    grace_t = (datetime.combine(date.today(), hm(tgt)) + timedelta(minutes=dash["grace_min"])).strftime("%H:%M")
    py, me = sys.executable, str(SELF)
    mode = appearance()
    out = [f"| templateImage={b64(IMG / 'spark.png')} dropdown=false", "---"]
    out.append(f"Asleep by {grace_t}: {reg['hits_10']} of last {reg['n_10']} nights | sfimage=moon.zzz size=13 color=#1d1d1f,#f4f2ec")
    if reg.get("sri_14") is not None:
        out.append(f"Regularity (SRI, 14 nights): {reg['sri_14']:.0f} / 100 | sfimage=waveform.path color=#1d1d1f,#f4f2ec "
                   f"tooltip=Sleep Regularity Index: how similar your sleep/wake pattern is from one day to the next (Phillips et al. 2017)")
    if reg.get("median_asleep_7"):
        spread = f", middle half within {reg['asleep_iqr_7_min']} min" if reg.get("asleep_iqr_7_min") is not None else ""
        out.append(f"Median fall-asleep, 7 nights: {reg['median_asleep_7']}{spread} | sfimage=clock color=#1d1d1f,#f4f2ec")
    if last:
        a = datetime.fromisoformat(last["asleep"]).strftime("%H:%M")
        wk = datetime.fromisoformat(last["wake"]).strftime("%H:%M")
        out.append(f"Last night: asleep {a} · woke {wk} · {fmt_dur(last['tst_min'])} | sfimage=bed.double color=#1d1d1f,#f4f2ec")
    out.append("---")
    out.append(f"| image={b64(IMG / ('timeline_' + mode + '.png'))} width=400 height=230")
    out.append(f"Columns: asleep → awake per night, 20:00 at the top; line = {tgt} target; dot above = something logged | size=11 color=#5f5d57,#c4c1b8")
    out.append("---")
    if dash["scopes"].get("recovery"):
        out.append(band_line("HRV", dash["hrv"], "ms", 1) + " | sfimage=heart color=#1d1d1f,#f4f2ec")
        out.append(f"| image={b64(IMG / ('hrv_' + mode + '.png'))} width=400 height=84")
        out.append(band_line("Resting HR", dash["rhr"], "bpm", 0) + " | sfimage=heart.fill color=#1d1d1f,#f4f2ec")
        out.append(f"| image={b64(IMG / ('rhr_' + mode + '.png'))} width=400 height=84")
        out.append("Line = 7-night average; band = 10th–90th percentile of your previous 60 nights | size=11 color=#5f5d57,#c4c1b8")
    else:
        out.append("HRV / resting HR: add the read:recovery scope to your WHOOP app, then run whoop_bee.py auth | sfimage=heart "
                   "href=https://developer-dashboard.whoop.com")
    out.append("---")
    night = date.fromisoformat(dash["log_night"])
    which = "last night" if datetime.now().hour < NOON else "tonight"
    tags_now = set((journal().get(night.isoformat()) or {}).keys())
    eff = set()
    for n in dash["nights"]:
        if n["night"] == night.isoformat():
            eff = set(n["tags"])
    out.append(f"Log for {which} ({night:%a %b %d}) | size=11 color=#5f5d57,#c4c1b8")
    for key, label, sym in TAGS:
        on = key in eff or (journal().get(night.isoformat()) or {}).get(key) is True
        out.append(f"{label} | sfimage={sym} {'checked=true ' if on else ''}bash={py} param1={me} param2=log param3={key} "
                   f"terminal=false refresh=true")
    out.append("---")
    out += legacy_bedtime_lines()
    out.append(f"Sync now | sfimage=arrow.clockwise bash={py} param1={me} param2=fetch terminal=false refresh=true")
    fetched = dash.get("fetched_at") or "never"
    try:
        fetched = datetime.fromisoformat(fetched).strftime("%a %H:%M")
    except ValueError:
        pass
    sc = dash["scopes"]
    scope_txt = "sleep" + (", recovery" if sc.get("recovery") else "") + (", workout" if sc.get("workout") else "")
    out.append(f"Updated {fetched} · WHOOP scopes: {scope_txt} | size=11 color=#5f5d57,#c4c1b8 href=https://developer-dashboard.whoop.com")
    print("\n".join(out))


# ---------------------------------------------------------------------------------------------------- commands
def toggle(tag):
    if tag not in TAG_KEYS:
        raise SystemExit(f"unknown tag {tag}; one of {TAG_KEYS}")
    night = relevant_night().isoformat()
    j = journal()
    day = j.setdefault(night, {})
    dash = read_json(DASH, {})
    auto = set()
    for n in dash.get("nights", []):
        if n["night"] == night:
            auto = set(n.get("auto_tags", []))
    cur = day.get(tag, tag in auto)
    day[tag] = not cur
    write_json(JOURNAL, j)
    render()
    log(f"log {night} {tag} -> {not cur}")


def render(cache=None):
    dash, _ = compute(cache)
    write_json(DASH, dash, 0o644)
    render_all(dash)
    return dash


def refresh_swiftbar():
    subprocess.run(["open", "-g", "swiftbar://refreshplugin?name=whoop"], capture_output=True)


def fetch_and_render(background=False):
    pid = DATA / "dash_fetch.pid"
    if background:
        pid.write_text(str(os.getpid()))
    try:
        cache = fetch()
        dash = render(cache)
        refresh_swiftbar()
        return dash
    finally:
        if background and pid.exists():
            pid.unlink()


def schedule():
    plist = {"Label": "com.whoopdash.fetch", "ProgramArguments": [sys.executable, str(SELF), "fetch"],
             "StartInterval": 3600, "RunAtLoad": True, "StandardOutPath": str(DATA / "dash_launchd.log"),
             "StandardErrorPath": str(DATA / "dash_launchd.log")}
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], capture_output=True, text=True)
    print("launchd job com.whoopdash.fetch installed (hourly + at login)" if r.returncode == 0 else f"launchctl: {r.stderr}")


def status():
    d = read_json(DASH, None)
    if not d:
        print("no dash.json yet; run fetch")
        return
    r = d["regularity"]
    print(f"fetched {d.get('fetched_at')} · {r['hits_10']}/{r['n_10']} nights asleep by target+{d['grace_min']} · "
          f"SRI {r['sri_14']} · HRV {d['hrv']['status']} · RHR {d['rhr']['status']} · scopes {d['scopes']}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "fetch":
        d = fetch_and_render()
        status()
    elif cmd == "fetch-bg":
        fetch_and_render(background=True)
    elif cmd == "render":
        render()
        status()
    elif cmd == "log":
        toggle(argv[2])
        refresh_swiftbar()
    elif cmd == "menubar":
        menubar()
    elif cmd == "schedule":
        schedule()
    elif cmd == "status":
        status()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
