# Sports Calendar

One combined, auto-updating Google Calendar feed for:
Oklahoma Sooners (football, men's basketball, baseball), Dallas Cowboys, Dallas Mavericks,
Texas Rangers, Formula 1 (qualifying, sprints, races) and NASCAR Cup Series.

- Data: ESPN public schedule API
- `build_calendar.py` → generates `sports.ics` (stdlib only, no installs)
- `.github/workflows/update.yml` → rebuilds every 6 hours and commits changes
- Edit `config.json` to add/remove teams, change emojis, game lengths, or F1 sessions

Subscribe URL: `https://raw.githubusercontent.com/SladerAmbrose/sports-calendar/main/sports.ics`

Run locally: `python3 build_calendar.py`
