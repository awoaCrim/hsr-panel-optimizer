"""星魂效果（exec DSL）模拟器接入测试。

队伍配置：红A E2（1 命 3 战技回 SP / 2 命终结技量子抗性+弱点）、花火 E4（大招额外
回 SP + 上限 +1）、知更鸟 E1（协奏全属性抗穿）、记忆主 E1（声援目标暴击 +10%）。
等级类 E3/E5 不接入。
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Action, Enemy, Rotation, Stats


def _mk(char_ids, eidolons, seed=0):
    from hsr_sim.build import resolve_equipment
    from hsr_sim.data.loader import load_equipment
    eq = load_equipment()
    chars = {}
    stats = {}
    for cid in char_ids:
        ch = load_character(DATA_DIR / "characters" / f"{cid}.json")
        ch.equipment_effects = resolve_equipment(
            {"light_cone": "", "relic_sets": [], "eidolon": eidolons.get(cid, 0), "cid": cid},
            eq)["effects"]
        chars[cid] = ch
        stats[cid] = Stats(atk=2000.0, speed=150.0, crit_rate=0.5, crit_dmg=1.0)
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Ice", hp=1e9, atk=1000,
        defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Quantum"])}
    return Simulator(chars, stats, enemies, Rotation(), target_av=400.0, seed=seed)


class TestArcherE1:
    def test_three_skills_refund_sp(self):
        """E1：单个回合内 3 次战技 → 回 2 SP（战技本身 -1 SP）。"""
        sim = _mk(["1015"], {"1015": 1})
        sim.sp = 4.0
        for i in range(3):
            sim.external_action = Action(unit_id="1015", action="skill")
            sim._character_act("1015")
        # 3 次战技：4-3=1，第 3 次触发 +2 → 3.0
        assert sim.sp == pytest.approx(3.0)
        # 普攻打断计数
        sim.external_action = Action(unit_id="1015", action="basic")
        sim._character_act("1015")
        assert "1015" not in sim.skill_streak

    def test_e1_no_effect_without_eidolon(self):
        sim = _mk(["1015"], {"1015": 0})
        sim.sp = 0.0
        for _ in range(4):
            sim.external_action = Action(unit_id="1015", action="skill")
            sim._character_act("1015")
        assert sim.sp == pytest.approx(0.0)


class TestArcherE2:
    def test_ult_quantum_pen_and_weakness(self):
        """E2：终结技使目标量子抗性 -20% + 添加量子弱点，持续 2 回合。"""
        sim = _mk(["1015"], {"1015": 2})
        sim.energy["1015"] = sim.chars["1015"].skills["ult"].energy_cost
        sim._execute_ult("1015", sim.chars["1015"].skills["ult"])
        assert sim.buffs.sum_for("enemy_res_pen:Quantum", "elite") == pytest.approx(0.20)
        assert sim.buffs.sum_for("enemy_weakness_add:Quantum", "elite") == pytest.approx(1.0)
        # 抗性生效：伤害计算吃减抗
        stats = sim._effective_stats("1015")
        bonus, di = sim._equip_damage("1015", "ult", "ult", "elite", stats)
        assert di == 0.0
        # 弱点生效：对非量子弱点敌人也能削韧（敌人弱点列表已含 Quantum，用 Ice 敌人验证）
        sim2 = _mk(["1015"], {"1015": 2})
        sim2.enemies["elite"].weaknesses = ["Fire"]     # 无量子弱点
        sim2.energy["1015"] = sim2.chars["1015"].skills["ult"].energy_cost
        sim2._execute_ult("1015", sim2.chars["1015"].skills["ult"])
        t0 = sim2.toughness["elite"]
        sim2._apply_toughness("1015", "elite", 30.0)
        assert sim2.toughness["elite"] == pytest.approx(t0 - 30.0)   # 动态弱点允许削韧


class TestSparkleE4:
    def test_sp_cap_and_refund(self):
        """E4：SP 上限 +1；终结技额外回 1 SP。"""
        sim = _mk(["1306"], {"1306": 4})
        assert sim.sp_max == pytest.approx(8.0)      # 5 基础 + 花火天赋 2 + E4 1
        sim.sp = 0.0
        sim.energy["1306"] = sim.chars["1306"].skills["ult"].energy_cost
        sim._execute_ult("1306", sim.chars["1306"].skills["ult"])
        # 大招自带 sp_bonus 4 + E4 额外 1 = 5
        assert sim.sp == pytest.approx(5.0)


class TestRobinE1:
    def test_concert_res_pen(self):
        """E1：协奏期间全属性抗性穿透 24%。"""
        sim = _mk(["1309"], {"1309": 1})
        m = sim._current_multipliers()
        assert m.res_pen == pytest.approx(0.0)       # 无协奏
        sim.concert_rounds = 2
        m = sim._current_multipliers()
        assert m.res_pen == pytest.approx(0.24)


class TestMemE1:
    def test_mems_support_crit(self):
        """E1：声援目标暴击率 +10%（与声援同步施加）。"""
        sim = _mk(["8007"], {"8007": 1})
        sim._ensure_memosprite_summon()
        sim.memosprite["charge"] = 100.0
        sim._memosprite_act()                        # 强化：声援主C（8007 自己）
        assert sim.buffs.sum_for("crit_rate", "8007") == pytest.approx(0.10)
        assert sim._effective_stats("8007").crit_rate == pytest.approx(0.5 + 0.10)

    def test_no_e1_no_crit_buff(self):
        sim = _mk(["8007"], {"8007": 0})
        sim._ensure_memosprite_summon()
        sim.memosprite["charge"] = 100.0
        sim._memosprite_act()
        assert sim.buffs.sum_for("crit_rate", "8007") == pytest.approx(0.0)
