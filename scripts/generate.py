"""Render the profile cards in assets/ from this year's commit history.

Needs GH_TOKEN. With a token that can read private repos, their commits count
toward the totals; private repo names never appear on a card.
"""
from __future__ import annotations

import base64
import collections
import datetime as dt
import html
import json
import math
import os
import pathlib
import re
import statistics
import sys
import time
import tomllib
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
W = 840
P = dict(bg="#0f1117", pane="#151821", line="#272c3b", grid="#1d2130", teal="#7cc9c0", amber="#e5b562",
         pink="#d68fa6", blue="#8fa3d6", lime="#b9d27f", violet="#c59be0", ink="#d3d7e2", dim="#6c7386",
         faint="#3a4052")
LANG_COLORS = [P["amber"], P["teal"], P["blue"], P["pink"], P["ink"]]
TYPE_ORDER = [("feat", P["amber"]), ("fix", P["pink"]), ("docs", P["blue"]), ("refactor", P["teal"]),
              ("perf", P["lime"]), ("test", P["violet"]), ("chore", P["dim"])]
DAYS = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
TYPE_RE = re.compile(r"(\w+)(\([^)]*\))?!?:")
e = html.escape


def gql(query: str, **variables) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {os.environ['GH_TOKEN']}", "Content-Type": "application/json"},
    )
    # GitHub's GraphQL endpoint intermittently returns 502/503/504; retry with backoff.
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as err:
            if err.code < 500 or attempt == 4:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
        time.sleep(2 ** attempt * 3)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


REPOS_Q = """query($c:String){viewer{id login repositories(first:100,after:$c,ownerAffiliations:OWNER){
pageInfo{hasNextPage endCursor} nodes{name isPrivate primaryLanguage{name}}}}}"""
HIST_Q = """query($o:String!,$r:String!,$a:ID!,$s:GitTimestamp!,$c:String){repository(owner:$o,name:$r){
defaultBranchRef{target{... on Commit{history(first:100,after:$c,since:$s,author:{id:$a}){
pageInfo{hasNextPage endCursor} nodes{committedDate messageHeadline additions deletions}}}}}}}"""


def fetch_commits(since: dt.datetime) -> list[dict]:
    repos, cur = [], None
    while True:
        v = gql(REPOS_Q, c=cur)["viewer"]
        repos += v["repositories"]["nodes"]
        if not v["repositories"]["pageInfo"]["hasNextPage"]:
            break
        cur = v["repositories"]["pageInfo"]["endCursor"]
    out = []
    for repo in repos:
        if repo["name"].lower() == v["login"].lower():
            continue
        cur = None
        while True:
            ref = gql(HIST_Q, o=v["login"], r=repo["name"], a=v["id"], s=since.isoformat(), c=cur)["repository"]["defaultBranchRef"]
            if not ref:
                break
            h = ref["target"]["history"]
            lang = (repo["primaryLanguage"] or {}).get("name")
            out += [{**n, "lang": lang} for n in h["nodes"]]
            if not h["pageInfo"]["hasNextPage"]:
                break
            cur = h["pageInfo"]["endCursor"]
    return out


def commit_type(headline: str) -> str:
    m = TYPE_RE.match(headline)
    return m.group(1).lower() if m else "untyped"


def streaks(days: set[dt.date], today: dt.date) -> tuple[int, int]:
    """(longest, current). The current streak survives a quiet today."""
    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        best, prev = max(best, run), d
    cur, d = 0, today if today in days else today - dt.timedelta(days=1)
    while d in days:
        cur, d = cur + 1, d - dt.timedelta(days=1)
    return best, cur


def compute(commits: list[dict], now: dt.datetime) -> dict:
    tz = now.tzinfo
    for c in commits:
        c["t"] = dt.datetime.fromisoformat(c["committedDate"].replace("Z", "+00:00")).astimezone(tz)
    today = now.date()
    days = {c["t"].date() for c in commits}
    best, cur = streaks(days, today)
    hours = [0] * 24
    grid = [[0] * 24 for _ in range(7)]
    months: dict[str, list[int]] = {dt.date(now.year, m, 1).strftime("%b"): [0, 0] for m in range(1, now.month + 1)}
    for c in commits:
        hours[c["t"].hour] += 1
        grid[c["t"].weekday()][c["t"].hour] += 1
        mo = months[c["t"].strftime("%b")]
        mo[0] += c["additions"]
        mo[1] += c["deletions"]
    n = len(commits) or 1
    return dict(
        year=now.year, n=len(commits), active=len(days), span=(today - dt.date(now.year, 1, 1)).days + 1,
        best=best, cur=cur, week=sum(1 for c in commits if (today - c["t"].date()).days < 7),
        hours=hours, grid=grid, months=months,
        types=collections.Counter(commit_type(c["messageHeadline"]) for c in commits),
        langs=[l for l, _ in collections.Counter(c["lang"] for c in commits if c["lang"]).most_common(5)],
        add=sum(c["additions"] for c in commits), dele=sum(c["deletions"] for c in commits),
        median=statistics.median([c["additions"] + c["deletions"] for c in commits]) if commits else 0,
        night=sum(1 for c in commits if c["t"].hour >= 22 or c["t"].hour < 6) / n,
        last=max((c["t"] for c in commits), default=None), tz=now.strftime("%Z"),
    )


