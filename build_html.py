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
    completed = (hs is not None and vs is not None and start_d < TODAY)
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

# ── GD map for predictions ─────────────────────────────────────────────────────
# Build average GD per matchup (home, visitor)
raw_gd = defaultdict(list)
for g in completed_games:
    raw_gd[(g['hid'], g['vid'])].append(g['hs'] - g['vs'])

gd_map = {k: sum(v)/len(v) for k, v in raw_gd.items()}

# Map team IDs to short names for prediction descriptions
def short_name(tid):
    n = team_names.get(tid, str(tid))
    parts = n.split(' - ')
    return parts[1] if len(parts) > 1 else n

def predict(h_id, v_id, gd_map):
    paths = []
    h_opps = [b for (a,b) in gd_map if a==h_id] + [a for (a,b) in gd_map if b==h_id]
    v_opps = [b for (a,b) in gd_map if a==v_id] + [a for (a,b) in gd_map if b==v_id]

    def get_gd(t1, t2):
        if (t1,t2) in gd_map: return gd_map[(t1,t2)]
        if (t2,t1) in gd_map: return -gd_map[(t2,t1)]
        return None

    for x in set(h_opps) & set(v_opps):
        if x in (h_id, v_id): continue
        gh = get_gd(h_id, x)
        gv = get_gd(v_id, x)
        if gh is not None and gv is not None:
            paths.append({'pred': gh - gv, 'weight': 1.0, 'hops': 1,
                         'desc': f"via {short_name(x)}"})

    for x in set(h_opps):
        if x in (h_id, v_id): continue
        for y in set(v_opps):
            if y in (h_id, v_id, x): continue
            if (x,y) not in gd_map and (y,x) not in gd_map: continue
            gh  = get_gd(h_id, x)
            gxy = get_gd(x, y)
            gv  = get_gd(v_id, y)
            if all(z is not None for z in [gh, gxy, gv]):
                paths.append({'pred': gh + gxy - gv, 'weight': 0.5, 'hops': 2,
                             'desc': f"via {short_name(x)}→{short_name(y)}"})

    if not paths: return None, 0, []
    tw   = sum(p['weight'] for p in paths)
    avg  = sum(p['pred']*p['weight'] for p in paths) / tw
    one_hop = sum(1 for p in paths if p['hops']==1)
    conf = min(1.0, one_hop/3 + 0.1*sum(1 for p in paths if p['hops']==2)/3)
    return avg, conf, paths

# Pre-compute predictions for all upcoming games
for g in upcoming_games:
    pgd, conf, paths = predict(g['hid'], g['vid'], gd_map)
    g['pred_gd']   = pgd
    g['pred_conf'] = conf
    g['pred_paths']= paths

# ── Disco Pickles specific ─────────────────────────────────────────────────────
dp_completed = [g for g in completed_games if DISCO_ID in (g['hid'], g['vid'])]
dp_upcoming  = [g for g in upcoming_games  if DISCO_ID in (g['hid'], g['vid'])]

dp_stats = stats[DISCO_ID]
dp_w, dp_l, dp_t = dp_stats['w'], dp_stats['l'], dp_stats['t']
dp_pts  = pts(dp_stats)
dp_gf   = dp_stats['gf']
dp_ga   = dp_stats['ga']
dp_gd_v = dp_gf - dp_ga

# Win probability from pred_gd (sigmoid-like)
def win_prob(pgd, conf):
    if pgd is None: return 0.5
    import math
    raw = 1 / (1 + math.exp(-pgd * 0.4))
    return 0.5 + (raw - 0.5) * conf

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
    return ''.join(spans) if spans else '<span class="badge badge-none">-</span>'

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
                    pred_str = f'<span class="pred-score">~{pgd:+.1f} GD</span>' if pgd is not None \
                               else '<span class="pred-score">No prediction</span>'
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

    # ── COMPLETED (newest first) ──────────────────────────────────────────────
    if completed_by_date:
        html += '<div class="results-section-hdr">✅ Completed Games <span class="sort-indicator">↓ Newest First</span></div>'
        html += game_rows_html(completed_by_date, sorted(completed_by_date.keys(), reverse=True))

    html += '</div>'
    return html

