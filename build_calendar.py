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
            # Scores stay in the description only (spoiler-safe titles).
            mine = home if home["team"].get("id") == t["team_id"] else away
            wl = {True: "W", False: "L"}.get(mine.get("winner"), "")
            res = f"Final: {nm(away)} {a}, {nm(home)} {h}"
            lines.insert(0, f"{res} ({wl})" if wl else res)
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


# ------------------------------------------------------------------------ WWE
# ESPN doesn't carry WWE, so PLEs come from Wikipedia's "List of WWE pay-per-view
# and livestreaming supercards" (upcoming table + this year's past table).
# Wikipedia has no start times: events are all-day unless config "start_times"
# has an entry. Month-only dates ("April") are skipped until a day is announced.

WIKI_API = "https://en.wikipedia.org/w/api.php"
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


def wiki_clean(s):
    import re
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.split(r"<br\s*/?>", s)[0]
    s = re.sub(r"\{\{small\|[^}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    s = re.sub(r"<[^>]+>", "", s).replace("'''", "").replace("''", "")
    return s.strip()


def split_cell(raw):
    """'rowspan="2" |[[X|Y]]' -> ({'rowspan': 2}, '[[X|Y]]'). Ignores pipes in [[ ]] / {{ }}."""
    import re
    depth = 0
    for i, ch in enumerate(raw):
        if raw.startswith(("[[", "{{"), i):
            depth += 1
        elif raw.startswith(("]]", "}}"), i):
            depth -= 1
        elif ch == "|" and depth == 0:
            attrs, val = raw[:i], raw[i + 1:]
            if "=" in attrs and "[[" not in attrs:
                m = re.search(r'rowspan\s*=\s*"?(\d+)', attrs)
                return (int(m.group(1)) if m else 1), val
            break
    return 1, raw


def parse_wiki_tables(text):
    """Yield (year, row_style, {column: raw_value}) for Date/Event tables."""
    import re
    year, headers, pending, row, style, in_tbl = None, [], {}, None, "", False

    def flush():
        if row is None or not headers:
            return None
        vals, cells = {}, list(row)
        for i, h in enumerate(headers):
            if pending.get(i, (None, 0))[1] > 0:
                v, n = pending[i]
                vals[h] = v
                pending[i] = (v, n - 1)
            elif cells:
                span, v = cells.pop(0)
                vals[h] = v
                if span > 1:
                    pending[i] = (v, span - 1)
        return vals

    for line in text.splitlines():
        line = line.rstrip()
        m = re.match(r"^=+\s*(\d{4})\s*=+$", line)
        if m:
            year = int(m.group(1))
        elif line.startswith("{|"):
            in_tbl, headers, pending, row, style = True, [], {}, None, ""
        elif not in_tbl:
            continue
        elif line.startswith("|}"):
            vals = flush()
            if vals and "Date" in headers and "Event" in headers:
                yield year, style, vals
            in_tbl, row = False, None
        elif line.startswith("|-"):
            vals = flush()
            if vals and "Date" in headers and "Event" in headers:
                yield year, style, vals
            row, style = [], line[2:].lower()
        elif line.startswith("!"):
            headers.append(wiki_clean(line.split("|")[-1]))
        elif line.startswith("|") and row is not None:
            row.append(split_cell(line[1:]))


def wiki_parse(page, section):
    q = f"{WIKI_API}?action=parse&format=json&prop=wikitext&page={page}&section={section}"
    return fetch(q)["parse"]["wikitext"]["*"]


def wwe_events(w):
    import re
    page = w["page"]
    secs = fetch(f"{WIKI_API}?action=parse&format=json&prop=sections&page={page}")["parse"]["sections"]
    this_year = str(datetime.now(timezone.utc).year)
    wanted = [s["index"] for s in secs if s["line"] == "Upcoming event schedule"]
    # This year's already-held events live under "Past events" -> "2020s" -> "<year>"
    past = next((s for s in secs if s["line"] == "Past events"), None)
    if past:
        wanted += [s["index"] for s in secs if s["line"] == this_year and int(s["index"]) > int(past["index"])
                   and (not wanted or int(s["index"]) < int(wanted[0]))]
    if not wanted:
        raise RuntimeError("couldn't find event sections on Wikipedia page")

    exclude = [c.lower() for c in w.get("exclude_row_colors", [])]
    out = []
    for idx in wanted:
        text = wiki_parse(page, idx)
        # A past-year section has no year heading inside it; seed one.
        if not re.search(r"^=+\s*\d{4}\s*=+$", text, re.M):
            text = f"=={this_year}==\n" + text
        elif idx != wanted[0]:
            text = f"=={this_year}==\n" + text
        for year, style, vals in parse_wiki_tables(text):
            if not year or any(c in style for c in exclude):
                continue
            name = wiki_clean(vals.get("Event", ""))
            m = re.match(r"([A-Za-z]+)\s+(\d{1,2})\b", wiki_clean(vals.get("Date", "")))
            if not name or not m or m.group(1).lower() not in MONTHS:
                continue
            day = datetime(year, MONTHS[m.group(1).lower()], int(m.group(2)))
            key = f"{day:%Y-%m-%d}"
            loc = ", ".join(x for x in (wiki_clean(vals.get("Venue", "")), wiki_clean(vals.get("Location", "")))
                            if x and x != "TBA")
            lines = [w["name"]]
            notes = wiki_clean(vals.get("Notes", ""))
            if notes:
                lines.append(notes)
            main = wiki_clean(vals.get("Main event", ""))
            if main:
                lines.append(f"Main event: {main}")
            st = w.get("start_times", {}).get(key)
            if st:
                start, valid = datetime.fromisoformat(st), True
            else:
                start, valid = day.replace(tzinfo=EASTERN), False
            upcoming = day.date() >= datetime.now(EASTERN).date()
            if not valid and upcoming:
                lines.append("Start time not announced yet")
            lines.append(f"https://en.wikipedia.org/wiki/{page}")
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            out.append(make_event(f"wwe-{key}-{slug}@sports-calendar",
                                  f"{w['emoji']} {name}" + ("" if valid or not upcoming else " (time TBD)"),
                                  start, w["hours"], valid, "\n".join(lines), loc))
            out[-1]["_name"] = name
    if not out:
        raise RuntimeError("parsed 0 WWE events — page layout may have changed")
    # Label multi-night events (same name on consecutive days) as Night 1, Night 2...
    out.sort(key=lambda e: e["start"])
    for i, e in enumerate(out):
        prev = out[i - 1] if i else None
        if prev and prev["_name"] == e["_name"] and \
                (datetime.fromisoformat(e["start"]) - datetime.fromisoformat(prev["start"])).days <= 1:
            e["_night"] = prev.setdefault("_night", 1) + 1
    for e in out:
        if "_night" in e:
            e["title"] = e["title"].replace(e["_name"], f"{e['_name']} – Night {e['_night']}", 1)
        e.pop("_name"), e.pop("_night", None)
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
              [(r["name"], racing_events, r) for r in cfg["racing"]] + \
              [(w["name"], wwe_events, w) for w in cfg.get("wwe", [])]
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
