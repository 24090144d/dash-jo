import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from i18n_enforcement import enforce as enforce_i18n

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data'
PATTERN = re.compile(r'^([^-]+)-([^-]+)-(.+)-JO-([^-]+)-([^.]+)\.csv$', re.IGNORECASE)


def parse_dt(v):
    if not v:
        return None
    for fmt in ('%d %b %Y %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            pass
    return None


def mins(a, b):
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 60.0, 2)


def safe_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def avg(vals):
    x = [v for v in vals if v is not None]
    return round(sum(x) / len(x), 2) if x else 0.0


def p90(vals):
    x = sorted([v for v in vals if v is not None])
    if not x:
        return 0.0
    return round(x[int((len(x) - 1) * 0.9)], 2)


def topn_pairs(rows, key, n=10):
    c = {}
    for r in rows:
        k = (r.get(key) or 'Unknown').strip() or 'Unknown'
        c[k] = c.get(k, 0) + 1
    out = sorted(c.items(), key=lambda t: t[1], reverse=True)
    if len(out) > 16:
        out = out[:10]
    return [{'name': k, 'y': v} for k, v in out[:n]]


def detect_grain(rows):
    ds = [datetime.strptime(r['created_date'], '%Y-%m-%d') for r in rows if r.get('created_date')]
    if not ds:
        return 'daily'
    days = (max(ds) - min(ds)).days + 1
    if days > 90:
        return 'monthly'
    if days >= 30:
        return 'weekly'
    return 'daily'


def group_metric(rows, dim, metric):
    g = defaultdict(list)
    for r in rows:
        k = (r.get(dim) or 'Unknown').strip() or 'Unknown'
        g[k].append(r)
    out = []
    for k, rs in g.items():
        if metric == 'count':
            v = len(rs)
        elif metric == 'timeout':
            v = sum(x['timeout_flag'] for x in rs)
        elif metric == 'sla_breach':
            v = sum(x['sla_breach_flag'] for x in rs)
        elif metric == 'sum_quantity':
            v = sum(x['quantity'] for x in rs)
        elif metric == 'sum_breach_min':
            v = sum(x['sla_breach_min'] for x in rs)
        elif metric == 'avg_response':
            v = avg([x['response_min'] for x in rs])
        elif metric == 'avg_resolution':
            v = avg([x['resolution_min'] for x in rs])
        elif metric == 'p90_response':
            v = p90([x['response_min'] for x in rs])
        elif metric == 'p90_resolution':
            v = p90([x['resolution_min'] for x in rs])
        elif metric == 'sla_rate':
            comp = [x for x in rs if x['completed_flag'] == 1]
            if comp:
                v = round((1 - (sum(x['sla_breach_flag'] for x in comp) / len(comp))) * 100, 2)
            else:
                v = 0
        else:
            v = 0
        out.append((k, v))
    out.sort(key=lambda t: t[1], reverse=True)
    if len(out) > 16:
        out = out[:10]
    return [k for k, _ in out], [v for _, v in out]


def kpis(rows):
    total = len(rows)
    comp = sum(r['completed_flag'] for r in rows)
    completed_rows = [r for r in rows if r['completed_flag'] == 1]
    breaches = sum(r['sla_breach_flag'] for r in completed_rows)
    return [
        {'label': 'Total Job Orders', 'value': total, 'sub': 'All JO records', 'formula': 'COUNT(JobOrder)'},
        {'label': 'Completion Rate', 'value': round((comp / total) * 100, 2) if total else 0, 'sub': '% completed jobs', 'formula': 'SUM(completed_flag)/COUNT(*)*100'},
        {'label': 'SLA Compliance', 'value': round((1 - breaches / len(completed_rows)) * 100, 2) if completed_rows else 0, 'sub': '% completed within SLA', 'formula': '(1-SUM(sla_breach_flag)/SUM(completed_flag))*100'},
        {'label': 'Timeout Rate', 'value': round((sum(r['timeout_flag'] for r in rows) / total) * 100, 2) if total else 0, 'sub': '% timed out', 'formula': 'SUM(timeout_flag)/COUNT(*)*100'},
        {'label': 'Escalation Rate', 'value': round((sum(r['escalated_flag'] for r in rows) / total) * 100, 2) if total else 0, 'sub': '% escalated', 'formula': 'SUM(escalated_flag)/COUNT(*)*100'},
        {'label': 'Reassignment Rate', 'value': round((sum(r['reassigned_flag'] for r in rows) / total) * 100, 2) if total else 0, 'sub': '% reassigned', 'formula': 'SUM(reassigned_flag)/COUNT(*)*100'},
        {'label': 'Avg Response (min)', 'value': avg([r['response_min'] for r in rows]), 'sub': 'Create to acknowledge', 'formula': 'AVG(response_min)'},
        {'label': 'P90 Response (min)', 'value': p90([r['response_min'] for r in rows]), 'sub': '90th percentile response', 'formula': 'P90(response_min)'},
        {'label': 'Avg Resolution (min)', 'value': avg([r['resolution_min'] for r in rows]), 'sub': 'Create to complete', 'formula': 'AVG(resolution_min)'},
        {'label': 'Total Quantity', 'value': round(sum(r['quantity'] for r in rows), 2), 'sub': 'Total requested quantity', 'formula': 'SUM(quantity)'},
    ]


def build_chart_specs(rows, page):
    specs = [
        ('Service Category -> Service Items (Drilldown)', 'donut_drilldown_count', 'Shows where demand is concentrated. Click a category slice to drill into its top service items. Impact: concentrated demand can overload teams and slow fulfillment. Resolution: rebalance staffing, pre-stage inventory, and standardize high-volume request handling for the largest item clusters.', 'COUNT(*) by category then item'),
        ('JO Closing Rate vs Jobs Trend by week', 'line2axis_week', 'Weekly workload (bars) versus closure efficiency (line) in ascending week order. Impact: rising jobs with falling close rate indicates backlog risk. Resolution: add short-term capacity, prioritize aging jobs, and enforce daily closure targets until rate stabilizes.', 'COUNT(*) and completed% by created_week'),
        ('SLA Compliance vs Jobs Trend by week', 'line2axis_week_sla', 'Compares weekly incoming volume with SLA performance in ascending week order. Impact: SLA dips during high-volume weeks reveal process bottlenecks. Resolution: deploy surge playbook, tighten handoff SLAs, and monitor breach-prone queues hourly.', 'COUNT(*) and SLA% by created_week'),
        ('Timeout Trend', 'column_week', 'Weekly timeout volume trend to detect service interruptions early. Impact: timeout spikes reduce guest satisfaction and increase repeat contacts. Resolution: identify root-cause weeks, fix routing/escalation delays, and set alert thresholds for timeout spikes.', 'SUM(timeout_flag) by created_week'),
        ('Status vs Top 10 Departments', 'stack_bar_status_dept', 'Vertical stacked view of status mix across the top 10 departments by volume. Impact: high open/pending share in specific departments signals queue congestion. Resolution: redistribute tickets, clear blockers, and set department-level WIP limits with daily review.', 'COUNT(*) by department and status'),
        ('Top 10 Service Category Volume', 'bar2axis_close', 'Shows demand (bars) and close rate (line) by top categories. Impact: high-volume/low-close categories are critical performance gaps. Resolution: assign category owners, create playbooks, and track close-rate recovery by category weekly.', 'COUNT(*) and completed% by category'),
        ('Top 10 Service Item Volume', 'bar', 'Ranks the most requested service items. Impact: item concentration can create recurring operational strain. Resolution: bundle common tasks, automate repetitive steps, and pre-allocate resources for top items.', 'COUNT(*) by service_item'),
        ('Top 10 Assigned Department Volume', 'bar', 'Shows departments receiving the highest assignment load. Impact: uneven load can cause response delays and burnout. Resolution: rebalance dispatch rules and cross-train teams to absorb peaks.', 'COUNT(*) by assigned_department'),
        ('Top 10 Created By Department Volume', 'bar', 'Shows request-origin departments generating the most JOs. Impact: large demand sources may indicate upstream process gaps. Resolution: run preventive actions with source departments to reduce avoidable requests.', 'COUNT(*) by created_by_department'),
        ('Top 10 Completed Department Volume', 'bar', 'Shows departments completing the highest JO volume. Impact: low completion share versus assignment share may indicate execution bottlenecks. Resolution: compare assigned vs completed mix and remove completion blockers.', 'COUNT(*) by completed_department'),
        ('Top Location Volume', 'bar', 'Highlights locations with the largest JO demand. Impact: hotspots can degrade on-site service quality if unmanaged. Resolution: deploy location-specific staffing, stock, and preventive maintenance actions.', 'COUNT(*) by location'),
        ('Avg Response by Service Category -> Service Items (Drilldown)', 'donut_drilldown_avg_response', 'Average first-response time by category; click to drill into item-level contributors. Impact: slow first response directly impacts guest perception. Resolution: define fast-response SOPs for worst items and introduce response-time alerts.', 'AVG(response_min) by category then item'),
        ('Avg Resolution by Service Category -> Service Items (Drilldown)', 'donut_drilldown_avg_resolution', 'Average end-to-end resolution time by category with item drilldown. Impact: long resolution cycles reduce operational throughput. Resolution: remove approval/parts delays and set item-level turnaround standards.', 'AVG(resolution_min) by category then item'),
        ('SLA Breach Minutes by Service Category -> Service Items (Drilldown)', 'donut_drilldown_breach_min', 'Total breach minutes concentration by category with item drilldown. Impact: concentrated breach minutes identify where SLA risk is financially and reputationally highest. Resolution: prioritize chronic breach items for process redesign and escalation governance.', 'SUM(sla_breach_min) by category then item'),
        ('Escalation by Service Category -> Service Items (Drilldown)', 'donut_drilldown_escalation', 'Escalation concentration by category; click for item-level problem areas. Impact: high escalation indicates service instability or unclear ownership. Resolution: strengthen first-line decision rights, update runbooks, and clarify escalation triggers.', 'SUM(escalated_flag) by category then item'),
        ('Top Reassignment by Department', 'bar_reassign', 'Departments with the highest reassignment volume. Impact: frequent reassignment adds cycle time and accountability gaps. Resolution: improve assignment accuracy rules, skill mapping, and triage quality at intake.', 'SUM(reassigned_flag) by department'),
        ('Response P90 by Service Category -> Service Items (Drilldown)', 'donut_drilldown_p90_response', 'P90 response time exposes tail-risk delays by category and item. Impact: long-tail response outliers hurt VIP/peak-time experience. Resolution: enforce priority routing and exception handling for high-P90 items.', 'P90(response_min) by category then item'),
        ('Resolution P90 by Service Category -> Service Items (Drilldown)', 'donut_drilldown_p90_resolution', 'P90 resolution time shows worst-case completion behavior by category and item. Impact: tail resolution delays drive complaints and SLA penalties. Resolution: target root-cause items with dedicated recovery plans and stricter completion SLAs.', 'P90(resolution_min) by category then item'),
    ]
    return [{'title': t, 'type': ty, 'categories': [], 'data': [], 'note': n, 'formula': f, 'show_values': True} for t, ty, n, f in specs]