def font_css() -> str:
    f = lambda n: base64.b64encode((ROOT / "fonts" / n).read_bytes()).decode()
    return (f"@font-face{{font-family:SG;font-weight:600;src:url(data:font/woff2;base64,{f('space-grotesk-600.woff2')}) format('woff2')}}"
            f"@font-face{{font-family:JBM;src:url(data:font/woff2;base64,{f('jetbrains-mono-400.woff2')}) format('woff2')}}")


MONO = "font-family:JBM,'JetBrains Mono',ui-monospace,monospace"
DISP = "font-family:SG,'Space Grotesk',system-ui,sans-serif"
BASE = (f".d{{{MONO};fill:{P['dim']};font-size:11px}}.k{{{MONO};fill:{P['ink']};font-size:12px}}"
        f".n{{{DISP};fill:{P['ink']};font-weight:600}}.lbl{{{MONO};font-size:11px;letter-spacing:.08em}}"
        ".fu{animation:fu .7s cubic-bezier(.2,.8,.2,1) both}@keyframes fu{from{opacity:0;transform:translateY(5px)}}"
        "@media (prefers-reduced-motion: reduce){*{animation:none!important}}")


def svg(h: int, body: str, style: str, label: str, fonts: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" role="img" '
            f'aria-label="{e(label)}"><title>{e(label)}</title><style>{fonts}{BASE}{style}</style>'
            f'<rect width="{W}" height="{h}" rx="14" fill="{P["bg"]}"/>{body}</svg>\n')


def pane(x, y, w, h, title, active=False, note=""):
    t = f" {title} ".upper()
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{P["pane"]}" stroke="{P["line"]}"/>'
           f'<rect x="{x+14}" y="{y-7}" width="{len(t)*6.9+4:.0f}" height="14" fill="{P["bg"]}"/>'
           f'<text x="{x+16}" y="{y+4}" class="lbl" fill="{P["teal"] if active else P["dim"]}">{e(t)}</text>')
    if note:
        out += (f'<rect x="{x+w-14-len(note)*6.6-8:.0f}" y="{y-7}" width="{len(note)*6.6+8:.0f}" height="14" fill="{P["bg"]}"/>'
                f'<text x="{x+w-18}" y="{y+4}" text-anchor="end" class="d">{e(note)}</text>')
    return out


def kv(x, y, v, k, size=26, col=None):
    return (f'<text x="{x}" y="{y}" class="n" style="font-size:{size}px" fill="{col or P["ink"]}">{e(v)}</text>'
            f'<text x="{x}" y="{y+17}" class="d">{e(k)}</text>')


def short(n: int) -> str:
    return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.1f}k" if n >= 1e4 else f"{n:,}"


def header(S, cfg, fonts):
    last = f"last commit {S['last'].strftime('%b %-d').lower()}" if S["last"] else "no commits yet"
    cells = [(f'{S["n"]:,}', f"commits in {S['year']}"), (f'{S["active"]}/{S["span"]}', "days with a commit"),
             (f'{S["best"]}d', "longest streak"), (str(S["week"]), "commits, last 7 days")]
    tiles = "".join(f'<g class="fu" style="animation-delay:{150 + i*110}ms">'
                    f'{kv(372 + (i % 2) * 220, 92 + (i // 2) * 66, v, k, 26, P["amber"] if i == 0 else None)}</g>'
                    for i, (v, k) in enumerate(cells))
    sep = f'<tspan fill="{P["faint"]}"> / </tspan>'
    langs = sep.join(f'<tspan fill="{c}">{e(l.lower())}</tspan>' for l, c in zip(S["langs"], LANG_COLORS))
    st = (".rip{transform-box:fill-box;transform-origin:center;animation:rp 2.4s ease-out infinite}"
          "@keyframes rp{from{transform:scale(1);opacity:.6}to{transform:scale(3.2);opacity:0}}")
    body = f'''{pane(20, 24, 320, 186, "id", True)}{pane(352, 24, 468, 186, str(S["year"]), False, "updated hourly")}
<text x="44" y="104" class="n" style="font-size:64px;letter-spacing:-.03em">{e(cfg["name"])}</text>
<text x="46" y="134" class="k" style="font-size:14px">{e(cfg["bio"])}</text>
<text x="46" y="162" class="k">{langs}</text>
<circle class="rip" cx="50" cy="186" r="4" fill="none" stroke="{P["teal"]}"/><circle cx="50" cy="186" r="4" fill="{P["teal"]}"/>
<text x="62" y="190" class="d">{e(last)}</text>{tiles}'''
    return svg(230, body, st, f'{cfg["name"]}: {cfg["bio"]}', fonts)


