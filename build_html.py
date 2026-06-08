#!/usr/bin/env python3
"""
Build the 10U Advance League 6130 HTML webpage.
"""

import json
from datetime import datetime, date
from collections import defaultdict

# ── Load data ──────────────────────────────────────────────────────────────────
import os as _os
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
_DATA_FILE = _os.path.join(_SCRIPT_DIR, 'data', 'league_6130.json')
with open(_DATA_FILE) as f:
    data = json.load(f)

team_names = {int(k): v for k, v in data['team_names'].items()}
games_raw  = data['games']
future_weekends = data.get('future_weekends', [])
season_info     = data.get('season_info', {})

# ── Apply score overrides ──────────────────────────────────────────────────────
_OVERRIDES_FILE = _os.path.join(_SCRIPT_DIR, 'data', 'overrides.json')
if _os.path.exists(_OVERRIDES_FILE):
    with open(_OVERRIDES_FILE) as _f:
        _overrides = json.load(_f)
    for _g in games_raw:
        if _g['id'] in _overrides:
            _ov = _overrides[_g['id']]
            if 'home_score'     in _ov: _g['attributes']['home_score']     = _ov['home_score']
            if 'visiting_score' in _ov: _g['attributes']['visiting_score'] = _ov['visiting_score']
            _g['attributes']['_override'] = True

# Last updated timestamp (written by update_league.py)
_raw_ts = data.get('last_updated', '')
if _raw_ts:
    try:
        _ts_dt = datetime.fromisoformat(_raw_ts)
        LAST_UPDATED = _ts_dt.strftime('%-I:%M %p on %A, %B %-d, %Y')
    except Exception:
        LAST_UPDATED = _raw_ts
else:
    # Fall back to file modification time
    import os as _os2
    _mtime = _os2.path.getmtime(_DATA_FILE)
    LAST_UPDATED = datetime.fromtimestamp(_mtime).strftime('%-I:%M %p on %A, %B %-d, %Y')

TODAY = date.today()
DISCO_ID = 14356

# ── Division mapping (by team-code prefix in name) ─────────────────────────────
def get_division(tid):
    name = team_names.get(tid, '')
    code = name.split(' ')[0]          # e.g. "10N1", "10S2", "10W3"
    if 'N' in code: return 'North'
    if 'S' in code: return 'South'
    if 'W' in code: return 'West'
    return 'Unknown'

# Collect ALL team IDs that appear in games (some may be missing from team_names dict)
_ids_from_games = set()
for _g in games_raw:
    _a = _g['attributes']
    if _a.get('hteam_id'): _ids_from_games.add(_a['hteam_id'])
    if _a.get('vteam_id'): _ids_from_games.add(_a['vteam_id'])
# Merge with team_names keys so we have a complete list
ALL_TEAM_IDS = sorted(set(team_names.keys()) | _ids_from_games)

# ── Parse games ───────────────────────────────────────────────────────────────
def parse_game(g):
    a = g['attributes']
    start_str = a['start']
    start_dt  = datetime.fromisoformat(start_str)
    start_d   = start_dt.date()
    hs  = a['home_score']
    vs  = a['visiting_score']
    completed = (hs is not None and vs is not None and start_d <= TODAY)
    is_placeholder = a.get('_placeholder', False)
    return {
        'id'          : g['id'],
        'hid'         : a['hteam_id'],
        'vid'         : a['vteam_id'],
        'hs'          : hs,
        'vs'          : vs,
        'start_dt'    : start_dt,
        'start_d'     : start_d,
        'completed'   : completed,
        'placeholder' : is_placeholder,
        'weekend_label': a.get('_weekend_label', ''),
    }

games = [parse_game(g) for g in games_raw]
games.sort(key=lambda g: g['start_dt'])

# Separate real games (with team IDs) from placeholder games (null team IDs)
real_games        = [g for g in games if g['hid'] is not None and g['vid'] is not None]
placeholder_games = [g for g in games if g['hid'] is None or g['vid'] is None]

completed_games = [g for g in real_games if g['completed']]
upcoming_games  = [g for g in real_games if not g['completed']]

# ── Standings ─────────────────────────────────────────────────────────────────
stats = {tid: {'w':0,'l':0,'t':0,'gf':0,'ga':0,'last3':[]} for tid in ALL_TEAM_IDS}

for g in completed_games:
    h, v, hs, vs = g['hid'], g['vid'], g['hs'], g['vs']
    stats[h]['gf'] += hs; stats[h]['ga'] += vs
    stats[v]['gf'] += vs; stats[v]['ga'] += hs
    if hs > vs:
        stats[h]['w'] += 1; stats[v]['l'] += 1
        stats[h]['last3'].append('W'); stats[v]['last3'].append('L')
    elif vs > hs:
        stats[v]['w'] += 1; stats[h]['l'] += 1
        stats[v]['last3'].append('W'); stats[h]['last3'].append('L')
    else:
        stats[h]['t'] += 1; stats[v]['t'] += 1
        stats[h]['last3'].append('T'); stats[v]['last3'].append('T')

def pts(s): return s['w']*2 + s['t']
def gd(s):  return s['gf'] - s['ga']

def last3_str(s):
    l3 = s['last3'][-3:]
    return ''.join(l3) if l3 else '-'

divisions = {'North':[], 'South':[], 'West':[]}
for tid in ALL_TEAM_IDS:
    div = get_division(tid)
    if div in divisions:
        divisions[div].append(tid)

def sort_key(tid):
    s = stats[tid]
    return (-pts(s), -gd(s), -s['gf'])

for div in divisions:
    divisions[div].sort(key=sort_key)

# Map team IDs to short names
def short_name(tid):
    n = team_names.get(tid, str(tid))
    parts = n.split(' - ')
    return parts[1] if len(parts) > 1 else n

# ── Massey Ratings ─────────────────────────────────────────────────────────────
# Massey method: find rating vector r such that r[i] - r[j] ≈ score_diff(i,j)
# System: M·r = p  where
#   M[i][i] = games played by team i
#   M[i][j] = -(games between i and j)   (i≠j)
# Last row replaced by Σr = 0 to anchor the solution (zero-sum constraint).

def _gauss_solve(M_in, p_in):
    """Solve M·x = p via Gaussian elimination with partial pivoting."""
    n = len(p_in)
    A = [M_in[i][:] + [p_in[i]] for i in range(n)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(A[r][col]))
        A[col], A[pivot_row] = A[pivot_row], A[col]
        if abs(A[col][col]) < 1e-12:
            continue
        piv = A[col][col]
        A[col] = [v / piv for v in A[col]]
        for row in range(n):
            if row == col:
                continue
            f = A[row][col]
            A[row] = [A[row][j] - f * A[col][j] for j in range(n + 1)]
    return [A[i][n] for i in range(n)]


def _find_components(completed_games):
    """Return list of frozensets, each a connected component of teams
    linked by at least one completed game."""
    # Build adjacency from completed games
    adj = defaultdict(set)
    all_t = set()
    for g in completed_games:
        h, v = g['hid'], g['vid']
        adj[h].add(v); adj[v].add(h)
        all_t.add(h); all_t.add(v)
    # BFS/DFS flood-fill
    seen, components = set(), []
    for start in sorted(all_t):
        if start in seen:
            continue
        component, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adj[node] - component)
        seen |= component
        components.append(frozenset(component))
    return components


def _solve_massey(teams, completed_games):
    """Solve Massey for a single connected component; return {tid: rating}."""
    n = len(teams)
    if n < 2:
        return {teams[0]: 0.0} if teams else {}
    idx = {t: i for i, t in enumerate(teams)}
    M   = [[0.0] * n for _ in range(n)]
    p   = [0.0] * n
    for g in completed_games:
        h, v = g['hid'], g['vid']
        if h not in idx or v not in idx:
            continue
        hi, vi = idx[h], idx[v]
        diff = g['hs'] - g['vs']
        M[hi][hi] += 1;  M[vi][vi] += 1
        M[hi][vi] -= 1;  M[vi][hi] -= 1
        p[hi] += diff;   p[vi] -= diff
    # Replace last row with Σr = 0
    M[n - 1] = [1.0] * n
    p[n - 1] = 0.0
    try:
        import numpy as np
        r = np.linalg.solve(np.array(M, dtype=float), np.array(p, dtype=float))
        return {teams[i]: float(r[i]) for i in range(n)}
    except Exception:
        r = _gauss_solve(M, p)
        return {teams[i]: r[i] for i in range(n)}


def compute_massey_ratings(completed_games):
    """Solve Massey per connected component.

    Teams that never share a game chain are in separate components and
    cannot be meaningfully compared — solving them together with a single
    zero-sum anchor would produce a spurious cross-group scale.

    Returns:
        ratings   – {team_id: float}
        components – list of sorted team-id lists, one per component
    """
    comps = _find_components(completed_games)
    ratings = {}
    component_list = []
    for comp in comps:
        teams = sorted(comp)
        r = _solve_massey(teams, completed_games)
        ratings.update(r)
        component_list.append(teams)
    return ratings, component_list


massey, massey_components = compute_massey_ratings(completed_games)

# Per-component ranked lists (best → worst within each component)
massey_ranked_by_component = [
    sorted([(tid, massey.get(tid, 0.0)) for tid in comp], key=lambda x: -x[1])
    for comp in massey_components
]

# Flat ranked list kept for convenience (intra-component use only)
massey_ranked = [pair for comp in massey_ranked_by_component for pair in comp]


def massey_predict(h_id, v_id):
    """Predicted margin (home − away) from Massey ratings, or None."""
    hr = massey.get(h_id)
    vr = massey.get(v_id)
    if hr is None or vr is None:
        return None
    return hr - vr


# Pre-compute Massey predictions for all upcoming games
for g in upcoming_games:
    pgd            = massey_predict(g['hid'], g['vid'])
    g['pred_gd']   = pgd
    g['pred_conf'] = 1.0  # Massey always gives a single, fully-specified prediction
    g['pred_paths']= []   # unused (kept for structural compatibility)

# ── Disco Pickles specific ─────────────────────────────────────────────────────
dp_completed = [g for g in completed_games if DISCO_ID in (g['hid'], g['vid'])]
dp_upcoming  = [g for g in upcoming_games  if DISCO_ID in (g['hid'], g['vid'])]

dp_stats = stats[DISCO_ID]
dp_w, dp_l, dp_t = dp_stats['w'], dp_stats['l'], dp_stats['t']
dp_pts  = pts(dp_stats)
dp_gf   = dp_stats['gf']
dp_ga   = dp_stats['ga']
dp_gd_v = dp_gf - dp_ga

# Win probability from Massey predicted margin (logistic curve).
# Scale 0.4 ≈ 3-goal margin → ~77 % win probability.
def win_prob(pgd, conf=1.0):   # conf kept for call-site compatibility
    if pgd is None: return 0.5
    import math
    return 1 / (1 + math.exp(-pgd * 0.4))

# ── HTML helpers ──────────────────────────────────────────────────────────────
def esc(s): return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def format_date(d):
    return d.strftime('%a %b %-d, %Y')

def last3_html(s):
    l3 = s['last3'][-3:]
    spans = []
    for r in l3:
        cls = {'W':'badge-w','L':'badge-l','T':'badge-t'}[r]
        spans.append(f'<span class="badge {cls}">{r}</span>')
    inner = ''.join(spans) if spans else '<span class="badge badge-none">-</span>'
    return f'<span class="badge-row">{inner}</span>'

def record_html(tid):
    s = stats[tid]
    return f"{s['w']}-{s['l']}-{s['t']}"

def gd_sign(v):
    if v > 0: return f'+{v}'
    return str(v)

# ── Build standings rows ───────────────────────────────────────────────────────
def build_standings_div(div_name, team_ids):
    rows = ''
    for rank, tid in enumerate(team_ids, 1):
        s    = stats[tid]
        name = team_names.get(tid, str(tid))
        short= name.split(' - ')[1] if ' - ' in name else name
        p    = pts(s)
        g    = gd(s)
        is_dp= (tid == DISCO_ID)
        is_leader = (rank == 1)
        row_cls = 'dp-row' if is_dp else ''
        leader_badge = ' 🥇' if is_leader else ''
        gd_cls = 'pos-gd' if g > 0 else ('neg-gd' if g < 0 else '')
        rows += f'''
        <tr class="{row_cls}">
          <td class="rank-cell">{rank}</td>
          <td class="team-cell">{esc(short)}{leader_badge}</td>
          <td>{s["w"]}</td><td>{s["l"]}</td><td>{s["t"]}</td>
          <td class="pts-cell">{p}</td>
          <td>{s["gf"]}</td><td>{s["ga"]}</td>
          <td class="{gd_cls}">{gd_sign(g)}</td>
          <td>{last3_html(s)}</td>
        </tr>'''
    return f'''
    <div class="standings-div">
      <h3 class="div-header">{div_name} Division</h3>
      <div class="standings-table-wrap">
        <table class="standings-table">
          <thead>
            <tr>
              <th>#</th><th>Team</th><th>W</th><th>L</th><th>T</th>
              <th>Pts</th><th>GF</th><th>GA</th><th>GD</th><th>Last 3</th>
            </tr>
          </thead>
          <tbody>{rows}
          </tbody>
        </table>
      </div>
    </div>'''

