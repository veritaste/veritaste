# Veritaste

Dining feedback and food-waste signals for Harvard's dining halls: menus with
ratings and allergen/spice signals, servery line times, interhouse access rules,
a negative-RSVP attendance channel, and a mock Winnow-style waste feed — built
as a Harvard Summer School ENSC S-106 class project.

> This is not affiliated with or endorsed by Harvard University or HUDS.

Live at <https://veritaste.org>.

## Layout

| Path | What it is |
|---|---|
| `server/` | REST API — Python, Flask + APIFlask, SQLite behind a storage seam |
| `web/` | Frontend — vanilla JS/HTML/CSS, no framework, no build step |
| `deploy/` | Droplet provisioning, systemd unit, nginx configs, TLS issuance |
| `openapi.json` | Generated OpenAPI 3.1 contract for the API (`/api/v1`) |

## Run locally

```
cd server
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows; use .venv/bin/pip elsewhere
.venv/Scripts/python wsgi.py                       # serves app + API on 127.0.0.1:8000
```

Interactive API docs at `/api/docs`; the live spec at `/api/openapi.json`.
Menu data comes from the keyless CS50 Dining API and is cached in SQLite, so
the first request to a hall/date is slow and the rest are instant.

Seed the mock waste feed: `python seed_waste.py --date 2026-04-15 --location 30`
(July HUDS data covers only Adams House and Annenberg; use a term date like
2026-04-15 to see all halls.)

## Status

Prototype. Sign-in is a mock over five fixed demonstration accounts — no page
in this app can accept a real credential, by design. Line-length data is
simulated; waste data is a fabricated feed of the shape Winnow produces.

Licensed under the [MIT License](LICENSE).
