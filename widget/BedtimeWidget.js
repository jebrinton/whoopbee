// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: deep-blue; icon-glyph: bed;

// Bedtime widget for Scriptable (iOS). Companion to whoop_bee.py.
//
// Shows minutes asleep before/after target for recent nights plus the Beeminder bank.
// Everything comes from Beeminder: whoop_bee.py writes "asleep Fri 01:25, +145 min vs target 23:00"
// into each datapoint comment, and this script parses that. It never talks to WHOOP or to the Mac.
//
// First run: open this script inside the Scriptable app once. It asks for your Beeminder username,
// auth token and goal slug and stores them in the iOS Keychain (not in this file).
// To change them later, run the script in the app and choose "Reset login".

const KEY = "whoop_bee.beeminder";
const API = "https://www.beeminder.com/api/v1";
const C = {
  // Dark values are deliberately luminous (coral, not red; pale blue) so they survive Night Shift / blue-light glasses.
  green: Color.dynamic(new Color("#1e8a3e"), new Color("#4cd471")),
  amber: Color.dynamic(new Color("#a86f00"), new Color("#ffe066")),
  red: Color.dynamic(new Color("#c9302c"), new Color("#ff9a8f")),
  blue: Color.dynamic(new Color("#125bca"), new Color("#9cc8ff")),
  gray: Color.dynamic(new Color("#6b6b6b"), new Color("#9a9a9a")),
  text: Color.dynamic(new Color("#111111"), new Color("#f2f2f2")),
  bg: Color.dynamic(new Color("#ffffff"), new Color("#1c1c1e")),
};
const ROAD = { green: C.green, blue: C.blue, orange: C.amber, red: C.red };
const PER_DAY = { y: 365.25, m: 30.4375, w: 7, d: 1, h: 1 / 24 };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

// ---------- data ----------

function mins(n, signed = true) {
  n = Math.round(n);
  if (!signed) return `${n}m`;
  return `${n > 0 ? "▲" : n < 0 ? "▼" : ""}${Math.abs(n)}m`; // ▲ late, ▼ early: shape backs up color
}

function parseNights(datapoints) {
  const re = /asleep \w{3} (\d{1,2}:\d{2}), ([+−-]?\d+) min vs target (\d{1,2}:\d{2})/;
  const nights = [];
  for (const d of datapoints) {
    const m = re.exec(d.comment || "");
    if (!m) continue; // manual or initial datapoints carry no bedtime
    const s = String(d.daystamp);
    const night = new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8) - 1); // datapoint lands the morning after
    const offset = parseInt(m[2].replace("−", "-"), 10), g = /travel grace \+(\d+) min/.exec(d.comment), grace = g ? +g[1] : 0;
    nights.push({ night, asleep: m[1], offset, target: m[3], grace, raw: offset + grace });
  }
  return nights.sort((a, b) => a.night - b.night);
}

// A night scored against a temporary travel target (whoop_bee.py moves it later after flying east) counts as on
// time at best, so a 3-hour grace can't make the average look early.
const scored = n => (n.grace ? Math.max(0, n.offset) : n.offset);

function summarize(goal, datapoints, now = new Date()) {
  const nights = parseNights(datapoints).slice(-7);
  const recent = nights.slice(-3);
  const last = nights[nights.length - 1] || null;
  const dueby = Object.entries(goal.dueby || {}).sort();
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  return {
    nights,
    last,
    lastLabel: last ? (last.night.getTime() === yesterday.getTime() ? "last night" : DAYS[last.night.getDay()]) : null,
    avg: recent.length ? recent.reduce((t, n) => t + scored(n), 0) / recent.length : null,
    avgTravel: recent.some(n => n.grace),
    avgCount: recent.length,
    bank: dueby.length ? dueby[dueby.length - 1][1].delta : null,
    allowance: (goal.rate || 0) / (PER_DAY[goal.runits] || 7), // one day's share of the budget
    road: goal.roadstatuscolor,
    safebuf: goal.safebuf,
    limsum: goal.limsumdays || goal.limsum || "",
  };
}

const tone = (offset, allowance) => (offset <= 0 ? C.green : offset <= allowance ? C.amber : C.red);

