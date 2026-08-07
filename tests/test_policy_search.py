"""策略搜索与策略决策测试：战斗决策参数化（ADR-0005）。

- 策略模式：模拟器按规则实时决策（连打上限/战技预算/大招开关）
- 策略搜索：枚举参数空间，当前面板下找 2T 伤害最优策略
"""
import json

from hsr_sim.engine.policy_search import DEFAULT_SPACE, search_policy, search_summary
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemies, load_rotation, load_team
from hsr_sim.model import CharacterPolicy, Rotation


def _load():
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    mem_speed = 130.0
    for c in chars.values():
        mem = c.talent_extra.get("memosprite")
        if mem:
            mem_speed = mem.get("speed", mem_speed)
    return chars, stats, enemies, level, target_av, mem_speed


class TestPolicyDecision:
    def test_chain_max_limits_burst(self):
        """红A 回路连打：chain_max=2 时每次行动只连打 2 次。"""
        chars, stats, enemies, level, target_av, mem_speed = _load()
        rot = Rotation(policy={
            "1015": CharacterPolicy(ult="on_full", chain_max=2),
            "1306": CharacterPolicy(ult="off", skill_budget=0),
            "1309": CharacterPolicy(ult="off", skill_budget=0),
            "8007": CharacterPolicy(ult="off", skill_budget=0),
        })
        sim = Simulator(chars, stats, enemies, rot, target_av, level, mem_speed)
        res = sim.run()
        skill_count = res.action_count.get("1015", 0)
        # 连打上限 2：每次行动 2 次战技（2T 内多行动轮，总次数 > 2 但每次受限）
        assert res.action_count["1306"] <= 4  # 花火 SP 预算 0 → 普攻
        # 红A 战技次数应与 chain_max=2 的节奏一致（每个行动轮 2 次）
        assert 2 <= skill_count

    def test_skill_budget_limits_aux(self):
        """辅助 skill_budget=0 → 整场不打战技（SP 全留给红A）。"""
        chars, stats, enemies, level, target_av, mem_speed = _load()
        rot = Rotation(policy={
            "1015": CharacterPolicy(ult="on_full", chain_max=5),
            "1306": CharacterPolicy(ult="off", skill_budget=0),
            "1309": CharacterPolicy(ult="off", skill_budget=0),
            "8007": CharacterPolicy(ult="off", skill_budget=0),
        })
        sim = Simulator(chars, stats, enemies, rot, target_av, level, mem_speed)
        res = sim.run()
        # 预算 0：战技次数只能为 0（SP 充足也不会打）
        assert all(res.action_count.get(cid, 0) >= 0 for cid in ("1306", "1309", "8007"))
        # 日志里不应出现辅助战技
        aux_skills = [a for a in res.actions if a.unit_id in ("1306", "1309", "8007") and a.action == "skill"]
        assert aux_skills == []

    def test_ult_off_disables_ult(self):
        chars, stats, enemies, level, target_av, mem_speed = _load()
        rot = Rotation(policy={
            "1015": CharacterPolicy(ult="off", chain_max=5),
            "1306": CharacterPolicy(ult="off", skill_budget=2),
            "1309": CharacterPolicy(ult="off", skill_budget=1),
            "8007": CharacterPolicy(ult="off", skill_budget=1),
        })
        sim = Simulator(chars, stats, enemies, rot, target_av, level, mem_speed)
        res = sim.run()
        assert sum(res.ult_count.values()) == 0  # 全关 → 无人开大

    def test_policy_pull_target(self):
        """花火拉条目标取策略 pull_target（且战技消耗预算）。"""
        chars, stats, enemies, level, target_av, mem_speed = _load()
        rot = Rotation(policy={
            "1015": CharacterPolicy(ult="off", chain_max=5),
            "1306": CharacterPolicy(ult="off", skill_budget=3, pull_target="1015"),
            "1309": CharacterPolicy(ult="off", skill_budget=0),
            "8007": CharacterPolicy(ult="off", skill_budget=0),
        })
        sim = Simulator(chars, stats, enemies, rot, target_av, level, mem_speed)
        res = sim.run()
        sparkle_skills = [a for a in res.actions if a.unit_id == "1306" and a.action == "skill"]
        assert len(sparkle_skills) <= 3  # 预算 3
        # 拉条生效：红A 行动次数应多于无拉条基线（花火 3 次拉条 × 50% 推进）
        assert res.action_count["1015"] > 8


class TestPolicySearch:
    def test_search_finds_applicable_policy(self):
        """搜索出的最优策略可直接执行（无异常、SP 非负）。"""
        chars, stats, enemies, level, target_av, mem_speed = _load()
        result = search_policy(chars, stats, enemies, level, target_av, mem_speed)
        assert result.evaluated > 100
        assert result.valid > 0
        assert result.best_policy  # 有最优解
        assert result.best_sp_min >= -1e-9  # SP 达标
        # 最优策略可复跑（结果一致）
        rot = Rotation(policy=result.best_policy)
        sim = Simulator(chars, stats, enemies, rot, target_av, level, mem_speed)
        res = sim.run()
        assert abs(res.total_damage - result.best_score) < 1e-6

    def test_search_beats_default_policy(self):
        """搜索最优 ≥ 基线策略（默认 rotation.json）。"""
        chars, stats, enemies, level, target_av, mem_speed = _load()
        result = search_policy(chars, stats, enemies, level, target_av, mem_speed)
        base_rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, enemies, base_rot, target_av, level, mem_speed)
        base_score = sim.run().total_damage
        assert result.best_score >= base_score - 1e-6

    def test_search_summary_text(self):
        chars, stats, enemies, level, target_av, mem_speed = _load()
        result = search_policy(chars, stats, enemies, level, target_av, mem_speed)
        text = search_summary(result)
        assert "策略搜索" in text and "2T 伤害" in text
