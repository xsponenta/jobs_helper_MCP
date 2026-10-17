"""Local HTML dashboard of the job search: applications, vacancies, CV history, weekly activity.

One self-contained file (HOME/dashboard.html) with the data embedded — no server, nothing leaves the computer.
Links open the vacancy page and the exact files that were sent (file://)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from jobs_helper import activity, config, cv_history, pipeline
from jobs_helper.models import now_iso


def _uri(path: str | None) -> str | None:
    try:
        return Path(path).resolve().as_uri() if path and Path(path).exists() else None
    except (OSError, ValueError):
        return None


def collect(root: Path | None = None) -> dict:
    apps = activity.applications(root=root)
    for a in apps:
        a["links"] = {k: u for k, v in (a.get("files") or {}).items() if (u := _uri(v))}
        if a.get("folder"):
            a["links"]["folder"] = _uri(a["folder"])
    used = Counter(a.get("cv_version") for a in apps)
    cvs = []
    prev = None
    current = cv_history.current_version()
    for e in cv_history.ensure_baseline():
        cvs.append({**{k: e.get(k) for k in ("version", "created_at", "reason", "template", "score", "level", "words")},
                    "date": e["created_at"][:10], "md": _uri(e["file"]), "pdf": _uri(e.get("pdf")),
                    "delta": (e["score"] - prev) if prev is not None and e.get("score") is not None else None,
                    "used_in": used.get(f"v{e['version']}", 0), "current": e["version"] == current})
        prev = e.get("score") if e.get("score") is not None else prev
    rep = pipeline.report(root)
    return {"generated_at": now_iso(), "applications": apps, "vacancies": activity.vacancies(root=root), "cv": cvs[::-1],
            "weekly": activity.weekly(root=root), "funnel": rep["funnel"], "follow_ups": len(rep["follow_ups_due"]),
            "upcoming": rep["upcoming"][:5], "current_cv": current}


def build(root: Path | None = None, out: Path | None = None) -> Path:
    data = collect(root)
    out = out or config.HOME / "dashboard.html"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мій пошук роботи</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#6b7280;--line:#e5e7eb;--accent:#1f5fbf;--accent-soft:#e8effb;
--good:#15803d;--good-soft:#dcfce7;--warn:#b45309;--warn-soft:#fef3c7;--bad:#b91c1c;--bad-soft:#fee2e2}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--card:#171b23;--text:#e6e8ec;--muted:#9aa3b2;
--line:#2a303b;--accent:#7aa7ff;--accent-soft:#1c2a45;--good:#4ade80;--good-soft:#14301f;--warn:#fbbf24;--warn-soft:#3a2d0c;
--bad:#f87171;--bad-soft:#3b1515}}
:root[data-theme="dark"]{--bg:#0f1218;--card:#171b23;--text:#e6e8ec;--muted:#9aa3b2;--line:#2a303b;--accent:#7aa7ff;
--accent-soft:#1c2a45;--good:#4ade80;--good-soft:#14301f;--warn:#fbbf24;--warn-soft:#3a2d0c;--bad:#f87171;--bad-soft:#3b1515}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.card b{display:block;font-size:22px}
.card span{color:var(--muted);font-size:12px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:14px;overflow-x:auto}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;padding:10px 14px;color:var(--muted);font:inherit;cursor:pointer;white-space:nowrap}
.tabs button.on{color:var(--text);border-color:var(--accent);font-weight:600}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}.bar input,.bar select{background:var(--card);color:var(--text);
border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit}.bar input{flex:1;min-width:180px}
.tbl{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:760px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600;cursor:pointer;user-select:none;white-space:nowrap}tr:last-child td{border-bottom:0}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.muted{color:var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;background:var(--accent-soft);color:var(--accent);white-space:nowrap}
.good{background:var(--good-soft);color:var(--good)}.warn{background:var(--warn-soft);color:var(--warn)}.bad{background:var(--bad-soft);color:var(--bad)}
.files a{margin-right:8px;white-space:nowrap}.nw{white-space:nowrap}.empty{padding:28px;text-align:center;color:var(--muted)}
.chart{display:flex;align-items:flex-end;gap:6px;height:160px;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:12px;overflow-x:auto}
.col{flex:1;min-width:34px;display:flex;flex-direction:column;align-items:center;gap:4px;height:100%;justify-content:flex-end}
.stack{width:100%;display:flex;flex-direction:column-reverse;gap:2px;flex:1;justify-content:flex-start}
.seg{width:100%;border-radius:3px}.lbl{font-size:11px;color:var(--muted)}.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin-bottom:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.theme{float:right;background:var(--card);border:1px solid var(--line);color:var(--text);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit}
</style></head><body><div class="wrap">
<button class="theme" id="theme" title="Тема">◐</button>
<h1>Мій пошук роботи</h1><p class="sub" id="sub"></p>
<div class="cards" id="cards"></div>
<div class="tabs" id="tabs"></div><div id="view"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = (s, el=document) => el.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dt = v => v ? String(v).slice(0, 10).split('-').reverse().join('.') : '';
const REASON = r => !r ? '' : r === 'original file' ? 'оригінальний файл' : r.startsWith('made default') ? 'покращене CV стало основним' :
  r.startsWith('created') ? 'створено з нуля' : r.startsWith('restored') ? 'повернуто ' + r.split(' ')[1] : r;
const FLAG = {stale:'стара', reposted:'перепублікована', no_salary:'без зарплати', vague:'розмитий опис', oversized_stack:'забагато технологій',
  level_mismatch:'рівень не збігається', pressure_language:'переробки / тиск', scam_pattern:'схоже на шахрайство', informal_channel:'Telegram / форма',
  unpaid_work:'неоплачена робота', anonymous:'без назви компанії', low_employee_rating:'низький рейтинг'};
const flagUa = f => FLAG[f] || (f.startsWith('reviews:') ? 'скарги: ' + f.slice(8) : f);
const pct = v => v == null ? '—' : Math.round(v * 100) + '%';
const store = {get(k){try{return localStorage.getItem(k)}catch(e){return null}}, set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const t = store.get('theme'); if (t) document.documentElement.dataset.theme = t;
$('#theme').onclick = () => { const n = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = n; store.set('theme', n); };
$('#sub').textContent = 'Оновлено ' + D.generated_at.replace('T', ' ').slice(0, 16) + ' · дані лише на вашому комп’ютері';
const f = D.funnel;
$('#cards').innerHTML = [
  [f.applied, 'відгуків надіслано'], [pct(f.response_rate), 'отримали відповідь'], [f.interview_or_further, 'дійшли до співбесіди'],
  [f.offer, 'оферів'], [D.follow_ups, 'час нагадати про себе'], [D.current_cv == null ? '—' : 'v' + D.current_cv, 'версія CV зараз']
].map(([v, l]) => `<div class="card"><b>${esc(v)}</b><span>${l}</span></div>`).join('');

const fitPill = v => v == null ? '<span class="muted">—</span>' : `<span class="pill ${v >= 70 ? 'good' : v >= 50 ? '' : 'warn'}">${v}</span>`;
const riskPill = r => !r ? '' : `<span class="pill ${r === 'high' ? 'bad' : r === 'medium' ? 'warn' : 'good'}">${{high:'високий ризик',medium:'є питання',low:'ок'}[r]}</span>`;
const statusPill = (s, ua) => `<span class="pill ${['offer','accepted','interview','test_task','screening'].includes(s) ? 'good' :
  ['rejected','failed','declined','ghosted','withdrawn'].includes(s) ? 'bad' : s === 'awaiting_user' ? 'warn' : ''}">${esc(ua || s)}</span>`;
const FILE_UA = {cv:'CV.pdf', cover_letter:'лист.pdf', screenshot:'скріншот', cv_text:'CV (текст)', cover_letter_text:'лист (текст)', folder:'папка'};

const TABS = {
  apps: {title: 'Відгуки', rows: D.applications, filters: ['source', 'status_ua'], search: ['company','title','source'], ph: 'Пошук: компанія, вакансія…',
    cols: [['date','Дата'],['company','Компанія'],['title','Вакансія'],['source','Сайт'],['form','Форма'],['sent_how','Як надіслано'],
           ['status_ua','Статус'],['fit','Відповідність'],['cv_version','CV'],['next_step','Наступний крок'],['links','Файли']],
    cell: (r, k) => k === 'date' ? `<span class="nw">${dt(r.date)}</span>` : k === 'title' ? (r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title)) :
      k === 'status_ua' ? statusPill(r.status, r.status_ua) : k === 'fit' ? fitPill(r.fit) :
      k === 'next_step' ? esc([r.next_step, dt(r.next_step_date)].filter(Boolean).join(' · ')) :
      k === 'links' ? '<span class="files">' + Object.entries(r.links || {}).map(([n, u]) => `<a href="${esc(u)}">${FILE_UA[n] || n}</a>`).join('') + '</span>' :
      esc(r[k]), empty: 'Відгуків ще немає. Попросіть помічника знайти вакансії й відгукнутися.'},
  vac: {title: 'Вакансії', rows: D.vacancies, filters: ['source', 'risk'], search: ['company','title'], ph: 'Пошук: компанія, вакансія…',
    cols: [['last_seen','Бачили'],['company','Компанія'],['title','Вакансія'],['source','Сайт'],['salary','Зарплата'],['fit','Відповідність'],
           ['risk','Прапорці'],['opened','Відкривали'],['status','Відгук']],
    cell: (r, k) => k === 'last_seen' ? `<span class="nw">${dt(r.last_seen)}</span>` : k === 'title' ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : k === 'fit' ? fitPill(r.fit) :
      k === 'risk' ? riskPill(r.risk) + (r.red_flags?.length ? `<div class="muted" style="font-size:12px">${esc(r.red_flags.map(flagUa).join(', '))}</div>` : '') :
      k === 'opened' ? (r.opened ? 'так' : '<span class="muted">у пошуку</span>') :
      k === 'status' ? (r.applied ? `${esc(r.applied)} · ${esc(r.status)}` : '<span class="muted">—</span>') : esc(r[k]),
    empty: 'Тут з’являться вакансії, які помічник знаходив і відкривав.'},
  cv: {title: 'Історія CV', rows: D.cv, filters: [], search: [],
    cols: [['version','Версія'],['date','Дата'],['reason','Що сталося'],['score','Оцінка'],['level','Рівень'],['template','Дизайн'],
           ['words','Слів'],['used_in','У відгуках'],['files','Файли']],
    cell: (r, k) => k === 'version' ? `v${r.version}` + (r.current ? ' <span class="pill good">зараз</span>' : '') :
      k === 'score' ? (r.score ?? '—') + (r.delta ? ` <span class="pill ${r.delta > 0 ? 'good' : 'bad'}">${r.delta > 0 ? '+' : ''}${r.delta}</span>` : '') :
      k === 'date' ? `<span class="nw">${dt(r.date)}</span>` : k === 'reason' ? esc(REASON(r.reason)) :
      k === 'files' ? '<span class="files">' + (r.pdf ? `<a href="${esc(r.pdf)}">PDF</a>` : '') + (r.md ? `<a href="${esc(r.md)}">текст</a>` : '') + '</span>' :
      esc(r[k] ?? '—'), empty: 'Історія з’явиться, щойно ви зробите покращене CV основним.'},
  act: {title: 'Активність по тижнях', rows: D.weekly.filter(w => w.found || w.opened || w.applied || w.responses || w.interviews || w.offers || w.rejections).reverse(), filters: [], search: [],
    cols: [['week_of','Тиждень з'],['found','Знайдено'],['opened','Відкрито'],['applied','Відгуків'],['responses','Відповідей'],
           ['interviews','Співбесід'],['offers','Оферів'],['rejections','Відмов'],['applied_by_source','Відгуки за сайтами']],
    cell: (r, k) => k === 'week_of' ? `<span class="nw">${dt(r.week_of)}</span>` : k === 'applied_by_source' ? esc(Object.entries(r[k] || {}).map(([s, n]) => `${s}: ${n}`).join(', ')) : esc(r[k]),
    empty: 'Ще немає активності.'}
};
let tab = store.get('tab') || 'apps'; if (!TABS[tab]) tab = 'apps';
const sortState = {};
$('#tabs').innerHTML = Object.entries(TABS).map(([k, v]) => `<button data-k="${k}">${v.title} <span class="muted">${k === 'act' ? '' : v.rows.length}</span></button>`).join('');
$('#tabs').onclick = e => { const b = e.target.closest('button'); if (b) { tab = b.dataset.k; store.set('tab', tab); render(); } };

function chart() {
  const w = D.weekly, max = Math.max(1, ...w.map(x => x.applied + x.responses + x.interviews));
  const C = [['applied','var(--accent)','відгуки'],['responses','var(--good)','відповіді'],['interviews','var(--warn)','співбесіди']];
  return `<div class="legend">${C.map(c => `<span><i style="background:${c[1]}"></i>${c[2]}</span>`).join('')}</div><div class="chart">` +
    w.map(x => `<div class="col" title="${x.week_of}: ${x.applied} відг., ${x.responses} відп., ${x.interviews} співб."><div class="stack">` +
      C.map(c => x[c[0]] ? `<div class="seg" style="height:${x[c[0]] / max * 100}%;background:${c[1]}"></div>` : '').join('') +
      `</div><div class="lbl">${x.week_of.slice(5).split('-').reverse().join('.')}</div></div>`).join('') + '</div>';
}

function render() {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.k === tab));
  const T = TABS[tab];
  const opts = k => [...new Set(T.rows.map(r => r[k]).filter(Boolean))].sort();
  const label = {source: 'усі сайти', status_ua: 'усі статуси', risk: 'усі ризики'};
  $('#view').innerHTML = (tab === 'act' ? chart() : '') + (T.search.length || T.filters.length ? `<div class="bar">` +
    (T.search.length ? `<input id="q" placeholder="${T.ph}">` : '') +
    T.filters.map(k => `<select data-f="${k}"><option value="">${label[k]}</option>${opts(k).map(o => `<option>${esc(o)}</option>`).join('')}</select>`).join('') +
    `</div>` : '') + `<div class="tbl" id="tbl"></div>`;
  const draw = () => {
    const q = ($('#q')?.value || '').toLowerCase();
    const fs = [...document.querySelectorAll('[data-f]')].map(s => [s.dataset.f, s.value]).filter(x => x[1]);
    let rows = T.rows.filter(r => (!q || T.search.some(k => String(r[k] ?? '').toLowerCase().includes(q))) && fs.every(([k, v]) => r[k] === v));
    const s = sortState[tab];
    if (s) rows = rows.slice().sort((a, b) => { const x = a[s.k] ?? '', y = b[s.k] ?? ''; return (x > y ? 1 : x < y ? -1 : 0) * s.dir; });
    $('#tbl').innerHTML = rows.length ? `<table><thead><tr>${T.cols.map(([k, l]) => `<th data-k="${k}">${l}${s && s.k === k ? (s.dir > 0 ? ' ↑' : ' ↓') : ''}</th>`).join('')}</tr></thead>` +
      `<tbody>${rows.map(r => `<tr>${T.cols.map(([k]) => `<td>${T.cell(r, k)}</td>`).join('')}</tr>`).join('')}</tbody></table>` :
      `<div class="empty">${T.rows.length ? 'Нічого не знайдено за фільтром.' : T.empty}</div>`;
    $('#tbl thead')?.addEventListener('click', e => { const th = e.target.closest('th'); if (!th) return;
      const cur = sortState[tab]; sortState[tab] = {k: th.dataset.k, dir: cur && cur.k === th.dataset.k ? -cur.dir : -1}; draw(); });
  };
  $('#q')?.addEventListener('input', draw); document.querySelectorAll('[data-f]').forEach(s => s.addEventListener('change', draw));
  draw();
}
render();
</script></body></html>
"""
