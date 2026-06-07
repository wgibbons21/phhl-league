#!/usr/bin/env python3
"""
update_league.py — Weekly update script for 10U Advance League (League 6130)

Usage:
  python3 update_league.py --token "eyJhbGc..."          # JWT token (Bearer prefix optional)
  python3 update_league.py --token "Bearer eyJhbGc..."
  python3 update_league.py --offline                      # Skip API fetch, just rebuild from cached data
"""

import json
import os
import sys
import argparse
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE  = os.path.join(SCRIPT_DIR, 'data', 'league_6130.json')
OVERRIDES_FILE = os.path.join(SCRIPT_DIR, 'data', 'overrides.json')

BASE_URL = "https://api.daysmartrecreation.com/v1/"

# All 16 team IDs in League 6130 — 10U Advanced division
# North (5): 14333-14337  |  South (5): 14345-14349  |  West (6): 14355-14360
TEAM_IDS = [
    14333, 14334, 14335, 14336, 14337,          # North
    14345, 14346, 14347, 14348, 14349,          # South
    14355, 14356, 14357, 14358, 14359, 14360,   # West
]


def fetch_games(token, existing_games):
    """Refresh game data from DaySmart API by fetching each known game by ID.

    The filter[hteam_id] endpoint only returns a subset of games (original
    schedule entries), so we fetch each game individually instead. This
    guarantees we pick up newly-entered scores for every game in our dataset.

    `token` is optional — the event endpoint accepts unauthenticated GETs as
    long as the `company=polarice` query param is attached. Pass None to skip
    auth entirely.
    """
    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' library not installed. Run: pip3 install requests")
        sys.exit(1)

    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    if token:
        if not token.startswith("Bearer "):
            token = "Bearer " + token
        headers["Authorization"] = token
    cookies = {
        "api_company": "polarice"
    }

    updated = []
    mode = "authenticated" if token else "anonymous (company=polarice)"
    print(f"  Refreshing {len(existing_games)} games from API [{mode}]...")
    ok = 0
    errs = 0
    for g in existing_games:
        gid = g['id']
        url = f"{BASE_URL}events/{gid}?company=polarice"
        try:
            resp = requests.get(url, headers=headers, cookies=cookies, timeout=10)
            if resp.status_code == 200:
                fresh = resp.json().get('data', {})
                updated.append(fresh)
                ok += 1
            else:
                updated.append(g)  # keep local copy on failure
                errs += 1
        except Exception:
            updated.append(g)
            errs += 1

    print(f"  Fetched: {ok} OK, {errs} errors")
    return updated


def fetch_playoff_games(token):
    """Fetch playoff games, which live under a team's `playoffEvents` relationship
    (NOT the regular league events feed). All West playoff games are reachable from
    any West team; we use Disco Pickles (14356). Returns a list of simplified game
    dicts, or None on failure (so the caller keeps the cached copy).
    """
    try:
        import requests
    except ImportError:
        return None
    headers = {"Accept": "application/vnd.api+json"}
    if token:
        if not token.startswith("Bearer "):
            token = "Bearer " + token
        headers["Authorization"] = token
    cookies = {"api_company": "polarice"}
    inc = "league.playoffEvents.homeTeam,league.playoffEvents.visitingTeam"
    url = f"{BASE_URL}teams/14356?cache[save]=false&include={inc}&company=polarice"
    try:
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=15)
    except Exception as e:
        print(f"  Playoff fetch failed: {e}")
        return None
    if resp.status_code != 200:
        print(f"  Playoff fetch: HTTP {resp.status_code} — keeping cached playoff games")
        return None
    games = []
    for o in resp.json().get('included', []):
        if o.get('type') != 'events':
            continue
        a = o.get('attributes', {})
        games.append({
            'id': o.get('id'),
            'hteam_id': a.get('hteam_id'),
            'vteam_id': a.get('vteam_id'),
            'home_score': a.get('home_score'),
            'visiting_score': a.get('visiting_score'),
            'is_overtime': a.get('is_overtime'),
            'start': a.get('start'),
        })
    scored = sum(1 for g in games if g['home_score'] is not None)
    print(f"  Playoff fetch: {len(games)} playoff events ({scored} with scores)")
    return games


