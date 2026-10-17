"""
CLT Flight Deal Tracker
-----------------------
Checks one-way fares from Charlotte Douglas (CLT) to a list of destinations,
keeps every quote in SQLite, and flags a *deal* when today's cheapest fare is
well below that route's recent typical price, or under a target price you set.

Standard library only.

    python flights.py check            # fetch fares (needs IGNAV_API_KEY) and build the report
    python flights.py check --demo     # same, using generated sample prices; no key needed
    python flights.py report           # rebuild docs/index.html from saved data
    python flights.py history LGA      # print price history for one route

Fare data: Ignav fares API (https://ignav.com), 1,000 free requests to start.
Set your key first:
    Windows PowerShell:  $env:IGNAV_API_KEY = "your-key"
    macOS / Linux:       export IGNAV_API_KEY=your-key
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import random
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DB_PATH = ROOT / "data" / "fares.db"
OUT_PATH = ROOT / "docs" / "index.html"
USER_AGENT = "clt-flight-deals/1.0 (+https://github.com/ryanstormrage/clt-flight-deals)"


# =========================================================================== #
# Providers
# =========================================================================== #
class Quote(dict):
    """price, carrier, stops, duration_min, depart_time"""


def ignav_one_way(origin: str, dest: str, depart: dt.date, max_stops: int) -> Quote | None:
    key = os.environ.get("IGNAV_API_KEY")
    if not key:
        sys.exit("IGNAV_API_KEY isn't set. Get a free key at https://ignav.com, or run with --demo.")
    body = json.dumps({"origin": origin, "destination": dest,
                       "departure_date": depart.isoformat(), "max_stops": max_stops,
                       "market": "US"}).encode()
    req = urllib.request.Request(
        "https://ignav.com/api/fares/one-way", data=body, method="POST",
        headers={"X-Api-Key": key, "Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as err:
        print(f"    ! {origin}->{dest} {depart}: HTTP {err.code} {err.read()[:200]!r}")
        return None

    best = None
    for it in data.get("itineraries", []):
        price = (it.get("price") or {}).get("amount")
        if price is None:
            continue
        out = it.get("outbound") or {}
        segs = out.get("segments") or []
        q = Quote(price=float(price), carrier=out.get("carrier") or (segs[0].get("operating_carrier_name") if segs else ""),
                  stops=max(len(segs) - 1, 0), duration_min=out.get("duration_minutes"),
                  depart_time=segs[0].get("departure_time_local") if segs else None)
        if best is None or q["price"] < best["price"]:
            best = q
    return best


def demo_one_way(origin: str, dest: str, depart: dt.date, max_stops: int) -> Quote:
    """Generated sample fares so the project runs without a key. Clearly marked as 'demo' in the database."""
    base = {"LGA": 140, "BOS": 160, "MCO": 120, "MIA": 150, "DEN": 190, "LAS": 210, "LAX": 260, "CUN": 330}.get(dest, 200)
    seed = int(hashlib.md5(f"{dest}{depart}{dt.date.today()}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    lead = (depart - dt.date.today()).days
    price = base * (1.25 if lead < 28 else 1.0) * rng.uniform(0.62, 1.3)
    return Quote(price=round(price, 2), carrier=rng.choice(["American", "Delta", "United", "Frontier", "Spirit"]),
                 stops=rng.choice([0, 0, 1]), duration_min=rng.randint(95, 420), depart_time=None)


# =========================================================================== #
# Storage
# =========================================================================== #
SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    id           INTEGER PRIMARY KEY,
    observed_on  TEXT NOT NULL,        -- date the price was seen
    origin       TEXT NOT NULL,
    dest         TEXT NOT NULL,
    depart_date  TEXT NOT NULL,
    price        REAL NOT NULL,
    carrier      TEXT,
    stops        INTEGER,
    duration_min INTEGER,
    source       TEXT NOT NULL,        -- 'ignav' or 'demo'
    UNIQUE (observed_on, origin, dest, depart_date, source)
);
CREATE INDEX IF NOT EXISTS ix_route ON quotes(origin, dest, observed_on);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# =========================================================================== #
# Commands
# =========================================================================== #
def cmd_check(demo: bool, backfill: int) -> None:
    con = connect()
    origin, dests = CONFIG["origin"], CONFIG["destinations"]
    provider, source = (demo_one_way, "demo") if demo else (ignav_one_way, "ignav")
    today = dt.date.today()

    observe_days = [today - dt.timedelta(days=d) for d in range(backfill, -1, -1)] if demo else [today]
    total = len(dests) * len(CONFIG["days_ahead"])
    print(f"Checking {total} fares from {origin} ({'DEMO data' if demo else 'Ignav API'})…")

    for observed in observe_days:
        for dest in dests:
            for ahead in CONFIG["days_ahead"]:
                depart = observed + dt.timedelta(days=ahead)
                if demo:
                    # make demo history vary by observation day
                    random.seed(f"{observed}{dest}{ahead}")
                q = provider(origin, dest, depart, CONFIG["max_stops"])
                if demo and observed != today:
                    q["price"] = round(q["price"] * random.uniform(0.85, 1.25), 2)
                if not q:
                    continue
                con.execute(
                    "INSERT OR REPLACE INTO quotes(observed_on,origin,dest,depart_date,price,carrier,stops,duration_min,source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (observed.isoformat(), origin, dest, depart.isoformat(), q["price"], q["carrier"],
                     q["stops"], q["duration_min"], source))
                if not demo:
                    time.sleep(0.5)
        con.commit()

    deals = cmd_report(con, source)
    if deals:
        print("\n*** DEALS ***")
        for d in deals:
            print(f"  {origin} → {d['dest']} on {d['depart_date']}: ${d['price']:.0f}  "
                  f"(typical ${d['typical']:.0f}, {d['why']})")
    else:
        print("\nNo deals today. Prices are at or near normal.")


def route_summary(con, source: str) -> list[dict]:
    origin = CONFIG["origin"]
    latest = con.execute("SELECT MAX(observed_on) FROM quotes WHERE source=?", (source,)).fetchone()[0]
    if not latest:
        return []
    window_start = (dt.date.fromisoformat(latest) - dt.timedelta(days=30)).isoformat()
    out = []
    for dest, name in CONFIG["destinations"].items():
        today_rows = con.execute(
            "SELECT * FROM quotes WHERE source=? AND origin=? AND dest=? AND observed_on=? ORDER BY price",
            (source, origin, dest, latest)).fetchall()
        if not today_rows:
            continue
        hist = [r[0] for r in con.execute(
            "SELECT price FROM quotes WHERE source=? AND origin=? AND dest=? AND observed_on>=?",
            (source, origin, dest, window_start))]
        daily_min = [(r[0], r[1]) for r in con.execute(
            "SELECT observed_on, MIN(price) FROM quotes WHERE source=? AND origin=? AND dest=? AND observed_on>=? "
            "GROUP BY observed_on ORDER BY observed_on", (source, origin, dest, window_start))]
        best = dict(today_rows[0])
        typical = statistics.median(hist)
        target = CONFIG.get("target_prices", {}).get(dest)
        why = None
        if target and best["price"] <= target:
            why = f"under your ${target} target"
        elif len(hist) >= 6 and best["price"] <= typical * (1 - CONFIG["deal_threshold"]):
            why = f"{(1 - best['price'] / typical) * 100:.0f}% below typical"
        out.append({**best, "name": name, "typical": typical, "why": why, "is_deal": bool(why),
                    "samples": len(hist), "daily_min": daily_min, "all_today": [dict(r) for r in today_rows]})
    out.sort(key=lambda r: (not r["is_deal"], r["price"] / r["typical"]))
    return out


def sparkline(points: list[tuple[str, float]], w=160, h=36) -> str:
    if len(points) < 2:
        return '<span class="muted small">not enough history yet</span>'
    vals = [p[1] for p in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    step = w / (len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{h - 4 - (v - lo) / span * (h - 8):.1f}" for i, v in enumerate(vals))
    lx, ly = (len(vals) - 1) * step, h - 4 - (vals[-1] - lo) / span * (h - 8)
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-label="price trend">'
            f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{pts}"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="currentColor"/></svg>')


def cmd_report(con=None, source: str | None = None) -> list[dict]:
    con = con or connect()
    if source is None:
        row = con.execute("SELECT source FROM quotes ORDER BY observed_on DESC, source='demo' LIMIT 1").fetchone()
        if not row:
            print("No fares saved yet. Run: python flights.py check --demo")
            return []
        source = row[0]
    routes = route_summary(con, source)
    e = html.escape
    cards = []
    for r in routes:
        others = "".join(f'<li>{e(x["depart_date"])}<span>${x["price"]:.0f}</span></li>' for x in r["all_today"])
        stops = "nonstop" if r["stops"] == 0 else f'{r["stops"]} stop'
        badge = f'<span class="badge">DEAL · {e(r["why"])}</span>' if r["is_deal"] else ""
        cards.append(f"""
