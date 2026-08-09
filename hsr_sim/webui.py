"""WebUI —— 队伍配置展示 + 装备库 + 推演控制台（纯 stdlib，无外部依赖）。

用法：
  python -m hsr_sim.webui [--port 8000] [--llm-config <json>]

页面：
  1. 队伍总览：4 角色完整配置（面板/光锥+精炼效果/套装 2·4 件/星魂/词条/信任度）
  2. 装备库：169 光锥 / 60 套装 / 星魂（搜索浏览）
  3. 推演控制台：启动推演（demo 或 LLM 指挥）、实时决策轨迹/事件流/报告
     （按用户要求不含 undo 操作）

API：
  /api/team                      队伍完整配置 JSON
  /api/equipment?kind=&q=        装备库（kind: light_cones|relic_sets|eidolons）
  /api/sim/start?mode=&seed=&max_acts=  启动后台推演（mode: demo|llm）
  /api/sim/status                推演进度（轨迹/事件流/报告）
  /api/sim/stop                  停止推演
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .loader import DATA_DIR

DEFAULT_TEAM = DATA_DIR / "team_real.json"
DEFAULT_ENEMY = DATA_DIR / "enemy_starforge12b.json"
BUILTIN_STAGES = (
    ("starforge12b", "忘却之庭·星启模式 第12关 第2节点", DEFAULT_ENEMY),
    ("elite90", "90级双精英靶场", DATA_DIR / "enemy_elite90.json"),
    ("boss90", "90级单Boss靶场", DATA_DIR / "enemy_boss90.json"),
    ("trash90", "90级三小怪靶场", DATA_DIR / "enemy_trash90.json"),
)

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>hsr-sim · 推演台</title>
<style>
:root { color-scheme: dark; }
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #0c0a14; color: #e8e4f0; margin: 0; }
header { display: flex; gap: 12px; padding: 12px 20px; background: #171226; border-bottom: 1px solid #2a2140; position: sticky; top: 0; z-index: 10; }
header button { background: none; border: none; color: #9a90b8; font-size: 15px; padding: 8px 14px; cursor: pointer; border-radius: 8px; }
header button.active { color: #fff; background: #2d2350; }
main { padding: 20px; max-width: 1200px; margin: 0 auto; }
h2 { color: #c9b8ff; }
.card { background: #151020; border: 1px solid #2a2140; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(520px, 1fr)); gap: 16px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
td, th { padding: 4px 8px; border-bottom: 1px solid #241c3a; text-align: left; }
.mono { font-family: Consolas, monospace; font-size: 12px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; margin-left: 6px; }
.tag.on { background: #123a24; color: #6fdc9c; }
.tag.off { background: #3a2412; color: #dc9f6f; }
.tag.dim { background: #262040; color: #8a82a8; }
input, select, button.ctl { background: #1d1630; color: #e8e4f0; border: 1px solid #3a2f60; border-radius: 8px; padding: 7px 12px; font-size: 14px; }
button.ctl { cursor: pointer; }
button.ctl:hover { background: #2d2350; }
button.ctl:disabled { opacity: .4; cursor: default; }
#trace { max-height: 380px; overflow-y: auto; font-size: 12px; }
#trace div { padding: 2px 4px; border-bottom: 1px solid #1e1830; }
.status-line { font-size: 13px; color: #9a90b8; }
.panel { display: none; } .panel.active { display: block; }
.eq-item { padding: 10px; border: 1px solid #241c3a; border-radius: 8px; margin-bottom: 8px; cursor: pointer; }
.eq-item .desc { color: #9a90b8; font-size: 12px; margin-top: 4px; }
.rank-line { font-size: 12px; color: #b8b0d0; margin: 2px 0; }
.stage-head { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.wave { border-left: 3px solid #7058b5; padding-left: 12px; margin-top: 14px; }
.enemy-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:10px; }
.enemy-card { background:#100c19; border:1px solid #2a2140; border-radius:9px; padding:11px; }
.enemy-card h4 { margin:0 0 7px; color:#d8caff; }
.warn { color:#f2b56b; }
</style>
</head>
<body>
<header>
  <button class="active" data-p="team">队伍配置</button>
  <button data-p="equip">装备库</button>
  <button data-p="sim">推演控制台</button>
</header>
<main>
  <div id="panel-team" class="panel active"><h2>队伍配置</h2><div id="team-root"></div></div>
  <div id="panel-equip" class="panel">
    <h2>装备库</h2>
    <div style="margin-bottom:12px">
      <select id="eq-kind">
        <option value="light_cones">光锥</option>
        <option value="relic_sets">遗器套装</option>
        <option value="eidolons">星魂</option>
      </select>
      <input id="eq-q" placeholder="搜索名称/描述…" style="width:280px">
    </div>
    <div id="eq-root"></div>
  </div>
  <div id="panel-sim" class="panel">
    <h2>推演控制台</h2>
    <div class="card">
      <div class="stage-head">
        <label>关卡 <select id="sim-stage"></select></label>
        <label>指挥 <select id="sim-mode"><option value="demo">演示策略</option><option value="llm">LLM 指挥</option></select></label>
        <label>Seed <input id="sim-seed" type="number" value="0" style="width:90px" title="随机 seed"></label>
        <label>Act 上限 <input id="sim-max" type="number" value="200" style="width:90px" title="act 上限"></label>
        <button class="ctl" id="sim-start">开始推演</button>
        <button class="ctl" id="sim-stop" disabled>停止</button>
        <span class="status-line" id="sim-status"></span>
      </div>
    </div>
    <div class="card"><h3>当前关卡</h3><div id="stage-root" class="status-line">加载中…</div></div>
    <div class="card"><h3>决策轨迹</h3><div id="trace"></div></div>
    <div class="card"><h3>推演报告</h3><pre id="report" style="white-space:pre-wrap;font-size:12px"></pre></div>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
document.querySelectorAll('header button').forEach(b => b.onclick = () => {
  document.querySelectorAll('header button').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); $('#' + 'panel-' + b.dataset.p).classList.add('active');
});
function clean(desc) { return (desc || '').replace(/<[^>]+>/g, ''); }
function tag(on, text) { return `<span class="tag ${on ? 'on' : 'off'}">${text}</span>`; }

async function loadTeam() {
  const r = await fetch('/api/team'); const d = await r.json();
  const root = $('#team-root'); root.innerHTML = '';
  const grid = document.createElement('div'); grid.className = 'grid';
  for (const c of d.characters) {
    const lc = c.light_cone ? `<div class="rank-line">光锥[${c.light_cone.id}] ${c.light_cone.name}（叠影${c.light_cone.refinement}；80级白值 ${JSON.stringify(c.light_cone.base_stats)}）</div>
      ${c.light_cone.effect ? `<div class="rank-line">${c.light_cone.effect.name}：${clean(c.light_cone.effect.desc)}${c.light_cone.effect.exec ? `${tag(true,'已接入模拟')} 执行值${JSON.stringify(c.light_cone.effect.exec)}` : ''}</div>` : ''}` : '<div class="rank-line">光锥：未配置</div>';
    const sets = (c.relic_sets || []).map(s => `<div class="rank-line">套装 ${s.name}（${s.pieces}件）${s.desc2 ? `：${clean(s.desc2)}` : ''}${s.desc4 ? `；${clean(s.desc4)}` : ''}</div>`).join('');
    const ranks = (c.ranks || []).map(r => `<div class="rank-line">E${r.rank} ${r.name}${r.exec ? tag(true,'已接入') : r.exec_skip ? tag(false,'未接入') : ''}：${clean(r.desc)}</div>`).join('');
    const s = c.stats;
    const card = document.createElement('div'); card.className = 'card';
    card.innerHTML = `<h3>${c.id} ${c.name} <span class="tag dim">${c.element} · ${c.path}</span><span class="tag dim">星魂 ${c.eidolon} 命</span></h3>
      <table>
        <tr><td>攻击</td><td class="mono">${s.atk.toFixed(0)}</td><td>速度</td><td class="mono">${s.speed.toFixed(1)}</td></tr>
        <tr><td>生命</td><td class="mono">${s.hp.toFixed(0)}</td><td>防御</td><td class="mono">${s.defense.toFixed(0)}</td></tr>
        <tr><td>暴击率</td><td class="mono">${(s.crit_rate*100).toFixed(1)}%</td><td>暴伤</td><td class="mono">${(s.crit_dmg*100).toFixed(1)}%</td></tr>
        <tr><td>充能效率</td><td class="mono">${(s.energy_regen*100).toFixed(1)}%</td><td>击破</td><td class="mono">${(s.break_effect*100).toFixed(1)}%</td></tr>
      </table>
      <h4>装备</h4>${lc}${sets}
      <h4>星魂</h4>${ranks || '<div class="rank-line">0 命</div>'}
      <h4>行迹等级</h4><div class="rank-line mono">${JSON.stringify(c.skill_levels || {})}</div>
      ${c.note ? `<div class="rank-line warn">${clean(c.note)}</div>` : ''}
      <h4>词条</h4><div class="rank-line">主词条 ${JSON.stringify(c.main_stats)}</div><div class="rank-line">副词条 ${JSON.stringify(c.substats)}</div>`;
    grid.appendChild(card);
  }
  root.appendChild(grid);
  if (d.trust && d.trust.unverified) {
    root.insertAdjacentHTML('beforeend', `<div class="card"><h3>信任度信封</h3><div class="rank-line">${d.trust.unverified.length} 处未验证输入：<span class="mono">${d.trust.unverified.slice(0,12).join('、')}${d.trust.unverified.length > 12 ? '…' : ''}</span></div></div>`);
  }
}

let eqTimer = null;
async function loadEquip() {
  const kind = $('#eq-kind').value, q = $('#eq-q').value;
  const r = await fetch(`/api/equipment?kind=${kind}&q=${encodeURIComponent(q)}`); const d = await r.json();
  $('#eq-root').innerHTML = (d.items || []).map(it => {
    const body = it.effect ? `<div class="desc">${it.effect.name ? '精1 ' + it.effect.name + '：' : ''}${clean(it.effect.desc)} 参数${JSON.stringify(it.effect.level_1_params || it.effect.params || [])}${it.effect.exec ? tag(true,'已接入') : ''}</div>` : '';
    const sets = it.sets ? `<div class="desc">${it.sets.map(x => `${x.key}件：${clean(x.desc)}`).join('<br>')}</div>` : '';
    const ranks = it.ranks ? `<div class="desc">${it.ranks.map(x => `E${x.rank} ${x.name}${x.exec ? tag(true,'已接入') : x.exec_skip ? tag(false,'未接入') : ''}：${clean(x.desc)}`).join('<br>')}</div>` : '';
    return `<div class="eq-item"><b>${it.id} ${it.name}</b>${it.extra || ''}${body}${sets}${ranks}</div>`;
  }).join('') || '<div class="status-line">无匹配</div>';
}
$('#eq-kind').onchange = loadEquip;
$('#eq-q').oninput = () => { clearTimeout(eqTimer); eqTimer = setTimeout(loadEquip, 300); };

let stageData = null;
function renderStage() {
  if (!stageData) return;
  const s = stageData.stages.find(x => x.id === $('#sim-stage').value) || stageData.stages[0];
  const waves = s.waves.map(w => `<div class="wave"><h4>波次 ${w.index}${w.note ? ` · ${clean(w.note)}` : ''}</h4>
    <div class="enemy-grid">${w.enemies.map(e => `<div class="enemy-card">
      <h4>${e.name} <span class="tag dim">${e.id}</span></h4>
      <table><tr><td>HP</td><td class="mono">${Number(e.hp).toLocaleString()}</td><td>速度</td><td class="mono">${e.speed}</td></tr>
      <tr><td>攻击</td><td class="mono">${e.atk}</td><td>防御</td><td class="mono">${e.defense}</td></tr>
      <tr><td>韧性</td><td class="mono">${e.toughness}</td><td>属性</td><td>${e.element || '-'}</td></tr></table>
      <div class="rank-line">弱点：${(e.weaknesses || []).join(' / ') || '-'}</div>
      <div class="rank-line">抗性：${Object.entries(e.resistances || {}).map(([k,v]) => `${k} ${(v*100).toFixed(0)}%`).join(' / ') || '0%'}</div>
      <div class="rank-line">技能：${(e.skills || []).map(sk => `${sk.name}×${sk.mult}（CD${sk.ai_cd}/受击回能${sk.sp_hit}）`).join('；') || '未配置'}</div>
    </div>`).join('')}</div></div>`).join('');
  const trust = (s.unverified_inputs || []).length
    ? `<div class="rank-line warn"><b>未验证近似：</b><br>${s.unverified_inputs.map(clean).join('<br>')}</div>`
    : `<div class="rank-line">${tag(true,'关卡输入已验证')}</div>`;
  $('#stage-root').innerHTML = `<div><b>${s.label}</b> <span class="tag dim">Lv${s.level}</span><span class="tag dim">${s.wave_count}波</span><span class="tag dim">目标 ${s.target_av} AV</span></div>
    <div class="rank-line">文件：${s.file}</div><div class="rank-line warn">${clean(s.note)}</div>${trust}${waves}`;
}
async function loadStages() {
  const r = await fetch('/api/stages'); stageData = await r.json();
  $('#sim-stage').innerHTML = stageData.stages.map(s => `<option value="${s.id}">${s.label}</option>`).join('');
  $('#sim-stage').value = stageData.default;
  renderStage();
}
$('#sim-stage').onchange = renderStage;

let pollTimer = null;
async function pollSim() {
  const r = await fetch('/api/sim/status'); const d = await r.json();
  const wave = d.wave ? ` · 波次 ${d.wave.index}/${d.wave.total}` : '';
  $('#sim-status').textContent = d.running ? `推演中… act ${d.trail.length}/${d.max_acts}${wave}（${d.stop_reason || ''}）` : (d.stop_reason ? `已结束：${d.stop_reason}${wave}` : '空闲');
  $('#sim-start').disabled = d.running; $('#sim-stop').disabled = !d.running;
  $('#trace').innerHTML = d.trail.map(a =>
    `<div>act#${a.index} t=${a.result.t.toFixed(2)} ${a.unit_id} ${a.skill}→${a.target||'-'} 伤害${a.result.damage_delta.toFixed(0)}${a.result.ult_used.length ? ` 大招[${a.result.ult_used}]` : ''}${a.note ? ` <span class="status-line">${a.note}</span>` : ''}</div>`).join('');
  $('#report').textContent = d.report || '';
}
$('#sim-start').onclick = async () => {
  const mode = $('#sim-mode').value, seed = $('#sim-seed').value, max = $('#sim-max').value;
  const stage = encodeURIComponent($('#sim-stage').value);
  await fetch(`/api/sim/start?mode=${mode}&seed=${seed}&max_acts=${max}&stage=${stage}`);
  pollTimer = setInterval(pollSim, 800);
};
$('#sim-stop').onclick = async () => { await fetch('/api/sim/stop'); };

loadTeam(); loadEquip(); loadStages(); pollSim();
setInterval(pollSim, 2000);
</script>
</body>
</html>"""


