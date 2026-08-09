"""④ 实战录像对账框架自检：进程内生成伪 replay → 重放对比 → 全通过。"""
import json

import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemies, load_rotation, load_team
from hsr_sim.rehearse import RehearsalSession, _demo_pilot

from scripts.compare_replay import build_sim, compare


def _build_replay():
    """demo 决策器跑完整局（与重放同构造：build_sim 路径）→ 伪实战记录。"""
    team = json.loads((DATA_DIR / "team_reda.json").read_text(encoding="utf-8"))
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    rot = load_rotation(DATA_DIR / "rotation.json")
    # 与重放同构造（build_sim 路径）：先组 replay 骨架再生成 demo
    replay = {
        "meta": {"stage": "self-test"},
        "team": {},
        "enemy": {eid: {"hp": e.hp, "defense": e.defense, "toughness": e.toughness,
                        "speed": e.speed, "weaknesses": e.weaknesses}
                  for eid, e in enemies.items()},
        "actions": [],
    }
    for cid, st in stats.items():
        b = team["builds"][cid]
        replay["team"][cid] = {
            "build": {"light_cone": b["light_cone"], "relic_sets": b["relic_sets"],
                      "eidolon": b["eidolon"]},
            "main_stats": b["main_stats"], "substats": b["substats"],
        }
    sim = build_sim(replay, "enemy_elite90.json", 0)
    s = RehearsalSession(sim)
    _demo_pilot(s, max_acts=200)
    replay["actions"] = [{"unit": a.unit_id, "skill": a.skill, "target": a.target or "",
                          "damage": round(a.result["damage_delta"], 1),
                          "ults": a.result.get("ult_used", [])} for a in s.acts]
    return replay


@pytest.fixture()
def pseudo_replay():
    return _build_replay()


class TestCompareReplay:
    def test_self_check_passes(self, pseudo_replay):
        """伪 replay（模拟器自生成）→ 重放对比全通过（框架自证）。"""
        rep = compare(pseudo_replay, build_sim(pseudo_replay, "enemy_elite90.json", 0))
        assert rep["seq_break"] is None, rep["seq_break"]
        assert not rep["damage_mismatch"], rep["damage_mismatch"][:3]
        assert rep["damage_ok"] == len(pseudo_replay["actions"])

    def test_damage_tamper_detected(self, pseudo_replay):
        """篡改一条伤害 → 必须被检出（框架有区分度）。"""
        replay = json.loads(json.dumps(pseudo_replay))
        replay["actions"][3]["damage"] = replay["actions"][3]["damage"] * 2.0
        rep = compare(replay, build_sim(replay, "enemy_elite90.json", 0))
        assert rep["damage_mismatch"], "篡改未被检出"