def build_rows(path, chain, code, hotel):
    rows = []
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for r in reader:
            created = parse_dt(r.get('Created Date/Time', ''))
            ack = parse_dt(r.get('Job Acknowledged Date & Time', ''))
            done = parse_dt(r.get('Job Completed Date & Time', ''))
            init_dl = parse_dt(r.get('Initial Deadline', ''))
            ext_dl = parse_dt(r.get('Extended Deadline', ''))
            final_dl = ext_dl or init_dl
            status = (r.get('Job Status', '') or '').strip()
            rows.append({
                'chain': chain,
                'hotel_code': code,
                'hotel_name': hotel,
                'job_order': r.get('Job Order', ''),
                'created_ts': created.strftime('%Y-%m-%d %H:%M:%S') if created else '',
                'created_date': created.strftime('%Y-%m-%d') if created else '',
                'created_week': created.strftime('%G-W%V') if created else '',
                'created_month': created.strftime('%Y-%m') if created else '',
                'status': status,
                'department_name': r.get('Department Name', ''),
                'service_category': r.get('Service Item Category', ''),
                'service_item': r.get('Service Item', ''),
                'created_by_department': r.get('Created By (Department)', ''),
                'assigned_department': r.get('Assigned To (Department)', ''),
                'completed_department': r.get('Completed By (Department)', ''),
                'location': r.get('Location', ''),
                'quantity': safe_float(r.get('Quantity', '0')),
                'response_min': mins(created, ack),
                'resolution_min': mins(created, done),
                'execution_min': mins(ack, done),
                'sla_breach_flag': 1 if (done and final_dl and done > final_dl) else 0,
                'sla_breach_min': max(0.0, mins(final_dl, done) or 0.0),
                'timeout_flag': 1 if status.lower() == 'timeout' else 0,
                'completed_flag': 1 if status.lower() == 'completed' else 0,
                'escalated_flag': 1 if (r.get('Escalation Group', '') or '').strip() else 0,
                'reassigned_flag': 1 if (r.get('Reassigned Job', '') or '').strip() else 0,
            })
    return rows


def model(chain, page, title, rows, links, accent):
    dr = sorted([r['created_date'] for r in rows if r['created_date']])
    top20 = sorted(rows, key=lambda x: (x['resolution_min'] or 0, x['sla_breach_min']), reverse=True)[:20]
    return {
        'chain': chain,
        'page': page,
        'title': title,
        'date_range': [dr[0], dr[-1]] if dr else ['', ''],
        'accent': accent,
        'alt_accent': '#0E7470' if accent == '#C55A10' else '#C55A10',
        'kpis': kpis(rows),
        'charts': build_chart_specs(rows, page),
        'records': rows,
        'record_chunks': [],
        'top20': [
            {
                'job_order': r['job_order'],
                'created_ts': r['created_ts'],
                'status': r['status'],
                'department_name': r['department_name'],
                'service_item': r['service_item'],
                'resolution_min': r['resolution_min'] or 0,
                'sla_breach_min': r['sla_breach_min'] or 0,
            }
            for r in top20
        ],
        'links': links,
    }


def render_html(m, data_url):
    return f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{m['chain']} {m['page']} JO</title>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>