# ── Build game results ─────────────────────────────────────────────────────────
def build_results_tab():
    # Separate real games into upcoming (future dates) vs completed/past
    # Past games without scores show as "Score Pending" in the completed section
    upcoming_by_date  = defaultdict(list)
    completed_by_date = defaultdict(list)
    for g in real_games:
        if g['start_d'] >= TODAY:
            upcoming_by_date[g['start_d']].append(g)
        else:
            completed_by_date[g['start_d']].append(g)

    def game_rows_html(date_dict, date_order):
        html = ''
        for d in date_order:
            html += f'<div class="date-group"><div class="date-header">{format_date(d)}</div>'
            for g in date_dict[d]:
                h_name = short_name(g['hid'])
                v_name = short_name(g['vid'])
                is_dp  = (DISCO_ID in (g['hid'], g['vid']))
                row_cls= 'game-row dp-game' if is_dp else 'game-row'

                if g['completed']:
                    hs, vs = g['hs'], g['vs']
                    if hs > vs:   h_res, v_res = 'res-w','res-l'
                    elif vs > hs: h_res, v_res = 'res-l','res-w'
                    else:         h_res = v_res = 'res-t'
                    score_html = f'<span class="{h_res}">{hs}</span> – <span class="{v_res}">{vs}</span>'
                    outcome_label = ''
                    if is_dp:
                        dp_won = (g['hid']==DISCO_ID and hs>vs) or (g['vid']==DISCO_ID and vs>hs)
                        dp_tie = hs == vs
                        outcome_label = '<span class="outcome-badge win-badge">DP Win</span>' if dp_won else \
                                        ('<span class="outcome-badge tie-badge">Tie</span>' if dp_tie else \
                                         '<span class="outcome-badge loss-badge">DP Loss</span>')
                elif g['start_d'] < TODAY:
                    # Past game, score not yet entered in system
                    score_html = '<span class="score-pending">Score Pending</span>'
                    outcome_label = ''
                else:
                    pgd = g.get('pred_gd')
                    pred_str = (f'<span class="pred-score">Massey: {pgd:+.1f}</span>'
                                if pgd is not None
                                else '<span class="pred-score">No prediction</span>')
                    score_html = f'<span class="upcoming-tbd">TBD</span> {pred_str}'
                    outcome_label = ''

                html += f'''
                <div class="{row_cls}">
                  <div class="game-teams">
                    <span class="home-team">{esc(h_name)}</span>
                    <span class="vs-sep">vs</span>
                    <span class="away-team">{esc(v_name)}</span>
                  </div>
                  <div class="game-score">{score_html} {outcome_label}</div>
                  <div class="game-time">{g["start_dt"].strftime("%-I:%M %p")}</div>
                </div>'''
            html += '</div>'
        return html

    html = '<div class="results-container">'

    # ── COMPLETED (newest first) ──────────────────────────────────────────────
    if completed_by_date:
        html += '<div class="results-section-hdr">✅ Completed Games <span class="sort-indicator">↓ Newest First</span></div>'
        html += game_rows_html(completed_by_date, sorted(completed_by_date.keys(), reverse=True))

    # ── UPCOMING (soonest first) ──────────────────────────────────────────────
    if upcoming_by_date:
        html += '<div class="results-section-hdr">📅 Upcoming Games &amp; Predictions</div>'
        html += game_rows_html(upcoming_by_date, sorted(upcoming_by_date.keys()))

    # ── TBD placeholder weekends ──────────────────────────────────────────────
    placeholder_by_weekend = defaultdict(list)
    for g in placeholder_games:
        lbl = g.get('weekend_label', 'TBD Weekend')
        placeholder_by_weekend[lbl].append(g)
    for wk_label in sorted(placeholder_by_weekend.keys()):
        wk_games = placeholder_by_weekend[wk_label]
        html += f'''<div class="date-group">
          <div class="date-header">{esc(wk_label)}</div>
          <div class="game-row" style="justify-content:center; color:var(--text-muted); font-style:italic; padding:1rem;">
            Matchups TBD &mdash; {len(wk_games)} games
          </div>
        </div>'''

    html += '</div>'
    return html

# ── Rolling prediction accuracy ───────────────────────────────────────────────
def build_prediction_accuracy():
    """For each completed week (starting week 2), compute rolling Massey from
    all prior weeks and score its predictions against actual outcomes."""
    from collections import defaultdict

    # Group completed games by date
    games_by_date = defaultdict(list)
    for g in completed_games:
        games_by_date[g['start_d']].append(g)
    weeks = sorted(games_by_date.keys())

    if len(weeks) < 2:
        return ''   # need at least 2 weeks to show anything

    overall_correct = overall_total = 0
    overall_mae = 0.0

    week_sections = ''
    prior_games = []

    for week_idx, week_date in enumerate(weeks):
        week_games = games_by_date[week_date]

        if week_idx == 0:
            # Week 1: no prior data — nothing to show, just accumulate
            prior_games.extend(week_games)
            continue

        # Compute Massey from all games before this week
        rolling_ratings, _ = compute_massey_ratings(prior_games)

        week_correct = week_total = 0
        week_mae = 0.0
        rows_html = ''

        for g in sorted(week_games, key=lambda x: x['start_dt']):
            h_name = short_name(g['hid'])
            v_name = short_name(g['vid'])
            hs, vs = g['hs'], g['vs']
            actual_gd = hs - vs

            hr = rolling_ratings.get(g['hid'])
            vr = rolling_ratings.get(g['vid'])

            if hr is not None and vr is not None:
                pred_gd = hr - vr
                error   = abs(pred_gd - actual_gd)
                week_mae += error
                week_total += 1

                pred_winner_home = pred_gd > 0
                actual_winner_home = hs > vs
                is_tossup = abs(pred_gd) <= 1.0

                if hs == vs:
                    correct = abs(pred_gd) <= 1.0
                elif is_tossup:
                    correct = True   # toss-up is never "wrong"
                else:
                    correct = (pred_winner_home == actual_winner_home)

                if hs == vs:
                    result_icon = '🟡'
                elif correct and not is_tossup:
                    result_icon = '✅'
                elif is_tossup:
                    result_icon = '🟡'
                else:
                    result_icon = '❌'

                if hs == vs:
                    actual_str = f'Tie {hs}–{vs}'
                elif hs > vs:
                    actual_str = f'{hs}–{vs} {h_name}'
                else:
                    actual_str = f'{vs}–{hs} {v_name}'

                pred_str = f'{pred_gd:+.1f}'
                err_str  = f'±{error:.1f}'

                if correct and not is_tossup:
                    week_correct += 1
                    row_cls = 'acc-row acc-correct'
                elif is_tossup:
                    row_cls = 'acc-row acc-tossup'
                else:
                    row_cls = 'acc-row acc-wrong'
            else:
                pred_str   = '—'
                err_str    = '—'
                result_icon= '⬜'
                actual_str = f'{hs}–{vs}'
                row_cls    = 'acc-row acc-nodata'

            is_dp = DISCO_ID in (g['hid'], g['vid'])
            dp_cls = ' acc-dp' if is_dp else ''

            rows_html += f'''
            <tr class="{row_cls}{dp_cls}">
              <td class="acc-matchup"><span class="acc-home">{esc(h_name)}</span><span class="acc-vs">vs</span><span class="acc-away">{esc(v_name)}</span></td>
              <td class="acc-pred">{pred_str}</td>
              <td class="acc-actual">{esc(actual_str)}</td>
              <td class="acc-err">{err_str}</td>
              <td class="acc-icon">{result_icon}</td>
            </tr>'''

        overall_correct += week_correct
        overall_total   += week_total
        if week_total:
            overall_mae += week_mae
            avg_mae = week_mae / week_total
            pct = int(week_correct / week_total * 100)
            summary_str = f'{week_correct}/{week_total} correct ({pct}%) · avg margin error {avg_mae:.1f}'
            summary_cls = 'acc-good' if pct >= 70 else ('acc-mid' if pct >= 50 else 'acc-poor')
        else:
            summary_str = 'No predictions available'
            summary_cls = 'acc-mid'

        week_label = week_date.strftime('Week of %b %-d')
        week_sections += f'''
        <div class="acc-week">
          <div class="acc-week-header">
            <span class="acc-week-label">{week_label}</span>
            <span class="acc-summary {summary_cls}">{summary_str}</span>
          </div>
          <div class="acc-table-wrap">
            <table class="acc-table">
              <thead><tr>
                <th>Matchup</th><th>Predicted GD</th><th>Actual Result</th><th>Margin Err</th><th></th>
              </tr></thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
        </div>'''

        prior_games.extend(week_games)

    if overall_total == 0:
        return ''

    overall_pct = int(overall_correct / overall_total * 100)
    overall_avg_mae = overall_mae / overall_total
    overall_cls = 'acc-good' if overall_pct >= 70 else ('acc-mid' if overall_pct >= 50 else 'acc-poor')

    return f'''
    <div class="acc-container">
      <div class="acc-header">
        <h3 class="acc-title">📈 Prediction Accuracy — Season to Date</h3>
        <span class="acc-summary {overall_cls}">{overall_correct}/{overall_total} correct ({overall_pct}%) · avg margin error {overall_avg_mae:.1f} goals</span>
      </div>
      <p class="acc-explainer">Each week's predictions were generated using Massey ratings built from all <em>prior</em> weeks only — no future data.</p>
      {week_sections}
    </div>'''


# ── Build predictions tab ──────────────────────────────────────────────────────
def build_predictions_tab():
    html = '<div class="predictions-container">'

    # ── Massey Power Rankings leaderboard (one panel per connected component) ────
    # West never plays North/South, so they form separate rating pools.
    # Ratings are only comparable within a component.

    def _comp_label(comp_teams):
        """Derive a human-readable label from the divisions represented."""
        divs = set()
        for tid in comp_teams:
            divs.add(get_division(tid))
        return ' / '.join(sorted(divs)) + ' Division' + ('s' if len(divs) > 1 else '')

    comp_panels_html = ''
    for comp_ranked in massey_ranked_by_component:
        max_abs = max((abs(r) for _, r in comp_ranked), default=1.0) or 1.0
        label   = _comp_label([tid for tid, _ in comp_ranked])
        note    = ('Ratings within this group are not comparable to other groups — '
                   'no cross-group games have been played.')
        rows_html = ''
        for rank, (tid, rating) in enumerate(comp_ranked, 1):
            name      = short_name(tid)
            is_dp     = (tid == DISCO_ID)
            sign      = '+' if rating >= 0 else ''
            bar_pct   = int(abs(rating) / max_abs * 100)
            bar_color = 'var(--win-fg)' if rating >= 0 else 'var(--loss-fg)'
            rat_cls   = 'pos-rating' if rating >= 0 else 'neg-rating'
            row_cls   = 'massey-row dp-massey-row' if is_dp else 'massey-row'
            gp        = sum(1 for g in completed_games + playoff_massey_games if tid in (g['hid'], g['vid']))
            rows_html += f'''
            <div class="{row_cls}">
              <span class="massey-rank">#{rank}</span>
              <span class="massey-team">{esc(name)}{' 🥒' if is_dp else ''}</span>
              <span class="massey-gp">{gp}&#8239;GP</span>
              <div class="massey-bar-wrap">
                <div class="massey-bar" style="width:{bar_pct}%;background:{bar_color}"></div>
              </div>
              <span class="massey-rating {rat_cls}">{sign}{rating:.2f}</span>
            </div>'''
        comp_panels_html += f'''
        <div class="massey-section">
          <div class="massey-header">
            <h3 class="massey-title">📊 Massey Rankings — {esc(label)}</h3>
            <span class="massey-explainer">Rating = expected goal margin vs. pool average&ensp;·&ensp;{len(comp_ranked)} teams</span>
          </div>
          <div class="massey-grid">{rows_html}
          </div>
        </div>'''

    html += f'''
    <div class="massey-panels">
      {comp_panels_html}
    </div>
    <p class="massey-isolation-note">
      ⚠️ West plays no games against North or South this season, so West ratings and
      North/South ratings are on independent scales and cannot be directly compared.
    </p>'''

    html += build_prediction_accuracy()

    # ── Per-game prediction cards ───────────────────────────────────────────────
    if not upcoming_games:
        html += '<p class="no-data">No upcoming games found.</p></div>'
        return html

    html += '<div class="predictions-grid">'

    for g in upcoming_games:
        h_name   = short_name(g['hid'])
        v_name   = short_name(g['vid'])
        is_dp    = (DISCO_ID in (g['hid'], g['vid']))
        pgd      = g.get('pred_gd')
        h_rating = massey.get(g['hid'])
        v_rating = massey.get(g['vid'])

        if pgd is None:
            card_cls       = 'pred-card no-pred'
            prediction_html = '<div class="pred-result"><p class="no-data">Insufficient game data.</p></div>'
        else:
            if pgd > 1:
                card_cls = 'pred-card home-favored'
                winner = h_name; margin = pgd
            elif pgd < -1:
                card_cls = 'pred-card away-favored'
                winner = v_name; margin = abs(pgd)
            else:
                card_cls = 'pred-card toss-up'
                winner = 'Toss-up'; margin = abs(pgd)

            if is_dp:
                card_cls += ' dp-pred-card'

            wp     = win_prob(pgd)
            wp_pct = int(wp * 100)

            dp_flag = (
                is_dp and (
                    (g['hid'] == DISCO_ID and pgd > 0) or
                    (g['vid'] == DISCO_ID and pgd < 0)
                )
            )
            margin_str = (f'by ~{margin:.1f}' if winner != 'Toss-up'
                          else f'(±{margin:.1f})')

            def rat_html(r):
                if r is None: return '<span class="text-muted">—</span>'
                cls = 'pos-rating' if r >= 0 else 'neg-rating'
                return f'<span class="{cls}">{r:+.2f}</span>'

            prediction_html = f'''
            <div class="pred-result">
              <div class="pred-ratings">
                <div class="pred-rating-row">
                  <span class="pred-rating-team">{esc(h_name)}</span>
                  <span class="pred-rating-label">Massey</span>
                  {rat_html(h_rating)}
                </div>
                <div class="pred-rating-row">
                  <span class="pred-rating-team">{esc(v_name)}</span>
                  <span class="pred-rating-label">Massey</span>
                  {rat_html(v_rating)}
                </div>
              </div>
              <div class="pred-winner">{'🏒 ' if dp_flag else ''}Predicted: <strong>{esc(winner)}</strong> {margin_str}</div>
              <div class="pred-gd-val">Expected margin: {pgd:+.2f} goals</div>
              <div class="conf-bar-wrap">
                <span class="conf-label">Home Win %</span>
                <div class="conf-bar"><div class="conf-fill" style="width:{wp_pct}%"></div></div>
                <span class="conf-pct">{wp_pct}%</span>
              </div>
            </div>'''

        dp_badge = '<span class="dp-badge">🥒 DP Game</span>' if is_dp else ''

        html += f'''
        <div class="{card_cls}">
          <div class="pred-card-header">
            <div class="pred-date">{format_date(g["start_d"])} · {g["start_dt"].strftime("%-I:%M %p")}</div>
            {dp_badge}
          </div>
          <div class="pred-matchup">
            <span class="pred-home">{esc(h_name)}</span>
            <span class="pred-vs">vs</span>
            <span class="pred-away">{esc(v_name)}</span>
          </div>
          {prediction_html}
        </div>'''

    html += '</div>'   # predictions-grid
    html += '</div>'   # predictions-container
    return html

