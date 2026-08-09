"""LLM 迭代循环测试：协议逻辑（验证→反馈→迭代→收敛），用假客户端注入，不调真实 API。"""
import json
from pathlib import Path

import pytest

from hsr_sim.llm.loop import run_iteration

DATA = Path(__file__).resolve().parent.parent / "data"


class FakeClient:
    def __init__(self, proposals):
        self.proposals = proposals
        self.calls = 0

    def chat_json(self, messages, temperature=0.2):
        p = self.proposals[min(self.calls, len(self.proposals) - 1)]
        self.calls += 1
        return p


def _make_team(tmp_path, atk_pct_substats=23, regen_substats=5):
    """词条分配形态的队伍方案（强红A：攻击+充能+2 速词条，30 词条预算内）。"""
    d = json.loads((DATA / "team_reda.json").read_text(encoding="utf-8"))
    subs = {"atk_pct": atk_pct_substats, "speed": 2}
    if regen_substats:
        subs["energy_regen"] = regen_substats
    d["builds"]["1015"] = {
        "main_stats": {"body": "crit_dmg", "feet": "speed", "sphere": "quantum_dmg", "rope": "atk_pct"},
        "substats": subs,
    }
    p = tmp_path / "team.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def _make_enemy(tmp_path, hp_per_enemy):
    d = json.loads((DATA / "enemy_elite90.json").read_text(encoding="utf-8"))
    for e in d["enemies"].values():
        e["hp"] = hp_per_enemy
        e.pop("skills", None)     # 面板迭代（llm_loop）是纯伤害语义：无敌人技能（无生存维度）
        e["speed"] = 10.0         # 靶子不插队（v1 语义）
    p = tmp_path / "enemy.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def _make_rotation(tmp_path, drop_aux_ult=True, drop_archer_ult=False):
    d = json.loads((DATA / "rotation.json").read_text(encoding="utf-8"))
    if drop_aux_ult:
        for cid in ("1306", "1309", "8007"):
            d["policy"][cid]["ult"] = "off"
    if drop_archer_ult:
        d["policy"]["1015"]["ult"] = "off"
    p = tmp_path / "rotation.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


class TestIterationProtocol:
    def test_converges_with_good_proposal(self, tmp_path):
        """收敛路径：红A 攻击词条拉满（词条预算内超强面板）+ 低 HP 靶场 → 击杀且全达标。"""
        team = _make_team(tmp_path, atk_pct_substats=23, regen_substats=5)
        rot = _make_rotation(tmp_path)
        enemy = _make_enemy(tmp_path, 120000.0)
        client = FakeClient([{"builds": {}, "speed_targets": {},
                              "rotation": {}, "reason": "高攻击方案"}])
        result = run_iteration(client, team, enemy, rot,
                               max_rounds=3, verbose=False, policy_search=False)
        assert result.converged
        assert result.rounds == 1
        assert result.reports[0].constraints[0].met   # 击杀

    def test_disk_baseline_restored(self, tmp_path):
        """迭代结束后磁盘上的手填基线必须恢复（迭代是内存态）。"""
        team = _make_team(tmp_path, atk_pct_substats=10)
        rot = _make_rotation(tmp_path, drop_aux_ult=False)
        before = json.loads(team.read_text(encoding="utf-8"))
        client = FakeClient([{"builds": {}, "speed_targets": {}, "rotation": {}}])
        run_iteration(client, team, DATA / "enemy_elite90.json", rot,
                      max_rounds=2, verbose=False, policy_search=False)
        assert json.loads(team.read_text(encoding="utf-8")) == before

    def test_max_rounds_no_convergence(self, tmp_path):
        """5 轮未收敛：保留最近报告，输出差距清单。"""
        team = _make_team(tmp_path, atk_pct_substats=10)
        rot = _make_rotation(tmp_path, drop_aux_ult=False)
        client = FakeClient([{"builds": {}, "speed_targets": {}, "rotation": {}}] * 5)
        result = run_iteration(client, team, DATA / "enemy_elite90.json", rot,
                               max_rounds=5, verbose=False, policy_search=False)
        assert not result.converged
        assert result.rounds == 5
        assert len(result.reports) == 5
        assert result.reports[-1].score < 1_100_000  # 未击杀