<link href='https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,800&family=JetBrains+Mono:wght@400;600&family=Manrope:wght@400;500;700&display=swap' rel='stylesheet'>
<script src='https://code.highcharts.com/highcharts.js'></script>
<script src='https://code.highcharts.com/highcharts-more.js'></script>
<script src='https://code.highcharts.com/modules/solid-gauge.js'></script>
<script src='https://code.highcharts.com/modules/drilldown.js'></script>
<script src='https://code.highcharts.com/maps/modules/map.js'></script>
<script src='https://code.highcharts.com/modules/exporting.js'></script>
<script src='https://code.highcharts.com/modules/export-data.js'></script>
<script src='https://code.highcharts.com/modules/full-screen.js'></script>
<style>
:root {{
  --paper:#f3ebdc; --ink:#1f1b16; --muted:#5d5348; --card:#fcf7ee;
  --accent:{m['accent']}; --alt:{m['alt_accent']}; --rule:#d6c7b0;
}}
[data-theme='dark'] {{
  --paper:#151311; --ink:#f2ece4; --muted:#b5a99a; --card:#1f1c18;
  --accent:{m['accent']}; --alt:{m['alt_accent']}; --rule:#3f372e;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font-family:'Manrope',sans-serif; position:relative; }}
body::before {{ content:''; position:fixed; inset:0; pointer-events:none; opacity:.18; mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220' viewBox='0 0 220 220'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='220' height='220' filter='url(%23n)' opacity='0.25'/%3E%3C/svg%3E"); }}
.header-row {{ display:flex; justify-content:space-between; align-items:flex-start; gap:14px; margin-bottom:10px; }}
.top {{ display:flex; justify-content:flex-end; flex-wrap:wrap; gap:10px; align-items:center; }}
select,button,input {{ padding:8px 12px; border:2px solid #3b342d; border-radius:12px; background:var(--card); color:var(--ink); font-family:'JetBrains Mono',monospace; font-size:12px; }}
.switch-pill {{ background:#0E7470; color:#f8f6f2; font-weight:700; }}
.switch-group {{ display:inline-flex; align-items:center; gap:8px; background:#0E7470; color:#f8f6f2; border:2px solid #3b342d; border-radius:12px; padding:7px 10px; font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:700; }}
.switch-group select {{ padding:3px 8px; border:1.5px solid #2e2a24; border-radius:8px; background:#f4efe3; color:#1f1b16; font-weight:600; }}
.btn-export {{ background:#C55A10; color:#fff; font-weight:700; }}
.wrap {{ padding:14px; }}
h3 {{ margin:0 0 2px; font-family:'Fraunces',serif; font-size:32px; line-height:1.08; }}
.sub {{ color:var(--muted); margin-bottom:0; font-size:13px; font-family:'JetBrains Mono',monospace; letter-spacing:.03em; }}
.filters-card {{ position:relative; margin-top:8px; margin-bottom:10px; background:#f8f3e8; border-radius:12px; border:1.5px solid #c9bba5; box-shadow:0 1px 0 rgba(0,0,0,.04); padding:11px 12px 11px 16px; display:flex; flex-wrap:wrap; align-items:center; gap:8px; }}
.filters-card::before {{ content:''; position:absolute; left:0; top:0; bottom:0; width:6px; background:#C55A10; border-radius:12px 0 0 12px; }}
.filters-label {{ font-family:'JetBrains Mono',monospace; font-size:11px; letter-spacing:.12em; color:var(--muted); text-transform:uppercase; margin-right:6px; }}
.chip {{ border:2px solid #4a433b; border-radius:12px; background:var(--card); padding:7px 11px; font-family:'JetBrains Mono',monospace; font-size:12px; }}
.hotel-dropdown {{ position:relative; }}
.hotel-panel {{ position:absolute; top:44px; left:0; z-index:30; width:160px; background:var(--card); border:2px solid #3b342d; border-radius:12px; padding:8px; display:none; }}
.hotel-panel.show {{ display:block; }}
.hotel-row {{ display:flex; align-items:center; gap:6px; margin:4px 0; font-family:'Manrope',sans-serif; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin-top:0; }}
.kpi {{ background:var(--card); border-left:4px solid var(--accent); border-radius:8px; padding:10px; transition:border-color .2s; }}
.kpi:hover {{ border-left-color:var(--alt); }}
.k-label {{ font-family:'JetBrains Mono',monospace; font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
.k-value {{ font-family:'Fraunces',serif; font-size:1.7rem; font-weight:800; line-height:1.1; margin-top:3px; }}
.k-sub {{ color:var(--muted); font-size:.67rem; margin-top:3px; }}
.section-title {{ margin:16px 0 8px; font-family:'Fraunces',serif; font-size:1.05rem; }}
.chart-grid {{ margin-top:14px; display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
.chart-card {{ background:var(--card); border-left:4px solid var(--accent); border-radius:8px; padding:10px; transition:border-color .2s; }}
.chart-card:hover {{ border-left-color:var(--alt); }}
.c-title {{ font-family:'Fraunces',serif; font-size:.95rem; font-weight:600; margin-bottom:8px; }}
.c-box {{ height:310px; }}
.meta {{ border-top:1px solid var(--rule); margin-top:8px; padding-top:7px; font-size:.67rem; color:var(--muted); }}
.meta code {{ font-family:'JetBrains Mono',monospace; background:color-mix(in srgb, var(--accent) 16%, transparent); color:var(--ink); padding:2px 6px; border-radius:999px; }}
.table-wrap {{ margin-top:16px; background:var(--card); border-left:4px solid var(--accent); border-radius:8px; padding:10px; overflow:auto; }}
.table-wrap h2 {{ margin:2px 0 8px; font-family:'Fraunces',serif; font-size:1.05rem; }}
table {{ width:100%; border-collapse:collapse; min-width:900px; }}
th,td {{ border:1px solid var(--rule); padding:6px 7px; font-size:12px; text-align:left; }}
th {{ font-family:'JetBrains Mono',monospace; font-size:11px; text-transform:uppercase; letter-spacing:.06em; background:color-mix(in srgb, var(--paper) 70%, var(--card)); }}
.info-wrap {{ margin-top:16px; background:var(--card); border-left:4px solid var(--accent); border-radius:8px; padding:10px; }}
.info-wrap h2 {{ margin:2px 0 10px; font-family:'Fraunces',serif; font-size:1.05rem; }}
.info-item {{ margin:0 0 10px; padding:8px; border:1px solid var(--rule); border-radius:8px; }}
.info-title {{ font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.02em; }}
.info-note {{ font-size:12px; color:var(--muted); margin-top:4px; }}
.info-formula {{ font-size:12px; margin-top:3px; }}
.info-formula code {{ font-family:'JetBrains Mono',monospace; background:color-mix(in srgb, var(--accent) 16%, transparent); padding:2px 6px; border-radius:999px; }}
.loading-overlay {{ position:fixed; inset:0; background:color-mix(in srgb, var(--paper) 84%, transparent); display:none; align-items:center; justify-content:center; z-index:9999; }}
.loading-overlay.show {{ display:flex; }}
.loading-card {{ background:var(--card); border:2px solid #3b342d; border-left:4px solid var(--accent); border-radius:12px; padding:16px 18px; min-width:220px; text-align:center; }}
.spinner {{ width:30px; height:30px; border:3px solid #c8b9a4; border-top-color:var(--accent); border-radius:50%; margin:0 auto 10px; animation:spin .8s linear infinite; }}
.loading-text {{ font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--muted); letter-spacing:.05em; text-transform:uppercase; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
@media (max-width:1200px) {{ .kpi-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); }} h3{{font-size:30px;}} }}
@media (max-width:900px) {{ .header-row{{flex-direction:column; align-items:stretch;}} .kpi-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .chart-grid {{ grid-template-columns:1fr; }} h3{{font-size:26px;}} }}
@media print {{
  .top {{ display:none; }}
  .filters-card {{ display:none !important; }}
  .section-title {{ display:none !important; }}
  .loading-overlay {{ display:none !important; }}
  #topIncidents, #dictionary {{ display:none !important; }}
  .kpi-grid {{ grid-template-columns:repeat(5,minmax(0,1fr)) !important; gap:10px !important; page-break-after:always; }}
  .chart-grid {{ display:block; }}
  .chart-card {{ break-before:page; page-break-before:always; min-height:95vh; }}
  .c-box {{ height:65vh; }}
}}
</style>
</head>
<body>
<div id='loadingOverlay' class='loading-overlay'>
  <div class='loading-card'>
    <div class='spinner'></div>
    <div class='loading-text' id='loadingText'>Loading dashboard...</div>
  </div>
</div>
<div class='wrap'>
  <div class='header-row'>
    <div>
      <h3 id='pageTitle'>{m['title']}</h3>
      <div class='sub' id='pageSubtitle'>FCS1 Job Order</div>
    </div>
    <div class='top' id='controls'>
      <label class='switch-group' id='switchLabel'>Switch:
        <select id='pageSwitch'></select>
      </label>
      <label class='lang-group' id='langLabel'>Language:
        <select id='langSelect'></select>
      </label>
      <button id='btnTheme'>Toggle Dark/Light</button>
      <button id='btnExportPdf' class='btn-export'>Export (PDF)</button>
    </div>
  </div>
  <section id='filterCard' class='filters-card'>
    <div class='filters-label' id='filtersLabel'>Filters</div>
    <div class='hotel-dropdown'>
      <button id='hotelBtn' class='chip'>▼ All Hotels</button>
      <div id='hotelPanel' class='hotel-panel'></div>
    </div>
    <label id='fromLabel'>From:</label><input id='fromDate' type='date' class='chip'>
    <label id='toLabel'>To:</label><input id='toDate' type='date' class='chip'>
    <button id='applyFilters' class='chip'>Apply</button>
    <button class='chip quick' data-days='1'>1 day</button>
    <button class='chip quick' data-days='7'>1 week</button>
    <button class='chip quick' data-days='14'>2 weeks</button>
    <button class='chip quick' data-days='30'>1 month</button>
    <button class='chip quick' data-days='90'>3 months</button>
    <button class='chip quick' data-days='180'>6 months</button>
    <button class='chip quick' data-days='365'>1 year</button>
    <button id='resetFilters' class='chip'>Reset</button>
  </section>
  <div class='kpi-grid' id='kpis'></div>
  <div class='section-title' id='execTitle'>Executive Analytics</div>
  <div class='chart-grid' id='featuredCharts'></div>
  <div class='section-title' id='coreTitle'>Core Dashboard Charts</div>
  <div class='chart-grid' id='charts'></div>
  <section id='topIncidents' class='table-wrap'>
    <h2 id='top20Title'>Top 20 JO</h2>
    <div id='top20'></div>
  </section>
  <section id='dictionary' class='info-wrap'>
    <h2 id='dictionaryTitle'>KPI & Chart Dictionary</h2>
    <div id='infoList'></div>
  </section>
  <section id='metaCard'></section>
</div>
<script>
let MODEL = null;
let FILTERED = [];
let CURRENT_LANG = 'en';
let I18N = {{}};
const DATA_URL = '{data_url}';
const LANG_OPTIONS = [
  ['en','English'],
  ['es','Español'],
  ['zh-CN','中文(简体)'],
  ['zh-TW','中文(繁體)'],
  ['ko','한국어'],
  ['ms','Bahasa Melayu'],
  ['th','ไทย'],
  ['vi','Tiếng Việt'],
  ['ja','日本語']
];
const CHARTS = {{}};
const FEATURED_CHARTS = {{}};
function $(id) {{ return document.getElementById(id); }}
function showLoading() {{ $('loadingOverlay').classList.add('show'); }}
function hideLoading() {{ $('loadingOverlay').classList.remove('show'); }}
function t(v) {{ return (I18N && Object.prototype.hasOwnProperty.call(I18N, v)) ? I18N[v] : v; }}
let resizeTimer = null;
function reflowAllCharts() {{
  Object.values(FEATURED_CHARTS).forEach(ch => {{ try {{ ch.reflow(); }} catch(e) {{}} }});
  Object.values(CHARTS).forEach(ch => {{ try {{ ch.reflow(); }} catch(e) {{}} }});
}}

async function loadLanguage(code) {{
  CURRENT_LANG = code || 'en';
  try {{ localStorage.setItem('jo_lang', CURRENT_LANG); }} catch(e) {{}}
  if (CURRENT_LANG === 'en') {{
    I18N = {{}};
    applyLanguageChrome();
    renderAll();
    return;
  }}
  const res = await fetch(`../${{CURRENT_LANG}}_lang.json`, {{ cache:'no-store' }});
  I18N = await res.json();
  applyLanguageChrome();
  renderAll();
}}

function applyLanguageChrome() {{
  document.title = t(MODEL && MODEL.title ? MODEL.title : document.title);
  $('pageTitle').textContent = t(MODEL && MODEL.title ? MODEL.title : $('pageTitle').textContent);
  $('pageSubtitle').textContent = t('FCS1 Job Order');
  $('switchLabel').childNodes[0].nodeValue = t('Switch:') + ' ';
  $('langLabel').childNodes[0].nodeValue = t('Language:') + ' ';
  $('filtersLabel').textContent = t('Filters');
  $('fromLabel').textContent = t('From:');
  $('toLabel').textContent = t('To:');
  $('applyFilters').textContent = t('Apply');
  $('resetFilters').textContent = t('Reset');
  $('btnTheme').textContent = t('Toggle Dark/Light');
  $('btnExportPdf').textContent = t('Export (PDF)');
  $('execTitle').textContent = t('Executive Analytics');
  $('coreTitle').textContent = t('Core Dashboard Charts');
  $('top20Title').textContent = t('Top 20 JO');
  $('dictionaryTitle').textContent = t('KPI & Chart Dictionary');
  $('hotelBtn').textContent = '▼ ' + t('All Hotels');
  document.querySelectorAll('.quick').forEach((b) => {{
    const days = b.dataset.days;
    const map = {{'1':'1 day','7':'1 week','14':'2 weeks','30':'1 month','90':'3 months','180':'6 months','365':'1 year'}};
    if (map[days]) b.textContent = t(map[days]);
  }});
  if ($('loadingText')) $('loadingText').textContent = t('Loading dashboard...');
}}

function renderAll() {{
  renderKpis();
  renderFeaturedCharts();
  renderCharts();
  renderTop20();
  renderDictionary();
  hideLoading();
}}

function hcColors() {{
  const cs = getComputedStyle(document.documentElement);
  const ink = cs.getPropertyValue('--ink').trim();
  const muted = cs.getPropertyValue('--muted').trim();
  const card = cs.getPropertyValue('--card').trim();
  const accent = cs.getPropertyValue('--accent').trim();
  const alt = cs.getPropertyValue('--alt').trim();
  return {{ ink, muted, card, accent, alt }};
}}

function applyThemeToHighcharts() {{
  const c = hcColors();
  Highcharts.setOptions({{
    chart: {{ backgroundColor: 'transparent', style: {{ fontFamily: 'Manrope, sans-serif' }} }},
    title: {{ style: {{ color: c.ink }} }},
    xAxis: {{ labels: {{ style: {{ color: c.muted }} }}, lineColor: c.muted, tickColor: c.muted }},
    yAxis: {{ labels: {{ style: {{ color: c.muted }} }}, gridLineColor: c.muted+'33', title: {{ style: {{ color: c.muted }} }} }},
    legend: {{ itemStyle: {{ color: c.ink }}, itemHoverStyle: {{ color: c.accent }} }},
    tooltip: {{ backgroundColor: c.card, style: {{ color: c.ink }} }},
    colors: [c.accent, c.alt, '#8C6A43', '#7A5D3E', '#5E4A35', '#A38A6B']
  }});
}}

function renderKpis() {{
  const box = $('kpis');
  box.innerHTML = '';
  const ks = computeKpis(FILTERED);
  ks.forEach((k, i) => {{
    const d = document.createElement('div');
    d.className = 'kpi';
    d.style.borderLeftColor = (i === 2 || i === 4) ? 'var(--alt)' : 'var(--accent)';
    d.innerHTML = `<div class='k-label'>${{t(k.label)}}</div><div class='k-value'>${{fmtSmart(Number(k.value||0))}}</div><div class='k-sub'>${{t(k.sub)}}</div>`;
    box.appendChild(d);
  }});
}}

function makeOptions(c, i) {{
  const isPie = c.type === 'pie';
  const nItems = (c.categories || []).length;
  const isVertical = ['column','line','spline','area'].includes(c.type);
  const isHorizontal = ['bar'].includes(c.type);
  const showDataLabels = isPie || (isVertical && nItems < 15) || (isHorizontal && nItems < 11);
  if (c.type === 'donut_drilldown') {{
    return {{
      chart: {{ type: 'pie' }},
      title: {{ text: '' }},
      exporting: {{ enabled: true }},
      credits: {{ enabled: false }},
      tooltip: {{ pointFormatter: function() {{ return `<span style="color:${{this.color}}">●</span> ${{this.name}}: <b>${{fmtSmart(this.y)}}</b><br/>`; }} }},
      plotOptions: {{ pie: {{ innerSize:'62%', dataLabels: {{ enabled:true, formatter:function() {{ return `${{this.point.name}}: ${{fmtSmart(this.y)}}`; }} }} }} }},
      series: [{{ name: c.title, colorByPoint: true, data: c.data || [] }}],
      drilldown: {{ series: c.drilldownSeries || [] }}
    }};
  }}
  if (c.type === 'line2axis_week') {{
    return {{
      chart: {{ zoomType:'xy' }},
      title: {{ text: '' }},
      exporting: {{ enabled: true }},
      credits: {{ enabled: false }},
      xAxis: [{{ categories: c.categories || [] }}],
      yAxis: [{{ title: {{ text:'Jobs' }} }}, {{ title: {{ text:'Closing Rate %' }}, opposite:true, min:0, max:100 }}],
      plotOptions: {{ series: {{ dataLabels: {{ enabled: (c.categories || []).length < 15 }} }} }},
      series: [
        {{ type:'column', name:'Jobs', data:c.data || [] }},
        {{ type:'spline', name:'Closing Rate %', yAxis:1, data:c.data2 || [] }}
      ]
    }};
  }}
  if (c.type === 'line2axis_week_sla') {{
    return {{
      chart: {{ zoomType:'xy' }},
      title: {{ text: '' }},
      exporting: {{ enabled: true }},
      credits: {{ enabled: false }},
      xAxis: [{{ categories: c.categories || [] }}],
      yAxis: [{{ title: {{ text:'Jobs' }} }}, {{ title: {{ text:'SLA Compliance %' }}, opposite:true, min:0, max:100 }}],
      plotOptions: {{ series: {{ dataLabels: {{ enabled: (c.categories || []).length < 15 }} }} }},
      series: [
        {{ type:'column', name:'Jobs', data:c.data || [] }},
        {{ type:'spline', name:'SLA Compliance %', yAxis:1, data:c.data2 || [] }}
      ]
    }};
  }}
  if (c.type === 'bar2axis_close') {{
    return {{
      chart: {{ zoomType:'xy' }},
      title: {{ text: '' }},
      exporting: {{ enabled: true }},
      credits: {{ enabled: false }},
      xAxis: [{{ categories: c.categories || [] }}],
      yAxis: [{{ title: {{ text:'JO Volume' }} }}, {{ title: {{ text:'Closing Rate %' }}, opposite:true, min:0, max:100 }}],
      plotOptions: {{ series: {{ dataLabels: {{ enabled: (c.categories || []).length < 15 }} }} }},
      series: [
        {{ type:'column', name:'JO Volume', data:c.data || [] }},
        {{ type:'spline', name:'Closing Rate %', yAxis:1, data:c.data2 || [] }}
      ]
    }};
  }}
  if (c.type === 'stack_bar_status_dept') {{
    return {{
      chart: {{ type:'column' }},
      title: {{ text: '' }},
      exporting: {{ enabled: true }},
      credits: {{ enabled: false }},
      xAxis: {{ categories: c.categories || [] }},
      yAxis: {{ min:0, title: {{ text:'Jobs' }}, stackLabels: {{ enabled: false }} }},
      legend: {{ enabled:true }},
      plotOptions: {{
        series: {{ stacking:'normal', dataLabels: {{ enabled: (c.categories || []).length < 11 }} }}
      }},
      series: c.series || []
    }};
  }}
  return {{
    chart: {{ type: c.type }},
    title: {{ text: '' }},
    exporting: {{ enabled: true }},
    credits: {{ enabled: false }},
    legend: {{ enabled: true }},
    xAxis: isPie ? undefined : {{ categories: c.categories }},
    yAxis: isPie ? undefined : {{ title: {{ text: null }} }},
    tooltip: {{ pointFormatter: function() {{
      const n = (this.category !== undefined && this.category !== null) ? (this.category + ': ') : ((this.name ? this.name + ': ' : ''));
      return `<span style="color:${{this.color}}">●</span> ${{n}}<b>${{fmtSmart(this.y)}}</b><br/>`;
    }} }},
    plotOptions: {{
      pie: {{ dataLabels: {{ enabled: true, formatter:function() {{ return `${{this.point.name}}: ${{fmtSmart(this.y)}}`; }} }} }},
      series: {{ dataLabels: {{ enabled: showDataLabels, formatter: function() {{
        if (this.point && this.point.name !== undefined) return this.point.name + ': ' + fmtSmart(this.y);
        return (this.y !== undefined ? fmtSmart(this.y) : '');
      }} }} }}
    }},
    series: [isPie ? {{ name: c.title, data: c.data }} : {{ name: c.title, data: c.data }}]
  }};
}}

function groupCount(rows, dim) {{
  const m = new Map();
  rows.forEach(r => {{
    const k = (r[dim] || 'Unknown').toString().trim() || 'Unknown';
    m.set(k, (m.get(k) || 0) + 1);
  }});
  const arr = [...m.entries()].sort((a,b)=>b[1]-a[1]);
  return arr;
}}

function buildFeatured() {{
  const rows = FILTERED;
  const byHotel = groupCount(rows, 'hotel_code');
  const byStatus = groupCount(rows, 'status');
  const byCategory = groupCount(rows, 'service_category');
  const byDayMap = new Map();
  rows.forEach(r => {{
    const d = r.created_date || 'Unknown';
    byDayMap.set(d, (byDayMap.get(d) || 0) + 1);
  }});
  const byDay = [...byDayMap.entries()].sort((a,b)=>a[0].localeCompare(b[0]));
  const byWeekMap = new Map();
  rows.forEach(r => {{
    const w = r.created_week || 'Unknown';
    byWeekMap.set(w, (byWeekMap.get(w) || 0) + 1);
  }});
  const byWeek = [...byWeekMap.entries()].sort((a,b)=>a[0].localeCompare(b[0]));
  const byWeekHotel = new Map();
  rows.forEach(r => {{
    const w = r.created_week || 'Unknown';
    const h = (r.hotel_code || 'Unknown').toString().trim() || 'Unknown';
    if (!byWeekHotel.has(w)) byWeekHotel.set(w, new Map());
    const m = byWeekHotel.get(w);
    m.set(h, (m.get(h) || 0) + 1);
  }});
  const hotelRaceWeeks = [...byWeekHotel.keys()].sort((a,b)=>a.localeCompare(b));
  const hotelRaceFrames = (() => {{
    const running = new Map();
    return hotelRaceWeeks.map(w => {{
      const wk = byWeekHotel.get(w) || new Map();
      for (const [h, v] of wk.entries()) {{
        running.set(h, (running.get(h) || 0) + v);
      }}
      const arr = [...running.entries()].sort((a,b)=>b[1]-a[1]);
      return arr.map(([name, y]) => ({{ name, y }}));
    }});
  }})();
  const joDaily = byDay.map(x => x[1]);
  const qtyDaily = byDay.map(([d]) => rows.filter(r => r.created_date === d).reduce((a,r)=>a+(r.quantity||0),0));
  let joRun = 0;
  let qtyRun = 0;
  const joVals = joDaily.map(v => (joRun += v));
  const qtyByDay = qtyDaily.map(v => (qtyRun += v));
  const compRows = rows.filter(r => (r.completed_flag||0)===1);
  const slaByHotel = byHotel.map(([h]) => {{
    const hrows = compRows.filter(r => r.hotel_code === h);
    if (!hrows.length) return [h, 0];
    const b = hrows.reduce((a,r)=>a+(r.sla_breach_flag||0),0);
    return [h, Number(((1 - b/hrows.length)*100).toFixed(2))];
  }});
  const rr = rows.map(r => [r.response_min||0, r.resolution_min||0]);
  const gaugeVal = Number((compRows.length ? (1 - (compRows.reduce((a,r)=>a+(r.sla_breach_flag||0),0)/compRows.length))*100 : 0).toFixed(2));

  const catDrillSeries = byCategory.map(([cat]) => {{
    const itemMap = new Map();
    rows.filter(r => (r.service_category || 'Unknown') === cat).forEach(r => {{
      const i = (r.service_item || 'Unknown').toString().trim() || 'Unknown';
      itemMap.set(i, (itemMap.get(i) || 0) + 1);
    }});
    const itemData = [...itemMap.entries()].sort((a,b)=>b[1]-a[1]).slice(0,10).map(([i,v]) => [i,v]);
    return {{ id:'cat_'+cat, name:`Items in ${{cat}}`, data:itemData }};
  }});
  const catParent = byCategory.map(([cat,v]) => {{ return {{ name:cat, y:v, drilldown:'cat_'+cat }}; }});

  const hotelDrillSeries = byHotel.map(([h]) => {{
    const deptMap = new Map();
    rows.filter(r => r.hotel_code === h).forEach(r => {{
      const d = (r.department_name || 'Unknown').toString().trim() || 'Unknown';
      deptMap.set(d, (deptMap.get(d) || 0) + 1);
    }});
    const deptData = [...deptMap.entries()].sort((a,b)=>b[1]-a[1]).slice(0,10).map(([d,v]) => [d,v]);
    return {{ id:'hotel_'+h, name:`Department in ${{h}}`, data:deptData }};
  }});
  const hotelParent = byHotel.map(([h,v]) => {{ return {{ name:h, y:v, drilldown:'hotel_'+h }}; }});

  const statusDrillSeries = byStatus.map(([s]) => {{
    const depMap = new Map();
    rows.filter(r => (r.status||'Unknown') === s).forEach(r => {{
      const d = (r.department_name || 'Unknown').toString().trim() || 'Unknown';
      depMap.set(d, (depMap.get(d) || 0) + 1);
    }});
    const depData = [...depMap.entries()].sort((a,b)=>b[1]-a[1]).slice(0,10).map(([d,v]) => [d,v]);
    return {{ id:'status_'+s, name:`Department in ${{s}}`, data:depData }};
  }});
  const statusParent = byStatus.map(([s,v]) => {{ return {{ name:s, y:v, drilldown:'status_'+s }}; }});

  const statusCatDrillSeries = byStatus.map(([s]) => {{
    const catMap = new Map();
    rows.filter(r => (r.status || 'Unknown') === s).forEach(r => {{
      const c = (r.service_category || 'Unknown').toString().trim() || 'Unknown';
      catMap.set(c, (catMap.get(c) || 0) + 1);
    }});
    const catData = [...catMap.entries()].sort((a,b)=>b[1]-a[1]).slice(0,10).map(([c,v]) => [c,v]);
    return {{ id:'status_cat_'+s, name:`Category in ${{s}}`, data:catData }};
  }});
  const statusCatParent = byStatus.map(([s,v]) => {{ return {{ name:s, y:v, drilldown:'status_cat_'+s }}; }});

  const countryMap = new Map();
  rows.forEach(r => {{
    const key = (r.hotel_code === 'WM' || r.hotel_code === 'WP') ? 'mo' : 'cn';
    countryMap.set(key, (countryMap.get(key) || 0) + 1);
  }});
  const worldData = [...countryMap.entries()].map(([k,v]) => {{ return {{ 'hc-key':k, value:v }}; }});
  const wmCount = rows.filter(r => r.hotel_code === 'WM').length;
  const wpCount = rows.filter(r => r.hotel_code === 'WP').length;
  const macauPins = [
    {{ name:`WM: ${{wmCount}}`, lat:22.1987, lon:113.5439, value:wmCount }},
    {{ name:`WP: ${{wpCount}}`, lat:22.2065, lon:113.5560, value:wpCount }}
  ];

  if (MODEL.page === 'OPS' || MODEL.page === 'GM') {{
    const byWeekCat = new Map();
    rows.forEach(r => {{
      const d = r.created_week || 'Unknown';
      const c = (r.service_category || 'Unknown').toString().trim() || 'Unknown';
      if (!byWeekCat.has(d)) byWeekCat.set(d, new Map());
      const m = byWeekCat.get(d);
      m.set(c, (m.get(c) || 0) + 1);
    }});
    const raceDates = [...byWeekCat.keys()].sort((a,b)=>a.localeCompare(b));
    const raceFrames = (() => {{
      const running = new Map();
      return raceDates.map(d => {{
        const wk = byWeekCat.get(d) || new Map();
        for (const [cat, v] of wk.entries()) {{
          running.set(cat, (running.get(cat) || 0) + v);
        }}
        let arr = [...running.entries()].sort((a,b)=>b[1]-a[1]);
        if (arr.length > 10) {{
          const top = arr.slice(0,10);
          const others = arr.slice(10).reduce((a,x)=>a+x[1],0);
          arr = others > 0 ? [...top, ['Others', others]] : top;
        }}
        return arr.map(([name, y]) => ({{ name, y }}));
      }});
    }})();
    const weekSla = byWeek.map(([w]) => {{
      const rs = rows.filter(r => r.created_week === w);
      const c = rs.filter(r => (r.completed_flag||0)===1);
      const b = c.reduce((a,r)=>a+(r.sla_breach_flag||0),0);
      return c.length ? Number(((1 - b/c.length)*100).toFixed(2)) : 0;
    }});
    const weekClose = byWeek.map(([w]) => {{
      const rs = rows.filter(r => r.created_week === w);
      const done = rs.reduce((a,r)=>a+(r.completed_flag||0),0);
      return rs.length ? Number(((done/rs.length)*100).toFixed(2)) : 0;
    }});
    return [
      {{ id:'fd_1', title:'Cumulative Weekly Service Category Share (Donut Race)', type:'donut_race_day', dates: raceDates, frames: raceFrames, note:'Animated cumulative weekly donut race showing long-run share shifts by service category. Impact: sustained cumulative dominance reveals structural demand pressure points. Resolution: rebalance capacity plans, inventory, and preventive actions toward categories with persistent cumulative growth.', formula:'RUNNING_SUM(COUNT(*)) BY service_category OVER created_week (ASC), Top 10 + Others' }},
      {{ id:'fd_2', title:'SLA vs Jobs by week', type:'column', categories: byWeek.map(x=>x[0]), data: byWeek.map(x=>x[1]), data2: weekSla, note:'Week-ascending workload bars with SLA compliance line.', formula:'COUNT(*) and SLA% BY created_week (ASC)' }},
      {{ id:'fd_3', title:'Closing Rate vs Jobs by week', type:'column', categories: byWeek.map(x=>x[0]), data: byWeek.map(x=>x[1]), data2: weekClose, note:'Week-ascending workload bars with closing rate line.', formula:'COUNT(*) and completed% BY created_week (ASC)' }},
      {{ id:'fd_4', title:'Status -> Service Category (Drilldown)', type:'donut_drilldown', data: statusCatParent, drilldownSeries: statusCatDrillSeries, note:'Click a status slice to drill down into service category mix.', formula:'COUNT(*) BY status, then COUNT(*) BY service_category within status' }},
    ];
  }}

  return [
    {{ id:'fd_1', title:'Cumulative Hotel Jobs by Week (Donut Race)', type:'donut_race_day', dates: hotelRaceWeeks, frames: hotelRaceFrames, note:'Animated cumulative weekly donut race of hotel job totals. Impact: persistent growth gap between hotels indicates long-term load imbalance. Resolution: rebalance structural staffing, budget, and support allocation based on cumulative trajectory.', formula:'RUNNING_SUM(COUNT(*)) BY hotel_code OVER created_week (ASC)' }},
    {{ id:'fd_2', title:'Comparison: Hotel JO Volume', type:'bar', categories: byHotel.map(x=>x[0]), data: byHotel.map(x=>x[1]), note:'Compare JO volume across hotels.', formula:'COUNT(*) BY hotel_code' }},
    {{ id:'fd_3', title:'2-Axis: JO vs SLA by Hotel', type:'column', categories: byHotel.map(x=>x[0]), data: byHotel.map(x=>x[1]), data2: slaByHotel.map(x=>x[1]), note:'Volume bars with SLA% line overlay.', formula:'COUNT(*) and SLA% BY hotel_code' }},
    {{ id:'fd_4', title:'Status -> Department (Drilldown)', type:'donut_drilldown', data: statusParent, drilldownSeries: statusDrillSeries, note:'Click a status slice to inspect department distribution under that status.', formula:'COUNT(*) BY status, then COUNT(*) BY department_name within status' }},
    {{ id:'fd_5', title:'JO Count and Quantity Trend', type:'multi_trend', categories: byDay.map(x=>x[0]), data: joVals, data2: qtyByDay, note:'Cumulative running totals of JO count and requested quantity over time to track growth trajectory.', formula:'RUNNING_SUM(COUNT(*)) and RUNNING_SUM(SUM(quantity)) BY created_date' }},
    {{ id:'fd_6', title:'Semi Gauge: SLA Compliance %', type:'semi_gauge', data:[gaugeVal], note:'Semicircular KPI gauge for SLA compliance health.', formula:'(1-SUM(sla_breach)/SUM(completed))*100' }},
    {{ id:'fd_7', title:'World Map Distribution', type:'worldmap', data: worldData, pins: macauPins, note:'Global map with explicit Macau markers for WM/WP visibility near Hong Kong.', formula:'COUNT(*) BY country key (hc-key) + mappoint labels for Macau hotels' }},
  ];
}}

function renderFeaturedCharts() {{
  const box = $('featuredCharts');
  box.innerHTML = '';
  Object.values(FEATURED_CHARTS).forEach(ch => {{ try {{ ch.destroy(); }} catch(e) {{}} }});
  const featured = buildFeatured();
  featured.forEach((c, i) => {{
    const card = document.createElement('div');
    card.className = 'chart-card';
    const n = String(i + 1).padStart(2, '0');
    card.innerHTML = `<div class='c-title'>${{n}}. ${{t(c.title)}}</div><div id='${{c.id}}' class='c-box'></div><div class='meta'><div><strong>${{t('NOTE')}}</strong> ${{t(c.note)}}</div><div><strong>${{t('FORMULA')}}</strong> <code>${{c.formula}}</code></div></div>`;
    box.appendChild(card);
    applyThemeToHighcharts();
    if (c.type === 'donut_race_day') {{
      const dates = c.dates || [];
      const frames = c.frames || [];
      const initDate = dates.length ? dates[0] : '';
      const frameTotal = (arr) => (arr || []).reduce((a,p)=>a + (Number(p.y)||0), 0);
      const inkColor = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim() || '#1f1b16';
      const placeCenterText = (chart) => {{
        if (!chart || !chart.series || !chart.series[0] || !chart.customCenterTotal) return;
        const s = chart.series[0];
        const center = s.center || [chart.plotWidth/2, chart.plotHeight/2];
        const cx = chart.plotLeft + center[0];
        const cy = chart.plotTop + center[1];
        const b = chart.customCenterTotal.getBBox();
        chart.customCenterTotal.attr({{ x: cx - (b.width/2), y: cy + (b.height/4) }});
      }};
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:'pie' }},
        title: {{ text:'' }},
        subtitle: {{ text: initDate ? ('Date: ' + initDate) : '' }},
        credits: {{ enabled:false }},
        plotOptions: {{ pie: {{ innerSize:'62%', dataLabels: {{ enabled:true, formatter:function() {{ return `${{this.point.name}}: ${{fmtSmart(this.y)}}`; }} }} }} }},
        series: [{{ name:'Category Share', colorByPoint:true, data: frames[0] || [] }}]
      }});
      const initChart = FEATURED_CHARTS[c.id];
      if (initChart) {{
        const initTotal = frameTotal(frames[0]);
        const cx = initChart.plotLeft + (initChart.plotWidth / 2);
        const cy = initChart.plotTop + (initChart.plotHeight / 2);
        initChart.customCenterTotal = initChart.renderer.text(fmtSmart(initTotal), cx, cy)
          .attr({{ zIndex:6 }})
          .css({{ fontSize:'26px', fontWeight:'800', color:inkColor, fontFamily:'Fraunces, Georgia, serif' }})
          .add();
        placeCenterText(initChart);
      }}
      if (frames.length > 1) {{
        let idx = 0;
        const stepMs = 1100;
        const loopPauseMs = 10000;
        const tick = () => {{
          const ch = FEATURED_CHARTS[c.id];
          if (!ch || !ch.series || !ch.series[0]) return;
          idx = (idx + 1) % frames.length;
          ch.series[0].setData(frames[idx], true, {{ duration: 350 }});
          ch.setSubtitle({{ text: 'Date: ' + dates[idx] }});
          const total = frameTotal(frames[idx]);
          if (ch.customCenterTotal) {{
            ch.customCenterTotal.attr({{ text: fmtSmart(total) }});
            placeCenterText(ch);
          }}
          const wait = (idx === frames.length - 1) ? loopPauseMs : stepMs;
          setTimeout(tick, wait);
        }};
        setTimeout(tick, stepMs);
      }}
      const cobj = FEATURED_CHARTS[c.id];
      if (cobj) {{
        Highcharts.addEvent(cobj, 'redraw', function() {{
          placeCenterText(this);
        }});
      }}
    }} else if (c.type === 'semi_gauge') {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:'solidgauge' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        pane: {{ startAngle:-90, endAngle:90, center:['50%','75%'], size:'140%', background:[{{ outerRadius:'100%', innerRadius:'60%', shape:'arc' }}] }},
        yAxis: {{ min:0, max:100, lineWidth:0, tickWidth:0, minorTickInterval:null, tickAmount:2, title: {{ text:'%' }}, labels: {{ y:16 }} }},
        plotOptions: {{ solidgauge: {{ dataLabels: {{ y:-14, borderWidth:0, useHTML:true, format:'<div style=\"text-align:center\"><span style=\"font-size:22px\">{{y}}</span><br/><span style=\"font-size:12px;opacity:.75\">SLA</span></div>' }} }} }},
        series: [{{ name:'SLA', data:c.data }}]
      }});
    }} else if (c.type === 'solidgauge') {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:'gauge' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        yAxis: {{ min:0, max:100, title: {{ text:'%' }} }},
        series: [{{ name:'SLA', data:c.data }}]
      }});
    }} else if (c.type === 'donut_drilldown') {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:'pie' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        tooltip: {{ pointFormatter: function() {{ return `<span style="color:${{this.color}}">●</span> ${{this.name}}: <b>${{fmtSmart(this.y)}}</b><br/>`; }} }},
        plotOptions: {{ pie: {{ innerSize:'62%', dataLabels: {{ enabled:true, formatter:function() {{ return `${{this.point.name}}: ${{fmtSmart(this.y)}}`; }} }} }} }},
        series: [{{ name:c.title, colorByPoint:true, data:c.data }}],
        drilldown: {{ series: c.drilldownSeries || [] }}
      }});
    }} else if (c.type === 'multi_trend') {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ zoomType:'xy' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        xAxis: [{{ categories:c.categories }}],
        yAxis: [{{ title: {{ text:'JO Count' }} }}, {{ title: {{ text:'Quantity' }}, opposite:true }}],
        series: [{{ type:'column', name:'JO Count', data:c.data }}, {{ type:'spline', name:'Quantity', yAxis:1, data:c.data2 || [] }}]
      }});
    }} else if (c.type === 'worldmap') {{
      fetch('https://code.highcharts.com/mapdata/custom/world.geo.json')
        .then(r => r.json())
        .then(mapData => {{
          const mapDataWithMO = (c.data || []).map(p => {{
            if (p['hc-key'] === 'mo') return Object.assign({{}}, p, {{ dataLabels: {{ enabled:true }} }});
            return p;
          }});
          FEATURED_CHARTS[c.id] = Highcharts.mapChart(c.id, {{
            chart: {{ map: mapData }},
            title: {{ text:'' }},
            credits: {{ enabled:false }},
            mapNavigation: {{ enabled:true, buttonOptions: {{ verticalAlign:'bottom' }} }},
            colorAxis: {{ min:0 }},
            series: [{{
              data: mapDataWithMO,
              joinBy:'hc-key',
              name:'JO',
              states: {{ hover: {{ color:'#0E7470' }} }},
              dataLabels: {{
                enabled:true,
                allowOverlap:true,
                crop:false,
                overflow:'allow',
                formatter:function() {{
                  if (this.point && this.point['hc-key'] === 'mo') return 'MO: ' + (this.point.value || 0);
                  return (this.point && this.point.value) ? (this.point.name + ': ' + this.point.value) : '';
                }}
              }}
            }},
            {{
              type: 'mapbubble',
              name: 'Macau Hotels',
              color: '#C55A10',
              minSize: 12,
              maxSize: 22,
              data: (c.pins || []).map(p => ({{ lat:p.lat, lon:p.lon, z: Math.max(1, p.value || 0), name:p.name, value:p.value }})),
              marker: {{ lineColor: '#1f1b16', lineWidth: 1 }},
              dataLabels: {{
                enabled: true,
                allowOverlap: true,
                crop: false,
                overflow: 'allow',
                align: 'left',
                x: 10,
                y: -6,
                formatter: function() {{ return this.point && this.point.name ? this.point.name : ''; }},
                style: {{ fontSize: '12px', fontWeight: '700', textOutline: '1px contrast', color:'#1f1b16' }}
              }},
              tooltip: {{ pointFormat: '{{point.name}}' }}
            }}]
          }});
        }})
        .catch(() => {{
          FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
            chart: {{ type:'column' }}, title: {{ text:'Map data unavailable' }}, credits: {{ enabled:false }},
            xAxis: {{ categories: (c.data||[]).map(x=>x['hc-key']) }},
            series: [{{ name:'JO', data:(c.data||[]).map(x=>x.value) }}]
          }});
        }});
    }} else if (c.type === 'scatter') {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:'scatter' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        xAxis: {{ title: {{ text:'Response Min' }} }}, yAxis: {{ title: {{ text:'Resolution Min' }} }},
        series: [{{ name:'Points', data:c.data }}]
      }});
    }} else if (c.type === 'pie') {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:'pie' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        plotOptions: {{ pie: {{ dataLabels: {{ enabled:true }} }} }},
        series: [{{ name:c.title, data:c.data }}]
      }});
    }} else if (c.type === 'column' && c.data2) {{
      const showLabels = (c.categories || []).length < 15;
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ zoomType:'xy' }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        xAxis: [{{ categories:c.categories }}],
        yAxis: [{{ title: {{ text:'JO Count' }} }}, {{ title: {{ text:'SLA %' }}, opposite:true, min:0, max:100 }}],
        series: [
          {{ type:'column', name:'JO', data:c.data, dataLabels: {{ enabled: showLabels, formatter:function() {{ return (this.x !== undefined && this.y !== undefined) ? (this.category + ': ' + fmtSmart(this.y)) : fmtSmart(this.y); }} }} }},
          {{ type:'spline', name:'SLA %', yAxis:1, data:c.data2, dataLabels: {{ enabled: showLabels, formatter:function() {{ return (this.x !== undefined && this.y !== undefined) ? (this.category + ': ' + fmtSmart(this.y)) : fmtSmart(this.y); }} }} }}
        ]
      }});
    }} else {{
      FEATURED_CHARTS[c.id] = Highcharts.chart(c.id, {{
        chart: {{ type:c.type }}, title: {{ text:'' }}, credits: {{ enabled:false }},
        xAxis: {{ categories:c.categories }}, yAxis: {{ title: {{ text:null }} }},
        series: [{{ name:c.title, data:c.data }}]
      }});
    }}
  }});
}}

