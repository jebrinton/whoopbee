#!/usr/bin/env python3
# <xbar.title>Bedtime</xbar.title>
# <xbar.desc>Minutes asleep before/after target (WHOOP) and the Beeminder bank. Companion to whoop_bee.py.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
"""SwiftBar/xbar plugin. Lines above the first '---' cycle in the menu bar: 3-night average, last night, tonight's allowance — padded to one fixed width.

Reads data/{config,nights,status}.json (written by whoop_bee.py) and asks Beeminder for the bank.
It never touches WHOOP: the refresh token rotates, so only whoop_bee.py may use it. Python 3.9+ stdlib only.
"""
import importlib.util, json, math, plistlib, subprocess, sys, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the repo; private state goes in data/
DATA = ROOT / "data"
SCRIPT, LOG, CACHE = ROOT / "whoop_bee.py", DATA / "log.txt", DATA / "bee_cache.json"
PLIST = Path.home() / "Library/LaunchAgents/com.whoopbee.daily.plist"
NOON_CUTOFF = 15  # same rule as whoop_bee.py: before 15:00 a sleep belongs to the previous night
STALE_HOURS = 26
WAKE_HOUR = 4  # from here until the morning sync, tngt is hidden
# light,dark pairs. The dark values are deliberately luminous (coral, not red; pale blue): f.lux and blue-light
# glasses dim saturated red and blue until they vanish on a dark menu bar. Arrows back the colors up with shape.
GREEN, AMBER, RED, BLUE, GRAY = "#1e8a3e,#4cd471", "#a86f00,#ffe066", "#c9302c,#ff9a8f", "#125bca,#9cc8ff", "#666666,#c2c2c2"
BEE_COLORS = {"green": GREEN, "blue": BLUE, "orange": AMBER, "red": RED}
ICON = "sfimage=bed.double.fill"


# Severity spectrum for the menu bar: minutes late -> OKLCH (lightness, chroma, hue), green through ochre and orange
# to crimson. The menu bar sits on a pale cream wallpaper that f.lux turns tan, while the system is in dark mode, so
# SwiftBar would pick the pale dark-mode half of a light,dark pair; the menu bar gets one color that works on cream.
# Lightness stays <= 0.56 because the menu bar renders these ~0.05 lighter than the hex; bold Menlo helps too.
HEAT = [(0, .50, .14, 155), (17, .53, .15, 125), (35, .56, .15, 85), (55, .55, .17, 62),
        (75, .52, .19, 45), (100, .48, .21, 30), (130, .42, .19, 20), (160, .36, .15, 10)]


def oklch_hex(L, C, h):
    """OKLCH -> sRGB hex, pulling chroma in until the color fits the sRGB gamut."""
    a, b = math.cos(math.radians(h)), math.sin(math.radians(h))
    while True:
        l_, m_, s_ = (L + .3963377774 * C * a + .2158037573 * C * b) ** 3, (L - .1055613458 * C * a - .0638541728 * C * b) ** 3, (L - .0894841775 * C * a - 1.2914855480 * C * b) ** 3
        rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + .2309699292 * s_, -1.2684380046 * l_ + 2.6097574011 * m_ - .3413193965 * s_, -.0041960863 * l_ - .7034186147 * m_ + 1.7076147010 * s_)
        if C <= 0 or all(-1e-4 <= v <= 1 + 1e-4 for v in rgb):
            break
        C -= .002
    enc = lambda v: 12.92 * v if v <= .0031308 else 1.055 * v ** (1 / 2.4) - .055
    return "#" + "".join("%02x" % round(255 * min(max(enc(max(v, 0)), 0), 1)) for v in rgb)


def heat(m, lift=0.0):
    """Color for m minutes late, interpolated along HEAT. lift raises lightness for dark backgrounds."""
    if m <= HEAT[0][0]:
        p = HEAT[0][1:]
    elif m >= HEAT[-1][0]:
        p = HEAT[-1][1:]
    else:
        (m0, *p0), (m1, *p1) = next((s, t) for s, t in zip(HEAT, HEAT[1:]) if m <= t[0])
        u = (m - m0) / (m1 - m0)
        p = [x + (y - x) * u for x, y in zip(p0, p1)]
    L, C, h = p
    return oklch_hex(min(L + lift, .9), C, h)


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def python():
    try:  # the interpreter setup chose for the scheduled job
        return plistlib.loads(PLIST.read_bytes())["ProgramArguments"][0]
    except Exception:
        return sys.executable


