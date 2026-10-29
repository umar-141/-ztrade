#!/usr/bin/env python3
"""ZTRADE Weekly Market Perspective: pulls FMP data, builds an HTML report, emails it.

Env vars:
  FMP_API_KEY        Financial Modeling Prep key
  GMAIL_USER         Gmail address used to send
  GMAIL_APP_PASSWORD Gmail app password (Google Account > Security > App passwords)
  REPORT_TO          Recipient (default: GMAIL_USER)
Usage:
  python weekly_report.py            # build and send
  python weekly_report.py --dry-run  # build, write report.html, do not send
"""
import math, os, smtplib, sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

BASE = "https://financialmodelingprep.com/stable"
KEY = os.environ.get("FMP_API_KEY", "")

MARKETS = [("S&P 500", "^GSPC"), ("Nasdaq Comp", "^IXIC"), ("Dow Jones", "^DJI"),
           ("Russell 2000", "^RUT"), ("VIX", "^VIX"), ("Bitcoin", "BTCUSD"),
           ("Ethereum", "ETHUSD"), ("Gold", "GCUSD")]


def get(path, **params):
    """Call FMP; return parsed JSON or None (plan-restricted or failed calls are skipped)."""
    try:
        r = requests.get(f"{BASE}/{path}", params={**params, "apikey": KEY}, timeout=20)
        if r.status_code != 200:
            print(f"warn: {path} {params} -> HTTP {r.status_code}", file=sys.stderr)
            return None
        return r.json()
    except Exception as e:
        print(f"warn: {path} failed: {e}", file=sys.stderr)
        return None


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def fmt_pct(v):
    if v is None:
        return "n/a"
    color = "#1e8449" if v > 0 else "#c0392b" if v < 0 else "#444"
    return f'<span style="color:{color}">{v:+.2f}%</span>'


def fmt_num(v):
    return "n/a" if v is None else f"{v:,.2f}"


def collect():
    data = {"quotes": {}}
    for name, sym in MARKETS:
        q = get("quote", symbol=sym)
        if q:
            data["quotes"][name] = q[0]
    today = date.today()
    mon = today + timedelta(days=(7 - today.weekday()) % 7 or 7)  # next Monday
    data["week_start"], data["week_end"] = mon, mon + timedelta(days=4)
    data["treasury"] = (get("treasury-rates", **{"from": str(today - timedelta(days=7)),
                                                  "to": str(today)}) or [None])[0]
    data["earnings"] = get("earnings-calendar", **{"from": str(mon), "to": str(data["week_end"]),
                                                    "limit": 200}) or []
    data["sectors"] = get("sector-performance-snapshot", date=str(today - timedelta(days=1))) or []
    return data


