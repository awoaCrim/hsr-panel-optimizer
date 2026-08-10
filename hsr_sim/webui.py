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
import copy
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
DEFAULT_ENEMY = DATA_DIR / "enemy_starforge12c.json"
BUILTIN_STAGES = (
    ("floor12a", "忘却之庭·扫除风暴其十二 第1节点 · 30124121", DATA_DIR / "enemy_floor12_node1.json"),
    ("floor12b", "忘却之庭·扫除风暴其十二 第2节点 · 30124122", DATA_DIR / "enemy_floor12_node2.json"),
    ("starforge12c", "忘却之庭·星启模式 值日行动其十二 第3节点 · 30124123", DEFAULT_ENEMY),
    ("elite90", "90级双精英靶场", DATA_DIR / "enemy_elite90.json"),
    ("boss90", "90级单Boss靶场", DATA_DIR / "enemy_boss90.json"),
    ("trash90", "90级三小怪靶场", DATA_DIR / "enemy_trash90.json"),
)

from .webui_page import PAGE


def _clean_desc(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r"<color=#[0-9a-fA-F]{8}>", "", s)
    return s.replace("</color>", "").replace("<unbreak>", "").replace("</unbreak>", "")


class SimRunner:
    """后台推演线程；在每个 LLM/act 边界发布可轮询的实时状态。"""

    def __init__(self, session_factory, llm_client=None) -> None:
        self._factory = session_factory
        self._llm = llm_client
        self._lock = threading.RLock()
        self._session = None
        self._thread = None
        self._stop_event = threading.Event()
        self._trail: list = []
        self._report = ""
        self._stop_reason = ""
        self._max_acts = 0
        self._stage_id = ""
        self._mode = ""
        self._activity = "idle"
        self._activity_detail: Dict[str, Any] = {}
        self._state: Optional[Dict[str, Any]] = None
        self._started_at: Optional[float] = None
        self._finished_at: Optional[float] = None
        self._updated_at: Optional[float] = None

    @staticmethod
    def _session_trail(session) -> list:
        return [{"index": a.index, "unit_id": a.unit_id, "skill": a.skill,
                 "target": a.target, "note": a.note, "result": dict(a.result)}
                for a in session.acts]

    def _publish(self, activity: str, session, detail: Optional[Dict[str, Any]] = None) -> None:
        detail = dict(detail or {})
        state = detail.pop("state", None)
        if state is None:
            state = session._state()
        with self._lock:
            self._session = session
            self._activity = activity
            self._activity_detail = copy.deepcopy(detail)
            self._state = copy.deepcopy(state)
            self._trail = self._session_trail(session)
            self._updated_at = time.time()

    def start(self, mode: str, seed: int, max_acts: int, stage_id: str = "") -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._trail = []
            self._report = ""
            self._stop_reason = ""
            self._max_acts = max_acts
            self._stage_id = stage_id
            self._mode = mode
            self._activity = "starting"
            self._activity_detail = {"seed": seed}
            self._state = None
            self._started_at = time.time()
            self._finished_at = None
            self._updated_at = self._started_at
            self._thread = threading.Thread(target=self._run,
                                            args=(mode, seed, max_acts, stage_id), daemon=True)
            self._thread.start()

    def _run_demo(self, session, max_acts: int) -> None:
        state = session.observe()
        acts = 0
        self._publish("executing_demo", session, {"state": state, "act": 1})
        while state["phase"] == "decision" and acts < max_acts and not self._stop_event.is_set():
            decision = state["decision"]
            selected = decision["default"]
            option = decision["skill_options"][selected]
            if option["target_type"] == "ally":
                target = decision["ally_targets"][0] if decision["ally_targets"] else ""
            elif option["target_type"] == "enemy":
                target = decision["targets"][0] if decision["targets"] else ""
            else:
                target = ""
            self._publish("executing_action", session, {
                "state": state, "unit": decision["unit"], "skill": selected,
                "target": target, "note": "demo", "act": acts + 1,
            })
            session.act(skill=selected, target=target, note="demo")
            acts += 1
            state = session.observe()
            self._publish("executing_demo", session, {"state": state, "act": acts + 1})
        self._stop_reason = ("用户停止" if self._stop_event.is_set()
                             else "演示推演完成")

    def _run(self, mode: str, seed: int, max_acts: int, stage_id: str) -> None:
        session = None
        try:
            session = self._factory(seed=seed, stage_id=stage_id)
            self._publish("preparing", session, {"mode": mode})
            if mode == "llm":
                if self._llm is None:
                    self._stop_reason = "LLM 未配置（--llm-config 或环境变量）"
                    self._activity = "error"
                    return
                from .llm.rehearsal import run_rehearsal
                result = run_rehearsal(
                    self._llm, session, max_acts=max_acts, verbose=False,
                    on_progress=self._publish,
                    should_stop=self._stop_event.is_set,
                )
                self._stop_reason = result.stop_reason
                self._report = result.report
            else:
                self._run_demo(session, max_acts)
                self._report = session.report(stop_reason=self._stop_reason)
            self._publish("finished", session, {"stop_reason": self._stop_reason})
        except Exception as e:  # 推演异常也回报给前端
            self._stop_reason = f"推演异常：{type(e).__name__}: {e}"
            if session is not None:
                self._publish("error", session, {"error": self._stop_reason})
            else:
                with self._lock:
                    self._activity = "error"
                    self._activity_detail = {"error": self._stop_reason}
        finally:
            with self._lock:
                self._finished_at = time.time()
                self._updated_at = self._finished_at
                self._thread = None

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._activity = "stop_requested"
                self._updated_at = time.time()

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def status(self) -> Dict[str, Any]:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            now = time.time()
            end = now if running else (self._finished_at or now)
            elapsed = end - self._started_at if self._started_at is not None else 0.0
            state = copy.deepcopy(self._state)
            wave = copy.deepcopy(state.get("wave")) if state else None
            return {
                "running": running,
                "mode": self._mode,
                "activity": self._activity,
                "activity_detail": copy.deepcopy(self._activity_detail),
                "state": state,
                "trail": copy.deepcopy(self._trail),
                "report": self._report,
                "stop_reason": self._stop_reason,
                "max_acts": self._max_acts,
                "stage_id": self._stage_id,
                "wave": wave,
                "elapsed": round(elapsed, 1),
                "updated_at": self._updated_at,
            }


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
        skill_payload = {}
        for slot, skill in ch.skills.items():
            skill_payload[slot] = {
                "is_attack": skill.mult > 0.0,
                "attack_scaling": "atk" if skill.mult > 0.0 else "none",
                "multiplier": skill.mult if skill.mult > 0.0 else 0.0,
                "multiplier_pct": round(skill.mult * 100.0, 4) if skill.mult > 0.0 else 0.0,
                "sp_delta": skill.sp,
                "energy": skill.energy,
                "energy_cost": skill.energy_cost,
                "toughness": skill.toughness,
                "note": skill.note,
            }
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
            "skills": skill_payload,
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
            "stage_id": d.get("stage_id"), "challenge_node": d.get("challenge_node", ""),
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
    default_stage: str = "starforge12c"

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