async function login() {
  if (Keychain.contains(KEY)) return JSON.parse(Keychain.get(KEY));
  if (config.runsInWidget) return null;
  const a = new Alert();
  a.title = "Beeminder login";
  a.message = "Stored in the iOS Keychain. The auth token is at beeminder.com/api/v1/auth_token.json";
  a.addTextField("username", "");
  a.addSecureTextField("auth token", "");
  a.addTextField("goal slug", "bedtime");
  a.addAction("Save");
  a.addCancelAction("Cancel");
  if ((await a.presentAlert()) === -1) return null;
  const creds = { user: a.textFieldValue(0).trim(), token: a.textFieldValue(1).trim(), goal: a.textFieldValue(2).trim() || "bedtime" };
  if (!creds.user || !creds.token) return null;
  Keychain.set(KEY, JSON.stringify(creds));
  return creds;
}

async function load(creds) {
  const fm = FileManager.local();
  const cache = fm.joinPath(fm.cacheDirectory(), "whoop_bee_widget.json");
  const base = `${API}/users/${encodeURIComponent(creds.user)}/goals/${encodeURIComponent(creds.goal)}`;
  const auth = `auth_token=${encodeURIComponent(creds.token)}`;
  try {
    const g = new Request(`${base}.json?${auth}`);
    const d = new Request(`${base}/datapoints.json?${auth}&count=14&sort=daystamp`);
    g.timeoutInterval = d.timeoutInterval = 15;
    const [goal, datapoints] = await Promise.all([g.loadJSON(), d.loadJSON()]);
    if (!goal || goal.errors || !Array.isArray(datapoints)) throw new Error(JSON.stringify(goal && goal.errors) || "bad response");
    const keep = { dueby: goal.dueby, rate: goal.rate, runits: goal.runits, roadstatuscolor: goal.roadstatuscolor,
                   safebuf: goal.safebuf, limsumdays: goal.limsumdays, limsum: goal.limsum };
    const slim = datapoints.map(p => ({ daystamp: p.daystamp, value: p.value, comment: p.comment }));
    fm.writeString(cache, JSON.stringify({ at: Date.now(), goal: keep, datapoints: slim }));
    return { goal: keep, datapoints: slim, at: Date.now(), fresh: true };
  } catch (e) {
    if (fm.fileExists(cache)) return { ...JSON.parse(fm.readString(cache)), fresh: false };
    throw e;
  }
}

// ---------- drawing ----------

function line(parent, text, font, color) {
  const t = parent.addText(text);
  t.font = font;
  t.textColor = color;
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.6;
  return t;
}

function bars(s, width, height) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const n = s.nights.length, gap = 6, labelH = 14;
  const w = (width - gap * (n - 1)) / Math.max(n, 1);
  const hi = Math.max(s.allowance, ...s.nights.map(x => x.offset), 1);
  const lo = Math.min(0, ...s.nights.map(x => x.offset));
  const plotH = height - labelH;
  const y = v => ((hi - v) / (hi - lo)) * plotH; // the target line sits at y(0)
  s.nights.forEach((x, i) => {
    const top = Math.min(y(x.offset), y(0)), h = Math.max(Math.abs(y(x.offset) - y(0)), 2);
    ctx.setFillColor(tone(x.offset, s.allowance));
    ctx.fillRect(new Rect(i * (w + gap), top, w, h));
    ctx.setFont(Font.systemFont(10));
    ctx.setTextColor(C.gray);
    ctx.setTextAlignedCenter();
    ctx.drawTextInRect(DAYS[x.night.getDay()][0], new Rect(i * (w + gap), plotH + 1, w, labelH));
  });
  ctx.setFillColor(C.gray);
  ctx.fillRect(new Rect(0, y(0), width, 1));
  return ctx.getImage();
}