# ── Build Team Spotlight (Disco Pickles) ──────────────────────────────────────
def build_spotlight_tab():
    # Game-by-game
    game_rows = ''
    for g in dp_completed:
        is_home = (g['hid'] == DISCO_ID)
        opp_id  = g['vid'] if is_home else g['hid']
        opp     = short_name(opp_id)
        dp_score = g['hs'] if is_home else g['vs']
        opp_score= g['vs'] if is_home else g['hs']
        ha = 'Home' if is_home else 'Away'
        if dp_score > opp_score:
            res = '<span class="res-chip win-chip">W</span>'; row_cls='win-row'
        elif dp_score < opp_score:
            res = '<span class="res-chip loss-chip">L</span>'; row_cls='loss-row'
        else:
            res = '<span class="res-chip tie-chip">T</span>'; row_cls='tie-row'
        game_rows += f'''
        <tr class="{row_cls}">
          <td>{format_date(g["start_d"])}</td>
          <td>{ha}</td>
          <td>{esc(opp)}</td>
          <td class="score-cell">{dp_score} – {opp_score}</td>
          <td>{res}</td>
        </tr>'''

    # Upcoming schedule
    sched_rows = ''
    for g in dp_upcoming:
        is_home = (g['hid'] == DISCO_ID)
        opp_id  = g['vid'] if is_home else g['hid']
        opp     = short_name(opp_id)
        ha = 'Home' if is_home else 'Away'
        pgd   = g.get('pred_gd')
        conf  = g.get('pred_conf', 0)
        if pgd is not None:
            dp_pgd = pgd if is_home else -pgd
            wp     = win_prob(dp_pgd, conf)
            prob_str = f'{int(wp*100)}% win prob'
            pred_cls = 'pred-win' if dp_pgd > 1 else ('pred-loss' if dp_pgd < -1 else 'pred-toss')
            pred_cell = f'<span class="{pred_cls}">{dp_pgd:+.1f} GD · {prob_str}</span>'
        else:
            pred_cell = '<span class="pred-none">No data</span>'
        sched_rows += f'''
        <tr>
          <td>{format_date(g["start_d"])}</td>
          <td>{ha}</td>
          <td>{esc(opp)}</td>
          <td>{pred_cell}</td>
        </tr>'''

    # Season scenario
    remaining = len(dp_upcoming)
    best  = f"{dp_w+remaining}-{dp_l}-{dp_t}"
    worst = f"{dp_w}-{dp_l+remaining}-{dp_t}"
    cur   = f"{dp_w}-{dp_l}-{dp_t}"

    html = f'''
    <div class="spotlight-container">
      <div class="stats-cards">
        <div class="stat-card">
          <div class="stat-val">{dp_w}-{dp_l}-{dp_t}</div>
          <div class="stat-lbl">Record</div>
        </div>
        <div class="stat-card accent">
          <div class="stat-val">{dp_pts}</div>
          <div class="stat-lbl">Points</div>
        </div>
        <div class="stat-card">
          <div class="stat-val">{dp_gf}</div>
          <div class="stat-lbl">Goals For</div>
        </div>
        <div class="stat-card">
          <div class="stat-val">{dp_ga}</div>
          <div class="stat-lbl">Goals Against</div>
        </div>
        <div class="stat-card {'pos-card' if dp_gd_v >= 0 else 'neg-card'}">
          <div class="stat-val">{gd_sign(dp_gd_v)}</div>
          <div class="stat-lbl">Goal Differential</div>
        </div>
        <div class="stat-card">
          <div class="stat-val">{remaining}</div>
          <div class="stat-lbl">Games Remaining</div>
        </div>
      </div>

      {'<h3 class="section-title">Completed Games</h3><div class="detail-table-wrap"><table class="detail-table"><thead><tr><th>Date</th><th>H/A</th><th>Opponent</th><th>Score</th><th>Result</th></tr></thead><tbody>' + game_rows + '</tbody></table></div>' if game_rows else '<p class="no-data">No completed games yet.</p>'}

      {'<h3 class="section-title">Remaining Schedule</h3><div class="detail-table-wrap"><table class="detail-table"><thead><tr><th>Date</th><th>H/A</th><th>Opponent</th><th>Prediction</th></tr></thead><tbody>' + sched_rows + '</tbody></table></div>' if sched_rows else '<p class="no-data">No remaining games.</p>'}

      <h3 class="section-title">Season Outlook</h3>
      <div class="outlook-cards">
        <div class="outlook-card best">
          <div class="outlook-label">Best Case</div>
          <div class="outlook-rec">{best}</div>
          <div class="outlook-pts">{(dp_w+remaining)*2+dp_t} pts</div>
        </div>
        <div class="outlook-card current">
          <div class="outlook-label">Current</div>
          <div class="outlook-rec">{cur}</div>
          <div class="outlook-pts">{dp_pts} pts</div>
        </div>
        <div class="outlook-card worst">
          <div class="outlook-label">Worst Case</div>
          <div class="outlook-rec">{worst}</div>
          <div class="outlook-pts">{dp_pts} pts</div>
        </div>
      </div>
    </div>'''
    return html

# ── Hero section (Disco Pickles upcoming previews) ────────────────────────────
def build_hero_upcoming():
    if not dp_upcoming:
        return '<p style="color:rgba(255,255,255,0.8)">No upcoming games – season complete!</p>'
    items = ''
    for g in dp_upcoming:                          # show ALL remaining games
        is_home = (g['hid'] == DISCO_ID)
        opp     = short_name(g['vid'] if is_home else g['hid'])
        ha      = 'vs' if is_home else '@'
        pgd     = g.get('pred_gd')
        if pgd is not None:
            dp_pgd   = pgd if is_home else -pgd
            wp       = win_prob(dp_pgd)
            pred_str = f'{int(wp*100)}% win'
            pred_cls = ('hero-pred-win'  if dp_pgd > 1  else
                        'hero-pred-loss' if dp_pgd < -1 else 'hero-pred-toss')
        else:
            pred_str = 'No pred'; pred_cls = 'hero-pred-toss'
        items += f'''
        <div class="hero-game">
          <div class="hero-game-date">{g["start_dt"].strftime("%b %-d")}</div>
          <div class="hero-game-opp">{ha} {esc(opp)}</div>
          <div class="hero-game-pred {pred_cls}">{pred_str}</div>
        </div>'''
    return f'<div class="hero-upcoming">{items}</div>'

# ── Build Season Schedule tab ─────────────────────────────────────────────────
def build_schedule_tab():
    html = '<div class="schedule-container">'

    if season_info:
        html += f'''
        <div class="schedule-overview">
          <div class="stat-card"><div class="stat-val">{season_info.get("total_weeks", "?")}</div><div class="stat-lbl">Total Weeks</div></div>
          <div class="stat-card"><div class="stat-val">{season_info.get("teams", "?")}</div><div class="stat-lbl">Teams</div></div>
          <div class="stat-card"><div class="stat-val">{season_info.get("games_per_week", "?")}</div><div class="stat-lbl">Games / Week</div></div>
          <div class="stat-card"><div class="stat-val">{season_info.get("games_per_team", "?")}</div><div class="stat-lbl">Games / Team</div></div>
        </div>'''

    # Build week-by-week schedule from real games + future_weekends
    # First, figure out which weeks have been played from real games
    weeks_from_games = defaultdict(list)
    for g in real_games:
        wk_date = g['start_d']
        weeks_from_games[wk_date].append(g)

    played_dates = sorted(weeks_from_games.keys())

    html += '<div class="schedule-weeks">'

    # Show played weeks
    week_num = 1
    for d in played_dates:
        wk_games = weeks_from_games[d]
        n_completed = sum(1 for g in wk_games if g['completed'])
        n_upcoming = len(wk_games) - n_completed
        if n_completed == len(wk_games):
            status_html = '<span class="sched-status sched-played">Played</span>'
        elif n_completed > 0:
            status_html = '<span class="sched-status sched-upcoming">In Progress</span>'
        else:
            status_html = '<span class="sched-status sched-upcoming">Upcoming</span>'

        html += f'''
        <div class="sched-week">
          <div class="sched-week-num">Week {week_num}</div>
          <div class="sched-week-date">{format_date(d)}</div>
          <div class="sched-week-info">{len(wk_games)} games</div>
          {status_html}
        </div>'''
        week_num += 1

    # Show future weekends from metadata
    for fw in future_weekends:
        fw_date = fw.get('date', '')
        fw_label = fw.get('label', '')
        fw_status = fw.get('status', 'scheduled')
        fw_week = fw.get('week')
        fw_matchups = fw.get('matchups_published', False)

        # Skip weekends that are already covered by real games
        try:
            from datetime import date as _date_cls
            fw_d = _date_cls.fromisoformat(fw_date)
            if fw_d in weeks_from_games:
                continue
        except (ValueError, TypeError):
            pass

        if fw_status == 'off':
            status_html = '<span class="sched-status sched-off">OFF</span>'
            info_text = 'No games'
        elif fw_matchups:
            status_html = '<span class="sched-status sched-upcoming">Upcoming</span>'
            info_text = '8 games'
        else:
            status_html = '<span class="sched-status sched-tbd">TBD</span>'
            info_text = 'Matchups TBD &mdash; 8 games'

        week_label = f'Week {fw_week}' if fw_week else fw_label
        try:
            from datetime import date as _date_cls
            date_str = format_date(_date_cls.fromisoformat(fw_date))
        except (ValueError, TypeError):
            date_str = fw_date

        html += f'''
        <div class="sched-week {'sched-week-off' if fw_status == 'off' else ''}">
          <div class="sched-week-num">{esc(week_label)}</div>
          <div class="sched-week-date">{date_str}</div>
          <div class="sched-week-info">{info_text}</div>
          {status_html}
        </div>'''

    html += '</div></div>'
    return html

# ── Weekly rotating pickle fact (advances each ISO week) ─────────────────────
_PICKLE_FACTS = [
    # 0 — Pickle Juice Game (Eagles 41-14 Cowboys, Sep 3 2000)
    'On September 3, 2000, the Philadelphia Eagles crushed the Dallas Cowboys 41–14 in 109°F heat — and their secret weapon was pickle juice. Players chugged it on the sideline to stop leg cramps, it worked, and the game is now literally known as <em>"The Pickle Juice Game"</em> in NFL history. Scientists later confirmed that pickle brine stops muscle cramps in about 85 seconds, faster than water or any sports drink. So if a Disco Pickle ever gets a cramp on the ice, you know exactly what to reach for.',
    # 1 — Disco Demolition Night (Jul 12 1979)
    'On July 12, 1979, the Chicago White Sox held <em>"Disco Demolition Night"</em> at Comiskey Park. Fans paid 98¢ admission if they brought a disco record to blow up on the field between doubleheader games. Over 50,000 people showed up to a stadium that held 44,000. The explosion destroyed the field, fans stormed the diamond, and the White Sox had to forfeit Game 2. It\'s widely credited as the night disco died — one exploded record at a time.',
    # 2 — Columbus and pickles
    'Christopher Columbus packed barrels of pickles on his 1492 voyage to the New World, specifically to prevent scurvy among his sailors. He also reportedly grew cucumbers on Caribbean islands mid-voyage to keep the supply stocked. Pickles are older than America — and apparently helped discover it.',
    # 3 — "Disco" etymology
    'The word <em>"disco"</em> comes from the French <em>"discothèque"</em> — which literally meant a library of phonograph records (<em>disques</em>). The first discothèques were just clubs that played recorded music instead of hiring live bands. So disco started as basically a playlist app, about 70 years before Spotify.',
    # 4 — Ancient pickles (Tigris Valley, ~2030 BC)
    'Humans have been pickling cucumbers for over 4,000 years. Some of the earliest evidence dates to around 2030 BC in the Tigris Valley of ancient Mesopotamia. Aristotle praised pickles for their healing properties, and Cleopatra reportedly credited them as a beauty secret. When archaeologists dig up snacks older than most civilizations, it really puts your pre-game nutrition into perspective.',
    # 5 — American pickle consumption stats
    'Americans eat roughly 9 pounds of pickles per person per year, adding up to about 2.5 billion pounds nationally. The United States also observes <em>National Pickle Day</em> every November 14th — which is absolutely a real holiday. Presumably not a federal one, but it should be.',
    # 6 — Word "pickle" etymology
    'The word <em>"pickle"</em> comes from the Dutch or Low German word <em>"pekel,"</em> meaning brine or salt water. Dutch and German traders brought both the word and the technique to England in the Middle Ages. Before that, English speakers just called them salted cucumbers — which is accurate, but considerably less fun to say.',
    # 7 — Saturday Night Fever soundtrack
    'The <em>Saturday Night Fever</em> soundtrack sold over 40 million copies and held the record as the best-selling movie soundtrack of all time for decades. The Bee Gees contributed seven songs — including "Stayin\' Alive" and "Night Fever" — after being approached just months before the film\'s 1977 release. John Travolta practiced his disco moves for months, reportedly dancing up to six hours a day. The white polyester suit he wore in the film later sold at auction for $145,000.',
]
# Week 19 of 2026 is when this rotation started (Pickle Juice Game = index 0)
_FACT_BASE_WEEK = 19
_pickle_fact = _PICKLE_FACTS[(date.today().isocalendar()[1] - _FACT_BASE_WEEK) % len(_PICKLE_FACTS)]

# ── Playoffs (10U West ADV bracket) ────────────────────────────────────────────
# Bracket facts hardcoded from phhl.org/spring26playoffs (not in the DaySmart feed).
# Each team gets a custom emoji "medallion" + signature color.
WEST_TEAMS = {
    14356: {'seed': 1, 'emoji': '🥒',   'color': '#4D7C0F'},   # Disco Pickles
    14357: {'seed': 2, 'emoji': '🐻‍❄️', 'color': '#0EA5E9'},   # Polar Predators
    14355: {'seed': 3, 'emoji': '💡',   'color': '#F59E0B'},   # Lamp Lighters
    14360: {'seed': 4, 'emoji': '🤠',   'color': '#475569'},   # Ice Outlaws
    14359: {'seed': 5, 'emoji': '🌀',   'color': '#7C3AED'},   # Carolina Havoc
    14358: {'seed': 6, 'emoji': '🎺',   'color': '#B45309'},   # Brass Bonanza
}
# Bracket rounds. 'winner' = team id once known (None = TBD); 'sa'/'sb' = scores for a/b.
# Playoff games aren't in the DaySmart feed, so results are entered here from phhl.org
# (or directly from the user when the league hasn't posted yet).
WEST_BRACKET = [
    {'round': 'Wild Card',    'date': 'Thu Jun 4', 'date_obj': date(2026, 6, 4), 'games': [
        {'a': 14360, 'b': 14359, 'when': '5:15 PM', 'loc': 'Invisalign Arena',      'winner': 14359, 'sa': 1, 'sb': 4},
        {'a': 14355, 'b': 14358, 'when': '5:30 PM', 'loc': 'Invisalign Arena',      'winner': 14355, 'sa': 8, 'sb': 6},
    ]},
    {'round': 'Semifinals',   'date': 'Sat Jun 6', 'date_obj': date(2026, 6, 6), 'games': [
        {'a': 14356, 'b': 14359, 'when': '4:00 PM', 'loc': 'Invisalign Arena',       'winner': 14356, 'sa': 4, 'sb': 2},
        {'a': 14357, 'b': 14355, 'when': '6:15 PM', 'loc': 'Polar Ice Wake Forest',  'winner': 14357, 'sa': 5, 'sb': 4, 'ot': True},
    ]},
    {'round': 'Championship', 'date': 'Sun Jun 7', 'date_obj': date(2026, 6, 7), 'games': [
        {'a': 14356, 'b': 14357, 'when': '11:15 AM', 'loc': 'Invisalign Arena', 'winner': 14356, 'sa': 5, 'sb': 1},
    ]},
]
WEST_CONSOLATION = {'round': 'Consolation', 'date': 'Sun Jun 7', 'date_obj': date(2026, 6, 7),
    'game': {'a': 14360, 'b': 14358, 'when': '1:30 PM', 'loc': 'Polar Ice Wake Forest', 'winner': None}}
SEMI_DATE = date(2026, 6, 6)

# Auto-fill bracket results from playoff games fetched by update_league.py
# (data['playoff_games'], under the team's playoffEvents relationship). Only fills
# games NOT already decided manually — so hand-entered results win (e.g. an OT flag
# the feed missed). Matched by the unordered pair of team IDs.
_pf_results = {}
for _pg in data.get('playoff_games', []):
    _h, _v = _pg.get('hteam_id'), _pg.get('vteam_id')
    _hs, _vs = _pg.get('home_score'), _pg.get('visiting_score')
    if _h and _v and _hs is not None and _vs is not None:
        _pf_results[frozenset((_h, _v))] = (_h, int(_hs), int(_vs), bool(_pg.get('is_overtime')))