def _clean_desc(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r"<color=#[0-9a-fA-F]{8}>", "", s)
    return s.replace("</color>", "").replace("<unbreak>", "").replace("</unbreak>", "")


class SimRunner:
    """后台推演线程（demo 或 LLM 模式）。"""

    def __init__(self, session_factory, llm_client=None) -> None:
        self._factory = session_factory
        self._llm = llm_client
        self._lock = threading.Lock()
        self._session = None
        self._thread = None
        self._stop = False
        self._trail: list = []
        self._report = ""
        self._stop_reason = ""
        self._max_acts = 0
        self._stage_id = ""

    def start(self, mode: str, seed: int, max_acts: int, stage_id: str = "") -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop = False
            self._trail = []
            self._report = ""
            self._stop_reason = ""
            self._max_acts = max_acts
            self._stage_id = stage_id
        self._thread = threading.Thread(target=self._run,
                                        args=(mode, seed, max_acts, stage_id), daemon=True)
        self._thread.start()

    def _run(self, mode: str, seed: int, max_acts: int, stage_id: str) -> None:
        session = self._factory(seed=seed, stage_id=stage_id)
        self._session = session
        try:
            if mode == "llm":
                if self._llm is None:
                    self._stop_reason = "LLM 未配置（--llm-config 或环境变量）"
                    return
                from .llm.rehearsal import run_rehearsal
                result = run_rehearsal(self._llm, session, max_acts=max_acts, verbose=False)
                self._stop_reason = result.stop_reason
                self._report = result.report
            else:
                from .rehearse import _demo_pilot
                _demo_pilot(session, max_acts=max_acts)
                self._stop_reason = "演示推演完成"
                self._report = session.report()
            # 轨迹（LLM 模式 run_rehearsal 已消费决策，从会话读取）
            self._trail = [{"index": a.index, "unit_id": a.unit_id, "skill": a.skill,
                            "target": a.target, "note": a.note, "result": a.result}
                           for a in session.acts]
        except Exception as e:  # 推演异常也回报给前端
            self._stop_reason = f"推演异常：{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self._thread = None

    def stop(self) -> None:
        self._stop = True

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> Dict[str, Any]:
        wave = None
        if self._session is not None:
            wave = {"index": self._session.sim.enemy_wave + 1,
                    "total": len(self._session.sim._waves)}
        return {"running": self.running, "trail": self._trail, "report": self._report,
                "stop_reason": self._stop_reason, "max_acts": self._max_acts,
                "stage_id": self._stage_id, "wave": wave}


