# CLT Flight Deal Tracker ✈️  *(in progress)*

Watches one-way fares out of **Charlotte Douglas International (CLT)** and tells me when one is actually cheap.

Each run checks every destination in `config.json` at several lead times (by default 3, 6 and 11 weeks out). Every
quote goes into SQLite, and a fare counts as a **deal** when:

- it's **20%+ below the median price** seen on that route over the last 30 days, or
- it's under a **target price** I set for that destination.

The output is a console summary plus a report page at `docs/index.html`, with a card per destination, a 30-day trend
sparkline and a deal badge.

**Stack:** Python 3.10+ (standard library only), SQLite, Ignav fares API, HTML/CSS

## Quick start

```bash
# try it with generated sample prices, no account needed
python flights.py check --demo
# then open docs/index.html
```

### Real fares

1. Sign up at [ignav.com](https://ignav.com). New accounts get 1,000 free requests, then about $2 per 1,000.
2. Set your key and run:

```powershell
# Windows PowerShell
$env:IGNAV_API_KEY = "your-key"
python flights.py check
```

```bash
# macOS / Linux
export IGNAV_API_KEY=your-key
python flights.py check
```

With the default config, one run uses **24 requests** (8 destinations × 3 dates), so the free tier covers about 40 runs.

### Other commands

```bash
python flights.py report          # rebuild the page from saved data
python flights.py history LGA     # daily low / average for one route
```

## Configure

```json
{
  "origin": "CLT",
  "destinations": { "LGA": "New York (LaGuardia)", "MCO": "Orlando", ... },
  "days_ahead": [21, 45, 75],
  "max_stops": 1,
  "deal_threshold": 0.20,
  "target_prices": { "LGA": 90, "MCO": 80 }
}
```

## Why Ignav?

Amadeus shut down its free self-service flight APIs in July 2026, which was the usual choice for student projects.
Ignav has a straightforward JSON API with a free starting allowance. The provider code is a single function
(`ignav_one_way`), so swapping in another source later only touches that one function.

## Roadmap

- [x] Store fare history in SQLite and detect deals
- [x] HTML report with trend sparklines
- [ ] Round-trip and flexible-date searches
- [ ] Email or Discord alert when a deal appears
- [ ] Scheduled daily run with GitHub Actions (store the key as a repo secret)
- [ ] Use the BTS on-time data from [clt-flight-delays-sql](https://github.com/ryanstormrage/clt-flight-delays-sql) to show how reliable each deal's airline is