function renderCharts() {{
  const box = $('charts');
  box.innerHTML = '';
  Object.values(CHARTS).forEach(ch => {{ try {{ ch.destroy(); }} catch(e) {{}} }});
  const dynamicCharts = build_chart_data(FILTERED, MODEL.charts);
  for (let i = 0; i < dynamicCharts.length; i++) {{
    const c = dynamicCharts[i];
    const card = document.createElement('div');
    card.className = 'chart-card';
    const n = String(i + 1).padStart(2, '0');
    card.innerHTML = `<div class='c-title'>${{n}}. ${{t(c.title)}}</div><div id='hc_${{i}}' class='c-box'></div><div class='meta'><div><strong>${{t('NOTE')}}</strong> ${{t(c.note)}}</div><div><strong>${{t('FORMULA')}}</strong> <code>${{c.formula}}</code></div></div>`;
    box.appendChild(card);
    applyThemeToHighcharts();
    CHARTS['hc_'+i] = Highcharts.chart('hc_'+i, makeOptions(c, i));
  }}
}}

function renderTop20() {{
  const top = [...FILTERED].sort((a,b)=>((b.resolution_min||0)-(a.resolution_min||0))).slice(0,20);
  const rows = top.map(r => `<tr><td>${{r.job_order}}</td><td>${{r.created_ts}}</td><td>${{r.status}}</td><td>${{r.department_name}}</td><td>${{r.service_item}}</td><td>${{fmtSmart(Number(r.resolution_min||0))}}</td><td>${{fmtSmart(Number(r.sla_breach_min||0))}}</td></tr>`).join('');
  $('top20').innerHTML = `<table><thead><tr><th>${{t('Job Order')}}</th><th>${{t('Created')}}</th><th>${{t('Status')}}</th><th>${{t('Department')}}</th><th>${{t('Service Item')}}</th><th>${{t('Resolution Min')}}</th><th>${{t('SLA Breach Min')}}</th></tr></thead><tbody>${{rows}}</tbody></table>`;
}}