def apply_overrides(games, overrides):
    """Apply manual score overrides to games (overrides win on conflicts)."""
    override_count = 0
    applied = []
    for game in games:
        game_id = str(game['id'])
        if game_id in overrides:
            o = overrides[game_id]
            old_hs = game['attributes'].get('home_score')
            old_vs = game['attributes'].get('visiting_score')
            game['attributes']['home_score'] = o['home_score']
            game['attributes']['visiting_score'] = o['visiting_score']
            game['attributes']['_override'] = True
            override_count += 1
            applied.append({
                'id': game_id,
                'old': (old_hs, old_vs),
                'new': (o['home_score'], o['visiting_score']),
                'note': o.get('note', '')
            })
    return games, override_count, applied


def detect_changes(old_games, new_games):
    """Compare old vs new game data and return list of changes."""
    old_map = {str(g['id']): g for g in old_games}
    new_map = {str(g['id']): g for g in new_games}
    changes = []

    for gid, ng in new_map.items():
        og = old_map.get(gid)
        if og is None:
            changes.append(f"  NEW GAME: {gid}")
            continue
        oa = og['attributes']
        na = ng['attributes']
        old_hs = oa.get('home_score')
        old_vs = oa.get('visiting_score')
        new_hs = na.get('home_score')
        new_vs = na.get('visiting_score')
        if (old_hs, old_vs) != (new_hs, new_vs):
            if new_hs is not None and new_vs is not None:
                changes.append(
                    f"  SCORE ENTERED: Game {gid} — {new_hs}-{new_vs} "
                    f"(was {old_hs}-{old_vs})"
                )
    return changes


def compute_dp_stats(games):
    """Compute Disco Pickles W-L-T, GF, GA from completed games."""
    DP_ID = 14356
    w = l = t = gf = ga = 0
    for g in games:
        a = g['attributes']
        h, v = a.get('hteam_id'), a.get('vteam_id')
        hs, vs = a.get('home_score'), a.get('visiting_score')
        if v is None or hs is None or vs is None:
            continue
        hs, vs = int(hs), int(vs)
        if h == DP_ID:
            gf += hs; ga += vs
            if hs > vs: w += 1
            elif vs > hs: l += 1
            else: t += 1
        elif v == DP_ID:
            gf += vs; ga += hs
            if vs > hs: w += 1
            elif hs > vs: l += 1
            else: t += 1
    return w, l, t, gf, ga


