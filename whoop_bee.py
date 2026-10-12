#!/usr/bin/env python3
"""whoop_bee.py: WHOOP sleep start -> Beeminder do-less goal ("minutes asleep past target").

  python3 whoop_bee.py setup   one-time: asks for 4 keys, creates the goal, authorizes WHOOP, schedules itself
  python3 whoop_bee.py         one run now (safe to re-run, backfills 10 days)
  python3 whoop_bee.py auto    what launchd calls: every 15 min 04:00-14:45 until last night lands, then every 3h; 15:00, then hourly to 23:00
  python3 whoop_bee.py auth    redo WHOOP authorization only
  python3 whoop_bee.py hc URL  ping a healthchecks.io check after every run ("hc off" to stop)

A failed run raises a macOS notification (network blips stay quiet unless 26h pass without a good run).
Every run also writes nights.json (signed minutes vs target) and status.json for the menu-bar plugin.
After flying east the target moves later by the body clock's remaining lag, an hour less each night (travel_grace).

Non-interactive setup: WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, BEE_USER, BEE_TOKEN, and optionally
BEE_GOAL, BEE_TARGET, BEE_WEEKLY_BUDGET in the environment replace the prompts.

Before setup: at developer-dashboard.whoop.com create an app with scopes read:sleep + offline
and redirect URL http://localhost:8765/callback. Code runs from this folder; config, tokens and logs live in data/ (gitignored). Stdlib only.
"""
import fcntl, getpass, json, os, plistlib, re, secrets, shutil, subprocess, sys
import urllib.error, urllib.parse, urllib.request, webbrowser
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import sleep

ROOT = Path(__file__).resolve().parent  # the repo; private state goes in data/
DATA = ROOT / "data"
CFG, SCRIPT, LOG = DATA / "config.json", ROOT / "whoop_bee.py", DATA / "log.txt"
NIGHTS, STATUS, LOCK = DATA / "nights.json", DATA / "status.json", DATA / "lock"
BACKFILL_DAYS = 10
PAGE = 25  # WHOOP's maximum page size
QUIET_HOURS = 26  # network errors only notify once this long has passed without a good run
PLIST = Path.home() / "Library/LaunchAgents/com.whoopbee.daily.plist"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SLEEP_URL = "https://api.prod.whoop.com/developer/v2/activity/sleep"
BEE = "https://www.beeminder.com/api/v1"
REDIRECT = "http://localhost:8765/callback"
NOON_CUTOFF = 15  # a sleep starting before 15:00 local belongs to the previous night
# Travel grace. The body clock re-entrains ~1 h/day after flying east and ~1.5 h/day after flying west (CDC Yellow
# Book 2026, "Jet Lag Disorder"). After an eastward jump the target moves later by however far local time still runs
# ahead of the body clock. Westward gets none: a bed-by deadline never binds while you're sleepy early.
EAST_RATE_H, WEST_RATE_H = 1.0, 1.5
# Poll every 15 minutes from 04:00 so last night lands soon after waking (~3 WHOOP calls a run against a 10,000/day
# limit, and launchd doesn't fire while the Mac sleeps: it runs one missed slot on wake). Once last night is in, a poll
# only runs if the last good run is REVISIT_HOURS old, to catch WHOOP revising a sleep; 15:00 always runs.
# Evening slots catch a phone that syncs late (Oct 7 2026: last night reached WHOOP at 18:58) before Beeminder's deadline.
MORNING_SLOTS = [{"Hour": h, "Minute": m} for h in range(4, NOON_CUTOFF) for m in (0, 15, 30, 45)]
FULL_SLOTS = [{"Hour": NOON_CUTOFF, "Minute": 0}]
EVENING_SLOTS = [{"Hour": h, "Minute": 0} for h in range(NOON_CUTOFF + 1, 24)]
REVISIT_HOURS = 3


def load():
    return json.loads(CFG.read_text())


def write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)  # atomic: a crash can't leave a half-written refresh token


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(cfg):
    write_json(CFG, cfg)