<article class="card{' deal' if r['is_deal'] else ''}">
  <header><div><h2>{e(r['dest'])}</h2><p>{e(r['name'])}</p></div>{badge}</header>
  <div class="price">${r['price']:.0f}<small>typical ${r['typical']:.0f}</small></div>
  <p class="meta">{e(r['depart_date'])} · {e(r['carrier'] or '—')} · {stops}</p>
  <div class="spark">{sparkline(r['daily_min'])}</div>
  <ul>{others}</ul>
</article>""")

    latest = routes[0]["observed_on"] if routes else "—"
    demo_note = ('<p class="demo">Showing <b>generated demo prices</b>. Set IGNAV_API_KEY and run '
                 '<code>python flights.py check</code> for real fares.</p>') if source == "demo" else ""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CLT Flight Deals</title>
<style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#1c1b19;--muted:#77736b;--line:#e4e0d6;--deal:#0f7b4f;--deal-bg:#e3f4ea;--accent:#c2410c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--card:#1f1e1c;--ink:#f1efe9;--muted:#9a968d;--line:#33312d;--deal:#4ade80;--deal-bg:#16301f;--accent:#fb923c}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:36px 16px 60px}}
.top{{display:flex;justify-content:space-between;align-items:end;gap:16px;flex-wrap:wrap;border-bottom:2px solid var(--ink);padding-bottom:16px}}
h1{{margin:0;font-size:clamp(1.8rem,4.5vw,3rem);letter-spacing:-.03em;line-height:1}}h1 span{{color:var(--accent)}}
.sub{{color:var(--muted);margin:6px 0 0}}.demo{{background:#fff7d6;color:#5c4a00;padding:10px 14px;border-radius:8px;margin:18px 0 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px;margin-top:22px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:6px}}
.card.deal{{border-color:var(--deal);box-shadow:0 0 0 1px var(--deal) inset}}
.card header{{display:flex;justify-content:space-between;gap:8px;align-items:start}}
h2{{margin:0;font-size:1.5rem;letter-spacing:-.02em}}.card header p{{margin:0;color:var(--muted);font-size:.85rem}}
.badge{{background:var(--deal-bg);color:var(--deal);font-size:.7rem;font-weight:700;padding:4px 8px;border-radius:6px;text-align:right}}
.price{{font-size:2.2rem;font-weight:700;letter-spacing:-.03em;display:flex;align-items:baseline;gap:10px}}
.price small{{font-size:.85rem;font-weight:400;color:var(--muted)}}.meta{{margin:0;color:var(--muted);font-size:.85rem}}
.spark{{color:var(--accent);min-height:36px}}.deal .spark{{color:var(--deal)}}
ul{{list-style:none;margin:6px 0 0;padding:8px 0 0;border-top:1px dashed var(--line);font-size:.85rem}}
li{{display:flex;justify-content:space-between;color:var(--muted)}}li span{{color:var(--ink);font-variant-numeric:tabular-nums}}
.muted{{color:var(--muted)}}.small{{font-size:.8rem}}footer{{margin-top:28px;color:var(--muted);font-size:.85rem}}a{{color:var(--accent)}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>CLT <span>→</span> anywhere cheap</h1>
<p class="sub">One-way fares from Charlotte Douglas · checked {e(latest)} · deal = {int(CONFIG['deal_threshold']*100)}%+ below the 30-day typical price, or under your target</p></div></div>
{demo_note}
<div class="grid">{''.join(cards) or '<p>No data yet.</p>'}</div>
<footer>Built by <a href="https://github.com/ryanstormrage">ryanstormrage</a> · fares via <a href="https://ignav.com">Ignav</a> ·
prices change constantly; always confirm with the airline before booking.</footer>
</div></body></html>"""
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(page, encoding="utf-8")
    print(f"Report written to {OUT_PATH.relative_to(ROOT)}")
    return [r for r in routes if r["is_deal"]]