function fmtSmart(v) {{
  const n = Number(v || 0);
  const abs = Math.abs(n);
  if (abs >= 10000) {{
    const k = n / 1000;
    const out = (Math.abs(k) >= 100) ? k.toFixed(0) : (Math.abs(k) >= 10 ? k.toFixed(1) : k.toFixed(2));
    return out.replace(/\\.0$/, '') + 'K';
  }}
  if (abs >= 1000) return n.toLocaleString(undefined, {{ maximumFractionDigits: 2 }});
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2).replace(/\\.00$/, '');
}}

function format2(n) {{
  return String(n).padStart(2, '0');
}}

function renderDictionary() {{
  const box = $('infoList');
  const kpiNotes = [
    'Total volume of job orders in scope.',
    'Percentage of jobs completed successfully.',
    'Completed jobs delivered within SLA.',
    'Percentage of jobs ending in timeout.',
    'Share of jobs escalated for intervention.',
    'Share of jobs reassigned across teams.',
    'Average minutes from create to acknowledge.',
    '90th percentile of response time.',
    'Average minutes from create to completion.',
    'Total requested quantity across all jobs.'
  ];
  const kpiFormulas = [
    'COUNT(JobOrder)',
    'SUM(completed_flag)/COUNT(*)*100',
    '(1-SUM(sla_breach_flag)/SUM(completed_flag))*100',
    'SUM(timeout_flag)/COUNT(*)*100',
    'SUM(escalated_flag)/COUNT(*)*100',
    'SUM(reassigned_flag)/COUNT(*)*100',
    'AVG(response_min)',
    'P90(response_min)',
    'AVG(resolution_min)',
    'SUM(quantity)'
  ];

  const chunks = [];
  const h = (t) => `<div class='info-item' style='background:color-mix(in srgb, var(--paper) 72%, var(--card));font-weight:700'>${{t}}</div>`;
  chunks.push(h('KPI List'));
  const kpis = computeKpis(FILTERED);
  kpis.forEach((k, i) => {{
    const idx = i + 1;
    chunks.push(
      `<div class='info-item'>` +
      `<div class='info-title'>${{format2(idx)}}.${{t(k.label)}} : KPI</div>` +
      `<div class='info-note'>${{t('NOTE')}}: ${{t(kpiNotes[i] || k.sub || '')}}</div>` +
      `<div class='info-formula'>${{t('FORMULA')}}: <code>${{kpiFormulas[i] || ''}}</code></div>` +
      `</div>`
    );
  }});

  chunks.push(h('Executive Analytics Charts'));
  const featured = buildFeatured();
  featured.forEach((c, i) => {{
    const idx = i + 1;
    chunks.push(
      `<div class='info-item'>` +
      `<div class='info-title'>${{format2(idx)}}.${{t(c.title)}} : ${{c.type}}</div>` +
      `<div class='info-note'>${{t('NOTE')}}: ${{t(c.note)}}</div>` +
      `<div class='info-formula'>${{t('FORMULA')}}: <code>${{c.formula}}</code></div>` +
      `</div>`
    );
  }});

  chunks.push(h('Core Dashboard Charts'));
  const dynamicCharts = build_chart_data(FILTERED, MODEL.charts);
  dynamicCharts.forEach((c, i) => {{
    const idx = i + 1;
    chunks.push(
      `<div class='info-item'>` +
      `<div class='info-title'>${{format2(idx)}}.${{t(c.title)}} : ${{c.type}}</div>` +
      `<div class='info-note'>${{t('NOTE')}}: ${{t(c.note)}}</div>` +
      `<div class='info-formula'>${{t('FORMULA')}}: <code>${{c.formula}}</code></div>` +
      `</div>`
    );
  }});
  box.innerHTML = chunks.join('');
}}