def clock(S, fonts):
    cx, cy, r0, r1 = 150, 176, 42, 104
    hrs = S["hours"]
    peak = max(hrs) or 1
    ph = hrs.index(max(hrs))
    bars, ticks = [], []
    for h, n in enumerate(hrs):
        a = h / 24 * 2 * math.pi - math.pi / 2
        a0, a1 = a - 0.11, a + 0.11
        rr = r0 + (r1 - r0) * n / peak
        col = P["amber"] if h == ph else (P["teal"] if h >= 22 or h < 6 else P["faint"])
        p = lambda r, ang: f"{cx + r*math.cos(ang):.1f},{cy + r*math.sin(ang):.1f}"
        bars.append(f'<path class="cb{" pk" if h == ph else ""}" style="animation-delay:{h*35}ms" fill="{col}" '
                    f'd="M{p(r0,a0)}L{p(rr,a0)}A{rr:.1f},{rr:.1f} 0 0 1 {p(rr,a1)}L{p(r0,a1)}A{r0},{r0} 0 0 0 {p(r0,a0)}Z"/>')
        if h % 6 == 0:
            ticks.append(f'<text x="{cx + (r1+14)*math.cos(a):.0f}" y="{cy + (r1+14)*math.sin(a) + 4:.0f}" text-anchor="middle" class="d">{h:02d}</text>')
    hand = (f'<g class="hand"><line x1="{cx}" y1="{cy-r0+2}" x2="{cx}" y2="{cy-r1-6}" stroke="{P["ink"]}" stroke-opacity=".45" '
            f'stroke-linecap="round"/><circle cx="{cx}" cy="{cy-r1-6}" r="2.5" fill="{P["ink"]}"/></g>')
    center = (f'<text x="{cx}" y="{cy-2}" text-anchor="middle" class="n" style="font-size:20px" fill="{P["amber"]}">{ph:02d}:00</text>'
              f'<text x="{cx}" y="{cy+14}" text-anchor="middle" class="d">peak hour</text>')
    gx, gy, cs = 330, 70, 19
    gmax = max(max(r) for r in S["grid"]) or 1
    cells = []
    for d, row in enumerate(S["grid"]):
        cells.append(f'<text x="{gx-14}" y="{gy + d*cs + 13}" class="d" text-anchor="middle">{"MTWTFSS"[d]}</text>')
        for h, n in enumerate(row):
            fill = f'fill="{P["teal"]}" fill-opacity="{0.08 + 0.92 * (n / gmax) ** 0.7:.2f}"' if n else f'fill="{P["grid"]}"'
            cells.append(f'<rect class="hc" style="animation-delay:{(d+h)*22}ms" x="{gx + h*cs}" y="{gy + d*cs}" '
                         f'width="{cs-3}" height="{cs-3}" rx="3" {fill}/>')
    cells.append(f'<rect class="ph" x="{gx-2}" y="{gy-2}" width="{cs+1}" height="{7*cs+1}" rx="4" fill="{P["ink"]}" '
                 f'fill-opacity=".05" stroke="{P["ink"]}" stroke-opacity=".35"/>')
    cells += [f'<text x="{gx + h*cs + 8}" y="{gy + 7*cs + 14}" text-anchor="middle" class="d">{h:02d}</text>' for h in (0, 6, 12, 18, 23)]
    wd = [sum(r) for r in S["grid"]]
    facts = (f'<text x="{gx}" y="{gy + 7*cs + 50}" class="k"><tspan fill="{P["amber"]}">{S["night"]*100:.0f}%</tspan>'
             f'<tspan fill="{P["dim"]}"> of commits land between 22:00 and 06:00 · busiest on </tspan>{DAYS[wd.index(max(wd))]}</text>')
    st = (f".cb{{transform-origin:{cx}px {cy}px;animation:cg .8s cubic-bezier(.2,.8,.2,1) both}}@keyframes cg{{from{{transform:scale(.4);opacity:0}}}}"
          ".pk{animation:cg .8s cubic-bezier(.2,.8,.2,1) both,gl 3s ease-in-out 1s infinite}@keyframes gl{50%{opacity:.6}}"
          f".hand{{transform-origin:{cx}px {cy}px;animation:rot 24s linear infinite}}@keyframes rot{{to{{transform:rotate(360deg)}}}}"
          ".hc{animation:hf .5s ease-out both}@keyframes hf{from{opacity:0}}"
          f".ph{{animation:ph 24s steps(24) infinite}}@keyframes ph{{to{{transform:translateX({24*cs}px)}}}}")
    body = f'''{pane(20, 24, 260, 300, "commit clock", True, S["tz"])}{pane(292, 24, 528, 300, "when", False, f'{S["n"]:,} commits')}
<circle cx="{cx}" cy="{cy}" r="{r1}" fill="none" stroke="{P["grid"]}"/>
{"".join(bars)}{hand}{"".join(ticks)}{center}{"".join(cells)}{facts}'''
    return svg(344, body, st, "When I commit", fonts)


