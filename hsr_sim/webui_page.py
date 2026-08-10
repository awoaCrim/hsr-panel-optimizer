"""WebUI 前端模板。

与 hsr_sim.webui 的 HTTP/API/runner 分离：这里仅负责信息架构、样式和轮询渲染。
纯原生 HTML/CSS/JS，无外部静态资源。
"""

PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22><rect width=%2264%22 height=%2264%22 rx=%2216%22 fill=%22%239b87f5%22/><text x=%2232%22 y=%2243%22 text-anchor=%22middle%22 font-size=%2236%22 font-family=%22Arial%22 font-weight=%22700%22 fill=%22%23090b11%22>R</text></svg>">
<title>HSR 战斗推演台</title>
<style>
:root {
  color-scheme: dark;
  --bg: #090b11; --surface: #11141d; --surface-2: #171b27; --surface-3: #1d2230;
  --line: #292f3e; --line-soft: #202534; --text: #eef1f7; --muted: #929bad;
  --purple: #9b87f5; --purple-2: #6f5dd4; --cyan: #5ccfe6; --green: #67d391;
  --amber: #e9b567; --red: #ee7474; --sidebar: 224px;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif; }
button, input, select { font: inherit; }
button { color: inherit; }
.sidebar {
  position: fixed; inset: 0 auto 0 0; width: var(--sidebar); padding: 22px 14px;
  border-right: 1px solid var(--line); background: #0d1018; z-index: 20;
  display: flex; flex-direction: column;
}
.brand { padding: 0 10px 22px; }
.brand-mark { display: flex; align-items: center; gap: 10px; font-weight: 750; letter-spacing: .02em; }
.brand-icon { width: 30px; height: 30px; display: grid; place-items: center; border-radius: 9px;
  background: linear-gradient(135deg, var(--purple), var(--cyan)); color: #090b11; font-weight: 900; }
.brand small { display: block; color: var(--muted); margin-top: 8px; font-size: 11px; line-height: 1.5; }
.nav { display: grid; gap: 6px; }
.nav button { border: 0; background: transparent; padding: 11px 12px; border-radius: 9px; text-align: left;
  color: var(--muted); cursor: pointer; display: flex; align-items: center; gap: 10px; }
.nav button:hover { background: var(--surface-2); color: var(--text); }
.nav button.active { background: #211d37; color: #fff; box-shadow: inset 3px 0 var(--purple); }
.nav-icon { width: 20px; color: var(--purple); text-align: center; }
.sidebar-foot { margin-top: auto; padding: 12px 10px 0; border-top: 1px solid var(--line-soft); color: var(--muted); font-size: 11px; }
.global-live { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #596070; }
.live-dot.running { background: var(--green); box-shadow: 0 0 0 5px rgba(103,211,145,.1); animation: pulse 1.5s infinite; }
.live-dot.error { background: var(--red); }
@keyframes pulse { 50% { opacity: .45; } }
.app { margin-left: var(--sidebar); min-height: 100vh; }
.panel { display: none; padding: 28px clamp(18px, 3vw, 42px) 56px; max-width: 1560px; margin: 0 auto; }
.panel.active { display: block; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-bottom: 20px; }
.eyebrow { color: var(--purple); text-transform: uppercase; letter-spacing: .16em; font-size: 10px; font-weight: 700; }
h1 { font-size: clamp(25px, 3vw, 36px); margin: 5px 0 3px; letter-spacing: -.025em; }
h2, h3, h4 { margin-top: 0; }
.page-sub { color: var(--muted); font-size: 13px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 13px; }
.command-bar { position: sticky; top: 0; z-index: 12; padding: 13px; margin: 0 0 14px;
  background: rgba(17,20,29,.94); backdrop-filter: blur(12px); }
.controls { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.field { display: grid; gap: 5px; color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
.field.stage { flex: 1 1 330px; }
input, select { min-height: 38px; color: var(--text); background: var(--surface-3); border: 1px solid #343b4d;
  border-radius: 8px; padding: 7px 10px; outline: none; }
input:focus, select:focus { border-color: var(--purple); box-shadow: 0 0 0 3px rgba(155,135,245,.12); }
.field.short input { width: 82px; }
.btn { min-height: 38px; border: 1px solid #394155; border-radius: 8px; padding: 7px 15px; background: var(--surface-3); cursor: pointer; }
.btn:hover { background: #252b3b; }
.btn.primary { border-color: #7968db; background: linear-gradient(135deg, #7968db, #5b4bb9); font-weight: 700; }
.btn.danger { color: #ffc2c2; border-color: #663c45; background: #2c1a20; }
.btn:disabled, input:disabled, select:disabled { opacity: .45; cursor: not-allowed; }
.run-state { display: flex; align-items: center; gap: 11px; padding: 14px 16px; margin-bottom: 14px; }
.run-state .state-copy { min-width: 0; }
.state-title { font-weight: 700; }
.state-detail { color: var(--muted); font-size: 12px; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.state-time { margin-left: auto; color: var(--muted); font: 12px Consolas, monospace; }
.metrics { display: grid; grid-template-columns: repeat(6, minmax(110px, 1fr)); gap: 10px; margin-bottom: 14px; }
.metric { padding: 13px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: 11px; }
.metric-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .09em; }
.metric-value { margin-top: 7px; font: 700 21px/1 Consolas, monospace; }
.metric-value small { color: var(--muted); font-size: 11px; font-family: inherit; font-weight: 400; }
.live-layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(360px, .75fr); gap: 14px; align-items: start; }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 15px 16px 12px; border-bottom: 1px solid var(--line-soft); }
.section-head h2, .section-head h3 { margin: 0; font-size: 15px; }
.section-kicker { color: var(--muted); font-size: 11px; }
.timeline { min-height: 410px; max-height: 650px; overflow: auto; padding: 8px 14px 16px; }
.timeline-empty { min-height: 320px; display: grid; place-items: center; text-align: center; color: var(--muted); font-size: 13px; }
.timeline-item { position: relative; padding: 13px 10px 13px 43px; border-bottom: 1px solid var(--line-soft); }
.timeline-item::before { content: attr(data-index); position: absolute; left: 4px; top: 14px; width: 26px; height: 26px;
  border-radius: 50%; display: grid; place-items: center; background: #28223f; color: #cfc5ff; font: 11px Consolas, monospace; }
.timeline-item::after { content: ""; position: absolute; left: 17px; top: 40px; bottom: -14px; width: 1px; background: var(--line); }
.timeline-item:last-child::after { display: none; }
.act-main { display: flex; align-items: baseline; flex-wrap: wrap; gap: 7px; }
.act-unit { font-weight: 700; }
.act-action { color: var(--cyan); }
.act-damage { margin-left: auto; color: #f2cf83; font: 700 13px Consolas, monospace; }
.act-meta, .act-note { margin-top: 6px; color: var(--muted); font-size: 11px; }
.act-note { color: #c6cbd6; padding-left: 9px; border-left: 2px solid #50476e; }
.badge { display: inline-block; margin: 0 5px 3px 0; padding: 2px 7px; border-radius: 99px; font-size: 10px;
  color: #bfc6d5; background: var(--surface-3); border: 1px solid #303748; }
.badge.good { color: #9de5b7; border-color: #315b42; background: #14271c; }
.badge.warn { color: #f2c786; border-color: #604c2e; background: #281f12; }
.battle-column { display: grid; gap: 14px; }
.current-decision { padding: 15px; background: linear-gradient(140deg, #17172a, #121721); }
.current-label { color: var(--purple); font-size: 10px; text-transform: uppercase; letter-spacing: .12em; }
.current-title { margin-top: 7px; font-size: 18px; font-weight: 750; }
.current-copy { color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 7px; }
.unit-section { padding: 14px; }
.unit-section h3 { margin: 0 0 10px; font-size: 13px; color: #c8ceda; }
.unit-card { padding: 10px 0; border-top: 1px solid var(--line-soft); }
.unit-card:first-of-type { border-top: 0; padding-top: 2px; }
.unit-line { display: flex; justify-content: space-between; gap: 10px; align-items: center; font-size: 12px; }
.unit-name { font-weight: 650; }
.unit-numbers { color: var(--muted); font: 10px Consolas, monospace; }
.bar { height: 5px; margin-top: 7px; overflow: hidden; background: #292e3a; border-radius: 99px; }
.bar > i { display: block; height: 100%; width: 0; border-radius: inherit; background: linear-gradient(90deg, #52bd7a, #76df99); transition: width .35s ease; }
.bar.energy > i { background: linear-gradient(90deg, #5e7ce8, #75c7e8); }
.bar.toughness > i { background: linear-gradient(90deg, #d69d45, #efca77); }
.queue { display: flex; flex-wrap: wrap; gap: 6px; }
.queue-chip { padding: 5px 7px; border-radius: 7px; background: var(--surface-3); color: var(--muted); font-size: 10px; }
.queue-chip:first-child { color: #fff; outline: 1px solid var(--purple); }
.queue-chip b { color: inherit; font-family: Consolas, monospace; }
.action-order-card { display: none; margin-bottom: 14px; overflow: hidden; }
.action-order-grid { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(340px, .85fr); }
.order-pane { min-width: 0; padding: 14px 16px 16px; }
.order-pane + .order-pane { border-left: 1px solid var(--line-soft); }
.order-pane-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 11px; }
.order-pane-head h2 { margin: 0; font-size: 15px; }
.turn-order { display: grid; grid-template-columns: repeat(auto-fit, minmax(142px, 1fr)); gap: 9px; padding: 2px 2px 10px; }
.turn-step { min-width: 0; position: relative; padding: 11px 11px 10px 38px; border: 1px solid var(--line);
  border-radius: 10px; background: var(--surface-2); }
.turn-step:first-child { border-color: var(--purple); box-shadow: 0 0 0 2px rgba(155,135,245,.10); }
.turn-rank { position: absolute; left: 10px; top: 11px; width: 20px; height: 20px; display: grid; place-items: center;
  border-radius: 50%; color: #d9d1ff; background: #2b2545; font: 700 10px Consolas, monospace; }
.turn-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12px; font-weight: 700; }
.turn-meta { margin-top: 7px; color: var(--muted); font: 10px/1.55 Consolas, monospace; }
.side-tag { display: inline-block; margin-top: 4px; padding: 1px 6px; border-radius: 99px; font-size: 9px; }
.side-character { color: #9de5b7; background: #14271c; border: 1px solid #315b42; }
.side-enemy { color: #ffaaaa; background: #2c191c; border: 1px solid #65383e; }
.side-memosprite { color: #a9e9f6; background: #14272c; border: 1px solid #315a64; }
.action-history { max-height: 190px; overflow: auto; padding-right: 3px; }
.action-history-row { display: grid; grid-template-columns: 25px 58px 46px minmax(90px,.8fr) minmax(105px,1fr); gap: 7px;
  align-items: baseline; padding: 7px 2px; border-top: 1px solid var(--line-soft); font-size: 11px; }
.action-history-row:first-child { border-top: 0; }
.action-history-index, .action-history-time { color: var(--muted); font-family: Consolas, monospace; }
.action-history-unit { font-weight: 650; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.action-history-action { color: var(--cyan); }
.action-history-detail { grid-column: 4 / -1; color: var(--muted); font-size: 10px; line-height: 1.4; }
.action-order-empty { min-height: 92px; display: grid; place-items: center; color: var(--muted); font-size: 11px; text-align: center; }
.aux { margin-top: 14px; }
details.card { overflow: hidden; }
details.card > summary { cursor: pointer; list-style: none; padding: 15px 16px; font-weight: 650; }
details.card > summary::-webkit-details-marker { display: none; }
details.card > summary::after { content: "+"; float: right; color: var(--muted); }
details.card[open] > summary::after { content: "−"; }
.details-body { padding: 0 16px 16px; border-top: 1px solid var(--line-soft); }
.stage-overview { padding-top: 14px; color: var(--muted); font-size: 12px; line-height: 1.6; }
.wave-block { margin-top: 13px; }
.wave-title { color: #d8d1fb; font-size: 12px; font-weight: 700; margin-bottom: 7px; }
.enemy-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(245px,1fr)); gap: 8px; }
.enemy-static { padding: 11px; background: var(--surface-2); border: 1px solid var(--line-soft); border-radius: 9px; font-size: 11px; color: var(--muted); }
.enemy-static b { color: var(--text); font-size: 12px; }
.report { white-space: pre-wrap; font: 11px/1.65 Consolas, monospace; color: #c9cfdb; max-height: 560px; overflow: auto; user-select: text; -webkit-user-select: text; cursor: text; }
.report-tools { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 10px 0 0; }
.data-grid { display: grid; grid-template-columns: repeat(2, minmax(320px,1fr)); gap: 14px; }
.char-card { padding: 16px; }
.char-head { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
.char-head h3 { margin: 0; }
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 13px 0; }
.stat { padding: 8px; background: var(--surface-2); border-radius: 7px; }
.stat span { display: block; color: var(--muted); font-size: 9px; text-transform: uppercase; }
.stat b { display: block; margin-top: 4px; font: 12px Consolas, monospace; }
.stat b.good { color: #9de5b7; }
.info-line { color: var(--muted); font-size: 11px; line-height: 1.55; margin: 4px 0; }
.info-line strong { color: #d9deea; }
.trust { color: var(--amber); }
.library-tools { display: flex; gap: 9px; margin-bottom: 14px; }
.library-tools input { flex: 1; }
.eq-list { display: grid; gap: 8px; }
.eq-item { padding: 0; }
.eq-item summary { padding: 13px 14px !important; }
.eq-desc { color: var(--muted); font-size: 11px; line-height: 1.6; }
.toast { position: fixed; right: 22px; bottom: 22px; max-width: 380px; padding: 11px 14px; border: 1px solid #61434a;
  background: #2a181d; color: #ffc4c4; border-radius: 9px; z-index: 50; display: none; }
.mono { font-family: Consolas, monospace; }
@media (max-width: 1080px) {
  .metrics { grid-template-columns: repeat(3, 1fr); }
  .live-layout { grid-template-columns: 1fr; }
  .action-order-grid { grid-template-columns: 1fr; }
  .order-pane + .order-pane { border-left: 0; border-top: 1px solid var(--line-soft); }
  .timeline { max-height: 520px; }
}
@media (max-width: 760px) {
  :root { --sidebar: 0px; }
  .sidebar { position: sticky; width: auto; height: auto; padding: 10px 12px; border-right: 0; border-bottom: 1px solid var(--line); flex-direction: row; align-items: center; }
  .brand { padding: 0; } .brand small, .sidebar-foot { display: none; }
  .nav { margin-left: auto; display: flex; } .nav button { padding: 9px; } .nav button span:last-child { display: none; }
  .app { margin-left: 0; } .panel { padding: 20px 12px 40px; }
  .command-bar { top: 51px; }
  .metrics { grid-template-columns: repeat(2, 1fr); }
  .data-grid { grid-template-columns: 1fr; }
  .field.stage { flex-basis: 100%; }
  .stat-grid { grid-template-columns: repeat(2,1fr); }
}
</style>
</head>
<body>
<aside class="sidebar">
  <div class="brand">
    <div class="brand-mark"><span class="brand-icon">R</span><span>战斗推演台</span></div>
    <small>LLM 指挥 · 固定 seed · 多波次事件流</small>
  </div>
  <nav class="nav">
    <button class="active" data-panel="battle"><span class="nav-icon">◈</span><span>实时推演</span></button>
    <button data-panel="team"><span class="nav-icon">◇</span><span>队伍档案</span></button>
    <button data-panel="equip"><span class="nav-icon">⌕</span><span>机制资料库</span></button>
  </nav>
  <div class="sidebar-foot">
    <div class="global-live"><i id="global-dot" class="live-dot"></i><span id="global-status">空闲</span></div>
    <div id="global-stage">尚未开始推演</div>
  </div>
</aside>
<main class="app">
  <section id="panel-battle" class="panel active">
    <div class="page-head">
      <div><div class="eyebrow">Battle rehearsal</div><h1>实时推演</h1><div class="page-sub">你负责验收；DeepSeek 负责逐步决策，模拟器负责结算。</div></div>
    </div>
    <div class="command-bar card">
      <div class="controls">
        <label class="field stage">关卡<select id="sim-stage"></select></label>
        <label class="field">指挥<select id="sim-mode"><option value="llm">DeepSeek 指挥</option><option value="demo">演示策略</option></select></label>
        <label class="field short">Seed<input id="sim-seed" type="number" value="0"></label>
        <label class="field short">Act 上限<input id="sim-max" type="number" value="200"></label>
        <button class="btn primary" id="sim-start">开始推演</button>
        <button class="btn danger" id="sim-stop" disabled>停止</button>
      </div>
    </div>
    <div class="run-state card">
      <i id="run-dot" class="live-dot"></i>
      <div class="state-copy"><div id="run-title" class="state-title">等待开始</div><div id="run-detail" class="state-detail">选择关卡与指挥方式后开始。</div></div>
      <div id="run-time" class="state-time">00:00</div>
    </div>
    <div class="metrics">
      <div class="metric"><div class="metric-label">Act</div><div id="metric-act" class="metric-value">0<small> / 200</small></div></div>
      <div class="metric"><div class="metric-label">波次</div><div id="metric-wave" class="metric-value">—</div></div>
      <div class="metric"><div class="metric-label">行动值</div><div id="metric-av" class="metric-value">0<small> AV</small></div></div>
      <div class="metric"><div class="metric-label">累计伤害</div><div id="metric-damage" class="metric-value">0</div></div>
      <div class="metric"><div class="metric-label">战技点</div><div id="metric-sp" class="metric-value">—</div></div>
      <div class="metric"><div class="metric-label">分支</div><div id="metric-branch" class="metric-value">0</div></div>
    </div>
    <section class="action-order-card card">
      <div class="action-order-grid">
        <div class="order-pane">
          <div class="order-pane-head"><div><h2>下一轮行动顺序</h2><div class="section-kicker">模拟器当前行动条真值 · AV 越小越先行动</div></div><span id="turn-order-count" class="badge">0 单位</span></div>
          <div id="turn-order-live" class="turn-order"><div class="action-order-empty">开始后显示所有我方、敌方与忆灵的下一次行动。</div></div>
        </div>
        <div class="order-pane">
          <div class="order-pane-head"><div><h2>完整实际行动顺序</h2><div class="section-kicker">我方 / 敌方 / 忆灵按实际发生时刻统一记录</div></div><span id="action-history-count" class="badge">0 次</span></div>
          <div id="action-history-live" class="action-history"><div class="action-order-empty">尚无已结算行动。</div></div>
        </div>
      </div>
    </section>
    <div class="live-layout">
      <section class="card">
        <div class="section-head"><div><h2>决策时间线</h2><div class="section-kicker">每个 act 完成后立即出现，不等待整局结束</div></div><span id="trace-count" class="badge">0 act</span></div>
        <div id="trace" class="timeline"><div class="timeline-empty"><div><b>尚无决策</b><br><br>开始后将实时显示模型理由、技能、目标、伤害与大招。</div></div></div>
      </section>
      <aside class="battle-column">
        <section id="current-decision" class="current-decision card">
          <div class="current-label">当前环节</div><div class="current-title">等待开始</div><div class="current-copy">这里会显示 DeepSeek 正在规划、执行或复盘哪一步。</div>
        </section>
        <section class="unit-section card"><h3>敌方状态</h3><div id="enemy-live" class="section-kicker">等待战斗状态</div></section>
        <section class="unit-section card"><h3>我方动态面板</h3><div id="ally-live" class="section-kicker">等待战斗状态</div></section>
        <section class="unit-section card"><h3>最近伤害</h3><div id="damage-live" class="section-kicker">等待伤害事件</div></section>
      </aside>
    </div>
    <div class="aux">
      <details class="card"><summary>关卡配置与敌人完整参数</summary><div class="details-body"><div id="stage-root" class="stage-overview">加载中…</div></div></details>
      <details id="report-details" class="card" style="margin-top:10px"><summary>最终推演报告</summary><div class="details-body"><div class="report-tools"><button class="btn" id="report-copy" type="button">复制报告</button></div><pre id="report" class="report">推演结束后生成完整报告。</pre></div></details>
    </div>
  </section>

  <section id="panel-team" class="panel">
    <div class="page-head"><div><div class="eyebrow">Team archive</div><h1>队伍档案</h1><div class="page-sub">真实面板、装备、星魂与行迹；属于推演配置，不在战斗中修改。</div></div></div>
    <div id="team-root" class="data-grid"></div>
    <div id="team-trust"></div>
  </section>

  <section id="panel-equip" class="panel">
    <div class="page-head"><div><div class="eyebrow">Mechanics library</div><h1>机制资料库</h1><div class="page-sub">检索光锥、遗器套装与星魂的原始描述及模拟接入状态。</div></div></div>
    <div class="library-tools"><select id="eq-kind"><option value="light_cones">光锥</option><option value="relic_sets">遗器套装</option><option value="eidolons">星魂</option></select><input id="eq-q" placeholder="搜索名称或描述…"></div>
    <div id="eq-root" class="eq-list"></div>
  </section>
</main>
<div id="toast" class="toast"></div>
<script>
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clean = value => String(value || '').replace(/<[^>]+>/g, '');
const clamp = n => Math.max(0, Math.min(100, Number(n) || 0));
const num = n => Number(n || 0).toLocaleString('zh-CN', {maximumFractionDigits: 0});
const pct = n => `${clamp(n).toFixed(1)}%`;
const skillName = s => ({basic:'普攻', skill:'战技', ult:'终结技'}[s] || s || '—');
let stageData = null, teamData = null, equipLoaded = false, pollBusy = false, pollTimer = null, lastTrailCount = 0;
let lastActionCount = 0;
let lastReport = '';
let unitNames = {};

function toast(message) { const el=$('#toast'); el.textContent=message; el.style.display='block'; clearTimeout(toast.t); toast.t=setTimeout(()=>el.style.display='none',4500); }
function selectPanel(name) {
  document.querySelectorAll('.panel').forEach(x => x.classList.toggle('active', x.id === `panel-${name}`));
  document.querySelectorAll('.nav button').forEach(x => x.classList.toggle('active', x.dataset.panel === name));
  history.replaceState(null, '', `#${name}`);
  if (name === 'equip' && !equipLoaded) { equipLoaded=true; loadEquip(); }
}
document.querySelectorAll('.nav button').forEach(b => b.onclick=()=>selectPanel(b.dataset.panel));

async function loadTeam() {
  const r=await fetch('/api/team'); if(!r.ok) throw new Error(`队伍 API ${r.status}`); teamData=await r.json();
  Object.assign(unitNames, Object.fromEntries(teamData.characters.map(c => [c.id, c.name])));
  $('#team-root').innerHTML=teamData.characters.map(c => {
    const s=c.stats, lc=c.light_cone;
    const stats=[['攻击',num(s.atk)],['速度',Number(s.speed).toFixed(1)],['生命',num(s.hp)],['防御',num(s.defense)],['暴击',`${(s.crit_rate*100).toFixed(1)}%`],['暴伤',`${(s.crit_dmg*100).toFixed(1)}%`],['充能',`${(s.energy_regen*100).toFixed(1)}%`],['击破',`${(s.break_effect*100).toFixed(1)}%`]];
    const sets=(c.relic_sets||[]).map(x=>`${esc(x.name)} ${x.pieces}件`).join(' · ') || '未配置';
    const ranks=(c.ranks||[]).map(x=>`E${x.rank} ${esc(x.name)}${x.exec?' [已接入]':x.exec_skip?' [未接入]':''}`).join('；') || '0 命';
    return `<article class="char-card card"><div class="char-head"><div><h3>${esc(c.name)}</h3><div class="info-line mono">${esc(c.id)} · ${esc(c.element)} · ${esc(c.path)}</div></div><span class="badge">E${c.eidolon}</span></div>
      <div class="stat-grid">${stats.map(([k,v])=>`<div class="stat"><span>${k}</span><b>${v}</b></div>`).join('')}</div>
      <div class="info-line"><strong>光锥：</strong>${lc?`${esc(lc.name)} · 叠影${lc.refinement}`:'未配置'}</div>
      <div class="info-line"><strong>套装：</strong>${sets}</div><div class="info-line"><strong>行迹：</strong><span class="mono">${esc(JSON.stringify(c.skill_levels||{}))}</span></div>
      <div class="info-line"><strong>技能倍率：</strong>${Object.entries(c.skills||{}).map(([k,x])=>`${skillName(k)} ${x.is_attack?`${Number(x.multiplier_pct).toFixed(1)}% 攻击力`:'非攻击'}`).join(' · ')}</div>
      <details><summary class="info-line"><strong>星魂与机制明细</strong></summary><div class="info-line">${ranks}</div>${lc?.effect?`<div class="info-line"><strong>${esc(lc.effect.name)}：</strong>${esc(clean(lc.effect.desc))} ${lc.effect.exec?'<span class="badge good">已接入</span>':''}</div>`:''}</details>
      ${c.note?`<div class="info-line trust">${esc(clean(c.note))}</div>`:''}</article>`;
  }).join('');
  const unv=teamData.trust?.unverified||[];
  $('#team-trust').innerHTML=unv.length?`<details class="card" style="margin-top:14px"><summary>信任度信封 · ${unv.length} 处未验证输入</summary><div class="details-body"><div class="info-line mono" style="padding-top:12px">${unv.map(esc).join('<br>')}</div></div></details>`:'';
}

async function loadStages() {
  const r=await fetch('/api/stages'); if(!r.ok) throw new Error(`关卡 API ${r.status}`); stageData=await r.json();
  $('#sim-stage').innerHTML=stageData.stages.map(s=>`<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
  $('#sim-stage').value=stageData.default; indexEnemyNames(); renderStage();
}
function indexEnemyNames(){ if(!stageData)return; for(const s of stageData.stages)for(const w of s.waves)for(const e of w.enemies)unitNames[e.key]=e.name; unitNames.MEM='迷迷'; }
function currentStage(){ return stageData?.stages.find(x=>x.id===$('#sim-stage').value)||stageData?.stages[0]; }
function renderStage(){
  const s=currentStage(); if(!s)return;
  const waves=s.waves.map(w=>`<div class="wave-block"><div class="wave-title">波次 ${w.index}${w.note?` · ${esc(clean(w.note))}`:''}</div><div class="enemy-grid">${w.enemies.map(e=>`<div class="enemy-static"><b>${esc(e.name)}</b> <span class="mono">${esc(e.key)}</span><br>HP ${num(e.hp)} · 速度 ${esc(e.speed)} · 防御 ${esc(e.defense)} · 韧性 ${esc(e.toughness)}<br>弱点 ${esc((e.weaknesses||[]).join(' / ')||'—')}<br>抗性 ${esc(Object.entries(e.resistances||{}).map(([k,v])=>`${k} ${Math.round(v*100)}%`).join(' / ')||'0%')}<br>技能 ${esc((e.skills||[]).map(x=>`${x.name} ×${x.mult}`).join('；')||'未配置')}</div>`).join('')}</div></div>`).join('');
  $('#stage-root').innerHTML=`<b>${esc(s.label)}</b> <span class="badge">Stage ${esc(s.stage_id||'—')}</span><span class="badge">Lv${s.level}</span><span class="badge">${s.wave_count} 波</span><span class="badge">${s.target_av} AV</span><br>${esc(clean(s.note))}${(s.unverified_inputs||[]).length?`<div class="trust" style="margin-top:8px">未验证近似：${s.unverified_inputs.map(x=>esc(clean(x))).join('；')}</div>`:''}${waves}`;
}
$('#sim-stage').onchange=renderStage;

let eqTimer=null;
async function loadEquip(){
  const kind=$('#eq-kind').value,q=$('#eq-q').value; const r=await fetch(`/api/equipment?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}`); if(!r.ok)throw new Error(`资料库 API ${r.status}`); const d=await r.json();
  $('#eq-root').innerHTML=(d.items||[]).map(it=>{const body=it.effect?`${it.effect.name?`<b>${esc(it.effect.name)}</b><br>`:''}${esc(clean(it.effect.desc))}${it.effect.exec?'<br><span class="badge good">已接入模拟</span>':''}`:it.sets?it.sets.map(x=>`${x.key}件：${esc(clean(x.desc))}`).join('<br>'):it.ranks?it.ranks.map(x=>`E${x.rank} ${esc(x.name)} ${x.exec?'<span class="badge good">已接入</span>':x.exec_skip?'<span class="badge warn">未接入</span>':''}<br>${esc(clean(x.desc))}`).join('<br><br>'):'';return `<details class="eq-item card"><summary><span class="mono">${esc(it.id)}</span> ${esc(it.name)} ${it.extra||''}</summary><div class="details-body"><div class="eq-desc" style="padding-top:12px">${body}</div></div></details>`}).join('')||'<div class="page-sub">无匹配结果</div>';
}
$('#eq-kind').onchange=loadEquip; $('#eq-q').oninput=()=>{clearTimeout(eqTimer);eqTimer=setTimeout(loadEquip,250)};

const activityText={
  idle:['等待开始','选择关卡与指挥方式后开始。'], starting:['正在创建推演','初始化固定 seed 与事件流。'], preparing:['正在准备战场','装配队伍、敌人与行动队列。'],
  waiting_llm_decision:['DeepSeek 正在规划','模型正在读取当前局面并选择技能、目标与大招时机。'], executing_action:['正在结算行动','模拟器正在执行技能与全部自动触发链。'],
  waiting_llm_evaluation:['DeepSeek 正在复盘','本 act 已出现在时间线；模型正在判断继续、回退或停止。'], applying_evaluation:['正在应用自评','处理模型的继续、回退或收敛决定。'],
  executing_demo:['演示策略运行中','模拟器正在按内置策略连续执行。'], stop_requested:['已请求停止','当前模型请求返回后将安全停止。'], finished:['推演已结束','完整报告已经生成。'], error:['推演异常','查看错误信息后重新开始。']
};
function activityDetail(d){
  const a=d.activity_detail||{}, st=d.state, names=unitNames;
  if(d.activity==='waiting_llm_decision') return `等待 act #${a.act||((st?.progression?.acts||0)+1)} · 决策单位 ${esc(names[a.unit]||a.unit||'—')}`;
  if(d.activity==='executing_action') return `${esc(names[a.unit]||a.unit||'—')} ${skillName(a.skill)} → ${esc(names[a.target]||a.target||'默认目标')}${a.note?` · ${esc(a.note)}`:''}`;
  if(d.activity==='waiting_llm_evaluation') return `act #${a.act} 已结算 · 伤害 ${num(a.act_result?.damage_delta)}${a.decision?.note?` · ${esc(a.decision.note)}`:''}`;
  if(d.activity==='applying_evaluation') return `${esc(a.verdict||'continue')}${a.reason?` · ${esc(a.reason)}`:''}`;
  if(d.activity==='error') return esc(a.error||d.stop_reason);
  if(d.stop_reason) return esc(d.stop_reason);
  return activityText[d.activity]?.[1]||'等待状态更新。';
}
function renderCurrent(d){
  const el=$('#current-decision'), a=d.activity_detail||{}, st=d.state, base=activityText[d.activity]||activityText.idle;
  let title=base[0], copy=activityDetail(d);
  if(d.activity==='waiting_llm_decision'&&st?.decision){const x=st.decision;title=`规划 act #${a.act} · ${unitNames[x.unit]||x.unit}`;copy=`可选 ${x.skills.map(skillName).join(' / ')} · 目标 ${x.targets.map(id=>unitNames[id]||id).join(' / ')}${x.ult_ready.length?` · 可放大招 ${x.ult_ready.map(id=>unitNames[id]||id).join(' / ')}`:''}`;}
  if(d.activity==='waiting_llm_evaluation'&&a.decision){title=`复盘 act #${a.act} · ${unitNames[a.decision.unit]||a.decision.unit} ${skillName(a.decision.skill)}`;copy=`造成 ${num(a.act_result?.damage_delta)} 伤害，SP ${a.act_result?.sp??'—'}。${a.decision.note?` 指挥理由：${a.decision.note}`:''}`;}
  el.innerHTML=`<div class="current-label">当前环节</div><div class="current-title">${esc(title)}</div><div class="current-copy">${esc(clean(copy))}</div>`;
}
function bar(width,kind=''){return `<div class="bar ${kind}"><i style="width:${clamp(width)}%"></i></div>`;}
function unitSideClass(type){return `side-${['character','enemy','memosprite'].includes(type)?type:'character'}`;}
function namedDetail(value){let text=String(value||'');for(const [id,name] of Object.entries(unitNames).sort((a,b)=>b[0].length-a[0].length))text=text.split(id).join(name);return text;}
function renderActionOrder(st){
  const upcoming=st?.action_order?.upcoming||[],history=st?.action_order?.history||[];
  $('#turn-order-count').textContent=`${upcoming.length} 单位`;
  $('#action-history-count').textContent=`${history.length} 次`;
  $('#turn-order-live').innerHTML=upcoming.length?upcoming.map(x=>`<article class="turn-step"><span class="turn-rank">${x.index}</span><div class="turn-name">${esc(x.name||unitNames[x.unit_id]||x.unit_id)}</div><span class="side-tag ${unitSideClass(x.unit_type)}">${esc(x.side)}</span><div class="turn-meta">距行动 ${Number(x.av).toFixed(1)} AV<br>预计 t ${Number(x.at).toFixed(1)} · 速 ${Number(x.speed).toFixed(1)}</div></article>`).join(''):'<div class="action-order-empty">当前行动条为空。</div>';
  const root=$('#action-history-live'),wasNearBottom=root.scrollHeight-root.scrollTop-root.clientHeight<45;
  root.innerHTML=history.length?history.map(x=>`<div class="action-history-row"><span class="action-history-index">#${x.index}</span><span class="action-history-time">t ${Number(x.t).toFixed(1)}</span><span class="side-tag ${unitSideClass(x.unit_type)}">${esc(x.side)}</span><span class="action-history-unit">${esc(x.name||unitNames[x.unit_id]||x.unit_id)}</span><span class="action-history-action">${esc(x.action_name||x.action)}</span>${x.detail?`<span class="action-history-detail">${esc(namedDetail(x.detail))}</span>`:''}</div>`).join(''):'<div class="action-order-empty">尚无已结算行动。</div>';
  if(wasNearBottom||history.length>lastActionCount)root.scrollTop=root.scrollHeight;
  lastActionCount=history.length;
}
function formatEffect(x){
  const percentStats=['crit_rate','crit_dmg','dmg_bonus','atk_pct','speed_pct','def_pct','break_effect','energy_regen','heal_bonus','true_dmg','concert_atk','mems_support','res_pen'];
  const value=percentStats.includes(x.stat)?`${x.value>=0?'+':''}${(Number(x.value)*100).toFixed(1)}%`:`${x.value>=0?'+':''}${Number(x.value).toFixed(1)}`;
  return `<span class="badge ${x.kind==='debuff'?'warn':'good'}">${esc(x.label||x.stat)} ${esc(value)} · ${esc(x.source_name||x.source)} · ${x.permanent?'常驻':`剩余${x.remaining}`}</span>`;
}
function renderBattleState(st){
  if(!st){$('#enemy-live').innerHTML=$('#ally-live').innerHTML='<span class="section-kicker">等待战斗状态</span>';return;}
  const enemyPanels=st.panels?.enemies||{};
  $('#enemy-live').innerHTML=Object.entries(st.enemies||{}).map(([id,e])=>{const effects=enemyPanels[id]||{};const tags=[...(effects.buffs||[]),...(effects.debuffs||[])].map(formatEffect).join('');return `<div class="unit-card"><div class="unit-line"><span class="unit-name">${esc(e.name||unitNames[id]||id)} ${e.broken?'<span class="badge warn">击破</span>':''}</span><span class="unit-numbers">${num(e.hp)} / ${num(e.hp_max)}</span></div>${bar(e.hp_pct)}<div class="unit-line" style="margin-top:6px"><span class="section-kicker">韧性</span><span class="unit-numbers">${num(e.toughness)} / ${num(e.toughness_max)}</span></div>${bar(e.toughness_max?e.toughness/e.toughness_max*100:0,'toughness')}${tags?`<div style="margin-top:8px">${tags}</div>`:''}</div>`}).join('')||'<span class="section-kicker">敌人已全灭</span>';
  const panels=st.panels?.characters||{};
  $('#ally-live').innerHTML=Object.entries(st.allies||{}).map(([id,a])=>{const p=panels[id]||{},base=p.base||{},eff=p.effective||base;const changed=(k)=>Math.abs(Number(eff[k]||0)-Number(base[k]||0))>1e-9?' good':'';const tags=[...(p.buffs||[]),...(p.debuffs||[])].map(formatEffect).join('');return `<div class="unit-card"><div class="unit-line"><span class="unit-name">${esc(a.name||unitNames[id]||id)} ${!a.alive?'<span class="badge warn">倒下</span>':''}</span><span class="unit-numbers">HP ${num(a.hp)} / ${num(a.hp_max)} · 能量 ${num(a.energy)} / ${num(a.energy_cost)}</span></div>${bar(a.hp_pct)}<div class="stat-grid" style="margin:9px 0 0"><div class="stat"><span>攻击</span><b class="${changed('atk')}">${num(eff.atk)}</b></div><div class="stat"><span>速度</span><b class="${Math.abs(Number(p.speed||0)-Number(base.speed||0))>1e-9?'good':''}">${Number(p.speed??eff.speed??0).toFixed(1)}</b></div><div class="stat"><span>暴击</span><b class="${changed('crit_rate')}">${(Number(eff.crit_rate||0)*100).toFixed(1)}%</b></div><div class="stat"><span>暴伤</span><b class="${changed('crit_dmg')}">${(Number(eff.crit_dmg||0)*100).toFixed(1)}%</b></div><div class="stat"><span>增伤</span><b class="${changed('dmg_bonus')}">${(Number(eff.dmg_bonus||0)*100).toFixed(1)}%</b></div><div class="stat"><span>击破</span><b class="${changed('break_effect')}">${(Number(eff.break_effect||0)*100).toFixed(1)}%</b></div><div class="stat"><span>充能</span><b class="${changed('energy_regen')}">${(Number(eff.energy_regen||0)*100).toFixed(1)}%</b></div><div class="stat"><span>防御</span><b class="${changed('defense')}">${num(eff.defense)}</b></div></div>${tags?`<div style="margin-top:8px">${tags}</div>`:'<div class="section-kicker" style="margin-top:7px">当前无战斗内增减益</div>'}</div>`}).join('');
  const recent=st.damage?.recent||[]; $('#damage-live').innerHTML=recent.slice().reverse().slice(0,5).map(x=>`<div class="unit-line" style="padding:4px 0"><span>${esc(unitNames[x[1]]||x[1])} → ${esc(unitNames[x[2]]||x[2])}</span><span class="unit-numbers">${num(x[3])} · ${esc(x[4])}</span></div>`).join('')||'<span class="section-kicker">等待伤害事件</span>';
}
function renderTimeline(trail){
  const root=$('#trace'), wasNearBottom=root.scrollHeight-root.scrollTop-root.clientHeight<70;
  $('#trace-count').textContent=`${trail.length} act`;
  if(!trail.length){root.innerHTML='<div class="timeline-empty"><div><b>尚无决策</b><br><br>开始后将实时显示模型理由、技能、目标、伤害与大招。</div></div>';lastTrailCount=0;return;}
  root.innerHTML=trail.map(a=>{const r=a.result||{},spDelta=Number(r.sp_delta||0);return `<article class="timeline-item" data-index="${a.index}"><div class="act-main"><span class="act-unit">${esc(unitNames[a.unit_id]||a.unit_id)}</span><span class="act-action">${esc(skillName(a.skill))} → ${esc(unitNames[a.target]||a.target||'默认')}</span><span class="act-damage">+${num(r.damage_delta)}</span></div><div class="act-meta"><span class="badge">t ${Number(r.t||0).toFixed(1)}</span><span class="badge">波次 ${r.wave||'—'}/${r.wave_count||'—'}</span><span class="badge">SP ${r.sp_before??'—'} → ${r.sp??'—'} (${spDelta>=0?'+':''}${spDelta})</span>${(r.ult_used||[]).map(id=>`<span class="badge good">大招 ${esc(unitNames[id]||id)}</span>`).join('')}${(r.new_breaks||[]).map(id=>`<span class="badge warn">击破 ${esc(unitNames[id]||id)}</span>`).join('')}</div>${a.note?`<div class="act-note">LLM理由（未经规则验证）：${esc(a.note)}</div>`:''}</article>`}).join('');
  if(wasNearBottom||trail.length>lastTrailCount)root.scrollTop=root.scrollHeight; lastTrailCount=trail.length;
}
function renderStatus(d){
  const st=d.state, base=activityText[d.activity]||activityText.idle, isError=d.activity==='error'||String(d.stop_reason||'').startsWith('推演异常');
  $('#global-dot').className=`live-dot ${d.running?'running':isError?'error':''}`; $('#run-dot').className=$('#global-dot').className;
  $('#global-status').textContent=d.running?'推演进行中':isError?'推演异常':d.stop_reason?'推演结束':'空闲'; $('#global-stage').textContent=currentStage()?.label||'尚未选择关卡';
  $('#run-title').textContent=base[0]; $('#run-detail').innerHTML=activityDetail(d); $('#run-time').textContent=`${String(Math.floor((d.elapsed||0)/60)).padStart(2,'0')}:${String(Math.floor((d.elapsed||0)%60)).padStart(2,'0')}`;
  const acts=st?.progression?.acts??d.trail.length; $('#metric-act').innerHTML=`${acts}<small> / ${d.max_acts||$('#sim-max').value}</small>`;
  $('#metric-wave').textContent=st?.wave?`${st.wave.index} / ${st.wave.total}`:'—'; $('#metric-av').innerHTML=`${Number(st?.t||0).toFixed(1)}<small> / ${st?.setup?.target_av||currentStage()?.target_av||'—'}</small>`;
  $('#metric-damage').textContent=num(st?.damage?.total||0); $('#metric-sp').textContent=st?.sp?`${st.sp.value} / ${st.sp.max}`:'—'; $('#metric-branch').textContent=st?.progression?.abandoned_routes||0;
  ['sim-stage','sim-mode','sim-seed','sim-max'].forEach(id=>$('#'+id).disabled=d.running); $('#sim-start').disabled=d.running; $('#sim-stop').disabled=!d.running;
  renderCurrent(d); renderBattleState(st); renderTimeline(d.trail||[]);
  const reportText=d.report||'推演结束后生成完整报告。';
  if(reportText !== lastReport){$('#report').textContent=reportText;lastReport=reportText;}
}
async function copyReport(){
  const text=$('#report').textContent||'';
  try{
    if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);}
    else{throw new Error('clipboard unavailable');}
  }catch(_e){
    const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();document.execCommand('copy');area.remove();
  }
  toast('报告已复制');
}
$('#report-copy').onclick=copyReport;
async function pollSim(immediate=false){
  if(immediate&&pollTimer){clearTimeout(pollTimer);pollTimer=null;}
  if(pollBusy)return; pollBusy=true; let delay=1500;
  try{const r=await fetch('/api/sim/status',{cache:'no-store'});if(!r.ok)throw new Error(`状态 API ${r.status}`);const d=await r.json();renderStatus(d);delay=d.running?450:1500;}
  catch(e){toast(`状态更新失败：${e.message}`);delay=2500;}finally{pollBusy=false;pollTimer=setTimeout(()=>pollSim(),delay);}
}
$('#sim-start').onclick=async()=>{try{const q=new URLSearchParams({mode:$('#sim-mode').value,seed:$('#sim-seed').value,max_acts:$('#sim-max').value,stage:$('#sim-stage').value});const r=await fetch(`/api/sim/start?${q}`);if(!r.ok)throw new Error((await r.json()).error||`HTTP ${r.status}`);lastTrailCount=0;lastActionCount=0;selectPanel('battle');pollSim(true);}catch(e){toast(`启动失败：${e.message}`);}};
$('#sim-stop').onclick=async()=>{try{await fetch('/api/sim/stop');pollSim(true);}catch(e){toast(`停止失败：${e.message}`);}};

(async function init(){
  try{await Promise.all([loadTeam(),loadStages()]);const hash=location.hash.slice(1);selectPanel(['battle','team','equip'].includes(hash)?hash:'battle');await pollSim();}
  catch(e){toast(`初始化失败：${e.message}`);}
})();
</script>
</body>
</html>'''