function initPageSwitcher() {{
  const s = $('pageSwitch');
  MODEL.links.forEach(l => {{
    const o = document.createElement('option');
    o.value = l.url;
    o.textContent = l.name;
    if (l.current) o.selected = true;
    s.appendChild(o);
  }});
  s.onchange = () => {{ location.href = s.value; }};
}}

function computeKpis(rows) {{
  const total = rows.length || 1;
  const comp = rows.reduce((a,r)=>a+(r.completed_flag||0),0);
  const crows = rows.filter(r=>(r.completed_flag||0)===1);
  const breaches = crows.reduce((a,r)=>a+(r.sla_breach_flag||0),0);
  const avg = (arr) => arr.length ? arr.reduce((a,b)=>a+b,0)/arr.length : 0;
  const p90 = (arr) => arr.length ? arr.slice().sort((a,b)=>a-b)[Math.floor((arr.length-1)*0.9)] : 0;
  return [
    {{label:'Total Job Orders', value:rows.length, sub:'All JO records'}},
    {{label:'Completion Rate', value:(comp/total)*100, sub:'% completed jobs'}},
    {{label:'SLA Compliance', value:crows.length?((1-breaches/crows.length)*100):0, sub:'% completed within SLA'}},
    {{label:'Timeout Rate', value:(rows.reduce((a,r)=>a+(r.timeout_flag||0),0)/total)*100, sub:'% timed out'}},
    {{label:'Escalation Rate', value:(rows.reduce((a,r)=>a+(r.escalated_flag||0),0)/total)*100, sub:'% escalated'}},
    {{label:'Reassignment Rate', value:(rows.reduce((a,r)=>a+(r.reassigned_flag||0),0)/total)*100, sub:'% reassigned'}},
    {{label:'Avg Response (min)', value:avg(rows.map(r=>r.response_min||0)), sub:'Create to acknowledge'}},
    {{label:'P90 Response (min)', value:p90(rows.map(r=>r.response_min||0)), sub:'90th percentile response'}},
    {{label:'Avg Resolution (min)', value:avg(rows.map(r=>r.resolution_min||0)), sub:'Create to complete'}},
    {{label:'Total Quantity', value:rows.reduce((a,r)=>a+(r.quantity||0),0), sub:'Total requested quantity'}},
  ];
}}