# ── Build predictions tab ──────────────────────────────────────────────────────
def build_predictions_tab():
    html = '<div class="predictions-grid">'
    if not upcoming_games:
        html += '<p class="no-data">No upcoming games found.</p>'
        html += '</div>'
        return html

    for g in upcoming_games:
        h_name = short_name(g['hid'])
        v_name = short_name(g['vid'])
        is_dp  = (DISCO_ID in (g['hid'], g['vid']))
        pgd    = g.get('pred_gd')
        conf   = g.get('pred_conf', 0)
        paths  = g.get('pred_paths', [])

        if pgd is None:
            card_cls = 'pred-card no-pred'
            prediction_html = '<div class="pred-result">No prediction data available</div>'
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

            conf_pct = int(conf * 100)
            wp = win_prob(pgd, conf)
            wp_pct = int(wp * 100)

            # Top paths
            one_hop = [p for p in paths if p['hops']==1][:3]
            two_hop = [p for p in paths if p['hops']==2][:2]
            path_items = ''
            for p in one_hop:
                path_items += f'<li><span class="hop-badge h1">1-hop</span> {esc(p["desc"])} → {p["pred"]:+.1f}</li>'
            for p in two_hop:
                path_items += f'<li><span class="hop-badge h2">2-hop</span> {esc(p["desc"])} → {p["pred"]:+.1f}</li>'
            paths_html = f'<ul class="pred-paths">{path_items}</ul>' if path_items else ''

            prediction_html = f'''
            <div class="pred-result">
              <div class="pred-winner">{'🏒 ' if is_dp and ((g["hid"]==DISCO_ID and pgd>0) or (g["vid"]==DISCO_ID and pgd<0)) else ''}Predicted: <strong>{esc(winner)}</strong> {f"by {margin:.1f}" if winner!="Toss-up" else "(±{:.1f})".format(margin)}</div>
              <div class="pred-gd-val">GD: {pgd:+.2f}</div>
              <div class="conf-bar-wrap">
                <span class="conf-label">Home Win %</span>
                <div class="conf-bar"><div class="conf-fill" style="width:{wp_pct}%"></div></div>
                <span class="conf-pct">{wp_pct}%</span>
              </div>
              <div class="conf-bar-wrap">
                <span class="conf-label">Confidence</span>
                <div class="conf-bar"><div class="conf-fill conf-fill-blue" style="width:{conf_pct}%"></div></div>
                <span class="conf-pct">{conf_pct}%</span>
              </div>
              {paths_html}
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

    html += '</div>'
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
    for g in dp_upcoming[:3]:
        is_home = (g['hid'] == DISCO_ID)
        opp     = short_name(g['vid'] if is_home else g['hid'])
        ha      = 'vs' if is_home else '@'
        pgd     = g.get('pred_gd')
        conf    = g.get('pred_conf', 0)
        if pgd is not None:
            dp_pgd = pgd if is_home else -pgd
            wp = win_prob(dp_pgd, conf)
            pred_str = f'{int(wp*100)}% win'
            pred_cls = 'hero-pred-win' if dp_pgd > 1 else ('hero-pred-loss' if dp_pgd < -1 else 'hero-pred-toss')
        else:
            pred_str = 'No pred'; pred_cls='hero-pred-toss'
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

# ── Assemble full HTML ─────────────────────────────────────────────────────────
standings_html = ''
for div_name in ['West','North','South']:
    standings_html += build_standings_div(div_name, divisions[div_name])

results_html    = build_results_tab()
predictions_html= build_predictions_tab()
spotlight_html  = build_spotlight_tab()
schedule_html   = build_schedule_tab()
hero_upcoming   = build_hero_upcoming()

dp_div_rank = divisions['West'].index(DISCO_ID) + 1 if DISCO_ID in divisions['West'] else '?'

HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🏒 10U Advance League – Season 2026</title>
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
  .hero-upcoming {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
  .hero-game {{
    background: rgba(255,255,255,.12);
    border: 1px solid rgba(255,255,255,.2);
    border-radius: 8px;
    padding: 0.6rem 1rem;
    text-align: center; min-width: 110px;
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

  /* ── Tabs ────────────────────────────────────────────────────── */
  .tabs-wrap {{ padding: 1.5rem; }}
  .tab-nav {{
    display: flex; gap: 0.25rem;
    border-bottom: 2px solid var(--border);
    margin-bottom: 1.5rem;
    overflow-x: auto;
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
  .standings-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px,1fr)); gap: 1.5rem; }}
  .standings-div {{
    background: var(--surface);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }}
  .div-header {{
    background: var(--navy);
    color: white;
    padding: 0.75rem 1rem;
    font-size: 1rem; font-weight: 700;
  }}
  .standings-table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
  .standings-table thead tr {{ background: #F5F0F0; }}
  .standings-table th {{
    padding: 0.5rem 0.6rem; text-align: center;
    font-size: 0.75rem; text-transform: uppercase; letter-spacing: .05em;
    color: var(--text-muted); font-weight: 600;
    border-bottom: 1px solid var(--border);
  }}
  .standings-table th:nth-child(2) {{ text-align: left; }}
  .standings-table td {{
    padding: 0.55rem 0.6rem; text-align: center;
    border-bottom: 1px solid var(--border);
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

  /* badges */
  .badge {{ display: inline-block; padding: 0.15rem 0.4rem; border-radius: 4px; font-size: 0.72rem; font-weight: 700; }}
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
  .pred-gd-val {{ font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; }}
  .conf-bar-wrap {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; font-size: 0.78rem; }}
  .conf-label {{ width: 80px; color: var(--text-muted); flex-shrink: 0; }}
  .conf-bar {{ flex: 1; height: 6px; background: #E2E8F0; border-radius: 3px; overflow: hidden; }}
  .conf-fill {{ height: 100%; background: var(--win-fg); border-radius: 3px; transition: width .3s; }}
  .conf-fill-blue {{ background: var(--blue); }}
  .conf-pct {{ width: 32px; text-align: right; font-weight: 600; color: var(--text-muted); }}
  .pred-paths {{ list-style: none; margin-top: 0.6rem; font-size: 0.78rem; color: var(--text-muted); display: flex; flex-direction: column; gap: 0.2rem; }}
  .hop-badge {{ display: inline-block; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.7rem; font-weight: 700; margin-right: 0.25rem; }}
  .h1 {{ background: #FFE4E4; color: #9B0000; }}
  .h2 {{ background: #EDE9FE; color: #6D28D9; }}

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

    /* Standings — hide GF & GA on small screens (GD summarises them) */
    .standings-table {{ font-size: 0.8rem; }}
    .standings-table th:nth-child(7),
    .standings-table td:nth-child(7),
    .standings-table th:nth-child(8),
    .standings-table td:nth-child(8) {{ display: none; }}
    .standings-table th,
    .standings-table td {{ padding: 0.45rem 0.4rem; }}
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
  <div class="season-badge">Season 2026</div>
  <div class="last-updated">🔄 Last updated: {LAST_UPDATED}</div>
</header>

<!-- ── Disco Pickles Hero ───────────────────────────────────────────────────── -->
<section class="hero">
  <div class="hero-left">
    <div class="division-badge">⭐ West Division Leaders</div>
    <div class="team-name">🥒 Disco Pickles</div>
    <div class="record-line">
      <span class="hero-stat">{dp_w}-{dp_l}-{dp_t}</span>
      <span class="hero-stat">{dp_pts} pts</span>
      <span class="hero-stat">GF: {dp_gf}</span>
      <span class="hero-stat">GA: {dp_ga}</span>
      <span class="hero-stat">GD: {gd_sign(dp_gd_v)}</span>
    </div>
    <div class="hero-upcoming-title">Upcoming Games</div>
    {hero_upcoming}
  </div>
  <div class="hero-right">
    <div class="hero-rank">#{dp_div_rank}</div>
    <div class="hero-rank-lbl">West Division</div>
  </div>
</section>

<!-- ── Main Content ─────────────────────────────────────────────────────────── -->
<main class="tabs-wrap">
  <nav class="tab-nav">
    <button class="tab-btn active" onclick="showTab('standings',this)">Standings</button>
    <button class="tab-btn" onclick="showTab('results',this)">Game Results</button>
    <button class="tab-btn" onclick="showTab('predictions',this)">Predictions</button>
    <button class="tab-btn" onclick="showTab('schedule',this)">Season Schedule</button>
    <button class="tab-btn" onclick="showTab('spotlight',this)">🥒 Disco Pickles</button>
  </nav>

  <!-- Standings -->
  <div id="tab-standings" class="tab-panel active">
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
</script>
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

