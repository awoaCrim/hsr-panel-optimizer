"""T2a Legacy Parity —— 重构后模拟器输出 == v1.5 冻结基线（逐行动）。

目的：证明 Effects 重构（ADR-0006 5.4 Step A）未改变 v1.5 行为。
注意：parity 不证明行为正确（v1.5 本身含已修正的公式错误），正确性由 T2b/T3/T4 保证。
基线文件：tests/golden/reda_v1.5_2t.json（v1.5 实现冻结输出，勿手改）。
"""
import json
from pathlib import Path

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemies, load_rotation, load_team

GOLDEN = Path(__file__).parent / "golden" / "reda_v1.5_2t.json"


def _snapshot():
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    rot = load_rotation(DATA_DIR / "rotation.json")
    sim = Simulator(chars, stats, enemies, rot, target_av, level)
    r = sim.run()
    return {
        "t_end": r.t_end,
        "total_damage": r.total_damage,
        "actions": [[a.t, a.unit_id, a.action, a.detail] for a in r.actions],
        "damage_events": [[e.t, e.source, e.target, round(e.amount, 6), e.kind]
                          for e in sim.damage_events],
        "sp_timeline": [[round(t, 4), sp] for t, sp in r.sp_timeline],
        "breaks": [[round(t, 4), e] for t, e in r.breaks],
        "enemy_hp_left": {k: round(v, 4) for k, v in r.enemy_hp_left.items()},
        "ult_count": r.ult_count,
        "action_count": r.action_count,
    }


def test_legacy_parity_reda_2t():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    new = _snapshot()
    for k in ["t_end", "total_damage", "actions", "damage_events", "sp_timeline",
              "breaks", "enemy_hp_left", "ult_count", "action_count"]:
        assert new[k] == golden[k], f"T2a parity 破坏：{k}"


def test_parity_golden_is_frozen():
    """基线文件不可手改（改了等于自欺）。"""
    meta = json.loads(GOLDEN.read_text(encoding="utf-8"))["meta"]
    assert meta["source"].startswith("v1.5 baseline")
