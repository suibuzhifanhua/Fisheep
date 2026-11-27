# -*- coding: utf-8 -*-
"""core.run_replay_page — 任务轨迹回放页（评审 P1-4 字面补做，v1.0.756）。

独立 HTML 页：自带内联 CSS / JS，**不引用 js/*.js**（首页已由 `js/_entry.js`
单一 ES Module 入口按 `JS_LOAD_ORDER` 顺序加载 18 个模块），所以 P0-3 的
ES Module 全量迁移不会波及它；首启迁移引导页（core/migration_guide.py）
也是同一套「独立页」范式。

数据在浏览器里直接 fetch `GET /api/runs` 与 `GET /api/runs/<run_id>`：
左侧列最近任务（失败标红），右侧一次拿到 summary（事件数 / 耗时 / 错误 /
token / 工具调用 / 最慢阶段）+ 完整事件时间线；支持 run_id 直接查询与
`?run_id=xxx` 直达，满足「任取一次失败任务，用 run_id 一次查询即可拿到完整
调用链与耗时分布，无需翻多份日志」。全部用 textContent 渲染，不拼 innerHTML。
"""
REPLAY_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>任务轨迹回放</title>
<style>
  :root { --bg:#f7f8fa; --panel:#fff; --line:#e5e7eb; --text:#1f2329;
          --muted:#8a9099; --red:#d93026; --accent:#2b6cf6; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--text); }
  header { display:flex; align-items:center; gap:12px; padding:14px 20px;
           background:var(--panel); border-bottom:1px solid var(--line); flex-wrap:wrap; }
  h1 { font-size:17px; margin:0 8px 0 0; }
  a.back { color:var(--accent); text-decoration:none; margin-right:auto; }
  input#rid { width:290px; padding:7px 10px; border:1px solid var(--line); border-radius:6px; }
  button { padding:7px 14px; border:1px solid var(--line); border-radius:6px;
           background:var(--panel); cursor:pointer; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  .wrap { display:flex; gap:16px; padding:16px 20px; align-items:flex-start; }
  .col { background:var(--panel); border:1px solid var(--line); border-radius:8px; }
  .list { width:340px; flex:none; max-height:78vh; overflow:auto; }
  .detail { flex:1; min-width:0; max-height:78vh; overflow:auto; padding:16px; }
  .item { padding:10px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
  .item:hover { background:#f2f6ff; }
  .item.sel { background:#e8f0ff; }
  .item.bad { border-left:3px solid var(--red); }
  .item .rid { font-family:monospace; font-size:12px; }
  .item .meta { color:var(--muted); font-size:12px; }
  .cards { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
  .card { border:1px solid var(--line); border-radius:6px; padding:8px 12px; min-width:112px; }
  .card b { display:block; font-size:18px; }
  .card span { color:var(--muted); font-size:12px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { padding:6px 8px; border-bottom:1px solid var(--line); text-align:left;
           vertical-align:top; word-break:break-all; }
  th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--panel); }
  tr.err td { background:#fff1f0; }
  .muted { color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>任务轨迹回放</h1>
  <a class="back" href="/">← 返回主界面</a>
  <input id="rid" placeholder="输入 run_id 直接查询" autocomplete="off">
  <button class="primary" id="go">查询</button>
  <button id="refresh">刷新列表</button>
</header>
<div class="wrap">
  <div class="col list" id="runList"><div class="item muted">加载中…</div></div>
  <div class="col detail" id="detail"><div class="muted">从左侧选一条任务，或直接输入 run_id 查询。</div></div>
</div>
<script>
(function () {
  var listEl = document.getElementById('runList');
  var detailEl = document.getElementById('detail');
  var ridEl = document.getElementById('rid');

  function txt(tag, cls, value) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    e.textContent = (value === undefined || value === null) ? '' : String(value);
    return e;
  }
  function fmtTs(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    return d.toLocaleTimeString('zh-CN', {hour12:false}) + '.' +
           String(d.getMilliseconds()).padStart(3, '0');
  }
  function loadRuns() {
    fetch('/api/runs?limit=50').then(function (r) { return r.json(); }).then(function (j) {
      listEl.textContent = '';
      var rows = j.status || (j.runs || []).map(function (r) { return {run_id: r}; });
      if (!rows.length) { listEl.appendChild(txt('div', 'item muted', '暂无任务轨迹')); return; }
      rows.forEach(function (row) {
        var it = txt('div', 'item' + (row.failed ? ' bad' : ''));
        it.appendChild(txt('div', 'rid', row.run_id));
        var meta = '事件 ' + (row.events || 0) + ' · 耗时 ' + (row.duration_s || 0) + 's';
        if (row.errors) meta += ' · 错误 ' + row.errors;
        if (row.tokens) meta += ' · token ' + row.tokens;
        it.appendChild(txt('div', 'meta', meta));
        it.onclick = function () { openRun(row.run_id, it); };
        listEl.appendChild(it);
      });
    }).catch(function (e) { listEl.textContent = '列表加载失败：' + e; });
  }
  function openRun(rid, it) {
    Array.prototype.forEach.call(listEl.children, function (c) { c.classList.remove('sel'); });
    if (it) it.classList.add('sel');
    ridEl.value = rid;
    detailEl.textContent = '加载中…';
    fetch('/api/runs/' + encodeURIComponent(rid)).then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { detailEl.textContent = j.error || '查询失败'; return; }
        renderDetail(rid, j.summary || {}, j.events || []);
      }).catch(function (e) { detailEl.textContent = '查询失败：' + e; });
  }
  function renderDetail(rid, s, events) {
    detailEl.textContent = '';
    detailEl.appendChild(txt('h2', '', 'run_id: ' + rid));
    var cards = txt('div', 'cards');
    [['事件数', s.events], ['总耗时', (s.duration_s || 0) + 's'], ['错误', s.errors],
     ['token', s.tokens || 0], ['工具调用', s.tool_calls || 0],
     ['状态', s.failed ? '失败' : '正常'], ['最慢阶段', (s.slowest_gap_s || 0) + 's']]
      .forEach(function (p) {
        var c = txt('div', 'card');
        c.appendChild(txt('b', '', p[1]));
        c.appendChild(txt('span', '', p[0]));
        cards.appendChild(c);
      });
    detailEl.appendChild(cards);
    if (s.first_error) {
      var fe = s.first_error;
      detailEl.appendChild(txt('p', '', '首个错误：' +
        (fe.text || fe.error || fe.reason || JSON.stringify(fe))));
    }
    var table = document.createElement('table');
    var thead = document.createElement('thead');
    var hr = document.createElement('tr');
    ['时间', '相对(s)', '事件', '工具', '摘要'].forEach(function (h) { hr.appendChild(txt('th', '', h)); });
    thead.appendChild(hr); table.appendChild(thead);
    var tb = document.createElement('tbody');
    var t0 = s.started_at || (events[0] && events[0].ts) || 0;
    events.forEach(function (e) {
      var tr = document.createElement('tr');
      var low = String(e.type || '').toLowerCase();
      if (low === 'error' || low === 'aborted' || e.ok === false ||
          String(e.code || '').toLowerCase() === 'error') tr.className = 'err';
      tr.appendChild(txt('td', '', fmtTs(e.ts)));
      tr.appendChild(txt('td', '', t0 ? Math.max(0, e.ts - t0).toFixed(3) : ''));
      tr.appendChild(txt('td', '', e.type || e.event || ''));
      tr.appendChild(txt('td', '', e.tool || e.name || ''));
      var bits = [];
      ['text', 'reason', 'summary', 'verdict', 'result', 'stage', 'model',
       'duration_ms', 'elapsed_ms', 'tokens', 'code']
        .forEach(function (k) {
          if (e[k] !== undefined && e[k] !== null && e[k] !== '') bits.push(k + '=' + e[k]);
        });
      tr.appendChild(txt('td', '', bits.join('  ')));
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    detailEl.appendChild(table);
  }
  document.getElementById('go').onclick = function () {
    var v = ridEl.value.trim(); if (v) openRun(v, null);
  };
  ridEl.onkeydown = function (e) {
    if (e.key === 'Enter') document.getElementById('go').click();
  };
  document.getElementById('refresh').onclick = loadRuns;
  var m = location.search.match(/[?&]run_id=([^&]+)/);
  if (m) { var pre = decodeURIComponent(m[1]); ridEl.value = pre; openRun(pre, null); }
  loadRuns();
})();
</script>
</body>
</html>
"""


def render():
    return REPLAY_HTML