def sync():
    with open(LOG, "a") as log:
        subprocess.run([python(), str(SCRIPT)], stdout=log, stderr=log)


def beeminder(cfg):
    """Return (goal summary, fetched-at datetime or None if nothing is available)."""
    keep = ("curval", "dueby", "limsumdays", "safebuf", "losedate", "pledge", "roadstatuscolor", "rate", "runits")
    url = "https://www.beeminder.com/api/v1/users/%s/goals/%s.json?%s" % (
        cfg["bee_user"], cfg["bee_goal"], urllib.parse.urlencode({"auth_token": cfg["bee_token"]}))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "whoop-bee-menubar/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            g = json.loads(r.read())
        cache = {"fetched": datetime.now().isoformat(timespec="seconds"), "goal": {k: g.get(k) for k in keep}}
        CACHE.write_text(json.dumps(cache))
    except Exception:
        cache = read_json(CACHE, None)
        if not cache:
            return None, None
    return cache["goal"], datetime.fromisoformat(cache["fetched"])


def mins(n, signed=True):
    n = int(round(n))
    return ("▲" if n > 0 else "▼" if n < 0 else "") + f"{abs(n)}m" if signed else f"{n}m"  # ▲ late, ▼ early


def travel(n):
    """Minutes of travel grace on this night (whoop_bee.py moves the target later after flying east), else 0."""
    return n.get("travel_grace_min") or 0


def scored(n):
    """Offset for averages: a night scored against a temporary travel target counts as on time at best."""
    return max(0, n["offset_min"]) if travel(n) else n["offset_min"]


def icon(ns):
    """Airplane instead of the bed when any of these nights was scored against a temporary travel target."""
    return "sfimage=airplane" if any(travel(n) for n in ns) else ICON


def grace_for(night):
    """Travel grace whoop_bee.py will give `night` if you sleep in the Mac's current time zone (0 if unknown)."""
    try:
        spec = importlib.util.spec_from_file_location("whoop_bee", SCRIPT)
        wb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wb)
        tz = {date.fromisoformat(n["night"]): n["tz"] for n in read_json(DATA / "nights.json", []) if n.get("tz") is not None}
        tz[night] = datetime.now().astimezone().utcoffset().total_seconds() / 3600
        return wb.travel_grace(tz).get(night, 0)
    except Exception:
        return 0


def ago(t, now):
    day = "today" if t.date() == now.date() else "yesterday" if t.date() == now.date() - timedelta(days=1) else f"{t:%a %b %d}"
    return f"{day} {t:%H:%M}"


NBSP = "\u00a0"  # SwiftBar trims ordinary trailing spaces; non-breaking ones survive
MONO = "font=Menlo-Bold size=13"


def fixed_width(lines):
    """Give every cycling menu-bar line the same character count, in a monospaced font, so the item keeps
    one width while it animates. Label left-aligned, value right-aligned."""
    parts = []
    for line in lines:
        text, _, params = line.partition(" | ")
        label, _, value = text.rpartition(" ")
        parts.append((label, value, params))
    lw = max(len(l) for l, _, _ in parts)
    vw = max(len(v) for _, v, _ in parts)
    return [f"{l.ljust(lw, NBSP)}{NBSP}{v.rjust(vw, NBSP)} | {p} {MONO}" for l, v, p in parts]


DAYS_PER_UNIT = {"h": 1 / 24, "d": 1, "w": 7, "m": 30, "y": 365}