def http(url, form=None, headers=None, fatal=True):
    """Return (status, parsed JSON). Non-fatal mode returns (status, error text) on HTTP errors."""
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "whoop-bee/1.1", **(headers or {})})
    for wait in (15, 45, None):  # launchd fires on wake, often before Wi-Fi is back
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            if fatal:
                sys.exit(f"HTTP {e.code} from {url.split('?')[0]}: {body}")
            return e.code, body
        except urllib.error.URLError as e:  # never connected, so retrying can't double-spend a refresh token
            if wait is None:  # still offline; the next run backfills
                sys.exit(f"network error reaching {url.split('?')[0]}: {e.reason}")
            sleep(wait)


# ---------- WHOOP ----------

def auth(cfg):
    state = secrets.token_hex(4)  # WHOOP wants an 8-character state
    q = urllib.parse.urlencode({
        "client_id": cfg["whoop_client_id"], "redirect_uri": REDIRECT,
        "response_type": "code", "scope": "read:sleep read:recovery read:workout offline", "state": state})
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200); self.end_headers()
            self.wfile.write(b"WHOOP authorized. Close this tab.")
        def log_message(self, *a): pass

    try:  # bind before opening the browser, so a busy port can't strand you on the consent page
        srv = HTTPServer(("localhost", 8765), H)
    except OSError as e:
        sys.exit(f"can't listen on localhost:8765 ({e}). Find the culprit: lsof -nP -iTCP:8765 -sTCP:LISTEN")
    srv.timeout = 300
    url = f"{AUTH_URL}?{q}"
    print(f"Opening WHOOP in your browser. If nothing opens, paste this:\n{url}\n", flush=True)
    webbrowser.open(url)
    srv.handle_request()
    srv.server_close()
    if not got:
        sys.exit("WHOOP authorization timed out: nobody clicked Authorize within 5 minutes.")
    if got.get("state", [""])[0] != state or "code" not in got:
        sys.exit(f"WHOOP authorization failed: {got}")
    _, tok = http(TOKEN_URL, {
        "grant_type": "authorization_code", "code": got["code"][0],
        "client_id": cfg["whoop_client_id"], "client_secret": cfg["whoop_client_secret"],
        "redirect_uri": REDIRECT})
    if "refresh_token" not in tok:
        sys.exit(f"WHOOP returned no refresh token (keys: {list(tok)}); the app lacks the 'offline' scope.")
    cfg["refresh_token"] = tok["refresh_token"]
    save(cfg)
    print("WHOOP authorized.")


def access_token(cfg):
    code, tok = http(TOKEN_URL, {
        "grant_type": "refresh_token", "refresh_token": cfg["refresh_token"],
        "client_id": cfg["whoop_client_id"], "client_secret": cfg["whoop_client_secret"],
        "scope": "offline"}, fatal=False)
    if code != 200:
        sys.exit(f"WHOOP refused the refresh token ({code}: {tok}). Run: python3 {SCRIPT} auth")
    cfg["refresh_token"] = tok["refresh_token"]  # WHOOP rotates it on every refresh
    save(cfg)
    return tok["access_token"]


def local_time(iso_utc, offset):
    """WHOOP gives UTC plus an offset like '-07:00' or 'Z'; return naive local time."""
    t = datetime.fromisoformat(re.sub(r"\.\d+", "", iso_utc).replace("Z", "+00:00"))
    if offset in ("Z", "", None):
        delta = timedelta(0)
    else:
        sign = -1 if offset[0] == "-" else 1
        hh, mm = offset[1:].split(":")
        delta = sign * timedelta(hours=int(hh), minutes=int(mm))
    return (t.astimezone(timezone.utc) + delta).replace(tzinfo=None)


def tz_hours(offset):
    """'-04:00' -> -4.0; None when WHOOP sent no offset (that night gets no grace and doesn't move the body clock)."""
    if offset in ("", None):
        return None
    if offset == "Z":
        return 0.0
    sign = -1 if offset[0] == "-" else 1
    hh, mm = offset[1:].split(":")
    return sign * (int(hh) + int(mm) / 60)


def travel_grace(tz_by_night):
    """{night: local UTC offset in hours} -> {night: minutes the target moves later}. The body clock starts on the
    earliest known night's zone, then each day moves toward the previous night's zone at EAST/WEST_RATE_H per day.
    Fly PT -> ET on Monday: Mon +180, Tue +120, Wed +60, Thu 0."""
    grace, body, prev = {}, None, None
    for night in sorted(tz_by_night):
        local = tz_by_night[night]
        if body is None:
            body = local
        else:
            days = (night - prev).days
            pull = tz_by_night[prev] - body
            body += max(-WEST_RATE_H * days, min(EAST_RATE_H * days, pull))
        grace[night] = round(max(0.0, local - body) * 60)
        prev = night
    return grace


