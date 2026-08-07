"""伤害公式对账测试：每个用例与手算值核对（验收标准①）。"""
import pytest

from hsr_sim.engine.damage import (
    break_damage,
    crit_expectation,
    defense_multiplier,
    expected_damage,
    flat_damage,
    resistance_multiplier,
)
from hsr_sim.model import Stats


class TestCritExpectation:
    def test_75_150(self):
        # 1 + 0.75 × 1.5 = 2.125
        assert crit_expectation(0.75, 1.50) == pytest.approx(2.125)

    def test_crit_rate_capped(self):
        # 120% 暴击率截断到 100%：1 + 1.0 × 1.5 = 2.5
        assert crit_expectation(1.20, 1.50) == pytest.approx(2.5)


class TestDefenseMultiplier:
    def test_level90_vs_def1000(self):
        # (200 + 900) / (1000 + 200 + 900) = 1100/2100
        assert defense_multiplier(90, 1000.0) == pytest.approx(1100 / 2100)

    def test_def_ignore(self):
        # 无视防御 20%：有效防御 800 → 1100/1900
        assert defense_multiplier(90, 1000.0, 0.2) == pytest.approx(1100 / 1900)


class TestResistanceMultiplier:
    def test_positive(self):
        assert resistance_multiplier(0.2) == pytest.approx(0.8)

    def test_penetration_overflow_half(self):
        # 抗性 0.2 - 穿透 0.3 = -0.1 → 溢出减半收益：1 - (-0.1)/2 = 1.05
        assert resistance_multiplier(0.2, 0.3) == pytest.approx(1.05)


class TestExpectedDamage:
    def test_full_chain_hand_calc(self):
        # mult=1.0 atk=1000 dmg_bonus=0 crit=50%/100% def=800 res=0.1 level=90
        # raw = 1000；crit = 1.5；def = 1100/1900；res = 0.9
        stats = Stats(atk=1000.0, crit_rate=0.5, crit_dmg=1.0)
        from hsr_sim.engine.damage import Multipliers
        dmg = expected_damage(1.0, 1000.0, stats, Multipliers(), 800.0, 0.1, 90)
        assert dmg == pytest.approx(1000 * 1.5 * (1100 / 1900) * 0.9)

    def test_true_damage_additive(self):
        stats = Stats(atk=1000.0, crit_rate=0.5, crit_dmg=1.0)
        from hsr_sim.engine.damage import Multipliers
        dmg = expected_damage(1.0, 1000.0, stats, Multipliers(true_dmg=0.10), 800.0, 0.1, 90)
        base = 1000 * 1.5 * (1100 / 1900) * 0.9
        assert dmg == pytest.approx(base * 1.10)


class TestFlatDamage:
    def test_robin_additional(self):
        # 倍率 1.44，攻击 2500，增伤 0.625，防御乘区 1100/2100，抗性 0
        # 固定双暴：1 + 1.0 × 1.5 = 2.5
        dmg = flat_damage(1.44, 2500.0, 0.625, 1100 / 2100, 1.0)
        assert dmg == pytest.approx(1.44 * 2500 * 1.625 * 2.5 * (1100 / 2100))


class TestBreakDamage:
    def test_level90_quantum(self):
        # 90 级量子击破（击破特攻 0）：3767.5 × 0.5 × 1.0
        assert break_damage(90, "Quantum", 0.0, 480) == pytest.approx(3767.5 * 0.5)

    def test_break_effect_scales(self):
        assert break_damage(90, "Quantum", 1.0, 480) == pytest.approx(3767.5 * 0.5 * 2.0)