def cmd_history(dest: str) -> None:
    con = connect()
    rows = con.execute(
        "SELECT observed_on, MIN(price) AS low, ROUND(AVG(price),2) AS avg, COUNT(*) AS n, source "
        "FROM quotes WHERE dest=? GROUP BY observed_on, source ORDER BY observed_on", (dest.upper(),)).fetchall()
    if not rows:
        print(f"No history for {dest.upper()} yet.")
        return
    print(f"{'date':<12}{'lowest':>9}{'average':>10}{'quotes':>8}  source")
    for r in rows:
        print(f"{r['observed_on']:<12}{r['low']:>9.0f}{r['avg']:>10.0f}{r['n']:>8}  {r['source']}")


def main():
    ap = argparse.ArgumentParser(description="Track cheap one-way flights out of CLT.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="fetch today's fares and rebuild the report")
    c.add_argument("--demo", action="store_true", help="use generated sample prices (no API key needed)")
    c.add_argument("--backfill", type=int, default=14, help="demo only: days of fake history to create (default 14)")
    sub.add_parser("report", help="rebuild docs/index.html from saved data")
    h = sub.add_parser("history", help="show price history for one destination")
    h.add_argument("dest")
    args = ap.parse_args()

    if args.cmd == "check":
        cmd_check(args.demo, args.backfill)
    elif args.cmd == "report":
        cmd_report()
    elif args.cmd == "history":
        cmd_history(args.dest)


if __name__ == "__main__":
    main()