def shift(target, minutes):
    """'23:00' moved 180 minutes later -> '02:00'."""
    h, m = map(int, target.split(":"))
    return (datetime.combine(date(2000, 1, 1), time(h, m)) + timedelta(minutes=minutes)).strftime("%H:%M")


def night_and_offset(start_local, target):
    """Return (night date, signed minutes vs target; negative = early). 01:30 on Saturday is Friday's night."""
    night = start_local.date() if start_local.hour >= NOON_CUTOFF else start_local.date() - timedelta(days=1)
    h, m = map(int, target.split(":"))
    tgt = datetime.combine(night, time(h, m))
    if h < NOON_CUTOFF:  # a target like "00:30" falls on the next calendar day
        tgt += timedelta(days=1)
    return night, round((start_local - tgt).total_seconds() / 60)


def remember(nights, target, grace):
    """Merge this run's nights into nights.json (the menu-bar plugin reads it; Beeminder only gets max(0, offset)).
    offset_min is against the night's effective target (target + travel grace); tz is kept so later runs know where
    you slept beyond the backfill window."""
    kept = {n["night"]: n for n in read_json(NIGHTS, []) if isinstance(n, dict) and "night" in n}
    for night, (start, offset, tz) in nights.items():
        key, g = night.isoformat(), grace.get(night, 0)
        row = {"night": key, "asleep": f"{start:%Y-%m-%dT%H:%M}", "offset_min": offset - g, "target": shift(target, g)}
        tz = tz if tz is not None else kept.get(key, {}).get("tz")
        if tz is not None:
            row["tz"] = tz
        if g:
            row.update(raw_offset_min=offset, travel_grace_min=g)
        kept[key] = row
    write_json(NIGHTS, sorted(kept.values(), key=lambda n: n["night"])[-90:])


# ---------- Beeminder ----------

def ensure_goal(cfg):
    base = f"{BEE}/users/{cfg['bee_user']}/goals"
    tok = urllib.parse.urlencode({"auth_token": cfg["bee_token"]})
    code, _ = http(f"{base}/{cfg['bee_goal']}.json?{tok}", fatal=False)
    if code == 200:
        return print(f"Beeminder goal '{cfg['bee_goal']}' already exists; leaving it alone.")
    if code == 401:
        sys.exit("Beeminder rejected that username/token pair.")
    code, g = http(f"{base}.json", {
        "auth_token": cfg["bee_token"], "slug": cfg["bee_goal"], "title": "Asleep on time",
        "goal_type": "drinker",  # API name for Do Less
        "gunits": "minutes late", "runits": "w", "rate": cfg["weekly_budget"],
        "goaldate": int((datetime.now() + timedelta(days=3650)).timestamp()),
        "goalval": "null", "initval": 0}, fatal=False)
    if code == 200:
        print(f"Created beeminder.com/{cfg['bee_user']}/{cfg['bee_goal']}: "
              f"rate {g.get('rate')} per '{g.get('runits')}' (expect {cfg['weekly_budget']} per 'w'; fix in goal settings if not).")
    else:
        print(f"Couldn't create the goal via API ({code}: {g}).\nCreate it by hand: beeminder.com/new -> Do Less, "
              f"slug '{cfg['bee_goal']}', {cfg['weekly_budget']} per week. Setup continues.")