for _r in WEST_BRACKET:
    for _g in _r['games']:
        if _g.get('a') and _g.get('b') and _g.get('winner') is None:
            _res = _pf_results.get(frozenset((_g['a'], _g['b'])))
            if _res:
                _h, _hs, _vs, _ot = _res
                _g['sa'], _g['sb'] = (_hs, _vs) if _g['a'] == _h else (_vs, _hs)
                _g['winner'] = _g['a'] if _g['sa'] > _g['sb'] else _g['b']
                if _ot:
                    _g['ot'] = True

# Fold decided playoff games into the Massey ratings (they aren't in the regular
# DaySmart feed). These refine the power rankings + playoff odds but are deliberately
# kept OUT of regular-season standings and the rolling prediction-accuracy backtest.
playoff_massey_games = [
    {'hid': g['a'], 'vid': g['b'], 'hs': g['sa'], 'vs': g['sb']}
    for r in WEST_BRACKET for g in r['games']
    if g.get('winner') and g.get('sa') is not None
]
massey, massey_components = compute_massey_ratings(completed_games + playoff_massey_games)
massey_ranked_by_component = [
    sorted([(tid, massey.get(tid, 0.0)) for tid in comp], key=lambda x: -x[1])
    for comp in massey_components
]
massey_ranked = [pair for comp in massey_ranked_by_component for pair in comp]

# Did the Disco Pickles win the championship? (drives the celebration + confetti)
_champ_game = next(r['games'][0] for r in WEST_BRACKET if r['round'] == 'Championship')
disco_champion = (_champ_game.get('winner') == DISCO_ID)


def _date_prefix(d):
    """'Today' / 'Tomorrow' / 'Sat Jun 7' relative to TODAY."""
    delta = (d - TODAY).days
    if delta == 0: return 'Today'
    if delta == 1: return 'Tomorrow'
    return d.strftime('%a %b %-d')


def disco_next_game():
    """Disco's next undecided playoff game as (round, game), or (None, None)."""
    for rnd in WEST_BRACKET:
        for g in rnd['games']:
            if DISCO_ID in (g.get('a'), g.get('b')) and not g.get('winner'):
                return rnd, g
    return None, None


def team_medal(tid, size='md'):
    """Colored emoji medallion for a team."""
    m = WEST_TEAMS[tid]
    return (f'<span class="po-medal po-medal-{size}" style="background:{m["color"]}" '
            f'title="{esc(short_name(tid))}">{m["emoji"]}</span>')


def disco_h2h(opp):
    """Disco Pickles' season head-to-head vs opp: (w, l, t, [(date, ds, os)…])."""
    games = []
    for g in completed_games:
        if DISCO_ID in (g['hid'], g['vid']) and opp in (g['hid'], g['vid']) and g['hid'] != g['vid']:
            ds  = g['hs'] if g['hid'] == DISCO_ID else g['vs']
            oss = g['vs'] if g['hid'] == DISCO_ID else g['hs']
            games.append((g['start_d'], ds, oss))
    games.sort()
    w = sum(1 for _, a, b in games if a > b)
    l = sum(1 for _, a, b in games if a < b)
    t = sum(1 for _, a, b in games if a == b)
    return w, l, t, games


def simulate_west_playoffs(n_sims=20000):
    """Monte-Carlo the remaining bracket, respecting any games already decided
    (known 'winner' is used as-is; undecided games are simulated from Massey)."""
    import random, math
    from collections import Counter
    rng = random.Random(42)
    teams = list(WEST_TEAMS)
    R = {t: massey.get(t, 0.0) for t in teams}
    semi_games = next(r['games'] for r in WEST_BRACKET if r['round'] == 'Semifinals')
    champ_game = next(r['games'] for r in WEST_BRACKET if r['round'] == 'Championship')[0]

    def play(a, b):
        pa = 1 / (1 + math.exp(-0.4 * (R[a] - R[b])))
        return a if rng.random() < pa else b

    title = Counter(); disco = Counter(); final_opp = Counter()
    for _ in range(n_sims):
        # semifinal winners — known result or simulated
        sw = [g['winner'] if g.get('winner') else play(g['a'], g['b']) for g in semi_games]
        ca = champ_game.get('a') or sw[0]
        cb = champ_game.get('b') or sw[1]
        champ = champ_game['winner'] if champ_game.get('winner') else play(ca, cb)
        title[champ] += 1
        if champ == DISCO_ID:
            disco['CHAMP'] += 1
            final_opp[cb if ca == DISCO_ID else ca] += 1
        elif DISCO_ID in (ca, cb):               # reached final, lost
            disco['RUNNER'] += 1
            final_opp[champ] += 1
        else:
            disco['OUT'] += 1
    n = float(n_sims)
    return {
        'teams': teams, 'R': R, 'n': n_sims,
        'title':     {t: title[t] / n for t in teams},
        'disco':     {k: disco[k] / n for k in ('CHAMP', 'RUNNER', 'OUT')},
        'final_opp': final_opp,
    }


def build_champ_banner():
    """Big celebratory champions hero (shown above everything when Disco win it all)."""
    if not disco_champion:
        return ''
    cg = _champ_game
    runner = cg['b'] if cg['a'] == DISCO_ID else cg['a']
    ws, ls = (cg['sa'], cg['sb']) if cg['winner'] == cg['a'] else (cg['sb'], cg['sa'])
    return f'''
    <section class="champ-hero" onclick="goPlayoffs()" role="button" tabindex="0" aria-label="West Division Champions">
      <div class="champ-cup">🏆</div>
      <div class="champ-text">
        <div class="champ-eyebrow">West Division Champions</div>
        <div class="champ-title">🥒 Disco Pickles</div>
        <div class="champ-score">defeated {WEST_TEAMS[runner]['emoji']} {esc(short_name(runner))} <strong>{ws}–{ls}</strong> in the final</div>
        <div class="champ-sub">Undefeated all season · Spring 2026 🏒</div>
      </div>
      <div class="champ-cup champ-cup-right">🏆</div>
    </section>'''


def build_playoff_strip(sim):
    """Compact playoff banner showing Disco's next game. Higher seed is home."""
    if disco_champion:
        return ''   # the big champ-hero banner handles the celebration
    rnd, g = disco_next_game()
    if not g:
        return f'''
    <section class="playoff-strip" onclick="goPlayoffs()" role="button" tabindex="0">
      <div class="ps-main">
        <span class="ps-tag">🏆 WEST PLAYOFFS</span>
        <span class="ps-headline">🥒 Disco Pickles — see the bracket</span>
      </div>
      <span class="ps-cta">View Bracket &amp; Odds →</span>
    </section>'''
    opp = g['a'] if g['b'] == DISCO_ID else g['b']
    disco_home = WEST_TEAMS[DISCO_ID]['seed'] < WEST_TEAMS[opp]['seed']
    de, oe = WEST_TEAMS[DISCO_ID]['emoji'], WEST_TEAMS[opp]['emoji']
    dha, oha = ('HOME', 'AWAY') if disco_home else ('AWAY', 'HOME')
    dcls = 'ps-ha' if disco_home else 'ps-ha away'
    ocls = 'ps-ha away' if disco_home else 'ps-ha'
    return f'''
    <section class="playoff-strip" onclick="goPlayoffs()" role="button" tabindex="0">
      <div class="ps-main">
        <span class="ps-tag">🏆 PLAYOFFS · {rnd['round'].upper()}</span>
        <span class="ps-headline">{de} Disco Pickles <span class="{dcls}">{dha}</span> <span class="ps-vs">vs</span> {oe} {esc(short_name(opp))} <span class="{ocls}">{oha}</span></span>
      </div>
      <div class="ps-stats">
        <div class="ps-stat"><span class="ps-stat-num">{_date_prefix(rnd['date_obj'])} · {esc(g['when'])}</span><span class="ps-stat-lbl">{esc(g['loc'])}</span></div>
      </div>
      <span class="ps-cta">View Bracket &amp; Odds →</span>
    </section>'''


def _bracket_slot(g, key, label_key):
    """One participant row inside a bracket matchup card."""
    tid = g.get(key)
    if tid is None:
        return (f'<div class="po-br-team po-br-tbd">'
                f'<span class="po-medal po-medal-sm po-medal-tbd">?</span>'
                f'<span class="po-br-name">{esc(g.get(label_key, "TBD"))}</span></div>')
    winner = g.get('winner')
    cls = 'po-br-won' if winner == tid else ('po-br-lost' if winner else '')
    dp  = ' po-br-dpteam' if tid == DISCO_ID else ''
    chk = '<span class="po-br-check">✓</span>' if winner == tid else ''
    sc  = g.get('sa' if key == 'a' else 'sb')
    score = f'<span class="po-br-score">{sc}</span>' if sc is not None else ''
    return (f'<div class="po-br-team {cls}{dp}">{team_medal(tid, "sm")}'
            f'<span class="po-br-seed">{WEST_TEAMS[tid]["seed"]}</span>'
            f'<span class="po-br-name">{esc(short_name(tid))}</span>{chk}{score}</div>')


def build_playoffs_tab(sim):
    champ_pct = sim['disco']['CHAMP']
    final_pct = sim['disco']['CHAMP'] + sim['disco']['RUNNER']
    final_opp = sim['final_opp'].most_common(1)
    final_opp_name = short_name(final_opp[0][0]) if final_opp else 'TBD'

    final_opp_id = final_opp[0][0] if final_opp else 14357   # Polar Predators
    final_wp = win_prob(massey.get(DISCO_ID, 0) - massey.get(final_opp_id, 0))

    # Championship result (for the path panel)
    cg = _champ_game
    if disco_champion:
        c_ds = cg['sa'] if cg['a'] == DISCO_ID else cg['sb']
        c_os = cg['sb'] if cg['a'] == DISCO_ID else cg['sa']
        champ_step = (f'<div class="po-path-step po-path-final po-path-champ">'
                      f'<span class="po-path-round">Championship</span>'
                      f'<span class="po-path-detail">🏆 beat <strong>{esc(final_opp_name)}</strong> '
                      f'{c_ds}–{c_os} — <strong>CHAMPIONS!</strong></span></div>')
    else:
        champ_step = (f'<div class="po-path-step po-path-final">'
                      f'<span class="po-path-round">Championship · {champ_when}</span>'
                      f'<span class="po-path-detail">vs <strong>{esc(final_opp_name)}</strong> · '
                      f'<span class="po-wp">{final_wp*100:.0f}% win</span></span></div>')

    # ── Connected bracket ───────────────────────────────────────────────────────
    cols = ''
    for ri, rnd in enumerate(WEST_BRACKET):
        col_cls = ['po-col-wc', 'po-col-semi', 'po-col-final'][ri]
        matches = ''
        for g in rnd['games']:
            is_dp = DISCO_ID in (g.get('a'), g.get('b'))
            top = _bracket_slot(g, 'a', 'a_label')
            bot = _bracket_slot(g, 'b', 'b_label')
            if g.get('winner'):
                if rnd['round'] == 'Championship':
                    status = f'<span class="po-br-status champ">🏆 {esc(short_name(g["winner"]))} — CHAMPIONS</span>'
                elif g.get('sa') is not None:
                    ot = ' (OT)' if g.get('ot') else ''
                    status = f'<span class="po-br-status done">Final{ot} · {esc(short_name(g["winner"]))} ✓</span>'
                else:
                    status = f'<span class="po-br-status done">{esc(short_name(g["winner"]))} advanced ✓</span>'
            elif rnd['date_obj'] == TODAY:
                status = '<span class="po-br-status live">🔴 Today</span>'
            elif rnd['date_obj'] > TODAY:
                status = ('<span class="po-br-status live">Tomorrow</span>' if (rnd['date_obj'] - TODAY).days == 1
                          else '<span class="po-br-status up">⏳ Upcoming</span>')
            else:
                status = '<span class="po-br-status up">No score posted yet</span>'
            matches += f'''
              <div class="po-br-match{' po-br-match-dp' if is_dp else ''}">
                {top}{bot}
                <div class="po-br-meta">🕑 {esc(rnd['date'])} · {esc(g['when'])} · 📍 {esc(g['loc'])}</div>
                {status}
              </div>'''
        cols += f'''
          <div class="po-br-col {col_cls}">
            <div class="po-br-colhdr"><span>{esc(rnd['round'])}</span><small>{esc(rnd['date'])}</small></div>
            {matches}
          </div>'''
    cg = WEST_CONSOLATION['game']
    consolation = f'''
      <div class="po-conso">
        <span class="po-conso-lbl">Consolation (2-game guarantee)</span>
        {team_medal(cg['a'], 'sm')} {esc(short_name(cg['a']))}
        <span class="po-conso-vs">vs</span>
        {team_medal(cg['b'], 'sm')} {esc(short_name(cg['b']))}
        <span class="po-conso-meta">{esc(WEST_CONSOLATION['date'])} · {esc(cg['when'])} · {esc(cg['loc'])}</span>
      </div>'''

    # ── Head-to-head vs the finalist + championship timing ──────────────────────
    fw, fl, ft, f_games = disco_h2h(final_opp_id)
    f_scores = ' · '.join(f'<span class="po-h2h-score">{a}–{b}</span>' for _, a, b in f_games)
    champ_rnd = next(r for r in WEST_BRACKET if r['round'] == 'Championship')
    champ_when = f"{_date_prefix(champ_rnd['date_obj'])} · {champ_rnd['games'][0]['when']}"

    # ── Title-odds bars ─────────────────────────────────────────────────────────
    ranked = sorted(sim['teams'], key=lambda t: -sim['title'][t])
    topp = max((sim['title'][t] for t in sim['teams']), default=1) or 1
    bars = ''
    for tt in ranked:
        p = sim['title'][tt]
        is_dp = (tt == DISCO_ID)
        bar_w = max(p / topp * 100, 1.5)
        color = WEST_TEAMS[tt]['color']
        bars += f'''
          <div class="po-odds-row{' po-odds-dp' if is_dp else ''}">
            <span class="po-odds-team">{team_medal(tt, 'sm')} {esc(short_name(tt))}</span>
            <div class="po-odds-track"><div class="po-odds-fill" style="width:{bar_w:.0f}%;background:{color}"></div></div>
            <span class="po-odds-pct">{p*100:.0f}%</span>
          </div>'''

    return f'''
    <div class="po-container">
      <!-- Bracket -->
      <div class="po-panel">
        <h3 class="po-panel-title">Bracket</h3>
        <div class="po-bracket2-wrap"><div class="po-bracket2">{cols}</div></div>
        {consolation}
      </div>

      <!-- Pickle fact (moved below the bracket) -->
      <div class="pickle-fact-bar pickle-fact-inline">
        <span class="pickle-fact-icon">🥒</span>
        <span class="pickle-fact-text"><strong>Did you know?</strong> {_pickle_fact}</span>
      </div>

      <div class="po-panels">
        <div class="po-panel">
          <h3 class="po-panel-title">Championship Odds — every West team</h3>
          <p class="po-panel-sub">{sim['n']:,} Monte-Carlo sims of the championship game (semifinals decided), using Massey ratings.</p>
          <div class="po-odds">{bars}</div>
        </div>

        <div class="po-panel">
          <h3 class="po-panel-title">🥒 Disco Pickles — {'title run' if disco_champion else 'path to the title'}</h3>
          <div class="po-path">
            <div class="po-path-step po-path-done"><span class="po-path-round">Wild Card</span><span class="po-path-detail">BYE — #1 seed ✓</span></div>
            <div class="po-path-step po-path-done"><span class="po-path-round">Semifinal</span><span class="po-path-detail">beat <strong>Carolina Havoc</strong> 4–2 ✓</span></div>
            {champ_step}
          </div>
          <p class="po-panel-sub">{f"🏆 <strong>Champions!</strong> Disco beat {esc(final_opp_name)} {c_ds}–{c_os} in the final to cap a perfect season — undefeated start to finish." if disco_champion else f"One game for the title. Season series vs <strong>{esc(final_opp_name)}</strong>: Disco <strong>{fw}-{fl}-{ft}</strong> &nbsp;{f_scores}."}</p>
        </div>
      </div>

      <!-- Full schedule -->
      <div class="po-panel">
        <h3 class="po-panel-title">Playoff Schedule</h3>
        <div class="po-sched">{build_playoff_schedule_rows()}</div>
      </div>

      <!-- Around the league: North/South ADV bracket -->
      <div class="po-panel po-aroundleague">
        <h3 class="po-panel-title">🏒 Around the 10U ADV — North/South Bracket</h3>
        <p class="po-panel-sub">The other 10U Advanced bracket (10 teams, North + South) ran alongside ours. Its final: <strong>Thunder Blades</strong> (#1) vs <strong>Flying SAUCErs</strong> (#3) — result pending.</p>
        <ul class="po-notes">
          <li><span class="po-note-tag">UPSET</span> #3 <strong>Flying SAUCErs</strong> stunned #2 <strong>Hawks (District 6)</strong> — the regular-season goal-differential leader (+70!) — <strong>3–2</strong> in the semifinal to crash the title game.</li>
          <li><span class="po-note-tag">UPSETS</span> Both bottom seeds won their Wild Card games: #10 <strong>10S3</strong> over #8 Puckaneers 4–0, and #9 <strong>Mighty Canes</strong> over #7 Frozen Fury 4–1.</li>
          <li>Top seed <strong>Thunder Blades</strong> (8-0-1) cruised to the final (7–2, then 4–1) — much like our Pickles' run.</li>
        </ul>
      </div>

      <p class="po-caveat">⚠️ Single-elimination is high-variance and these are 10U games — a hot goalie swings everything. Playoff scores are entered manually (they aren't in the DaySmart feed). Odds recompute each rebuild.</p>
    </div>'''


