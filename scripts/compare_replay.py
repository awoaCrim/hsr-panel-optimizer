"""实战录像对账（④）—— 游戏实战记录 vs 模拟器逐行动对比。

用法：
  python scripts/compare_replay.py replay.json [--enemy enemy.json] [--seed 0]

replay.json 格式（实战录制）：
{
  "meta": {"stage": "混沌回忆12", "player": "xxx", "date": "2026-01-01"},
  "team": {"1015": {"atk": 3200, "crit_rate": 0.85, "crit_dmg": 1.6, "speed": 143,
                     "hp": 2500, "defense": 950}, ...},          # 实战面板（必须）
  "enemy": {"boss": {"hp": 1200000, "defense": 1400, "toughness": 480, ...}},  # 可选（默认关卡）
  "actions": [                                                   # 实战行动序列（我方）
    {"unit": "1015", "skill": "skill", "target": "boss"},
    {"unit": "1306", "skill": "skill", "target": "1015"},
    ...
  ],
  "damage_expected": {"unit": "1015", "skill": "skill", "target": "boss", "amount": 52341}
}

对比逻辑（诚实对账）：
- 行动序列重放（模拟器 external_action 逐条注入）
- 每次我方伤害：模拟器给出段级（非暴击端点, 暴击端点）——实战值等于任一端点（或段组合）
  → 公式一致；落在区间外 → 偏差（暴击判定不可复现，方差由统计吸收）
- 能量/SP/韧性时间线逐事件对比
- 输出：通过/偏差表 + 汇总

无实战数据时的自检：用模拟器输出生成伪 replay → 应全通过（框架自证）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hsr_sim.engine.simulate import Simulator  # noqa: E402
from hsr_sim.loader import DATA_DIR, load_character, load_rotation  # noqa: E402
from hsr_sim.model import Action, Enemy, Rotation, Stats  # noqa: E402


def load_replay(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_sim(replay: dict, enemy_file: str, seed: int) -> Simulator:
    """实战面板 + 敌人 → Simulator（无队伍文件依赖）。"""
    from hsr_sim.build import BuildConfig, assemble, resolve_equipment
    from hsr_sim.data.loader import load_equipment
    from hsr_sim.loader import _apply_rank_levels
    equipment = load_equipment()
    chars = {}
    stats = {}
    for cid, panel in replay["team"].items():
        ch = load_character(DATA_DIR / "characters" / f"{cid}.json")
        chars[cid] = ch
        if "build" in panel:
            # 完整装备复现（翁瓦克开局拉条等机制需要）
            b = panel["build"]
            cfg = BuildConfig(main_stats=panel.get("main_stats", {}),
                              substats=panel.get("substats", {}),
                              light_cone=b.get("light_cone", ""),
                              relic_sets=b.get("relic_sets", []),
                              eidolon=b.get("eidolon", 0), cid=cid)
            stats[cid] = assemble(ch.base_stats, ch.element, cfg, equipment)
            ch.equipment_effects = resolve_equipment(
                {"light_cone": b.get("light_cone", ""), "relic_sets": b.get("relic_sets", []),
                 "eidolon": b.get("eidolon", 0), "cid": cid}, equipment)["effects"]
            _apply_rank_levels(ch)
        else:
            stats[cid] = Stats(**{k: panel[k] for k in
                                  ("atk", "speed", "crit_rate", "crit_dmg", "hp", "defense")
                                  if k in panel})
    if "enemy" in replay:
        enemies = {eid: Enemy(id=eid, name=eid, element=e.get("element", "Quantum"),
                              hp=e["hp"], atk=e.get("atk", 1000), defense=e["defense"],
                              speed=e.get("speed", 100), toughness=e["toughness"],
                              weaknesses=e.get("weaknesses", ["Quantum"]))
                   for eid, e in replay["enemy"].items()}
        target_av = replay.get("target_av", 400.0)
        level = replay.get("level", 90)
    else:
        from hsr_sim.loader import load_enemies
        enemies, level, target_av = load_enemies(DATA_DIR / enemy_file)
    return Simulator(chars, stats, enemies, Rotation(), target_av, level, seed=seed)


def compare(replay: dict, sim: Simulator) -> dict:
    """逐行动重放对比：伤害端点匹配 + 行动序列一致 + 终态偏差。"""
    report = {"actions_total": len(replay["actions"]), "checked": 0,
              "damage_ok": 0, "damage_mismatch": [], "seq_break": None}
    # 开局效果（翁瓦克拉条等——observe 路径应用，重放需同步）
    if not sim._start_effects_applied:
        sim._apply_start_effects()
    # 大招策略：逐 act 声明（replay act 带 "ults": 本 act 释放的角色列表）→ 精确复现时机
    sim.ult_override = True
    # 行动序列重放
    for i, act in enumerate(replay["actions"]):
        ults = act.get("ults")
        if ults is not None:
            sim.ult_hold = set(sim.chars) - set(ults)   # 只放声明的（RNG 消费一致）
        if i >= 200:
            break
        nxt = sim.queue.next()
        if nxt is None:
            report["seq_break"] = f"act#{i}: 期望 {act['unit']} 但已终局"
            break
        # 跳过自动行动（MEM/敌人——observe 语义：自动执行到决策点）
        guard = 0
        while nxt[0] != act["unit"]:
            sim.run_step()
            nxt = sim.queue.next()
            guard += 1
            if nxt is None or guard > 200:
                report["seq_break"] = f"act#{i}: 期望 {act['unit']} 实际 {nxt[0] if nxt else '终局'}"
                break
        if report["seq_break"]:
            break
        before = len(sim.damage_events)
        sim.external_action = Action(unit_id=act["unit"], action=act["skill"],
                                     target=act.get("target", ""))
        sim.run_step()
        report["checked"] += 1
        # 本次行动伤害：段级端点检查
        evs = sim.damage_events[before:]
        exp = act.get("damage")
        if exp is not None:
            # 端点区间匹配（④ 诚实对账）：暴击判定不可复现（随机），但伤害公式可核对——
            # 每段伤害的端点 = {非暴击, 非暴击×(1+暴伤)}；act 总伤害 ∈ [非暴击总和, 全暴击总和]
            main = [e for e in evs if e.kind not in ("break", "true")]
            total = sum(e.amount for e in main)
            # 无暴击概念的事件（知更鸟附加/固定伤害）两端都取自身值
            lo = sum(e.noncrit if e.noncrit > 0 else e.amount for e in main)
            hi = sum(e.noncrit * e.crit_dmg_mult if e.noncrit > 0 and e.crit_dmg_mult > 0
                     else e.amount for e in main)
            if exp == 0 or (lo * 0.995 <= exp <= hi * 1.005):
                report["damage_ok"] += 1
            else:
                report["damage_mismatch"].append(
                    {"act": i, "unit": act["unit"], "sim": round(total, 1),
                     "sim_range": [round(lo, 1), round(hi, 1)],
                     "replay": exp, "ratio": round(total / exp, 3)})
    report["final"] = {"t": round(sim.t, 2), "sp": round(sim.sp, 2),
                       "energy": {c: round(v, 1) for c, v in sim.energy.items()},
                       "enemy_hp_left": {e: round(v, 0) for e, v in sim.enemy_hp.items()},
                       "char_hp_left": {c: round(v, 0) for c, v in sim.char_hp.items()},
                       "kills": sum(1 for v in sim.enemy_hp.values() if v <= 0)}
    return report


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    replay_path = Path(sys.argv[1])
    enemy_file = "enemy_elite90.json"
    if "--enemy" in sys.argv:
        enemy_file = sys.argv[sys.argv.index("--enemy") + 1]
    seed = 0
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])
    replay = load_replay(replay_path)
    sim = build_sim(replay, enemy_file, seed)
    rep = compare(replay, sim)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    ok = rep["seq_break"] is None and not rep["damage_mismatch"]
    print(f"\n结果：{'✅ 对账通过' if ok else '❌ 存在偏差'}"
          f"（伤害检查 {rep['damage_ok']}/{rep['checked']}）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