def build_team_payload(team_path: Path = DEFAULT_TEAM) -> Dict[str, Any]:
    from .data.loader import load_equipment, load_team_normalized
    characters, stats, speed_targets, unverified = load_team_normalized(team_path)
    equipment = load_equipment()
    team = json.loads(team_path.read_text(encoding="utf-8"))
    builds = team["builds"]
    out = []
    for cid, ch in characters.items():
        b = builds.get(cid, {})
        lc_id = b.get("light_cone", "")
        lc = (equipment.get("light_cones") or {}).get(lc_id) or {}
        sets = []
        for rs_cfg in b.get("relic_sets", []) or []:
            sid = rs_cfg.get("id") if isinstance(rs_cfg, dict) else rs_cfg
            pieces = int(rs_cfg.get("pieces", 4)) if isinstance(rs_cfg, dict) else 4
            rs = (equipment.get("relic_sets") or {}).get(str(sid)) or {}
            sets.append({"id": sid, "name": rs.get("name"), "pieces": pieces,
                         "desc2": (rs.get("two_piece") or {}).get("desc"),
                         "desc4": (rs.get("four_piece") or {}).get("desc") if pieces >= 4 else None})
        el = int(b.get("eidolon", 0) or 0)
        eid = (equipment.get("eidolons") or {}).get(cid) or {}
        ranks = [{"rank": rk, **{k: rv.get(k) for k in ("name", "desc", "exec", "exec_skip")}}
                 for rk, rv in list((eid.get("ranks") or {}).items())[:el]]
        st = stats[cid]
        out.append({
            "id": cid, "name": ch.name, "element": ch.element, "path": ch.path,
            "eidolon": el,
            "stats": {"atk": st.atk, "speed": st.speed, "crit_rate": st.crit_rate,
                      "crit_dmg": st.crit_dmg, "energy_regen": st.energy_regen,
                      "break_effect": st.break_effect, "hp": st.hp,
                      "defense": st.defense},
            "light_cone": {"id": lc_id, "name": lc.get("name"), "base_stats": lc.get("base_stats"),
                           "refinement": int(b.get("light_cone_rank", 1)),
                           "effect": lc.get("effect")} if lc_id else None,
            "relic_sets": sets,
            "ranks": ranks,
            "skill_levels": b.get("skill_levels", {}), "note": b.get("note", ""),
            "main_stats": b.get("main_stats", {}), "substats": b.get("substats", {}),
        })
    return {"characters": out, "team_file": team_path.name,
            "trust": {"unverified": unverified}}