def shape(S, fonts):
    t, n = S["types"], S["n"] or 1
    vals = [(k, col, t[k]) for k, col in TYPE_ORDER] + [("untyped", P["faint"], S["n"] - sum(t[k] for k, _ in TYPE_ORDER))]
    x, bar, leg, lx, ly = 44, [], [], 44, 128
    for i, (k, col, v) in enumerate(vals):
        w = 732 * v / n
        bar.append(f'<rect class="hb" style="animation-delay:{i*70}ms" x="{x:.1f}" y="70" width="{max(w-3, 1):.1f}" height="22" rx="3" fill="{col}"/>')
        label = f"{k} {v*100/n:.0f}%"
        if lx + len(label) * 7.2 + 18 > 790:
            lx, ly = 44, ly + 20
        leg.append(f'<g class="fu" style="animation-delay:{400 + i*60}ms"><rect x="{lx}" y="{ly-9}" width="9" height="9" rx="2" fill="{col}"/>'
                   f'<text x="{lx+14}" y="{ly}" class="k">{label}</text></g>')
        lx += len(label) * 7.2 + 30
        x += w
    mo = S["months"]
    peak = max((max(a, d) for a, d in mo.values()), default=0) or 1
    by, hh = 282, 70
    bw = min(50, (656 - 10 * (len(mo) - 1)) / max(len(mo), 1))
    ch = []
    for i, (m, (a, d)) in enumerate(mo.items()):
        ha, hd = max(hh * a / peak, 1.5), max(hh * d / peak, 1.5)
        x0 = 44 + i * (bw + 10)
        ch.append(f'<rect class="vb up" style="animation-delay:{i*50}ms" x="{x0:.1f}" y="{by-ha:.1f}" width="{bw:.1f}" height="{ha:.1f}" rx="2" fill="{P["teal"]}"/>'
                  f'<rect class="vb dn" style="animation-delay:{i*50}ms" x="{x0:.1f}" y="{by+2}" width="{bw:.1f}" height="{hd:.1f}" rx="2" fill="{P["pink"]}" fill-opacity=".8"/>'
                  f'<text x="{x0+bw/2:.1f}" y="{by+hh+18}" text-anchor="middle" class="d">{m.lower()}</text>')
    nums = "".join(kv(716, 222 + i * 54, v, k, 20, c) for i, (v, k, c) in enumerate([
        (f'+{short(S["add"])}', "lines added", P["teal"]), (f'-{short(S["dele"])}', "lines removed", P["pink"]),
        (f'{S["median"]:.0f}', "median diff", None)]))
    st = (".hb{transform-box:fill-box;animation:gx .9s cubic-bezier(.2,.8,.2,1) both}@keyframes gx{from{transform:scaleX(0)}}"
          ".vb{transform-box:fill-box;animation:gy .7s ease-out both}.up{transform-origin:bottom}.dn{transform-origin:top}"
          "@keyframes gy{from{transform:scaleY(0)}}")
    body = f'''{pane(20, 24, 800, 136, "what kind of change", True, "conventional-commit prefix")}
{"".join(bar)}{"".join(leg)}
{pane(20, 176, 800, 222, "churn by month", False, "added ↑  removed ↓")}
<line x1="44" y1="{by+1}" x2="700" y2="{by+1}" stroke="{P["line"]}"/>{"".join(ch)}{nums}'''
    return svg(418, body, st, "Shape of the work", fonts)


def main() -> None:
    cfg = tomllib.loads((ROOT / "profile.toml").read_text())
    tz = ZoneInfo(cfg["timezone"])
    now = dt.datetime.now(tz)
    since = dt.datetime(now.year, 1, 1, tzinfo=tz).astimezone(dt.timezone.utc)
    commits = fetch_commits(since)
    S = compute(commits, now)
    fonts = font_css()
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    for name, text in {"header.svg": header(S, cfg, fonts), "clock.svg": clock(S, fonts), "shape.svg": shape(S, fonts)}.items():
        (out / name).write_text(text)
    print(f"{S['n']} commits, {S['active']} active days", file=sys.stderr)


if __name__ == "__main__":
    main()
