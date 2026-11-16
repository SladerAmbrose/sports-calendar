#!/usr/bin/env python3
"""Build a single iCalendar feed (sports.ics) of every team/series in config.json,
using ESPN's public schedule API. Standard library only.

If a source can't be fetched, the last good copy from cache/ is used so one
flaky request never wipes games off the calendar.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
API = "https://site.api.espn.com/apis/site/v2/sports"
CACHE_DIR = os.path.join(ROOT, "cache")
OUT = os.path.join(ROOT, "sports.ics")
EASTERN = ZoneInfo("America/New_York")  # ESPN encodes "time TBD" as midnight Eastern


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sports-calendar/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  retry {url}: {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))


def parse_dt(s):
    # ESPN uses "2026-09-05T00:00Z" (and occasionally with seconds)
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def broadcasts(comp):
    names = []
    for b in comp.get("broadcasts") or []:
        if "media" in b:
            names.append(b["media"].get("shortName"))
        names.extend(b.get("names") or [])
    return ", ".join(dict.fromkeys(n for n in names if n))


def venue(comp, event=None):
    v = comp.get("venue") or {}
    name = v.get("fullName") or ((event or {}).get("circuit") or {}).get("fullName")
    addr = v.get("address") or {}
    place = ", ".join(x for x in (addr.get("city"), addr.get("state")) if x)
    if name and place and place.split(",")[0] not in name:
        return f"{name}, {place}"
    return name or place


def make_event(uid, title, start, hours, time_valid, description, location):
    return {
        "uid": uid,
        "title": title,
        "start": start.isoformat(),
        "hours": hours,
        "time_valid": time_valid,
        "description": description,
        "location": location or "",
    }


# ---------------------------------------------------------------- team sports

def team_events(t):
    base = f"{API}/{t['sport']}/{t['league']}/teams/{t['team_id']}/schedule"
    year = datetime.now(timezone.utc).year
    urls = [base, base + "?seasontype=3"]
    # Next season's schedule appears under the next season year before it's "current".
    for y in (year, year + 1):
        urls += [f"{base}?season={y}", f"{base}?season={y}&seasontype=3"]

    raw, ok = {}, False
    for u in urls:
        try:
            for e in fetch(u).get("events", []):
                raw[e["id"]] = e
            ok = True
        except Exception as e:  # noqa: BLE001
            print(f"  WARN {u}: {e}", file=sys.stderr)
    if not ok:
        raise RuntimeError(f"all requests failed for {t['name']}")

    out = []
    for e in raw.values():
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue

        def nm(c):
            tm = c["team"]
            return tm.get("shortDisplayName") or tm.get("displayName")

        sep = "vs" if comp.get("neutralSite") else "@"
        title = f"{t['emoji']} {nm(away)} {sep} {nm(home)}"

        status = (comp.get("status") or {}).get("type") or {}
        lines = [t["name"]]
        stype = (e.get("seasonType") or {}).get("name")
        week = (e.get("week") or {}).get("text")
        notes = [n.get("headline") for n in comp.get("notes") or [] if n.get("headline")]
        lines.append(" · ".join(x for x in [stype, week, *notes] if x))

        if status.get("completed"):
            def sc(c):
                s = c.get("score")
                return s.get("displayValue") if isinstance(s, dict) else s
            a, h = sc(away), sc(home)
            if a is not None and h is not None:
                title += f" ({a}–{h})"
            lines.append(f"Final: {nm(away)} {a}, {nm(home)} {h}")
        elif status.get("state") == "post" or "postpon" in status.get("name", "").lower() \
                or "cancel" in status.get("name", "").lower():
            title += f" [{status.get('shortDetail') or status.get('description')}]"
        tv = broadcasts(comp)
        if tv:
            lines.append(f"📺 {tv}")
        link = next((l["href"] for l in e.get("links") or [] if "gamecast" in (l.get("rel") or []) or "summary" in (l.get("rel") or [])), None)
        if link:
            lines.append(link)

        time_valid = e.get("timeValid", True) and comp.get("timeValid", True)
        if not time_valid:
            title += " (time TBD)"
        out.append(make_event(
            f"espn-{t['league']}-{e['id']}@sports-calendar",
            title, parse_dt(e["date"]), t["hours"], time_valid,
            "\n".join(l for l in lines if l), venue(comp)))
    return out


# --------------------------------------------------------------------- racing

def racing_events(r):
    year = datetime.now(timezone.utc).year
    raw, ok = {}, False
    for y in (year, year + 1):
        u = f"{API}/racing/{r['league']}/scoreboard?dates={y}"
        try:
            for e in fetch(u).get("events", []):
                raw[e["id"]] = e
            ok = True
        except Exception as e:  # noqa: BLE001
            print(f"  WARN {u}: {e}", file=sys.stderr)
    if not ok:
        raise RuntimeError(f"all requests failed for {r['name']}")

    out = []
    for e in raw.values():
        for comp in e.get("competitions", []):
            abbr = (comp.get("type") or {}).get("abbreviation") or "*"
            sess = r["sessions"].get(abbr) or (r["sessions"].get("*") if len(e["competitions"]) == 1 else None)
            if not sess:
                continue
            label, hours = sess
            name = e.get("shortName") or e["name"] if r["league"] == "f1" else e["name"]
            title = f"{r['emoji']} {name}" + (f" – {label}" if label and label != "Grand Prix" else "")
            if r["league"] == "f1" and label == "Grand Prix" and "Grand Prix" not in title:
                title += " – Race"
            lines = [r["name"], e["name"]]
            status = (comp.get("status") or {}).get("type") or {}
            if status.get("completed"):
                winner = next((c for c in comp.get("competitors", []) if c.get("winner") or c.get("order") == 1), None)
                if winner:
                    who = (winner.get("athlete") or {}).get("displayName") or (winner.get("team") or {}).get("displayName")
                    if who:
                        lines.append(f"🏆 {who}")
            tv = broadcasts(comp)
            if tv:
                lines.append(f"📺 {tv}")
            out.append(make_event(
                f"espn-{r['league']}-{e['id']}-{comp.get('id', abbr)}@sports-calendar",
                title, parse_dt(comp.get("date") or e["date"]), hours,
                comp.get("timeValid", True), "\n".join(lines), venue(comp, e)))
    return out


# ------------------------------------------------------------------- ICS output

def esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        cb = ch.encode("utf-8")
        if len(cur) + len(cb) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += cb
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def to_ics(cfg, events, stamp):
    fmt = "%Y%m%dT%H%M%SZ"
    L = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//sports-calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(cfg['calendar_name'])}",
        f"X-WR-TIMEZONE:{cfg['timezone']}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for ev in sorted(events, key=lambda x: (x["start"], x["uid"])):
        start = datetime.fromisoformat(ev["start"]).astimezone(timezone.utc)
        L += ["BEGIN:VEVENT", f"UID:{ev['uid']}", f"DTSTAMP:{stamp}"]
        if ev["time_valid"]:
            end = start + timedelta(hours=ev["hours"])
            L += [f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}"]
        else:
            day = start.astimezone(EASTERN).date()
            L += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                  "TRANSP:TRANSPARENT"]
        L += [f"SUMMARY:{esc(ev['title'])}", f"DESCRIPTION:{esc(ev['description'])}"]
        if ev["location"]:
            L.append(f"LOCATION:{esc(ev['location'])}")
        L.append("END:VEVENT")
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in L) + "\r\n"


def main():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    os.makedirs(CACHE_DIR, exist_ok=True)

    sources = [(t["name"], team_events, t) for t in cfg["teams"]] + \
              [(r["name"], racing_events, r) for r in cfg["racing"]]
    events, failures = {}, 0
    for name, fn, src in sources:
        cache = os.path.join(CACHE_DIR, name.lower().replace(" ", "-") + ".json")
        try:
            evs = fn(src)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(evs, f, ensure_ascii=False, indent=0)
            print(f"{name}: {len(evs)} events")
        except Exception as e:  # noqa: BLE001
            failures += 1
            if os.path.exists(cache):
                with open(cache, encoding="utf-8") as f:
                    evs = json.load(f)
                print(f"{name}: FAILED ({e}); using {len(evs)} cached events", file=sys.stderr)
            else:
                print(f"{name}: FAILED ({e}); no cache", file=sys.stderr)
                evs = []
        for ev in evs:
            events[ev["uid"]] = ev

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics = to_ics(cfg, events.values(), stamp)

    # Only rewrite when something other than DTSTAMP changed (keeps git history clean).
    def strip(s):
        return "\n".join(l for l in s.splitlines() if not l.startswith("DTSTAMP:"))
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if strip(old) != strip(ics):
        with open(OUT, "w", encoding="utf-8", newline="") as f:
            f.write(ics)
        print(f"Wrote {OUT} ({len(events)} events)")
    else:
        print("No changes.")
    if failures == len(sources):
        sys.exit(1)


if __name__ == "__main__":
    main()