def main():
    parser = argparse.ArgumentParser(description='Update 10U ADV League tracker')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--token', help='DaySmart JWT token (optional; Bearer prefix optional)')
    group.add_argument('--offline', action='store_true', help='Skip API fetch, rebuild from cached data')
    args = parser.parse_args()
    # Default behavior (no flag): fetch anonymously via company=polarice query param

    print("=" * 60)
    print("10U ADV LEAGUE TRACKER — WEEKLY UPDATE")
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Load existing data
    if not os.path.exists(DATA_FILE):
        print(f"ERROR: Data file not found: {DATA_FILE}")
        sys.exit(1)

    with open(DATA_FILE) as f:
        stored_data = json.load(f)

    old_games = stored_data.get('games', [])
    team_names = stored_data.get('team_names', {})
    future_weekends = stored_data.get('future_weekends', [])

    print(f"\nLoaded {len(old_games)} games from local store.")

    # Fetch new data or use offline
    if args.offline:
        print("\n[OFFLINE MODE] Skipping API fetch, using cached data.")
        new_games = [g for g in old_games]  # copy
    else:
        if args.token:
            print("\n[ONLINE — AUTHENTICATED] Fetching fresh data from DaySmart API...")
        else:
            print("\n[ONLINE — ANONYMOUS] Fetching via company=polarice (no token needed)...")
        new_games = fetch_games(args.token, old_games)
        print(f"  Result: {len(new_games)} games")

    # Playoff games live under a separate relationship (not in the main feed)
    if args.offline:
        playoff_games = stored_data.get('playoff_games', [])
    else:
        _pf = fetch_playoff_games(args.token)
        playoff_games = _pf if _pf is not None else stored_data.get('playoff_games', [])

    # Detect changes before overrides
    changes = detect_changes(old_games, new_games)

    # Load and apply overrides
    overrides = {}
    if os.path.exists(OVERRIDES_FILE):
        with open(OVERRIDES_FILE) as f:
            overrides = json.load(f)
        print(f"\nLoaded {len(overrides)} override(s) from overrides.json.")
    else:
        print("\nNo overrides.json found — skipping overrides.")

    new_games, override_count, applied_overrides = apply_overrides(new_games, overrides)

    # Save merged data
    merged = {
        'games': new_games,
        'playoff_games': playoff_games,
        'team_names': team_names,
        'future_weekends': future_weekends,
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    with open(DATA_FILE, 'w') as f:
        json.dump(merged, f, indent=2)
    print(f"\nSaved merged data to {DATA_FILE}")

    # Report changes
    if changes:
        print(f"\n{'─'*40}")
        print(f"CHANGES DETECTED ({len(changes)}):")
        for c in changes:
            print(c)
    else:
        print("\nNo score changes detected from API.")

    if applied_overrides:
        print(f"\n{'─'*40}")
        print(f"OVERRIDES APPLIED ({override_count}):")
        for o in applied_overrides:
            print(f"  Game {o['id']}: {o['old']} → {o['new']}")
            if o['note']:
                print(f"    Note: {o['note']}")

    # Playoff scores pulled from the feed
    _scored_pf = [g for g in playoff_games if g.get('home_score') is not None]
    if _scored_pf:
        _names = {int(k): v for k, v in team_names.items()}
        def _sn(tid):
            n = _names.get(tid, str(tid)); p = n.split(' - '); return p[1] if len(p) > 1 else n
        print(f"\n{'─'*40}")
        print(f"PLAYOFF SCORES IN FEED ({len(_scored_pf)}):")
        for g in sorted(_scored_pf, key=lambda x: x.get('start', '')):
            ot = ' (OT)' if g.get('is_overtime') else ''
            print(f"  {_sn(g['hteam_id'])} {g['home_score']}-{g['visiting_score']} {_sn(g['vteam_id'])}{ot}")

    # DP stats after override
    w, l, t, gf, ga = compute_dp_stats(new_games)
    gd = gf - ga
    print(f"\n{'─'*40}")
    print(f"DISCO PICKLES RECORD:")
    print(f"  Record: {w}-{l}-{t}  |  GF: {gf}  GA: {ga}  GD: {gd:+d}")
    print(f"  Points: {w*2+t}")

    # Future weekends reminder
    print(f"\n{'─'*40}")
    print("FUTURE WEEKENDS:")
    for fw in future_weekends:
        status = fw['status'].upper()
        pub = 'matchups published' if fw['matchups_published'] else 'matchups TBD'
        print(f"  {fw['label']} ({fw['date']}) — {status} — {pub}")

    # Rebuild output files
    print(f"\n{'─'*40}")
    print("REBUILDING OUTPUT FILES...")

    build_xlsx = os.path.join(SCRIPT_DIR, 'build_xlsx.py')
    build_html = os.path.join(SCRIPT_DIR, 'build_html.py')

    for script_path, label in [(build_xlsx, 'Excel'), (build_html, 'HTML')]:
        if not os.path.exists(script_path):
            print(f"  WARNING: {script_path} not found — skipping {label} build.")
            continue
        print(f"  Building {label}...")
        result = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"    {label} build successful.")
            if result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    print(f"    {line}")
        else:
            print(f"    ERROR building {label}:")
            print(result.stderr)

    print(f"\n{'='*60}")
    print("UPDATE COMPLETE")
    print(f"  Excel: /Users/wgibbons/Desktop/10U_ADV_League_6130.xlsx")
    print(f"  HTML:  /Users/wgibbons/Desktop/10U_ADV_League_6130.html")
    print("=" * 60)


if __name__ == '__main__':
    main()