function build_chart_data(rows, baseCharts) {{
  const groupMetric = (dim, metric) => {{
    const m = new Map();
    rows.forEach(r => {{
      const k = (r[dim] || 'Unknown').toString().trim() || 'Unknown';
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(r);
    }});
    const out = [];
    for (const [k, rs] of m.entries()) {{
      let v = 0;
      if (metric === 'count') v = rs.length;
      else if (metric === 'timeout') v = rs.reduce((a,r)=>a+(r.timeout_flag||0),0);
      else if (metric === 'sla_breach') v = rs.reduce((a,r)=>a+(r.sla_breach_flag||0),0);
      else if (metric === 'sum_quantity') v = rs.reduce((a,r)=>a+(r.quantity||0),0);
      else if (metric === 'sum_breach_min') v = rs.reduce((a,r)=>a+(r.sla_breach_min||0),0);
      else if (metric === 'avg_response') v = rs.reduce((a,r)=>a+(r.response_min||0),0)/rs.length;
      else if (metric === 'avg_resolution') v = rs.reduce((a,r)=>a+(r.resolution_min||0),0)/rs.length;
      else if (metric === 'p90_response') {{ const s=rs.map(r=>r.response_min||0).sort((a,b)=>a-b); v=s[Math.floor((s.length-1)*0.9)]||0; }}
      else if (metric === 'p90_resolution') {{ const s=rs.map(r=>r.resolution_min||0).sort((a,b)=>a-b); v=s[Math.floor((s.length-1)*0.9)]||0; }}
      else if (metric === 'sla_rate') {{ const c = rs.filter(r=>(r.completed_flag||0)===1); const b=c.reduce((a,r)=>a+(r.sla_breach_flag||0),0); v=c.length?((1-b/c.length)*100):0; }}
      out.push([k, Number(v.toFixed(2))]);
    }}
    out.sort((a,b)=>b[1]-a[1]);
    const lim = out.length > 16 ? 10 : out.length;
    return out.slice(0, lim);
  }};

  const weekSort = (arr) => arr.sort((a,b) => (a[0] || '').localeCompare(b[0] || ''));
  const weekJobs = weekSort(groupMetric('created_week', 'count'));
  const weekClose = weekSort((() => {{
    const m = new Map();
    rows.forEach(r => {{
      const w = (r.created_week || 'Unknown').toString();
      if (!m.has(w)) m.set(w, []);
      m.get(w).push(r);
    }});
    const out = [];
    for (const [w, rs] of m.entries()) {{
      const done = rs.reduce((a,r)=>a+(r.completed_flag||0),0);
      const rate = rs.length ? (done / rs.length) * 100 : 0;
      out.push([w, Number(rate.toFixed(2))]);
    }}
    return out;
  }})());
  const weekSla = weekSort((() => {{
    const m = new Map();
    rows.forEach(r => {{
      const w = (r.created_week || 'Unknown').toString();
      if (!m.has(w)) m.set(w, []);
      m.get(w).push(r);
    }});
    const out = [];
    for (const [w, rs] of m.entries()) {{
      const c = rs.filter(r => (r.completed_flag||0) === 1);
      const b = c.reduce((a,r)=>a+(r.sla_breach_flag||0),0);
      const rate = c.length ? ((1 - b / c.length) * 100) : 0;
      out.push([w, Number(rate.toFixed(2))]);
    }}
    return out;
  }})());
  const weekTimeout = weekSort(groupMetric('created_week', 'timeout'));

  const buildDrilldown = (metric) => {{
    const catMap = new Map();
    rows.forEach(r => {{
      const cat = (r.service_category || 'Unknown').toString().trim() || 'Unknown';
      if (!catMap.has(cat)) catMap.set(cat, []);
      catMap.get(cat).push(r);
    }});
    let cats = [...catMap.entries()];
    cats = cats.map(([k, rs]) => {{
      let y = 0;
      if (metric === 'count') y = rs.length;
      else if (metric === 'avg_response') y = rs.reduce((a,x)=>a+(x.response_min||0),0)/(rs.length||1);
      else if (metric === 'avg_resolution') y = rs.reduce((a,x)=>a+(x.resolution_min||0),0)/(rs.length||1);
      else if (metric === 'sum_breach_min') y = rs.reduce((a,x)=>a+(x.sla_breach_min||0),0);
      else if (metric === 'escalation') y = rs.reduce((a,x)=>a+(x.escalated_flag||0),0);
      else if (metric === 'p90_response') {{ const s=rs.map(x=>x.response_min||0).sort((a,b)=>a-b); y=s[Math.floor((s.length-1)*0.9)]||0; }}
      else if (metric === 'p90_resolution') {{ const s=rs.map(x=>x.resolution_min||0).sort((a,b)=>a-b); y=s[Math.floor((s.length-1)*0.9)]||0; }}
      return [k, Number(y.toFixed(2)), rs];
    }}).sort((a,b)=>b[1]-a[1]);
    if (cats.length > 16) cats = cats.slice(0, 10);
    const parent = cats.map(([k, y]) => ({{ name:k, y, drilldown:'cat_' + k }}));
    const series = cats.map(([k, _y, rs]) => {{
      const itemMap = new Map();
      rs.forEach(r => {{
        const it = (r.service_item || 'Unknown').toString().trim() || 'Unknown';
        if (!itemMap.has(it)) itemMap.set(it, []);
        itemMap.get(it).push(r);
      }});
      let items = [...itemMap.entries()].map(([it, irs]) => {{
        let y = 0;
        if (metric === 'count') y = irs.length;
        else if (metric === 'avg_response') y = irs.reduce((a,x)=>a+(x.response_min||0),0)/(irs.length||1);
        else if (metric === 'avg_resolution') y = irs.reduce((a,x)=>a+(x.resolution_min||0),0)/(irs.length||1);
        else if (metric === 'sum_breach_min') y = irs.reduce((a,x)=>a+(x.sla_breach_min||0),0);
        else if (metric === 'escalation') y = irs.reduce((a,x)=>a+(x.escalated_flag||0),0);
        else if (metric === 'p90_response') {{ const s=irs.map(x=>x.response_min||0).sort((a,b)=>a-b); y=s[Math.floor((s.length-1)*0.9)]||0; }}
        else if (metric === 'p90_resolution') {{ const s=irs.map(x=>x.resolution_min||0).sort((a,b)=>a-b); y=s[Math.floor((s.length-1)*0.9)]||0; }}
        return [it, Number(y.toFixed(2))];
      }}).sort((a,b)=>b[1]-a[1]);
      if (items.length > 16) items = items.slice(0, 10);
      return {{ id:'cat_' + k, name:'Items in ' + k, data:items }};
    }});
    return {{ data: parent, drilldownSeries: series }};
  }};

  const statusDept = (() => {{
    const topDept = groupMetric('department_name', 'count').map(x=>x[0]).slice(0, 10);
    const statuses = [...new Set(rows.map(r => (r.status || 'Unknown').toString().trim() || 'Unknown'))];
    const series = statuses.map(s => {{
      const data = topDept.map(d => rows.filter(r => ((r.department_name||'Unknown').toString().trim()||'Unknown')===d && ((r.status||'Unknown').toString().trim()||'Unknown')===s).length);
      return {{ name:s, data }};
    }});
    return {{ categories: topDept, series }};
  }})();

  return baseCharts.map(c => {{
    if (c.type === 'line2axis_week') {{
      return {{ ...c, categories: weekJobs.map(x=>x[0]), data: weekJobs.map(x=>x[1]), data2: weekClose.map(x=>x[1]) }};
    }}
    if (c.type === 'line2axis_week_sla') {{
      return {{ ...c, categories: weekJobs.map(x=>x[0]), data: weekJobs.map(x=>x[1]), data2: weekSla.map(x=>x[1]) }};
    }}
    if (c.type === 'column_week') {{
      return {{ ...c, type:'column', categories: weekTimeout.map(x=>x[0]), data: weekTimeout.map(x=>x[1]) }};
    }}
    if (c.type === 'stack_bar_status_dept') {{
      return {{ ...c, categories: statusDept.categories, series: statusDept.series }};
    }}
    if (c.type === 'bar2axis_close') {{
      const vol = groupMetric('service_category', 'count').slice(0, 10);
      const close = vol.map(([cat]) => {{
        const rs = rows.filter(r => ((r.service_category||'Unknown').toString().trim()||'Unknown') === cat);
        const done = rs.reduce((a,r)=>a+(r.completed_flag||0),0);
        const rate = rs.length ? (done / rs.length) * 100 : 0;
        return [cat, Number(rate.toFixed(2))];
      }});
      return {{ ...c, categories: vol.map(x=>x[0]), data: vol.map(x=>x[1]), data2: close.map(x=>x[1]) }};
    }}
    const drillMap = {{
      'donut_drilldown_count':'count',
      'donut_drilldown_avg_response':'avg_response',
      'donut_drilldown_avg_resolution':'avg_resolution',
      'donut_drilldown_breach_min':'sum_breach_min',
      'donut_drilldown_escalation':'escalation',
      'donut_drilldown_p90_response':'p90_response',
      'donut_drilldown_p90_resolution':'p90_resolution'
    }};
    if (drillMap[c.type]) {{
      const dd = buildDrilldown(drillMap[c.type]);
      return {{ ...c, type:'donut_drilldown', data: dd.data, drilldownSeries: dd.drilldownSeries }};
    }}
    const dimMetric = {{
      'Top 10 Service Item Volume':['service_item','count'],
      'Top 10 Assigned Department Volume':['assigned_department','count'],
      'Top 10 Created By Department Volume':['created_by_department','count'],
      'Top 10 Completed Department Volume':['completed_department','count'],
      'Top Location Volume':['location','count'],
      'Top Reassignment by Department':['department_name','reassign']
    }};
    const [dim, metric] = dimMetric[c.title] || ['department_name','count'];
    const grouped = metric === 'reassign'
      ? groupMetric(dim, 'count').map(([k]) => [k, rows.filter(r => ((r[dim]||'Unknown').toString().trim()||'Unknown')===k).reduce((a,r)=>a+(r.reassigned_flag||0),0)]).sort((a,b)=>b[1]-a[1]).slice(0,10)
      : groupMetric(dim, metric).slice(0,10);
    return {{ ...c, type:'bar', categories: grouped.map(x=>x[0]), data: grouped.map(x=>x[1]) }};
  }});
}}

function initFilters() {{
  const hotels = [...new Set(MODEL.records.map(r => r.hotel_code).filter(Boolean))].sort();
  const panel = $('hotelPanel');
  panel.innerHTML = `<label class='hotel-row'><input type='checkbox' id='h_all' checked>All Hotels</label>` +
    hotels.map(h => `<label class='hotel-row'><input type='checkbox' class='h_opt' value='${{h}}' checked>${{h}}</label>`).join('');
  const langSel = $('langSelect');
  langSel.innerHTML = LANG_OPTIONS.map(([code, label]) => `<option value='${{code}}'>${{label}}</option>`).join('');
  langSel.value = CURRENT_LANG || 'en';
  langSel.onchange = () => loadLanguage(langSel.value);
  $('hotelBtn').onclick = () => panel.classList.toggle('show');
  const all = $('h_all');
  const opts = [...panel.querySelectorAll('.h_opt')];
  all.onchange = () => opts.forEach(o => o.checked = all.checked);
  opts.forEach(o => o.onchange = () => {{ all.checked = opts.every(x => x.checked); }});

  $('fromDate').value = MODEL.date_range[0];
  $('toDate').value = MODEL.date_range[1];
  const apply = (days=null) => {{
    if (days !== null) {{
      const end = new Date(MODEL.date_range[1] + 'T00:00:00');
      const start = new Date(end); start.setDate(start.getDate() - days + 1);
      $('fromDate').value = start.toISOString().slice(0,10);
      $('toDate').value = end.toISOString().slice(0,10);
    }}
    const selected = opts.filter(x => x.checked).map(x => x.value);
    const from = $('fromDate').value;
    const to = $('toDate').value;
    FILTERED = MODEL.records.filter(r => {{
      const okHotel = selected.length ? selected.includes(r.hotel_code) : true;
      const okFrom = from ? r.created_date >= from : true;
      const okTo = to ? r.created_date <= to : true;
      return okHotel && okFrom && okTo;
    }});
    $('hotelBtn').textContent = '▼ ' + (selected.length === hotels.length ? t('All Hotels') : selected.join(', '));
    showLoading();
    setTimeout(() => {{
      renderKpis(); renderFeaturedCharts(); renderCharts(); renderTop20(); renderDictionary();
      hideLoading();
    }}, 0);
  }};
  $('applyFilters').onclick = () => apply();
  $('resetFilters').onclick = () => {{
    all.checked = true; opts.forEach(o => o.checked = true);
    $('fromDate').value = MODEL.date_range[0]; $('toDate').value = MODEL.date_range[1];
    apply();
  }};
  [...document.querySelectorAll('.quick')].forEach(b => b.onclick = () => apply(Number(b.dataset.days)));
}}