def allowance(goal, due, day):
    """Late minutes the datapoint landing on `day` can carry without derailing, or None if Beeminder didn't say.
    A negative limit means the total already breaks the road, so the goal derails at losedate whatever happens
    tonight. Every derail on this goal so far (Sep 21, 24, 26) reset the road to the day's total plus 7 days of
    rate (120 min) and resumed the rate, so from then on the room is that buffer plus a day's rate per day since."""
    entry = due.get(day.strftime("%Y%m%d"))
    if entry is None:
        return None
    room = entry["delta"]
    if room < 0 and goal.get("losedate") and goal.get("rate"):
        per_day = goal["rate"] / DAYS_PER_UNIT.get(goal.get("runits"), 7)
        room = per_day * (7 + (day - datetime.fromtimestamp(goal["losedate"]).date()).days)
    return math.floor(room)  # floored: the limit is fractional, and rounding up would show a minute that derails


def main():
    cfg = read_json(DATA / "config.json", None)
    if not cfg:
        print(f"bedtime ? | {ICON} color={GRAY}\n---\nNo data/config.json yet. Run: python3 whoop_bee.py setup")
        return
    now = datetime.now()
    today = now.date()
    allow = cfg["weekly_budget"] / 7  # one day's share of the weekly budget
    tone = lambda off: f"{heat(off)},{heat(off, .25)}"  # dropdown rows: the menu itself is dark, so lift the dark half
    nights = [n for n in read_json(DATA / "nights.json", []) if date.fromisoformat(n["night"]) >= today - timedelta(days=8)]
    status = read_json(DATA / "status.json", {})
    goal, fetched = beeminder(cfg)

    head = []
    last_ok = status.get("last_ok") and datetime.fromisoformat(status["last_ok"])
    stale = not last_ok or now - last_ok > timedelta(hours=STALE_HOURS)
    if stale:
        since = f"{int((now - last_ok).total_seconds() // 3600)}h" if last_ok else "ever"
        head.append(f"sync stale {since} | sfimage=exclamationmark.triangle.fill color={RED}")
    recent = nights[-3:]
    if recent:
        avg = sum(scored(n) for n in recent) / len(recent)
        head.append(f"{len(recent)}avg {mins(avg)} | {icon(recent)} color={heat(avg)}")
        last = recent[-1]
        night = date.fromisoformat(last["night"])
        label = "last" if night == today - timedelta(days=1) else f"{night:%a}"
        head.append(f"{label} {mins(last['offset_min'])} | {icon([last])} color={heat(last['offset_min'])}")
    dueby = sorted((goal or {}).get("dueby", {}).items())
    # Tonight's allowance: late minutes Beeminder lets through on the datapoint tonight's sleep lands on (the next day).
    # Floored, because the limit is fractional and rounding up would show a minute that derails.
    yesterday = today - timedelta(days=1)
    unsynced = now.hour < NOON_CUTOFF and yesterday.isoformat() not in {n["night"] for n in nights}
    pending = yesterday if unsynced else today
    # From 04:00 until last night's WHOOP sleep arrives (11:00 sync, 15:00 fallback), last night is over but unscored
    # and tonight's number depends on it, so the screen is left out instead of showing a guess.
    waiting = unsynced and now.hour >= WAKE_HOUR
    room = allowance(goal or {}, dict(dueby), pending + timedelta(days=1))
    tonight_g = grace_for(pending)
    color = BEE_COLORS.get((goal or {}).get("roadstatuscolor"), GRAY)
    if waiting:
        pass
    elif room is not None:
        head.append(f"tngt {mins(room, signed=False)} | {'sfimage=airplane' if tonight_g else ICON} color={heat(2 * (60 - room))}")  # 60m+ of room is green, 0m is red
    elif dueby:
        head.append(f"bank {mins(dueby[-1][1]['delta'], signed=False)} | {ICON} color={color}")
    print("\n".join(fixed_width(head or [f"bedtime – | {ICON} color={GRAY}"])))

    print("---")
    week = nights[-7:]
    print(f"Minutes vs {cfg['target']} target (▼ early, ▲ late){'; ✈ = temporary travel target' if any(travel(n) for n in week) else ''} | size=11 color={GRAY}")
    for n in reversed(week):
        night, asleep = date.fromisoformat(n["night"]), datetime.fromisoformat(n["asleep"])
        note = f"   ✈ vs {n['target']} temp, {mins(n.get('raw_offset_min', n['offset_min'] + travel(n)))} vs {cfg['target']}" if travel(n) else ""
        print(f"{night:%a %b %d}   asleep {asleep:%H:%M}   {mins(n['offset_min']):>6}{note} | font=Menlo size=12 color={tone(n['offset_min'])}")
    if week:
        late = sum(max(0, n["offset_min"]) for n in week)
        print(f"Late minutes, last {len(week)} nights: {late} (budget {cfg['weekly_budget']}/week) | size=11 color={RED if late > cfg['weekly_budget'] else GRAY}")
    else:
        print("No nights recorded yet")

    print("---")
    if goal:
        color = BEE_COLORS.get(goal.get("roadstatuscolor"), GRAY)
        print(f"Beeminder: {goal.get('limsumdays') or ''} | color={color}")
        recorded = {n["night"] for n in nights}
        yesterday = today - timedelta(days=1)
        pending = yesterday if now.hour < NOON_CUTOFF and yesterday.isoformat() not in recorded else today
        first = date.fromisoformat(cfg["since"])  # first datapoint day; its night is the day before
        due = dict(dueby)
        if pending + timedelta(days=1) < first:
            pending = first - timedelta(days=1)
            print(f"Counting starts the night of {pending:%a %b %d}")
        key = (pending + timedelta(days=1)).strftime("%Y%m%d")
        room = allowance(goal, due, pending + timedelta(days=1))
        if room is not None:
            after = " (after the derail resets the road)" if due[key]["delta"] < 0 else ""
            g = grace_for(pending)
            tgt = (datetime.combine(pending, datetime.strptime(cfg["target"], "%H:%M").time()) + timedelta(minutes=g)).strftime("%H:%M")
            print(f"Allowance for the night of {pending:%a %b %d}: {mins(room, signed=False)} late{after}" + (f" past {tgt} (✈ temporary target)" if g else ""))
        if dueby:
            end = datetime.strptime(dueby[-1][0], "%Y%m%d")
            print(f"Bank through {end:%a %b %d}: {mins(dueby[-1][1]['delta'], signed=False)}")
        if goal.get("losedate"):
            locked = dueby and dueby[0][1]["delta"] < 0  # today's total is already over: nothing can change it
            print(f"Derails {datetime.fromtimestamp(goal['losedate']):%a %b %d %H:%M}"
                  f"{' (locked in)' if locked else ' if nothing changes'} | size=11 color={RED if locked else GRAY}")
        print(f"Pledge ${goal.get('pledge') or 0:g} | size=11 color={GRAY}")
        if now - fetched > timedelta(minutes=30):
            print(f"Beeminder unreachable; numbers from {ago(fetched, now)} | size=11 color={AMBER}")
    else:
        print(f"Beeminder unreachable | color={AMBER}")
    print(f"Open beeminder.com/{cfg['bee_user']}/{cfg['bee_goal']} | href=https://www.beeminder.com/{cfg['bee_user']}/{cfg['bee_goal']}")

    print("---")
    if status.get("last_error"):
        print(f"Last run failed {ago(datetime.fromisoformat(status['last_run']), now)} | color={RED}")
        print(f"{status['last_error'][:110].replace('|', '/')} | size=11 color={RED}")
    print(f"Last good sync: {ago(last_ok, now) if last_ok else 'never'} | color={RED if stale else GRAY}")
    print(f"Healthchecks.io pings: {'on' if cfg.get('healthcheck_url') else 'off'} | size=11 color={GRAY}")
    print(f"Sync now | bash={Path(__file__).resolve()} param1=sync terminal=false refresh=true")
    print(f"Open log | bash=/usr/bin/open param1={LOG} terminal=false")


if __name__ == "__main__":
    if sys.argv[1:] == ["sync"]:
        sync()
    else:
        try:
            main()
        except Exception as e:  # a plugin that crashes vanishes from the menu bar; show the problem instead
            print(f"bedtime ! | {ICON} color={RED}\n---\n{type(e).__name__}: {str(e)[:150].replace('|', '/')}")