def build_html(d):
    q = d["quotes"]
    rows = ""
    for i, (name, _) in enumerate(MARKETS):
        x = q.get(name)
        if not x:
            continue
        p = x["price"]
        bg = "background:#f6f8fa;" if i % 2 else ""
        rows += (f'<tr style="text-align:right;{bg}"><td style="text-align:left"><b>{name}</b></td>'
                 f'<td>{fmt_num(p)}</td><td>{fmt_pct(x.get("changePercentage"))}</td>'
                 f'<td>{fmt_pct(pct(p, x.get("priceAvg50")))}</td>'
                 f'<td>{fmt_pct(pct(p, x.get("priceAvg200")))}</td>'
                 f'<td>{fmt_pct(pct(p, x.get("yearHigh")))}</td></tr>')

    # Expected move from VIX (approximation: VIX is a 30-day measure)
    em = ""
    spx, vix = q.get("S&P 500"), q.get("VIX")
    if spx and vix:
        p, iv = spx["price"], vix["price"] / 100
        w, dly = p * iv * math.sqrt(5 / 252), p * iv * math.sqrt(1 / 252)
        em = (f"<p>With VIX at {vix['price']:.2f}: 1-day &plusmn;{dly:,.0f} pts "
              f"({iv*math.sqrt(1/252)*100:.2f}%), 1-week &plusmn;{w:,.0f} pts "
              f"({iv*math.sqrt(5/252)*100:.2f}%), about <b>{p-w:,.0f} to {p+w:,.0f}</b> "
              f"(roughly 68% probability).</p>")

    lv = ""
    if spx:
        p = spx["price"]
        for label, key in [("52-week high", "yearHigh"), ("50-day average", "priceAvg50"),
                           ("200-day average", "priceAvg200"), ("52-week low", "yearLow")]:
            if spx.get(key):
                lv += (f"<tr><td><b>S&amp;P 500 {label}</b></td><td>{spx[key]:,.2f} "
                       f"({fmt_pct(pct(spx[key], p))} from price)</td></tr>")

    t = d["treasury"]
    tr = ""
    if t:
        tr = (f"<p>Treasury curve ({t['date']}): 2y {t['year2']}%, 5y {t['year5']}%, "
              f"10y {t['year10']}%, 30y {t['year30']}%. 10s-2s spread "
              f"{(t['year10']-t['year2'])*100:+.0f} bps.</p>")

    sec = ""
    if d["sectors"]:
        s = sorted(d["sectors"], key=lambda r: r["averageChange"], reverse=True)
        sec = ("<p>Sectors (" + s[0]["date"] + ", NASDAQ-listed): leaders "
               + ", ".join(f"{r['sector']} {r['averageChange']:+.2f}%" for r in s[:3])
               + "; laggards "
               + ", ".join(f"{r['sector']} {r['averageChange']:+.2f}%" for r in s[-3:]) + ".</p>")

    er = ""
    if d["earnings"]:
        # keep larger revenue names first, max 25
        e = sorted(d["earnings"], key=lambda r: r.get("revenueEstimated") or 0, reverse=True)[:25]
        e.sort(key=lambda r: r["date"])
        for r in e:
            eps = r.get("epsEstimated")
            er += (f"<tr><td><b>{r['date']}</b></td><td>{r['symbol']}</td>"
                   f"<td>{'n/a' if eps is None else f'{eps:.2f}'}</td></tr>")
        er = ('<table cellpadding="5" style="width:100%;font-size:13px"><tr style="background:#eef3f8">'
              "<th align=left>Date</th><th align=left>Ticker</th><th align=left>Est. EPS</th></tr>"
              + er + "</table>")

    h = "margin:18px 0 8px;color:#0f2a43"
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:0 auto;color:#1a1f2b;line-height:1.5">
<div style="background:#0f2a43;color:#fff;padding:22px 24px;border-radius:6px 6px 0 0">
<div style="font-size:12px;letter-spacing:2px;color:#7fd3f0">ZTRADE WEEKLY</div>
<div style="font-size:26px;font-weight:bold">A Market Perspective</div>
<div style="font-size:13px;color:#cfe3f0">Week of {d['week_start']:%b %d} to {d['week_end']:%b %d, %Y} | Prepared {date.today():%b %d, %Y}</div></div>
<div style="border:1px solid #dde3ea;border-top:none;padding:22px 24px">
<h3 style="{h}">Snapshot and trend position</h3>
<table cellpadding="6" style="border-collapse:collapse;width:100%;font-size:13px">
<tr style="background:#0f2a43;color:#fff;text-align:right"><th align=left>Market</th><th>Last</th><th>Today</th><th>vs 50d</th><th>vs 200d</th><th>From 52w high</th></tr>{rows}</table>
<h3 style="{h}">Expected move (S&amp;P 500)</h3>{em}
<h3 style="{h}">Reference levels</h3><table cellpadding="5" style="width:100%;font-size:13px">{lv}</table>
<h3 style="{h}">Rates and sectors</h3>{tr}{sec}
<h3 style="{h}">Earnings this week (largest by est. revenue)</h3>{er}
<hr style="border:none;border-top:1px solid #dde3ea;margin:22px 0 10px">
<p style="font-size:11px;color:#7a8492">Educational information only, not financial advice. Data: Financial Modeling Prep. Expected move uses the VIX as an approximation of implied volatility.</p>
</div></div>"""


def send(html, subject):
    user, pw = os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("REPORT_TO", user)
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText("Open this email in an HTML-capable client.", "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())


if __name__ == "__main__":
    d = collect()
    if not d["quotes"]:
        sys.exit("No market data returned. Check FMP_API_KEY and plan limits.")
    html = build_html(d)
    subject = f"Weekly Market Perspective: Week of {d['week_start']:%b %d}, {d['week_start']:%Y}"
    if "--dry-run" in sys.argv:
        open("report.html", "w").write(html)
        print("Wrote report.html")
    else:
        send(html, subject)
        print("Sent:", subject)