def build_playoff_schedule_rows():
    """Flat schedule list across all playoff rounds (incl. consolation)."""
    items = [(r['round'], r['date'], r['date_obj'], g) for r in WEST_BRACKET for g in r['games']]
    items.append((WEST_CONSOLATION['round'], WEST_CONSOLATION['date'],
                  WEST_CONSOLATION['date_obj'], WEST_CONSOLATION['game']))
    rows = ''
    for rname, rdate, dobj, g in items:
        a, b = g.get('a'), g.get('b')
        if a and b:
            match = (f'{team_medal(a, "sm")} {esc(short_name(a))} '
                     f'<span class="po-sched-vs">vs</span> {team_medal(b, "sm")} {esc(short_name(b))}')
        else:
            match = f'{esc(g.get("a_label", "TBD"))} <span class="po-sched-vs">vs</span> {esc(g.get("b_label", "TBD"))}'
        winner = g.get('winner')
        if winner:
            if g.get('sa') is not None:
                ws, ls = (g['sa'], g['sb']) if winner == a else (g['sb'], g['sa'])
                ot = ' OT' if g.get('ot') else ''
                chip = f'<span class="po-sched-chip done">{esc(short_name(winner))} {ws}–{ls}{ot} ✓</span>'
            else:
                chip = f'<span class="po-sched-chip done">{esc(short_name(winner))} ✓</span>'
        elif dobj == TODAY:
            chip = '<span class="po-sched-chip live">🔴 Today</span>'
        elif dobj > TODAY:
            chip = ('<span class="po-sched-chip live">Tomorrow</span>' if (dobj - TODAY).days == 1
                    else '<span class="po-sched-chip up">⏳ Upcoming</span>')
        else:
            chip = '<span class="po-sched-chip up">No score posted yet</span>'
        dp = ' po-sched-dp' if DISCO_ID in (a, b) else ''
        rows += f'''
          <div class="po-sched-row{dp}">
            <span class="po-sched-round">{esc(rname)}</span>
            <span class="po-sched-match">{match}</span>
            <span class="po-sched-meta">{esc(rdate)} · {esc(g['when'])} · {esc(g['loc'])}</span>
            {chip}
          </div>'''
    return rows


# ── Assemble full HTML ─────────────────────────────────────────────────────────
standings_html = ''
for div_name in ['West','North','South']:
    standings_html += build_standings_div(div_name, divisions[div_name])

results_html    = build_results_tab()
predictions_html= build_predictions_tab()
playoff_sim     = simulate_west_playoffs()
playoffs_html   = build_playoffs_tab(playoff_sim)
playoff_strip   = build_playoff_strip(playoff_sim)
champ_banner    = build_champ_banner()

# Floating confetti — only when the Disco Pickles are champions. 🥒🏆
confetti_html = '''
<div class="confetti-layer" id="confettiLayer" aria-hidden="true"></div>
<script>
(function(){
  var layer = document.getElementById('confettiLayer');
  if(!layer) return;
  var colors = ['#4D7C0F','#65A30D','#84CC16','#FCD34D','#F59E0B','#16A34A','#ffffff'];
  var emojis = ['\\uD83E\\uDD52','\\uD83C\\uDFC6','\\uD83C\\uDF89'];  // pickle, trophy, party
  var N = 90;
  for(var i=0;i<N;i++){
    var p = document.createElement('span');
    var useEmoji = Math.random() < 0.12;
    p.className = 'confetti-piece' + (useEmoji ? ' confetti-emoji' : '');
    if(useEmoji){ p.textContent = emojis[Math.floor(Math.random()*emojis.length)]; }
    else { p.style.background = colors[Math.floor(Math.random()*colors.length)]; }
    p.style.left = (Math.random()*100) + 'vw';
    if(!useEmoji){ var s = 6 + Math.random()*8; p.style.width = s+'px'; p.style.height = (s*0.45+3)+'px'; }
    var dur = 6 + Math.random()*7;
    p.style.animationDuration = dur + 's';
    p.style.animationDelay = (-Math.random()*dur) + 's';
    p.style.setProperty('--sway', (8 + Math.random()*24) + 'px');
    layer.appendChild(p);
  }
})();
</script>''' if disco_champion else ''
spotlight_html  = build_spotlight_tab()
schedule_html   = build_schedule_tab()
hero_upcoming   = build_hero_upcoming()

dp_div_rank = divisions['West'].index(DISCO_ID) + 1 if DISCO_ID in divisions['West'] else '?'

HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🏒 10U Advance League – Spring 2026</title>
<style>
  :root {{
    --navy:       #23282B;
    --blue:       #CD0000;
    --blue-light: #FFE8E8;
    --pickle:     #CD0000;
    --pickle-dark:#8B0000;
    --pickle-bg:  #FEF2F2;
    --pickle-bdr: #FECACA;
    --win-bg:     #DCFCE7;
    --win-fg:     #16A34A;
    --loss-bg:    #FEE2E2;
    --loss-fg:    #DC2626;
    --tie-bg:     #FEF3C7;
    --tie-fg:     #D97706;
    --bg:         #FAF9F5;
    --surface:    #FFFFFF;
    --border:     #E2E0DC;
    --text:       #1A1A1A;
    --text-muted: #6B6460;
    --shadow:     0 1px 3px rgba(0,0,0,.12), 0 1px 2px rgba(0,0,0,.08);
    --shadow-md:  0 4px 6px rgba(0,0,0,.10), 0 2px 4px rgba(0,0,0,.07);
    --radius:     12px;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }}

  /* ── Header ─────────────────────────────────────────────────── */
  .site-header {{
    background: linear-gradient(135deg, #1A1A1A 0%, #23282B 60%, #3a0000 100%);
    color: white;
    padding: 1.25rem 2rem;
    display: flex; align-items: center; gap: 1rem;
    box-shadow: 0 2px 12px rgba(0,0,0,.5);
    border-bottom: 3px solid var(--blue);
  }}
  .site-header .logo {{ display: flex; align-items: center; }}
  .site-header .logo img {{ height: 52px; width: 52px; object-fit: contain; border-radius: 50%; }}
  .site-header h1 {{ font-size: 1.6rem; font-weight: 700; }}
  .site-header .subtitle {{ font-size: 0.85rem; opacity: 0.75; margin-top: 2px; }}
  .site-header .season-badge {{
    margin-left: auto;
    background: rgba(255,255,255,0.15);
    border: 1px solid rgba(255,255,255,.3);
    border-radius: 20px;
    padding: 0.3rem 1rem;
    font-size: 0.85rem; font-weight: 600;
  }}
  .site-header .last-updated {{
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,.2);
    border-radius: 20px;
    padding: 0.3rem 1rem;
    font-size: 0.78rem;
    opacity: 0.85;
    white-space: nowrap;
  }}

  /* ── Hero ────────────────────────────────────────────────────── */
  .hero {{
    background: linear-gradient(135deg, #7a0000 0%, #CD0000 50%, #A00000 100%);
    color: white;
    padding: 2rem;
    margin: 1.5rem 1.5rem 0;
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 1.5rem;
    align-items: center;
  }}
  .hero-left .team-name {{ font-size: 2rem; font-weight: 800; margin-bottom: 0.5rem; }}
  .hero-left .record-line {{
    font-size: 1rem; opacity: 0.9; margin-bottom: 1rem;
    display: flex; flex-wrap: wrap; gap: 1rem; align-items: center;
  }}
  .hero-left .record-line span {{ font-weight: 700; font-size: 1.1rem; }}
  .hero-stat {{ background: rgba(255,255,255,.15); border-radius: 8px; padding: 0.25rem 0.75rem; font-size: 0.9rem; }}
  .division-badge {{
    display: inline-flex; align-items: center; gap: 0.3rem;
    background: #FCD34D; color: #92400E;
    border-radius: 20px; padding: 0.25rem 0.9rem;
    font-size: 0.8rem; font-weight: 700;
    margin-bottom: 1rem;
  }}
  .hero-upcoming {{ display: flex; gap: 0.65rem; flex-wrap: wrap; }}
  .hero-game {{
    background: rgba(255,255,255,.12);
    border: 1px solid rgba(255,255,255,.2);
    border-radius: 8px;
    padding: 0.5rem 0.75rem;
    text-align: center; min-width: 95px; flex: 1;
  }}
  .hero-game-date {{ font-size: 0.75rem; opacity: 0.8; }}
  .hero-game-opp  {{ font-size: 0.9rem; font-weight: 600; margin: 0.2rem 0; }}
  .hero-game-pred {{ font-size: 0.8rem; font-weight: 700; padding: 0.15rem 0.4rem; border-radius: 4px; margin-top:4px; }}
  .hero-pred-win  {{ background:#86EFAC; color:#15803D; }}
  .hero-pred-loss {{ background:#FCA5A5; color:#991B1B; }}
  .hero-pred-toss {{ background:#FDE68A; color:#92400E; }}
  .hero-right {{ text-align: center; }}
  .hero-rank {{ font-size: 3.5rem; font-weight: 900; line-height:1; }}
  .hero-rank-lbl {{ font-size: 0.8rem; opacity: 0.8; margin-top: 4px; }}
  .hero-upcoming-title {{ font-size: 0.8rem; opacity: 0.75; margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: .05em; }}
  /* Hero badges + undefeated emphasis */
  .hero-badges {{ display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 1rem; }}
  .hero-badges .division-badge {{ margin-bottom: 0; }}
  .undefeated-badge {{
    display: inline-flex; align-items: center; gap: 0.3rem;
    background: linear-gradient(135deg, #F59E0B, #DC2626);
    color: #fff; border-radius: 20px; padding: 0.25rem 0.9rem;
    font-size: 0.8rem; font-weight: 800; letter-spacing: .03em;
    box-shadow: 0 2px 8px rgba(220,38,38,.45);
  }}
  .hero-record {{ background: #FCD34D !important; color: #92400E !important; font-weight: 800 !important; }}
  .hero-undefeated {{ font-size: 0.85rem; opacity: 0.92; font-weight: 600; }}

  /* Compact hero (slim bar) */
  .hero-compact {{
    margin: 1.25rem 1.5rem 0;
    background: linear-gradient(135deg, #7a0000 0%, #CD0000 60%, #A00000 100%);
    color: #fff; border-radius: var(--radius); box-shadow: var(--shadow-md);
    padding: 0.6rem 1.25rem; display: flex; align-items: center; gap: 0.9rem; flex-wrap: wrap;
  }}
  .hc-medal {{ font-size: 1.7rem; line-height: 1; }}
  .hc-id {{ display: flex; flex-direction: column; gap: 0.15rem; }}
  .hc-name {{ font-size: 1.25rem; font-weight: 800; line-height: 1.05; }}
  .hc-tags {{ display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }}
  .hc-tag-lead {{ font-size: 0.64rem; font-weight: 700; background: rgba(255,255,255,.18); padding: 0.08rem 0.5rem; border-radius: 10px; white-space: nowrap; }}
  .hero-compact .undefeated-badge {{ font-size: 0.64rem; padding: 0.08rem 0.5rem; }}
  .hc-stats {{ display: flex; flex-direction: column; gap: 0.15rem; margin-left: 0.4rem; }}
  .hc-rec {{ background: #FCD34D; color: #92400E; font-weight: 800; border-radius: 8px; padding: 0.1rem 0.6rem; font-size: 1rem; width: max-content; }}
  .hc-meta {{ font-size: 0.74rem; opacity: 0.92; }}
  .hc-rank {{ margin-left: auto; text-align: center; line-height: 1; }}
  .hc-rank-num {{ font-size: 1.9rem; font-weight: 900; }}
  .hc-rank-lbl {{ font-size: 0.68rem; opacity: 0.82; display: block; margin-top: 2px; }}
  @media (max-width: 600px) {{ .hc-stats {{ margin-left: 0; }} .hc-rank {{ margin-left: auto; }} }}

  /* ── Playoff Strip ───────────────────────────────────────────── */
  .playoff-strip {{
    margin: 0.9rem 1.5rem 0.4rem;
    background: linear-gradient(135deg, #1a2e05 0%, #3f6212 55%, #4d7c0f 100%);
    border-left: 5px solid #FCD34D;
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
    padding: 1.15rem 1.6rem;
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
    cursor: pointer; color: #fff;
    transition: transform .15s, box-shadow .15s;
  }}
  .playoff-strip:hover {{ transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.28); }}
  .ps-main {{ display: flex; flex-direction: column; gap: 0.2rem; }}
  .ps-tag {{ font-size: 0.8rem; font-weight: 800; color: #FCD34D; letter-spacing: .04em; }}
  .ps-headline {{ font-size: 1.05rem; font-weight: 700; }}
  .ps-stats {{ display: flex; gap: 1.75rem; margin-left: auto; }}
  .ps-stat {{ display: flex; flex-direction: column; text-align: center; }}
  .ps-stat-num {{ font-size: 1.5rem; font-weight: 800; line-height: 1; }}
  .ps-stat-lbl {{ font-size: 0.72rem; opacity: 0.82; margin-top: 0.2rem; white-space: nowrap; }}
  .ps-cta {{
    background: #FCD34D; color: #1a2e05; font-weight: 800;
    border-radius: 20px; padding: 0.5rem 1.1rem; font-size: 0.85rem; white-space: nowrap;
  }}
  .playoff-strip:hover .ps-cta {{ background: #fde68a; }}
  /* Champions banner + confetti */
  .playoff-strip.champions {{
    background: linear-gradient(135deg, #3f6212 0%, #4D7C0F 40%, #B8860B 100%);
    border-left-color: #FCD34D;
    box-shadow: 0 0 0 1px rgba(252,211,77,.4), var(--shadow-md);
    animation: champ-glow 2.4s ease-in-out infinite;
  }}
  .playoff-strip.champions .ps-tag {{ color: #FEF3C7; letter-spacing: .06em; }}
  @keyframes champ-glow {{
    0%,100% {{ box-shadow: 0 0 0 1px rgba(252,211,77,.35), 0 4px 14px rgba(0,0,0,.18); }}
    50%     {{ box-shadow: 0 0 18px 2px rgba(252,211,77,.55), 0 6px 20px rgba(0,0,0,.22); }}
  }}
  .champ-hero {{
    margin: 1rem 1.5rem 0.5rem;
    background: linear-gradient(135deg, #365314 0%, #4D7C0F 45%, #B8860B 100%);
    border: 2px solid #FCD34D;
    border-radius: var(--radius);
    box-shadow: 0 0 24px rgba(252,211,77,.40), var(--shadow-md);
    padding: 1.4rem 1.75rem;
    display: flex; align-items: center; justify-content: center; gap: 1.5rem; flex-wrap: wrap;
    color: #fff; text-align: center; cursor: pointer;
    animation: champ-glow 2.4s ease-in-out infinite;
  }}
  .champ-cup {{ font-size: 3.6rem; line-height: 1; filter: drop-shadow(0 3px 6px rgba(0,0,0,.35)); animation: champ-bob 1.9s ease-in-out infinite; }}
  .champ-cup-right {{ animation-delay: .35s; }}
  .champ-text {{ display: flex; flex-direction: column; gap: 0.15rem; }}
  .champ-eyebrow {{ font-size: 0.82rem; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; color: #FEF3C7; }}
  .champ-title {{ font-size: 2rem; font-weight: 900; line-height: 1.05; }}
  .champ-score {{ font-size: 1rem; opacity: .96; }}
  .champ-sub {{ font-size: 0.8rem; opacity: .85; margin-top: 0.15rem; }}
  @keyframes champ-bob {{ 0%,100% {{ transform: translateY(0) rotate(-5deg); }} 50% {{ transform: translateY(-7px) rotate(5deg); }} }}
  @media (max-width: 560px) {{ .champ-cup-right {{ display: none; }} .champ-title {{ font-size: 1.6rem; }} }}

  .confetti-layer {{ position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 9999; }}
  .confetti-piece {{
    position: absolute; top: -8vh; border-radius: 2px; opacity: .9;
    animation-name: confetti-fall; animation-timing-function: linear;
    animation-iteration-count: infinite; will-change: transform;
  }}
  .confetti-emoji {{ font-size: 1.15rem; opacity: 1; }}
  @keyframes confetti-fall {{
    0%   {{ transform: translateY(-10vh) translateX(0) rotate(0deg); }}
    50%  {{ transform: translateY(50vh)  translateX(var(--sway, 12px)) rotate(180deg); }}
    100% {{ transform: translateY(110vh) translateX(0) rotate(360deg); }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .confetti-layer {{ display: none; }}
    .playoff-strip.champions, .champ-hero, .champ-cup {{ animation: none; }}
  }}
  @media (max-width: 768px) {{
    .playoff-strip {{ gap: 1rem; }}
    .ps-stats {{ margin-left: 0; width: 100%; justify-content: space-between; gap: 1rem; }}
    .ps-cta {{ width: 100%; text-align: center; }}
  }}

  /* ── Tabs ────────────────────────────────────────────────────── */
  .tabs-wrap {{ padding: 1.5rem; }}
  .tab-nav {{
    display: flex; gap: 0.25rem;
    border-bottom: 2px solid var(--border);
    margin-bottom: 1.5rem;
    overflow-x: auto;
  }}
  /* Tab nav pinned directly under the site header */
  .tab-nav-top {{
    margin-bottom: 0;
    padding: 0 1.5rem;
    background: var(--surface);
    position: sticky; top: 0; z-index: 30;
    box-shadow: 0 1px 6px rgba(0,0,0,.05);
  }}
  .tab-btn {{
    padding: 0.6rem 1.4rem;
    background: none; border: none; cursor: pointer;
    font-size: 0.95rem; font-weight: 600;
    color: var(--text-muted);
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    transition: color .15s, border-color .15s;
    white-space: nowrap;
  }}
  .tab-btn:hover {{ color: var(--blue); }}
  .tab-btn.active {{ color: var(--blue); border-bottom-color: var(--blue); }}
  .tab-panel {{ display: none; animation: fadeIn .2s ease; }}
  .tab-panel.active {{ display: block; }}
  @keyframes fadeIn {{ from {{ opacity:0; transform:translateY(4px); }} to {{ opacity:1; transform:none; }} }}

  /* ── Standings ───────────────────────────────────────────────── */
  .standings-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px,1fr)); gap: 1.5rem; }}
  .standings-div {{
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    /* Enable container queries so columns can respond to the card's own width */
    container-type: inline-size;
    container-name: standings;
  }}
  /* Scrollable wrapper — safety net if content still overflows */
  .standings-table-wrap {{
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }}
  .div-header {{
    background: var(--navy);
    color: white;
    padding: 0.75rem 1rem;
    font-size: 1rem; font-weight: 700;
  }}
  .standings-table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; min-width: 340px; }}
  .standings-table thead tr {{ background: #F5F0F0; }}
  .standings-table th {{
    padding: 0.5rem 0.5rem; text-align: center;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: .04em;
    color: var(--text-muted); font-weight: 600;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}
  .standings-table th:nth-child(2) {{ text-align: left; }}
  .standings-table td {{
    padding: 0.5rem 0.5rem; text-align: center;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }}
  .standings-table td:nth-child(2) {{ text-align: left; font-weight: 600; }}
  .standings-table tbody tr:hover {{ background: #FDF5F5; }}
  .standings-table tbody tr:last-child td {{ border-bottom: none; }}
  .dp-row {{ background: var(--pickle-bg) !important; }}
  .dp-row td {{ color: var(--pickle-dark); font-weight: 600; }}
  .pts-cell {{ font-weight: 700; color: var(--navy); }}
  .rank-cell {{ color: var(--text-muted); font-size: 0.8rem; }}
  .pos-gd {{ color: var(--win-fg); font-weight: 600; }}
  .neg-gd {{ color: var(--loss-fg); font-weight: 600; }}
  .team-cell {{ max-width: 180px; }}

  /* Container queries — hide GF/GA when the card itself is narrower than 400px.
     This fires at any layout (1-up, 2-up, 3-up) without needing a specific
     viewport breakpoint, which is exactly what container queries are for. */
  @container standings (max-width: 400px) {{
    .standings-table th:nth-child(7),
    .standings-table td:nth-child(7),
    .standings-table th:nth-child(8),
    .standings-table td:nth-child(8) {{ display: none; }}
  }}
  /* Very narrow cards (e.g. small phone) — also hide T column */
  @container standings (max-width: 310px) {{
    .standings-table th:nth-child(5),
    .standings-table td:nth-child(5) {{ display: none; }}
  }}

  /* badges */
  .badge-row {{
    display: inline-flex;
    flex-wrap: nowrap;
    gap: 3px;
    align-items: center;
  }}
  .badge {{ display: inline-block; padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.72rem; font-weight: 700; white-space: nowrap; flex-shrink: 0; }}
  .badge-w {{ background: var(--win-bg); color: var(--win-fg); }}
  .badge-l {{ background: var(--loss-bg); color: var(--loss-fg); }}
  .badge-t {{ background: var(--tie-bg); color: var(--tie-fg); }}
  .badge-none {{ background: #F1F5F9; color: var(--text-muted); }}

  /* ── Results ─────────────────────────────────────────────────── */
  .results-container {{ display: flex; flex-direction: column; gap: 1.5rem; }}
  .results-section-hdr {{
    display: flex; align-items: center; gap: 0.75rem;
    font-size: 0.8rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .07em; color: var(--text-muted);
    padding: 0.25rem 0.25rem 0;
    border-top: 2px solid var(--border);
    margin-top: 0.25rem;
  }}
  .results-container > .results-section-hdr:first-child {{ border-top: none; margin-top: 0; }}
  .sort-indicator {{
    font-size: 0.72rem; font-weight: 700;
    background: var(--navy); color: white;
    border-radius: 10px; padding: 0.15rem 0.6rem;
    letter-spacing: .03em; text-transform: none;
  }}
  .date-group {{
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }}
  .date-header {{
    background: var(--blue);
    color: white;
    padding: 0.5rem 1rem;
    font-size: 0.85rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: .05em;
  }}
  .game-row {{
    display: flex; align-items: center; gap: 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }}
  .game-row:last-child {{ border-bottom: none; }}
  .game-row:hover {{ background: #FDF5F5; }}
  .dp-game {{ background: var(--pickle-bg); }}
  .dp-game:hover {{ background: #FEE2E2; }}
  .game-teams {{ flex: 1; display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; }}
  .home-team {{ font-weight: 600; }}
  .away-team {{ font-weight: 600; color: var(--text-muted); }}
  .vs-sep {{ color: var(--text-muted); font-size: 0.8rem; }}
  .game-score {{ font-weight: 700; font-size: 1rem; min-width: 80px; text-align:center; }}
  .game-time {{ color: var(--text-muted); font-size: 0.78rem; }}
  .res-w {{ color: var(--win-fg); }}
  .res-l {{ color: var(--loss-fg); }}
  .res-t {{ color: var(--tie-fg); }}
  .upcoming-tbd {{ color: var(--text-muted); font-style: italic; }}
  .score-pending {{ color: var(--tie-fg); font-style: italic; font-size: 0.82rem; font-weight: 600; }}
  .pred-score {{ color: var(--blue); font-size: 0.82rem; font-weight: 500; }}
  .outcome-badge {{ padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 700; }}
  .win-badge  {{ background: var(--win-bg);  color: var(--win-fg);  }}
  .loss-badge {{ background: var(--loss-bg); color: var(--loss-fg); }}
  .tie-badge  {{ background: var(--tie-bg);  color: var(--tie-fg);  }}

  /* ── Pickle Fact Bar ─────────────────────────────────────────── */
  .pickle-fact-bar {{
    background: var(--pickle-bg);
    border-top: 2px solid var(--pickle-bdr);
    border-bottom: 2px solid var(--pickle-bdr);
    padding: 0.65rem 1.5rem;
    display: flex; align-items: flex-start; gap: 0.65rem;
    font-size: 0.85rem; color: var(--text); line-height: 1.5;
  }}
  .pickle-fact-icon {{ font-size: 1.3rem; flex-shrink: 0; margin-top: 0.05rem; }}
  .pickle-fact-text {{ flex: 1; }}
  .pickle-fact-text strong {{ color: var(--pickle); }}
  .pickle-fact-inline {{ border: 1px solid var(--pickle-bdr); border-radius: var(--radius); }}

  /* ── Playoffs ────────────────────────────────────────────────── */
  .po-container {{ display: flex; flex-direction: column; gap: 1.5rem; }}
  .po-head {{ text-align: center; }}
  .po-head-top {{ margin: 1.4rem 1.5rem 0; }}
  .po-head-top .po-title {{ font-size: 1.35rem; }}
  .po-title {{ font-size: 1.5rem; font-weight: 800; margin-bottom: .35rem; }}
  .po-sub {{ color: var(--text-muted); font-size: .9rem; max-width: 640px; margin: 0 auto; line-height: 1.5; }}
  .po-hero {{ display: grid; grid-template-columns: 1.6fr 1fr 1fr; gap: 1rem; }}
  .po-hero-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow); padding: 1.1rem 1rem; display: flex; flex-direction: column;
    align-items: center; justify-content: center; text-align: center; gap: .25rem;
  }}
  .po-hero-main {{ background: linear-gradient(135deg, var(--pickle-bg), var(--surface)); border-color: var(--pickle-bdr); }}
  .po-hero-label {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); font-weight: 700; }}
  .po-hero-big {{ font-size: 3rem; font-weight: 800; color: var(--pickle-dark); line-height: 1; }}
  .po-hero-mid {{ font-size: 2rem; font-weight: 800; color: var(--blue); line-height: 1; }}
  .po-hero-note {{ font-size: .76rem; color: var(--text-muted); }}
  .po-panels {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; }}
  .po-panel {{
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow); padding: 1.1rem 1.2rem;
  }}
  .po-panel-title {{ font-size: 1rem; font-weight: 700; margin-bottom: .2rem; }}
  .po-panel-sub {{ font-size: .78rem; color: var(--text-muted); margin-bottom: .85rem; line-height: 1.45; }}
  .po-odds {{ display: flex; flex-direction: column; gap: .55rem; }}
  .po-odds-row {{ display: grid; grid-template-columns: 7.5rem 1fr 2.6rem; align-items: center; gap: .6rem; }}
  .po-odds-team {{ font-size: .85rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .po-odds-dp .po-odds-team {{ color: var(--pickle-dark); font-weight: 800; }}
  .po-odds-track {{ background: var(--bg); border-radius: 6px; height: 16px; overflow: hidden; }}
  .po-odds-fill {{ height: 100%; border-radius: 6px; transition: width .3s; }}
  .po-odds-pct {{ font-size: .82rem; font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }}
  .po-path {{ display: flex; flex-direction: column; gap: .5rem; }}
  .po-path-step {{
    display: flex; justify-content: space-between; align-items: center; gap: .75rem;
    padding: .6rem .8rem; background: var(--bg); border-radius: 8px; border-left: 3px solid var(--border);
  }}
  .po-path-final {{ border-left-color: var(--pickle-dark); background: var(--pickle-bg); }}
  .po-path-round {{ font-size: .8rem; font-weight: 700; color: var(--text-muted); }}
  .po-path-detail {{ font-size: .85rem; text-align: right; }}
  .po-wp {{ color: var(--win-fg); font-weight: 700; white-space: nowrap; }}
  .po-bracket {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
  .po-round {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: .8rem; }}
  .po-round-champ {{ background: var(--pickle-bg); border-color: var(--pickle-bdr); }}
  .po-round-hdr {{ display: flex; flex-direction: column; gap: .1rem; margin-bottom: .65rem; }}
  .po-round-name {{ font-size: .95rem; font-weight: 800; }}
  .po-round-sub {{ font-size: .72rem; color: var(--text-muted); }}
  .po-game {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: .55rem .65rem; margin-bottom: .5rem; }}
  .po-game:last-child {{ margin-bottom: 0; }}
  .po-game-top {{ display: flex; align-items: baseline; gap: .4rem; margin-bottom: .3rem; }}
  .po-slot {{ font-size: .68rem; font-weight: 800; color: var(--blue); background: var(--blue-light); padding: .05rem .35rem; border-radius: 4px; }}
  .po-match {{ font-size: .82rem; font-weight: 600; line-height: 1.3; }}
  .po-game-meta {{ display: flex; flex-direction: column; gap: .15rem; }}
  .po-when, .po-loc {{ font-size: .74rem; color: var(--text-muted); }}
  .po-caveat {{ font-size: .78rem; color: var(--text-muted); text-align: center; font-style: italic; line-height: 1.5; }}
  @media (max-width: 720px) {{
    .po-hero {{ grid-template-columns: 1fr; }}
    .po-panels {{ grid-template-columns: 1fr; }}
  }}

  /* Team medallions */
  .po-medal {{ display:inline-flex; align-items:center; justify-content:center; border-radius:50%; color:#fff; flex-shrink:0; box-shadow:0 1px 3px rgba(0,0,0,.25); line-height:1; }}
  .po-medal-sm {{ width:1.55rem; height:1.55rem; font-size:.85rem; }}
  .po-medal-md {{ width:2.1rem;  height:2.1rem;  font-size:1.1rem; }}
  .po-medal-lg {{ width:3.4rem;  height:3.4rem;  font-size:1.9rem; }}
  .po-medal-tbd {{ background:var(--border) !important; color:var(--text-muted); }}
  .ps-vs {{ opacity:.7; font-weight:600; font-size:.85rem; }}
  .ps-ha {{ font-size:.58rem; font-weight:800; letter-spacing:.04em; background:#FCD34D; color:#1a2e05; padding:.05rem .4rem; border-radius:6px; vertical-align:middle; margin-left:.15rem; }}
  .ps-ha.away {{ background:rgba(255,255,255,.22); color:#fff; }}
  .po-path-done .po-path-detail {{ color:var(--win-fg); font-weight:600; }}

  /* Semifinal spotlight */
  .po-spotlight {{ background:linear-gradient(135deg,#1A1A1A,#3a0000); color:#fff; border-radius:var(--radius); box-shadow:var(--shadow-md); padding:1.25rem 1.5rem; text-align:center; }}
  .po-spot-tag {{ font-size:.78rem; font-weight:800; letter-spacing:.05em; color:#FCD34D; text-transform:uppercase; }}
  .po-spot-match {{ display:flex; align-items:center; justify-content:center; gap:1.75rem; margin:.85rem 0; }}
  .po-spot-team {{ display:flex; flex-direction:column; align-items:center; gap:.3rem; min-width:120px; }}
  .po-spot-team.dp .po-spot-name {{ color:#A3E635; }}
  .po-spot-name {{ font-weight:800; font-size:1.05rem; }}
  .po-spot-seed {{ font-size:.72rem; opacity:.7; }}
  .po-spot-vs {{ display:flex; flex-direction:column; gap:.3rem; align-items:center; }}
  .po-spot-vs > span:first-child {{ font-size:.95rem; font-weight:800; opacity:.55; }}
  .po-spot-wp {{ background:var(--win-fg); color:#fff; border-radius:12px; padding:.15rem .6rem; font-size:.78rem; font-weight:700; }}
  .po-spot-meta {{ font-size:.8rem; opacity:.85; }}
  .po-spot-h2h {{ margin-top:.7rem; font-size:.85rem; background:rgba(255,255,255,.08); border-radius:8px; padding:.5rem .9rem; display:inline-block; }}
  .po-h2h-lbl {{ opacity:.75; }}
  .po-h2h-score {{ background:rgba(255,255,255,.13); border-radius:5px; padding:.05rem .4rem; font-weight:700; font-size:.8rem; }}

  /* Connected bracket */
  .po-bracket2-wrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch; padding-bottom:.5rem; }}
  .po-bracket2 {{ display:flex; gap:2.4rem; min-width:640px; align-items:stretch; }}
  .po-br-col {{ display:flex; flex-direction:column; justify-content:space-around; flex:1 1 0; position:relative; gap:1.5rem; }}
  .po-col-final {{ justify-content:center; }}
  .po-br-colhdr {{ text-align:center; font-weight:800; font-size:.85rem; margin-bottom:.15rem; }}
  .po-br-colhdr small {{ display:block; font-weight:500; font-size:.7rem; color:var(--text-muted); }}
  .po-br-match {{ position:relative; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:.45rem; box-shadow:var(--shadow); }}
  .po-br-match-dp {{ border-color:var(--pickle-dark); box-shadow:0 0 0 2px var(--pickle-bdr); }}
  .po-br-team {{ display:flex; align-items:center; gap:.4rem; padding:.3rem .2rem; font-size:.85rem; }}
  .po-br-team + .po-br-team {{ border-top:1px solid var(--border); }}
  .po-br-seed {{ font-size:.7rem; color:var(--text-muted); font-weight:700; min-width:.9rem; text-align:center; }}
  .po-br-name {{ flex:1; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .po-br-won {{ font-weight:800; }}
  .po-br-won .po-br-name {{ color:var(--win-fg); }}
  .po-br-lost {{ opacity:.45; }}
  .po-br-dpteam .po-br-name {{ color:var(--pickle-dark); }}
  .po-br-check {{ color:var(--win-fg); font-weight:800; }}
  .po-br-score {{ font-weight:800; font-size:.95rem; min-width:1.1rem; text-align:right; font-variant-numeric:tabular-nums; }}
  .po-br-won .po-br-score {{ color:var(--win-fg); }}
  .po-br-tbd {{ opacity:.6; font-style:italic; }}
  .po-br-meta {{ font-size:.67rem; color:var(--text-muted); margin-top:.3rem; text-align:center; line-height:1.3; }}
  .po-br-status {{ display:block; text-align:center; font-size:.7rem; font-weight:700; margin-top:.2rem; }}
  .po-br-status.done {{ color:var(--win-fg); }}
  .po-br-status.live {{ color:var(--loss-fg); }}
  .po-br-status.up {{ color:var(--text-muted); }}
  .po-br-status.champ {{ color:#B8860B; font-weight:800; }}
  .po-path-champ .po-path-detail {{ color:#B8860B; font-weight:700; }}
  .po-notes {{ list-style:none; display:flex; flex-direction:column; gap:.55rem; margin-top:.4rem; }}
  .po-notes li {{ font-size:.86rem; line-height:1.45; padding-left:.2rem; }}
  .po-note-tag {{ display:inline-block; font-size:.62rem; font-weight:800; letter-spacing:.04em; color:#fff; background:var(--loss-fg); border-radius:5px; padding:.05rem .4rem; margin-right:.4rem; vertical-align:middle; }}
  .po-col-wc .po-br-match::after, .po-col-semi .po-br-match::after {{ content:''; position:absolute; left:100%; top:50%; width:1.2rem; height:2px; background:var(--border); }}
  .po-col-semi .po-br-match::before, .po-col-final .po-br-match::before {{ content:''; position:absolute; right:100%; top:50%; width:1.2rem; height:2px; background:var(--border); }}
  .po-col-semi::after {{ content:''; position:absolute; left:100%; margin-left:1.2rem; top:25%; bottom:25%; width:2px; background:var(--border); }}

  /* Consolation + schedule */
  .po-conso {{ margin-top:1rem; display:flex; flex-wrap:wrap; align-items:center; gap:.5rem; background:var(--bg); border:1px dashed var(--border); border-radius:8px; padding:.6rem .9rem; font-size:.85rem; }}
  .po-conso-lbl {{ font-weight:700; color:var(--text-muted); font-size:.78rem; margin-right:.5rem; }}
  .po-conso-vs {{ color:var(--text-muted); font-size:.8rem; }}
  .po-conso-meta {{ margin-left:auto; font-size:.75rem; color:var(--text-muted); }}
  .po-sched {{ display:flex; flex-direction:column; gap:.4rem; }}
  .po-sched-row {{ display:grid; grid-template-columns:6.5rem 1fr auto auto; align-items:center; gap:.75rem; padding:.5rem .7rem; background:var(--bg); border-radius:8px; font-size:.85rem; }}
  .po-sched-dp {{ background:var(--pickle-bg); }}
  .po-sched-round {{ font-weight:700; font-size:.72rem; color:var(--text-muted); text-transform:uppercase; }}
  .po-sched-match {{ display:flex; align-items:center; gap:.35rem; flex-wrap:wrap; }}
  .po-sched-vs {{ color:var(--text-muted); font-size:.78rem; }}
  .po-sched-meta {{ font-size:.74rem; color:var(--text-muted); white-space:nowrap; }}
  .po-sched-chip {{ font-size:.72rem; font-weight:700; border-radius:10px; padding:.15rem .55rem; white-space:nowrap; }}
  .po-sched-chip.done {{ background:var(--win-bg); color:var(--win-fg); }}
  .po-sched-chip.live {{ background:var(--loss-bg); color:var(--loss-fg); }}
  .po-sched-chip.up {{ background:#E2E8F0; color:var(--text-muted); }}
  @media (max-width: 768px) {{
    .po-spot-match {{ gap:.85rem; }}
    .po-spot-team {{ min-width:88px; }}
    .po-sched-row {{ grid-template-columns:1fr auto; }}
    .po-sched-meta {{ grid-column:1 / -1; white-space:normal; }}
  }}

  /* ── Predictions ─────────────────────────────────────────────── */
  .predictions-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.25rem; }}
  .pred-card {{
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    border-top: 4px solid var(--border);
    transition: transform .15s, box-shadow .15s;
  }}
  .pred-card:hover {{ transform: translateY(-2px); box-shadow: var(--shadow-md); }}
  .home-favored {{ border-top-color: var(--win-fg); }}
  .away-favored {{ border-top-color: var(--loss-fg); }}
  .toss-up      {{ border-top-color: var(--tie-fg); }}
  .dp-pred-card {{ border-top-color: var(--pickle); box-shadow: 0 0 0 2px var(--pickle-bdr), var(--shadow); }}
  .no-pred      {{ border-top-color: var(--text-muted); }}

  .pred-card-header {{
    padding: 0.6rem 1rem;
    background: #F5F0F0;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    font-size: 0.8rem;
  }}
  .pred-date {{ color: var(--text-muted); }}
  .dp-badge {{ background: var(--pickle); color: white; border-radius: 12px; padding: 0.15rem 0.6rem; font-size: 0.73rem; font-weight: 700; }}

  .pred-matchup {{
    padding: 0.9rem 1rem;
    display: flex; align-items: center; gap: 0.5rem;
    font-size: 1rem; font-weight: 700;
  }}
  .pred-home {{ flex: 1; }}
  .pred-vs   {{ color: var(--text-muted); font-size: 0.8rem; font-weight: 400; }}
  .pred-away {{ flex: 1; text-align: right; color: var(--text-muted); }}

  .pred-result {{ padding: 0.75rem 1rem 1rem; }}
  .pred-winner {{ font-size: 0.9rem; margin-bottom: 0.6rem; }}
  .pred-gd-val {{ font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.6rem; }}
  .conf-bar-wrap {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; font-size: 0.78rem; }}
  .conf-label {{ width: 80px; color: var(--text-muted); flex-shrink: 0; }}
  .conf-bar {{ flex: 1; height: 7px; background: #E2E8F0; border-radius: 4px; overflow: hidden; }}
  .conf-fill {{ height: 100%; background: var(--win-fg); border-radius: 4px; transition: width .3s; }}
  .conf-pct {{ width: 32px; text-align: right; font-weight: 600; color: var(--text-muted); }}

  /* Per-game Massey rating rows inside prediction cards */
  .pred-ratings {{ margin-bottom: 0.65rem; display: flex; flex-direction: column; gap: 0.3rem;
                   border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem 0.65rem;
                   background: #FAFAFA; }}
  .pred-rating-row {{ display: flex; align-items: center; font-size: 0.82rem; gap: 0.4rem; }}
  .pred-rating-team {{ flex: 1; color: var(--text); font-weight: 500; }}
  .pred-rating-label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing:.04em;
                        color: var(--text-muted); flex-shrink: 0; }}
  .pos-rating {{ color: var(--win-fg); font-weight: 700; font-variant-numeric: tabular-nums; }}
  .neg-rating {{ color: var(--loss-fg); font-weight: 700; font-variant-numeric: tabular-nums; }}
  .text-muted {{ color: var(--text-muted); }}

  /* Massey leaderboard panels */
  .predictions-container {{ display: flex; flex-direction: column; gap: 1.5rem; }}
  .massey-panels {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px,1fr)); gap: 1.25rem; }}
  .massey-isolation-note {{
    font-size: 0.8rem; color: var(--text-muted);
    background: var(--tie-bg); border: 1px solid #FDE68A;
    border-radius: 8px; padding: 0.6rem 1rem;
    margin-top: -0.25rem;
  }}
  .massey-section {{
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }}
  .massey-header {{
    background: var(--navy);
    color: white;
    padding: 0.8rem 1.25rem;
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.75rem;
  }}
  .massey-title {{ font-size: 1rem; font-weight: 700; margin: 0; }}
  .massey-explainer {{ font-size: 0.75rem; opacity: 0.75; font-weight: 400; }}
  .massey-grid {{ padding: 0.5rem 0.75rem 0.75rem; display: flex; flex-direction: column; gap: 0.3rem; }}
  .massey-row {{
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.35rem 0.5rem; border-radius: 6px;
    font-size: 0.85rem;
  }}
  .massey-row:hover {{ background: #F5F0F0; }}
  .dp-massey-row {{ background: var(--pickle-bg) !important; font-weight: 600; }}
  .massey-rank {{ min-width: 28px; color: var(--text-muted); font-weight: 700; font-size: 0.78rem; text-align: right; }}
  .massey-team {{ flex: 1; }}
  .massey-gp {{ font-size: 0.73rem; color: var(--text-muted); flex-shrink: 0; min-width: 30px; text-align: right; }}
  .massey-bar-wrap {{ width: 80px; height: 7px; background: #E2E0DC; border-radius: 4px; overflow: hidden; flex-shrink: 0; }}
  .massey-bar {{ height: 100%; border-radius: 4px; }}
  .massey-rating {{ min-width: 46px; text-align: right; font-weight: 700;
                    font-variant-numeric: tabular-nums; font-size: 0.85rem; }}

  /* ── Prediction Accuracy ────────────────────────────────────── */
  .acc-container {{
    background: var(--surface); border-radius: var(--radius);
    box-shadow: var(--shadow); overflow: hidden;
  }}
  .acc-header {{
    background: var(--navy); color: white;
    padding: 0.8rem 1.25rem;
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.75rem;
  }}
  .acc-title {{ font-size: 1rem; font-weight: 700; margin: 0; }}
  .acc-explainer {{
    font-size: 0.78rem; color: var(--text-muted);
    padding: 0.6rem 1.25rem 0; margin: 0;
  }}
  .acc-week {{
    border-top: 1px solid var(--border);
  }}
  .acc-week:first-of-type {{ border-top: none; }}
  .acc-week-header {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem;
    padding: 0.6rem 1.25rem;
    background: #F7F5F3;
    border-bottom: 1px solid var(--border);
  }}
  .acc-week-label {{ font-weight: 700; font-size: 0.9rem; color: var(--navy); }}
  .acc-summary {{ font-size: 0.8rem; }}
  .acc-good {{ color: var(--win-fg); font-weight: 600; }}
  .acc-mid  {{ color: #B45309; font-weight: 600; }}
  .acc-poor {{ color: var(--loss-fg); font-weight: 600; }}
  .acc-table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  .acc-table {{
    width: 100%; border-collapse: collapse; font-size: 0.84rem; min-width: 480px;
  }}
  .acc-table thead th {{
    background: #F0EDE8; color: var(--text-muted);
    padding: 0.4rem 0.75rem; text-align: left;
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: .04em;
    white-space: nowrap;
  }}
  .acc-row td {{ padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  .acc-row:last-child td {{ border-bottom: none; }}
  .acc-correct {{ background: var(--win-bg); }}
  .acc-wrong   {{ background: var(--loss-bg); }}
  .acc-tossup  {{ background: var(--tie-bg); }}
  .acc-nodata  {{ opacity: 0.55; }}
  .acc-dp      {{ font-weight: 600; }}
  .acc-matchup {{ display: flex; align-items: center; gap: 0.4rem; white-space: nowrap; }}
  .acc-home    {{ font-weight: 600; }}
  .acc-vs      {{ color: var(--text-muted); font-size: 0.75rem; }}
  .acc-away    {{ color: var(--text-muted); }}
  .acc-pred    {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap; }}
  .acc-actual  {{ white-space: nowrap; font-weight: 500; }}
  .acc-err     {{ text-align: right; font-variant-numeric: tabular-nums; color: var(--text-muted); white-space: nowrap; }}
  .acc-icon    {{ text-align: center; font-size: 1rem; width: 2rem; }}

  /* ── Spotlight ───────────────────────────────────────────────── */
  .spotlight-container {{ display: flex; flex-direction: column; gap: 1.5rem; }}
  .stats-cards {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
  .stat-card {{
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 1rem 1.5rem;
    text-align: center; flex: 1; min-width: 100px;
  }}
  .stat-card.accent {{ background: var(--navy); color: white; }}
  .stat-card.pos-card {{ background: var(--win-bg); color: var(--win-fg); }}
  .stat-card.neg-card {{ background: var(--loss-bg); color: var(--loss-fg); }}
  .stat-val {{ font-size: 1.8rem; font-weight: 800; }}
  .stat-lbl {{ font-size: 0.78rem; color: inherit; opacity: 0.7; margin-top: 2px; text-transform: uppercase; letter-spacing: .05em; }}

  .section-title {{
    font-size: 1.1rem; font-weight: 700; color: var(--navy);
    padding-bottom: 0.5rem; border-bottom: 2px solid var(--blue);
    margin-bottom: 0.5rem;
  }}
  .detail-table {{ width: 100%; border-collapse: collapse; background: var(--surface); border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); font-size: 0.9rem; }}
  .detail-table th {{ background: var(--navy); color: white; padding: 0.6rem 1rem; text-align: left; font-size: 0.8rem; text-transform: uppercase; letter-spacing:.05em; }}
  .detail-table td {{ padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); }}
  .detail-table tbody tr:last-child td {{ border-bottom: none; }}
  .detail-table tbody tr:hover {{ background: #FDF5F5; }}
  .win-row  {{ background: var(--win-bg); }}
  .loss-row {{ background: var(--loss-bg); }}
  .tie-row  {{ background: var(--tie-bg); }}
  .score-cell {{ font-weight: 700; }}
  .res-chip {{ display: inline-block; width: 24px; text-align:center; border-radius: 4px; font-weight: 700; font-size: 0.85rem; padding: 0.1rem 0; }}
  .win-chip  {{ background: var(--win-fg); color: white; }}
  .loss-chip {{ background: var(--loss-fg); color: white; }}
  .tie-chip  {{ background: var(--tie-fg); color: white; }}
  .pred-win  {{ color: var(--win-fg); font-weight: 600; }}
  .pred-loss {{ color: var(--loss-fg); font-weight: 600; }}
  .pred-toss {{ color: var(--tie-fg); font-weight: 600; }}
  .pred-none {{ color: var(--text-muted); font-style: italic; }}

  .outlook-cards {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
  .outlook-card {{
    flex: 1; min-width: 120px;
    border-radius: var(--radius);
    padding: 1rem;
    text-align: center;
    box-shadow: var(--shadow);
  }}
  .outlook-card.best    {{ background: var(--win-bg); color: var(--win-fg); }}
  .outlook-card.current {{ background: var(--pickle-bg); color: var(--pickle-dark); font-weight: 700; }}
  .outlook-card.worst   {{ background: var(--loss-bg); color: var(--loss-fg); }}
  .outlook-label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: .05em; font-weight: 600; opacity: .8; }}
  .outlook-rec   {{ font-size: 1.6rem; font-weight: 800; margin: 0.25rem 0; }}
  .outlook-pts   {{ font-size: 0.9rem; font-weight: 600; }}
  .no-data {{ color: var(--text-muted); font-style: italic; padding: 1rem; }}

  /* ── Season Schedule ────────────────────────────────────────── */
  .schedule-container {{ display: flex; flex-direction: column; gap: 1.5rem; }}
  .schedule-overview {{ display: flex; flex-wrap: wrap; gap: 1rem; margin-bottom: 0.5rem; }}
  .schedule-weeks {{ display: flex; flex-direction: column; gap: 0.5rem; }}
  .sched-week {{
    display: flex; align-items: center; gap: 1rem;
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 0.8rem 1.2rem;
  }}
  .sched-week-off {{ background: #F9FAFB; opacity: 0.7; }}
  .sched-week-num {{ font-weight: 700; min-width: 90px; color: var(--navy); }}
  .sched-week-date {{ flex: 1; color: var(--text); }}
  .sched-week-info {{ color: var(--text-muted); font-size: 0.85rem; min-width: 180px; }}
  .sched-status {{
    display: inline-block; padding: 0.2rem 0.7rem; border-radius: 12px;
    font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: .03em;
  }}
  .sched-played  {{ background: var(--win-bg); color: var(--win-fg); }}
  .sched-upcoming {{ background: #DBEAFE; color: #1D4ED8; }}
  .sched-tbd     {{ background: var(--tie-bg); color: var(--tie-fg); }}
  .sched-off     {{ background: #F1F5F9; color: var(--text-muted); }}

  /* ── Footer ──────────────────────────────────────────────────── */
  .site-footer {{
    text-align: center;
    padding: 1.5rem;
    color: var(--text-muted);
    font-size: 0.8rem;
    border-top: 1px solid var(--border);
    margin-top: 2rem;
  }}

  /* ── Responsive ──────────────────────────────────────────────── */

  /* Tablet (≤768px) */
  @media (max-width: 768px) {{
    .tabs-wrap {{ padding: 1rem; }}
    .standings-grid {{ grid-template-columns: 1fr; }}
    .predictions-grid {{ grid-template-columns: 1fr; }}
    .stats-cards {{ gap: 0.75rem; }}
    .outlook-cards {{ gap: 0.75rem; }}
  }}

  /* Mobile (≤600px) */
  @media (max-width: 600px) {{
    /* Header: stack last-updated below title, hide season badge */
    .site-header {{
      flex-wrap: wrap;
      padding: 0.9rem 1rem;
      gap: 0.4rem;
    }}
    .site-header .logo img {{ height: 38px; width: 38px; }}
    .site-header h1 {{ font-size: 1.25rem; }}
    .site-header .subtitle {{ font-size: 0.78rem; }}
    .site-header .season-badge {{ display: none; }}
    .site-header .last-updated {{
      width: 100%;
      text-align: center;
      font-size: 0.72rem;
      padding: 0.25rem 0.75rem;
    }}

    /* Hero */
    .hero {{ grid-template-columns: 1fr; padding: 1rem; gap: 0; }}
    .hero-right {{ display: none; }}
    .hero-left .team-name {{ font-size: 1.5rem; }}
    .hero-stat {{ font-size: 0.8rem; padding: 0.2rem 0.5rem; }}
    .hero-game {{ min-width: 90px; padding: 0.5rem 0.75rem; }}

    /* Tabs */
    .tabs-wrap {{ padding: 0.75rem; }}
    .tab-btn {{ padding: 0.45rem 0.7rem; font-size: 0.8rem; }}

    /* Standings — tighten padding; GF/GA already hidden via container query */
    .standings-table {{ font-size: 0.8rem; }}
    .standings-table th,
    .standings-table td {{ padding: 0.4rem 0.35rem; }}
    .team-cell {{ max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .standings-grid {{ gap: 1rem; }}

    /* Scrollable detail tables (Disco Pickles tab) */
    .spotlight-container .section-title + * {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    .detail-table {{ font-size: 0.82rem; }}
    .detail-table th, .detail-table td {{ padding: 0.45rem 0.6rem; white-space: nowrap; }}
    .detail-table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: var(--radius); box-shadow: var(--shadow); }}

    /* Game rows */
    .game-row {{ padding: 0.55rem 0.75rem; gap: 0.4rem; }}
    .game-score {{ min-width: 56px; font-size: 0.88rem; }}
    .game-time {{ font-size: 0.72rem; }}
    .game-teams {{ font-size: 0.84rem; }}

    /* Predictions grid */
    .predictions-grid {{ grid-template-columns: 1fr; gap: 1rem; }}

    /* Schedule */
    .sched-week {{ flex-wrap: wrap; gap: 0.35rem; padding: 0.6rem 0.75rem; }}
    .sched-week-num {{ min-width: auto; font-size: 0.9rem; }}
    .sched-week-date {{ font-size: 0.9rem; }}
    .sched-week-info {{ min-width: auto; width: 100%; font-size: 0.8rem; }}

    /* Stat cards */
    .stats-cards {{ gap: 0.5rem; }}
    .stat-card {{ min-width: 75px; padding: 0.75rem 0.5rem; }}
    .stat-val {{ font-size: 1.4rem; }}
    .stat-lbl {{ font-size: 0.7rem; }}

    /* Outlook cards */
    .outlook-cards {{ gap: 0.5rem; }}
    .outlook-card {{ min-width: 75px; padding: 0.75rem 0.5rem; }}
    .outlook-rec {{ font-size: 1.2rem; }}
    .outlook-pts {{ font-size: 0.8rem; }}

    /* Misc */
    .section-title {{ font-size: 0.95rem; }}
    .tabs-wrap {{ padding: 0.75rem; }}
  }}
</style>
</head>
<body>

<!-- ── Site Header ──────────────────────────────────────────────────────────── -->
<header class="site-header">
  <div class="logo">
    <img src="https://cdn4.sportngin.com/attachments/logo_graphic/8511/0262/YouthHockey_Logos_vF-04_WhiteJersey_small.png"
         alt="PHHL Hurricanes House League" title="Polar Hurricanes House League">
  </div>
  <div>
    <h1>10U Advance League</h1>
    <div class="subtitle">Polar Hurricanes House League</div>
  </div>
  <div class="season-badge">Spring 2026</div>
  <div class="last-updated">🔄 Last updated: {LAST_UPDATED}</div>
</header>

<!-- ── Tab Navigation (directly below header) ───────────────────────────────── -->
<nav class="tab-nav tab-nav-top">
  <button class="tab-btn active" onclick="showTab('playoffs',this)">🏆 Playoffs</button>
  <button class="tab-btn" onclick="showTab('standings',this)">Standings</button>
  <button class="tab-btn" onclick="showTab('results',this)">Game Results</button>
  <button class="tab-btn" onclick="showTab('predictions',this)">Predictions</button>
  <button class="tab-btn" onclick="showTab('schedule',this)">Season Schedule</button>
  <button class="tab-btn" onclick="showTab('spotlight',this)">🥒 Disco Pickles</button>
</nav>

<!-- ── Champions hero (only when Disco win the title) ───────────────────────── -->
{champ_banner}

<!-- ── West Division Playoffs title (above the semifinal strip) ──────────────── -->
<div class="po-head po-head-top">
  <h2 class="po-title">🏆 West Division Playoffs</h2>
  <p class="po-sub">6 teams · single elimination · two-game guarantee. Disco Pickles are the <strong>#1 seed</strong> ({dp_w}-{dp_l}-{dp_t}, undefeated). June 4–7, 2026.</p>
</div>

<!-- ── Playoff Strip ────────────────────────────────────────────────────────── -->
{playoff_strip}

<!-- ── Main Content ─────────────────────────────────────────────────────────── -->
<main class="tabs-wrap">
  <!-- Standings -->
  <div id="tab-standings" class="tab-panel">
    <div class="standings-grid">
      {standings_html}
    </div>
  </div>

  <!-- Results -->
  <div id="tab-results" class="tab-panel">
    {results_html}
  </div>

  <!-- Predictions -->
  <div id="tab-predictions" class="tab-panel">
    {predictions_html}
  </div>

  <!-- Playoffs -->
  <div id="tab-playoffs" class="tab-panel active">
    {playoffs_html}
  </div>

  <!-- Season Schedule -->
  <div id="tab-schedule" class="tab-panel">
    {schedule_html}
  </div>

  <!-- Team Spotlight -->
  <div id="tab-spotlight" class="tab-panel">
    {spotlight_html}
  </div>
</main>

<footer class="site-footer">
  Generated {TODAY.strftime('%B %-d, %Y')} · 10U Advance League · Polar Ice Hockey League · {len(completed_games)} games completed · {len(upcoming_games)} upcoming · {len(placeholder_games)} TBD
</footer>

<script>
  function showTab(name, btn) {{
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    btn.classList.add('active');
  }}
  function goPlayoffs() {{
    const btn = [...document.querySelectorAll('.tab-btn')].find(b => /Playoffs/.test(b.textContent));
    if (btn) showTab('playoffs', btn);
    document.querySelector('.tabs-wrap').scrollIntoView({{ behavior: 'smooth' }});
  }}
</script>
{confetti_html}
</body>
</html>'''

# ── Write output ───────────────────────────────────────────────────────────────
out_path = '/Users/wgibbons/Desktop/10U_ADV_League_6130.html'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(HTML)

print(f"Written: {out_path}")
print(f"File size: {len(HTML):,} bytes")
print(f"Teams: {len(team_names)}")
print(f"Total games: {len(games)} ({len(real_games)} real + {len(placeholder_games)} placeholder)")
print(f"Completed: {len(completed_games)}")
print(f"Upcoming: {len(upcoming_games)}")
print(f"Placeholder (TBD): {len(placeholder_games)}")
print(f"Disco Pickles record: {dp_w}-{dp_l}-{dp_t}, {dp_pts} pts, GD {gd_sign(dp_gd_v)}")
print(f"Disco Pickles division rank: #{dp_div_rank}")
print()
print("Division standings summary:")
for div in ['North','South','West']:
    print(f"  {div}:")
    for tid in divisions[div]:
        s = stats[tid]
        print(f"    {team_names[tid]:35s}  {s['w']}-{s['l']}-{s['t']}  {pts(s)}pts  GD{gd_sign(gd(s))}")