function summaryColumn(parent, s, compact) {
  const head = parent.addStack();
  head.centerAlignContent();
  line(head, "BEDTIME", Font.semiboldSystemFont(10), C.gray);
  head.addSpacer(4);
  line(head, "●", Font.systemFont(9), ROAD[s.road] || C.gray);
  parent.addSpacer(compact ? 2 : 4);
  if (s.last) {
    line(parent, mins(s.last.offset), Font.boldRoundedSystemFont(compact ? 26 : 32), tone(s.last.offset, s.allowance));
    line(parent, `${s.lastLabel} · asleep ${s.last.asleep}`, Font.systemFont(11), C.gray);
    if (s.last.grace) line(parent, `✈ vs ${s.last.target} temp · ${mins(s.last.raw)} vs usual`, Font.systemFont(11), C.blue);
    parent.addSpacer(compact ? 2 : 6);
    line(parent, `${s.avgCount}d avg ${mins(s.avg)}${s.avgTravel ? " ✈" : ""}`, Font.mediumSystemFont(13), tone(s.avg, s.allowance));
  } else {
    line(parent, "no nights yet", Font.mediumSystemFont(15), C.gray);
    parent.addSpacer(6);
  }
  if (s.bank !== null) {
    const safe = s.safebuf === undefined || s.safebuf === null ? "" : ` · ${s.safebuf}d safe`;
    line(parent, `bank ${mins(s.bank, false)}${safe}`, Font.mediumSystemFont(13), ROAD[s.road] || C.gray);
  }
}

function build(s, family, stale) {
  const w = new ListWidget();
  if (family === "accessoryInline") {
    line(w, s.last ? `${mins(s.last.offset)}${s.last.grace ? "✈" : ""} · bank ${mins(s.bank || 0, false)}` : `bank ${mins(s.bank || 0, false)}`, Font.systemFont(12), C.text);
    return w;
  }
  if (family === "accessoryCircular") {
    line(w, s.last ? mins(s.last.offset) : "–", Font.boldRoundedSystemFont(16), C.text).centerAlignText();
    line(w, "bed", Font.systemFont(9), C.text).centerAlignText();
    return w;
  }
  if (family === "accessoryRectangular") {
    line(w, s.last ? `${s.lastLabel} ${mins(s.last.offset)}${s.last.grace ? " ✈" : ""}` : "no nights yet", Font.boldSystemFont(14), C.text);
    if (s.avg !== null) line(w, `${s.avgCount}d avg ${mins(s.avg)}${s.avgTravel ? " ✈" : ""}`, Font.systemFont(12), C.text);
    line(w, `bank ${mins(s.bank || 0, false)}`, Font.systemFont(12), C.text);
    return w;
  }
  w.backgroundColor = C.bg;
  w.setPadding(14, 14, 14, 14);
  if (family === "medium" || family === "large") {
    const row = w.addStack();
    const left = row.addStack();
    left.layoutVertically();
    summaryColumn(left, s, false);
    row.addSpacer();
    if (s.nights.length) {
      const right = row.addStack();
      right.layoutVertically();
      right.addSpacer();
      right.addImage(bars(s, 150, 96));
      right.addSpacer();
    }
  } else {
    summaryColumn(w, s, true);
  }
  if (stale) {
    w.addSpacer(2);
    line(w, "offline · cached", Font.systemFont(9), C.amber);
  }
  return w;
}

function message(text) {
  const w = new ListWidget();
  w.backgroundColor = C.bg;
  line(w, "BEDTIME", Font.semiboldSystemFont(10), C.gray);
  w.addSpacer(4);
  const t = line(w, text, Font.systemFont(12), C.text);
  t.lineLimit = 4;
  return w;
}

// ---------- main ----------

async function main() {
  if (!config.runsInWidget && Keychain.contains(KEY)) {
    const a = new Alert();
    a.title = "Bedtime widget";
    a.addAction("Preview");
    a.addDestructiveAction("Reset login");
    a.addCancelAction("Cancel");
    const pick = await a.presentAlert();
    if (pick === -1) return;
    if (pick === 1) Keychain.remove(KEY);
  }
  const creds = await login();
  let widget;
  if (!creds) {
    widget = message("Open Scriptable and run this script once to log in to Beeminder.");
  } else {
    try {
      const data = await load(creds);
      widget = build(summarize(data.goal, data.datapoints), config.widgetFamily || "medium", !data.fresh);
      widget.url = `https://www.beeminder.com/${creds.user}/${creds.goal}`;
    } catch (e) {
      widget = message(`Beeminder unreachable: ${String(e.message || e).slice(0, 80)}`);
    }
  }
  widget.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);
  if (config.runsInWidget) Script.setWidget(widget);
  else await widget.presentMedium();
  Script.complete();
}

if (typeof module !== "undefined" && module.exports) module.exports = { parseNights, summarize, mins }; // for tests under node
await main();