def build_stage_payload(stage_id: str, label: str, path: Path) -> Dict[str, Any]:
    """关卡展示载荷：波次/敌人面板/弱点抗性/技能全部可视化。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    raw_waves = d.get("waves") or [{"enemies": d.get("enemies", {})}]
    waves = []
    for index, wave in enumerate(raw_waves, 1):
        enemies = []
        for eid, e in wave.get("enemies", {}).items():
            enemies.append({
                "key": eid, "id": e.get("id", eid), "name": e.get("name", eid),
                "element": e.get("element"), "hp": e.get("hp", 0), "atk": e.get("atk", 0),
                "defense": e.get("defense", 0), "speed": e.get("speed", 0),
                "toughness": e.get("toughness", 0), "weaknesses": e.get("weaknesses", []),
                "resistances": e.get("resistances", {}), "skills": e.get("skills", []),
            })
        waves.append({"index": index, "note": wave.get("note", ""), "enemies": enemies})
    return {"id": stage_id, "label": label, "file": path.name, "note": d.get("note", ""),
            "level": d.get("level", 90), "target_av": d.get("target_av", 250),
            "wave_count": len(waves), "waves": waves,
            "unverified_inputs": d.get("unverified_inputs", [])}


def build_stages_payload(stage_paths: Dict[str, tuple], default_stage: str) -> Dict[str, Any]:
    return {"default": default_stage,
            "stages": [build_stage_payload(sid, label, path)
                       for sid, (label, path) in stage_paths.items()]}


def _stage_registry(default_enemy: Path) -> tuple[Dict[str, tuple], str]:
    stages: Dict[str, tuple] = {sid: (label, path) for sid, label, path in BUILTIN_STAGES}
    resolved = default_enemy.resolve()
    default_id = next((sid for sid, (_label, path) in stages.items()
                       if path.resolve() == resolved), "")
    if not default_id:
        default_id = "custom"
        stages[default_id] = (f"自定义关卡 · {default_enemy.name}", default_enemy)
    return stages, default_id


def build_equipment_payload(kind: str, q: str) -> Dict[str, Any]:
    from .data.loader import load_equipment
    eq = load_equipment()
    items = []
    ql = q.lower()
    if kind == "light_cones":
        for cid, lc in (eq.get("light_cones") or {}).items():
            eff = lc.get("effect") or {}
            text = f"{lc.get('name', '')} {_clean_desc(eff.get('desc', ''))}"
            if ql and ql not in text.lower():
                continue
            items.append({"id": cid, "name": lc.get("name"),
                          "extra": f" <span class='tag dim'>{'★' * (lc.get('rarity') or 0)} {lc.get('path')}</span>",
                          "effect": eff})
    elif kind == "relic_sets":
        for sid, rs in (eq.get("relic_sets") or {}).items():
            sets = []
            for key, lab in (("two_piece", "2"), ("four_piece", "4")):
                p = rs.get(key) or {}
                if p:
                    sets.append({"key": lab, "desc": p.get("desc"), "params": p.get("params")})
            text = f"{rs.get('name', '')} {_clean_desc(' '.join(x['desc'] for x in sets))}"
            if ql and ql not in text.lower():
                continue
            items.append({"id": sid, "name": rs.get("name"), "sets": sets})
    else:
        for cid, eid in (eq.get("eidolons") or {}).items():
            ranks = [{"rank": rk, **{k: rv.get(k) for k in ("name", "desc", "exec", "exec_skip")}}
                     for rk, rv in (eid.get("ranks") or {}).items()]
            text = f"{eid.get('name', '')} {_clean_desc(' '.join(r['desc'] for r in ranks))}"
            if ql and ql not in text.lower():
                continue
            items.append({"id": cid, "name": eid.get("name"), "ranks": ranks})
    return {"items": items, "total": len(items)}


class Handler(BaseHTTPRequestHandler):
    runner: SimRunner = None  # type: ignore
    llm_client = None
    team_path: Path = DEFAULT_TEAM
    stage_paths: Dict[str, tuple] = {}
    default_stage: str = "starforge12b"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        try:
            if p == "/" or p == "/index.html":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif p == "/api/team":
                self._send(200, json.dumps(build_team_payload(self.team_path), ensure_ascii=False).encode())
            elif p == "/api/stages":
                payload = build_stages_payload(self.stage_paths, self.default_stage)
                self._send(200, json.dumps(payload, ensure_ascii=False).encode())
            elif p == "/api/equipment":
                qs = parse_qs(u.query)
                payload = build_equipment_payload(qs.get("kind", ["light_cones"])[0],
                                                  qs.get("q", [""])[0])
                self._send(200, json.dumps(payload, ensure_ascii=False).encode())
            elif p == "/api/sim/start":
                qs = parse_qs(u.query)
                self.runner.start(qs.get("mode", ["demo"])[0],
                                  int(qs.get("seed", ["0"])[0]),
                                  int(qs.get("max_acts", ["200"])[0]),
                                  qs.get("stage", [self.default_stage])[0])
                self._send(200, b'{"ok": true}')
            elif p == "/api/sim/stop":
                self.runner.stop()
                self._send(200, b'{"ok": true}')
            elif p == "/api/sim/status":
                self._send(200, json.dumps(self.runner.status(), ensure_ascii=False).encode())
            else:
                self._send(404, b'{"error": "not found"}')
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"},
                                       ensure_ascii=False).encode())


def _make_session_factory(team: Path, stages: Dict[str, tuple], default_stage: str,
                          rotation: Path, legacy: bool):
    def factory(seed: int = 0, stage_id: str = ""):
        from .rehearse import RehearsalSession
        selected = stage_id if stage_id in stages else default_stage
        enemy = stages[selected][1]
        return RehearsalSession.from_files(team=team, enemy=enemy, rotation=rotation,
                                           seed=seed, legacy=legacy,
                                           name=f"webui:{selected}")
    return factory


def main(argv=None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="hsr-sim-webui", description="队伍配置 + 推演控制台 WebUI")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--team", default=str(DEFAULT_TEAM))
    parser.add_argument("--enemy", default=str(DEFAULT_ENEMY),
                        help="默认关卡；页面仍可在内置关卡间切换")
    parser.add_argument("--rotation", default=str(DATA_DIR / "rotation.json"))
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--llm-config", default=None, help="LLM 配置 JSON（推演控制台 LLM 模式用）")
    args = parser.parse_args(argv)

    team_path = Path(args.team)
    stage_paths, default_stage = _stage_registry(Path(args.enemy))
    Handler.team_path = team_path
    Handler.stage_paths = stage_paths
    Handler.default_stage = default_stage
    Handler.runner = SimRunner(_make_session_factory(team_path, stage_paths, default_stage,
                                                     Path(args.rotation), args.legacy))
    if args.llm_config:
        from .llm.client import LLMClient
        cfg = json.loads(Path(args.llm_config).read_text(encoding="utf-8"))
        Handler.llm_client = LLMClient(cfg["base_url"], cfg["api_key"], cfg["model"],
                                       disable_thinking=bool(cfg.get("no_thinking")))
        Handler.runner._llm = Handler.llm_client

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"WebUI: http://127.0.0.1:{args.port}  "
          f"（队伍 {team_path.name} / 默认关卡 {stage_paths[default_stage][0]} / Ctrl+C 退出）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