def run(cfg):
    tok = access_token(cfg)
    since_utc = (datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    records, params = [], {"limit": PAGE, "start": since_utc}
    for _ in range(20):  # naps can push 10 days past one page
        _, data = http(f"{SLEEP_URL}?{urllib.parse.urlencode(params)}", headers={"Authorization": f"Bearer {tok}"})
        if "records" not in data:
            sys.exit(f"unexpected WHOOP response, keys: {list(data)}")
        records += data["records"]
        if not data.get("next_token"):
            break
        params["nextToken"] = data["next_token"]

    nights = {}  # keep the earliest non-nap sleep per night, so a 4am re-sleep can't count as "late"
    for s in records:
        if s.get("nap") or not s.get("end"):
            continue
        start = local_time(s["start"], s.get("timezone_offset"))
        night, offset = night_and_offset(start, cfg["target"])
        if night not in nights or start < nights[night][0]:
            nights[night] = (start, offset, tz_hours(s.get("timezone_offset")))
    if not nights:
        print(f"WHOOP returned no completed sleeps in the last {BACKFILL_DAYS} days.")
    tz_by_night = {date.fromisoformat(n["night"]): n["tz"] for n in read_json(NIGHTS, [])
                   if isinstance(n, dict) and n.get("tz") is not None}  # where you slept before the backfill window
    tz_by_night.update({night: v[2] for night, v in nights.items() if v[2] is not None})
    grace = travel_grace(tz_by_night)
    remember(nights, cfg["target"], grace)

    first_day = date.fromisoformat(cfg["since"])
    bee = f"{BEE}/users/{cfg['bee_user']}/goals/{cfg['bee_goal']}/datapoints.json"
    errors = []
    for night, (start, raw, _) in sorted(nights.items()):
        day = night + timedelta(days=1)  # the datapoint lands on the morning after
        g = grace.get(night, 0)
        offset, target = raw - g, shift(cfg["target"], g)
        note = f"; travel grace +{g} min" if g else ""
        late = max(0, offset)
        line = f"{night:%a %b %d}: asleep {start:%H:%M}, {offset:+d} min vs {target}{note}"
        if day < first_day:
            print(f"{line}  (before start date, not posted)")
            continue
        code, resp = http(bee, {
            "auth_token": cfg["bee_token"], "value": late, "daystamp": day.strftime("%Y%m%d"),
            "comment": f"asleep {start:%a %H:%M}, {offset:+d} min vs target {target} (WHOOP){note}",  # the phone widget parses this
            "requestid": f"whoop{night:%Y%m%d}"}, fatal=False)  # idempotency key: re-runs update, never duplicate
        ok = code == 200 or "uplicate" in str(resp)
        print(f"{line}  -> {'posted' if code == 200 else 'already posted' if ok else f'BEEMINDER ERROR {code}: {resp}'}")
        if not ok:
            errors.append(f"{night:%b %d}: {code} {str(resp)[:120]}")
    if errors:
        sys.exit("Beeminder rejected datapoints: " + "; ".join(errors))


# ---------- health ----------

def notify(msg):
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", "display notification "
                        f"{json.dumps(msg[:240], ensure_ascii=False)} with title \"whoop_bee\" subtitle \"bedtime sync failed\""],
                       capture_output=True)