function applyTheme(theme) {{
  document.documentElement.setAttribute('data-theme', theme);
  try {{ localStorage.setItem('jo_theme', theme); }} catch(e) {{}}
}}

function initTheme() {{
  let t = 'light';
  try {{
    t = localStorage.getItem('jo_theme') || 'light';
  }} catch(e) {{}}
  applyTheme(t === 'dark' ? 'dark' : 'light');
}}

$('btnTheme').onclick = () => {{
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  renderFeaturedCharts();
  renderCharts();
  setTimeout(reflowAllCharts, 0);
}};
$('btnExportPdf').onclick = () => {{
  const chain = (MODEL.chain || 'CHAIN').toString().trim();
  const pageType = (MODEL.page || 'TYPE').toString().trim();
  const moduleName = 'JO';
  const one = (MODEL.records && MODEL.records[0]) ? MODEL.records[0] : null;
  const hotelCode = pageType === 'CORP' ? 'ALL' : ((one && one.hotel_code) ? String(one.hotel_code).trim() : 'ALL');
  const hotelName = pageType === 'CORP' ? 'All Hotels' : ((one && one.hotel_name) ? String(one.hotel_name).trim() : 'All Hotels');
  const safe = (s) => String(s || '').replace(/[\\\\/:*?\"<>|]/g, '-').replace(/\\s+/g, ' ').trim();
  const fname = `${{safe(chain)}}-${{safe(hotelCode)}}-${{safe(hotelName)}}-${{safe(pageType)}}-${{safe(moduleName)}}`;
  const prev = document.title;
  document.title = fname;
  window.print();
  setTimeout(() => {{ document.title = prev; }}, 300);
}};
window.addEventListener('resize', () => {{
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(reflowAllCharts, 120);
}});

async function boot() {{
  showLoading();
  try {{
    initTheme();
    const res = await fetch(DATA_URL, {{ cache: 'no-store' }});
    MODEL = await res.json();
    MODEL.records = Array.isArray(MODEL.records) ? MODEL.records : [];
    if ((!MODEL.records || MODEL.records.length === 0) && Array.isArray(MODEL.record_chunks) && MODEL.record_chunks.length) {{
      const parts = await Promise.all(
        MODEL.record_chunks.map(c =>
          fetch(c.file, {{ cache:'no-store' }}).then(r => r.json()).then(d => Array.isArray(d.records) ? d.records : [])
        )
      );
      MODEL.records = parts.flat();
    }}
    FILTERED = MODEL.records.slice();
    initPageSwitcher();
    initFilters();
    const savedLang = (() => {{ try {{ return localStorage.getItem('jo_lang') || 'en'; }} catch(e) {{ return 'en'; }} }})();
    $('langSelect').value = savedLang;
    await loadLanguage(savedLang);
  }} catch (e) {{
    hideLoading();
    console.error('Failed to load JSON data:', e);
    alert('Failed to load dashboard data JSON.');
  }}
}}
boot();
</script>
</body>
</html>
"""


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def time_chunks(rows):
    out = defaultdict(list)
    for r in rows:
        key = (r.get('created_date') or r.get('created_week') or r.get('created_month') or 'unknown').strip() or 'unknown'
        out[key].append(r)
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def main():
    chains = defaultdict(list)
    for p in DATA_DIR.glob('*.csv'):
        m = PATTERN.match(p.name)
        if not m:
            continue
        chain, code, hotel, country, dr = m.groups()
        rows = build_rows(p, chain, code, hotel)
        chains[chain].append({'code': code, 'hotel': hotel, 'rows': rows})

    if not chains:
        raise RuntimeError('No JO csv files found in data folder')

    chain_cards = []
    for chain, hotels in chains.items():
        cdir = ROOT / chain
        cdir.mkdir(parents=True, exist_ok=True)
        for stale in cdir.glob(f'{chain}-JO-*-records-*.json'):
            try:
                stale.unlink()
            except Exception:
                pass
        all_rows = [r for h in hotels for r in h['rows']]

        corp_html = f'{chain}-JO-Corp.html'
        links = [{'name': 'CORP', 'url': corp_html, 'current': True}]
        for h in hotels:
            links.append({'name': f"OPS-{h['code']}", 'url': f"{chain}-JO-OPS-{h['code']}.html", 'current': False})
            links.append({'name': f"GM-{h['code']}", 'url': f"{chain}-JO-GM-{h['code']}.html", 'current': False})

        corp = model(chain, 'CORP', f'Corporate Dashboard ({chain})', all_rows, [{**x, 'current': x['url'] == corp_html} for x in links], '#C55A10')
        corp_chunks = time_chunks(all_rows)
        corp['records'] = []
        corp['record_chunks'] = []
        for range_key, mrows in corp_chunks.items():
            chunk_file = f'{chain}-JO-Corp-records-{range_key}.json'
            write_json(cdir / chunk_file, {'records': mrows})
            dr = sorted([r['created_date'] for r in mrows if r.get('created_date')])
            corp['record_chunks'].append({
                'file': chunk_file,
                'range_key': range_key,
                'count': len(mrows),
                'date_range': [dr[0], dr[-1]] if dr else ['', '']
            })
        corp_json_name = f'{chain}-JO-Corp.json'
        write_json(cdir / corp_json_name, corp)
        (cdir / corp_html).write_text(render_html(corp, corp_json_name), encoding='utf-8')

        for h in hotels:
            ops_html = f"{chain}-JO-OPS-{h['code']}.html"
            gm_html = f"{chain}-JO-GM-{h['code']}.html"
            ops = model(chain, 'OPS', f"OPS Dashboard ({h['hotel']})", h['rows'], [{**x, 'current': x['url'] == ops_html} for x in links], '#C55A10')
            gm = model(chain, 'GM', f"GM Dashboard ({h['hotel']})", h['rows'], [{**x, 'current': x['url'] == gm_html} for x in links], '#0E7470')
            ops_chunks = time_chunks(h['rows'])
            gm_chunks = time_chunks(h['rows'])
            ops['records'] = []
            gm['records'] = []
            ops['record_chunks'] = []
            gm['record_chunks'] = []
            for range_key, mrows in ops_chunks.items():
                chunk_file = f"{chain}-JO-OPS-{h['code']}-records-{range_key}.json"
                write_json(cdir / chunk_file, {'records': mrows})
                dr = sorted([r['created_date'] for r in mrows if r.get('created_date')])
                ops['record_chunks'].append({
                    'file': chunk_file,
                    'range_key': range_key,
                    'count': len(mrows),
                    'date_range': [dr[0], dr[-1]] if dr else ['', '']
                })
            for range_key, mrows in gm_chunks.items():
                chunk_file = f"{chain}-JO-GM-{h['code']}-records-{range_key}.json"
                write_json(cdir / chunk_file, {'records': mrows})
                dr = sorted([r['created_date'] for r in mrows if r.get('created_date')])
                gm['record_chunks'].append({
                    'file': chunk_file,
                    'range_key': range_key,
                    'count': len(mrows),
                    'date_range': [dr[0], dr[-1]] if dr else ['', '']
                })
            ops_json_name = f"{chain}-JO-OPS-{h['code']}.json"
            gm_json_name = f"{chain}-JO-GM-{h['code']}.json"
            write_json(cdir / ops_json_name, ops)
            write_json(cdir / gm_json_name, gm)
            (cdir / ops_html).write_text(render_html(ops, ops_json_name), encoding='utf-8')
            (cdir / gm_html).write_text(render_html(gm, gm_json_name), encoding='utf-8')

        corp_card_html = ""
        if links:
            x = links[0]
            corp_card_html = f"<a class='card' href='{x['url']}' target='_blank' rel='noopener noreferrer'><div class='code'>Page</div><div class='name'>{x['name']}</div></a>"
        ops_gm_cards_html = ''.join([
            f"<a class='card' href='{x['url']}' target='_blank' rel='noopener noreferrer'><div class='code'>Page</div><div class='name'>{x['name']}</div></a>" for x in links[1:]
        ])
        (cdir / 'index.html').write_text(
            "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{chain} JO Index</title>"
            "<style>body{font-family:Manrope,Arial,sans-serif;background:#f4efe3;color:#1f1b16;margin:0}.wrap{max-width:1100px;margin:0 auto;padding:24px}h1{font-family:Fraunces,Georgia,serif;margin:0 0 4px}.sub{font-family:'JetBrains Mono',monospace;color:#6d655a;font-size:13px}.corp-grid{display:grid;grid-template-columns:1fr;gap:12px;margin-top:18px}.opsgm-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}.card{display:block;text-decoration:none;color:inherit;background:#f8f3e8;border:1.5px solid #3a3228;border-left:4px solid #0e7470;border-radius:12px;padding:12px}.code{font-family:'JetBrains Mono',monospace;font-size:12px;color:#6d655a;letter-spacing:.08em;text-transform:uppercase}.name{font-family:Fraunces,Georgia,serif;font-size:22px;font-weight:700}@media(max-width:900px){.opsgm-grid{grid-template-columns:1fr}}</style></head>"
            f"<body><main class='wrap'><h1>{chain} JO Dashboard</h1><div class='sub'>FCS1 JO Management</div><section class='corp-grid'>{corp_card_html}</section><section class='opsgm-grid'>{ops_gm_cards_html}</section></main></body></html>",
            encoding='utf-8'
        )
        chain_cards.append(chain)

    master_cards = ''.join([
        f"<a class='card' href='./{c}/index.html' target='_blank' rel='noopener noreferrer'><div class='code'>Chain</div><div class='name'>{c}</div></a>" for c in chain_cards
    ])
    (ROOT / 'index.html').write_text(
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>JO Master Dashboard</title>"
        "<style>body{font-family:Manrope,Arial,sans-serif;background:#f4efe3;color:#1f1b16;margin:0}.wrap{max-width:1100px;margin:0 auto;padding:24px}h1{font-family:Fraunces,Georgia,serif;margin:0 0 4px}.sub{font-family:'JetBrains Mono',monospace;color:#6d655a;font-size:13px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:18px}.card{display:block;text-decoration:none;color:inherit;background:#f8f3e8;border:1.5px solid #3a3228;border-left:4px solid #0e7470;border-radius:12px;padding:12px}.code{font-family:'JetBrains Mono',monospace;font-size:12px;color:#6d655a;letter-spacing:.08em;text-transform:uppercase}.name{font-family:Fraunces,Georgia,serif;font-size:22px;font-weight:700}</style></head>"
        f"<body><main class='wrap'><h1>JO Master Dashboard</h1><div class='sub'>FCS1 JO Management</div><section class='grid'>{master_cards}</section></main></body></html>",
        encoding='utf-8'
    )

    enforce_i18n(ROOT)
    print('Build complete')


if __name__ == '__main__':
    main()