def ping(url, body=None):
    """healthchecks.io: GET url on success, POST the error to url/fail. Never fatal."""
    try:
        req = urllib.request.Request(url, data=body.encode() if body else None, headers={"User-Agent": "whoop-bee/1.1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def daily():
    with open(LOCK, "w") as lock:
        try:  # launchd and the menu bar's "Sync now" must never refresh the (rotating) WHOOP token at the same time
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return print("another run is in progress; skipping.")
        locked_run()


def locked_run():
    cfg, err = load(), None
    try:
        run(cfg)
    except SystemExit as e:
        err = None if e.code in (0, None) else str(e.code)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    now, st = datetime.now(), read_json(STATUS, {})
    last_ok = st.get("last_ok")
    st["last_run"] = now.isoformat(timespec="seconds")
    st["last_error"] = err and err[:400]
    if not err:
        st["last_ok"] = st["last_run"]
    write_json(STATUS, st)
    if cfg.get("healthcheck_url"):
        ping(cfg["healthcheck_url"] + ("/fail" if err else ""), err)
    if err:
        blip = err.startswith("network error") and last_ok and \
            now - datetime.fromisoformat(last_ok) < timedelta(hours=QUIET_HOURS)
        if not blip:
            notify(err)
        sys.exit(err)


def healthcheck(arg):
    cfg = load()
    if arg == "off":
        cfg.pop("healthcheck_url", None)
        save(cfg)
        return print("healthchecks.io pings turned off.")
    if not re.fullmatch(r"https://\S+", arg):
        sys.exit("expected a ping URL like https://hc-ping.com/<uuid>")
    cfg["healthcheck_url"] = arg.rstrip("/")
    save(cfg)
    print("Saved. Test ping " + ("succeeded." if ping(cfg["healthcheck_url"]) else "FAILED; check the URL."))


# ---------- setup ----------

def ask(label, default=None, secret=False, env=None):
    if env and os.environ.get(env, "").strip():
        return os.environ[env].strip()
    if not sys.stdin.isatty():  # non-interactive: env var, else saved/default value, else give up
        return default or sys.exit(f"{label} is required (set {env})")
    tail = " [keep existing]" if secret and default else f" [{default}]" if default else ""
    v = (getpass.getpass if secret else input)(f"{label}{tail}{' (hidden as you paste)' if secret else ''}: ").strip()
    return v or default or sys.exit(f"{label} is required")


def schedule():
    if sys.platform != "darwin":
        return print(f"Add to crontab:  0 11,15 * * * {sys.executable} {SCRIPT} >> {LOG} 2>&1")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps({
        "Label": "com.whoopbee.daily",
        "ProgramArguments": [sys.executable, str(SCRIPT), "auto"],
        "StartCalendarInterval": MORNING_SLOTS + FULL_SLOTS + EVENING_SLOTS,
        "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG)}))
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    r = subprocess.run(["launchctl", "load", "-w", str(PLIST)], capture_output=True, text=True)
    print("Scheduled every 15 min 04:00-14:45 until last night lands, then every 3h, plus 15:00 and hourly to 23:00 (launchd runs a missed job on wake). Log: " + str(LOG)
          if r.returncode == 0 else f"launchctl failed: {r.stderr.strip()}")


def setup():
    DATA.mkdir(mode=0o700, exist_ok=True)
    old = load() if CFG.exists() else {}
    cfg = dict(old)
    cfg["whoop_client_id"] = ask("WHOOP client ID", old.get("whoop_client_id"), env="WHOOP_CLIENT_ID")
    cfg["whoop_client_secret"] = ask("WHOOP client secret", old.get("whoop_client_secret"), secret=True, env="WHOOP_CLIENT_SECRET")
    cfg["bee_user"] = ask("Beeminder username", old.get("bee_user"), env="BEE_USER")
    cfg["bee_token"] = ask("Beeminder auth token", old.get("bee_token"), secret=True, env="BEE_TOKEN")
    cfg["bee_goal"] = ask("Goal slug", old.get("bee_goal", "bedtime"), env="BEE_GOAL")
    cfg["target"] = ask("Asleep-by target, 24h HH:MM", old.get("target", "23:00"), env="BEE_TARGET")
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", cfg["target"]):
        sys.exit("target must look like 23:00")
    cfg["weekly_budget"] = int(ask("Weekly budget, minutes late", str(old.get("weekly_budget", 120)), env="BEE_WEEKLY_BUDGET"))
    cfg.setdefault("since", (date.today() + timedelta(days=1)).isoformat())  # tonight is the first night that counts
    save(cfg)
    ensure_goal(cfg)
    if not cfg.get("refresh_token") or (
            sys.stdin.isatty() and input("Re-authorize WHOOP? [y/N]: ").lower().startswith("y")):
        auth(cfg)
    print("\nRecent nights (dry run for anything before the start date):")
    run(cfg)
    schedule()
    print(f"\nDone. Tonight is the first night that counts. Raise the pledge at beeminder.com/{cfg['bee_user']}/{cfg['bee_goal']}.")


if __name__ == "__main__":
    cmd = sys.argv[1:]
    if cmd == ["setup"]:
        setup()
    elif cmd == ["auth"]:
        auth(load())
    elif len(cmd) == 2 and cmd[0] == "hc":
        healthcheck(cmd[1])
    elif cmd == ["auto"] and datetime.now().hour < NOON_CUTOFF and \
            (date.today() - timedelta(days=1)).isoformat() in {n.get("night") for n in read_json(NIGHTS, [])} and \
            datetime.now() - datetime.fromisoformat(read_json(STATUS, {}).get("last_ok") or "2000-01-01") < timedelta(hours=REVISIT_HOURS):
        pass  # last night is in and was fetched recently: skip without touching the WHOOP token
    elif not cmd or cmd == ["auto"]:
        print(f"--- {datetime.now():%Y-%m-%d %H:%M}", flush=True)
        try:
            daily()
        finally:  # show the new night (or the error) in the menu bar now rather than at its next 10-minute refresh
            if sys.platform == "darwin":
                subprocess.run(["open", "-g", "swiftbar://refreshallplugins"], capture_output=True)
    else:
        sys.exit(__doc__)
