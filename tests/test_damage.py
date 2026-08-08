"""伤害公式对账测试：每个用例与手算值核对（T1 单元层，公式定值见 docs/mechanics-spec.md）。

T1 仅证明"实现符合已定值公式"；与游戏实测的对账在 T2b/T3/T4。
"""
import pytest

from hsr_sim.engine.damage import (
    BREAK_BASE_DAMAGE,
    break_damage,
    broken_multiplier,
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
    def test_level80_vs_def1000(self):
        # (200 + 800) / (1000 + 200 + 800) = 1000/2000
        assert defense_multiplier(80, 1000.0) == pytest.approx(1000 / 2000)

    def test_level80_vs_def1100_level90_enemy(self):
        # 80 级攻方 vs 90 级敌人（防御 = 200 + 10×90 = 1100）：1000/2100
        assert defense_multiplier(80, 1100.0) == pytest.approx(1000 / 2100)

    def test_def_ignore(self):
        # 无视防御 20%：有效防御 800 → 1000/1800
        assert defense_multiplier(80, 1000.0, 0.2) == pytest.approx(1000 / 1800)


class TestResistanceMultiplier:
    def test_positive(self):
        assert resistance_multiplier(0.2) == pytest.approx(0.8)

    def test_penetration_overflow_no_half(self):
        # 星铁无负抗减半（原神规则）：抗性 0.2 - 穿透 0.3 = -0.1 → 1 - (-0.1) = 1.1
        assert resistance_multiplier(0.2, 0.3) == pytest.approx(1.1)


class TestBrokenMultiplier:
    def test_unbroken_09_broken_10(self):
        assert broken_multiplier(False) == pytest.approx(0.9)
        assert broken_multiplier(True) == pytest.approx(1.0)


class TestExpectedDamage:
    def test_full_chain_hand_calc(self):
        # mult=1.0 atk=1000 dmg_bonus=0 crit=50%/100% def=800 res=0.1 攻方 80 级，敌人已击破
        # raw = 1000；crit = 1.5；def = 1000/1800；res = 0.9；broken = 1.0
        stats = Stats(atk=1000.0, crit_rate=0.5, crit_dmg=1.0)
        dmg = expected_damage(1.0, 1000.0, stats, __import__("hsr_sim.engine.damage", fromlist=["Multipliers"]).Multipliers(),
                              800.0, 0.1, 80, enemy_broken=True)
        assert dmg == pytest.approx(1000 * 1.5 * (1000 / 1800) * 0.9)

    def test_unbroken_applies_09(self):
        # 同一配置，敌人韧性未破 → 整体 ×0.9
        stats = Stats(atk=1000.0, crit_rate=0.5, crit_dmg=1.0)
        dmg_broken = expected_damage(1.0, 1000.0, stats,
                                     __import__("hsr_sim.engine.damage", fromlist=["Multipliers"]).Multipliers(),
                                     800.0, 0.0, 80, enemy_broken=True)
        dmg_tough = expected_damage(1.0, 1000.0, stats,
                                    __import__("hsr_sim.engine.damage", fromlist=["Multipliers"]).Multipliers(),
                                    800.0, 0.0, 80, enemy_broken=False)
        assert dmg_tough == pytest.approx(dmg_broken * 0.9)

    def test_final_dmg_multiplier(self):
        # 最终增伤 10%：独立乘区
        stats = Stats(atk=1000.0, crit_rate=0.5, crit_dmg=1.0)
        from hsr_sim.engine.damage import Multipliers
        base = expected_damage(1.0, 1000.0, stats, Multipliers(), 800.0, 0.0, 80, enemy_broken=True)
        dmg = expected_damage(1.0, 1000.0, stats, Multipliers(final_dmg=0.10), 800.0, 0.0, 80, enemy_broken=True)
        assert dmg == pytest.approx(base * 1.10)

    def test_true_damage_additive(self):
        stats = Stats(atk=1000.0, crit_rate=0.5, crit_dmg=1.0)
        from hsr_sim.engine.damage import Multipliers
        dmg = expected_damage(1.0, 1000.0, stats, Multipliers(true_dmg=0.10), 800.0, 0.1, 80, enemy_broken=True)
        base = 1000 * 1.5 * (1000 / 1800) * 0.9
        assert dmg == pytest.approx(base * 1.10)


class TestFlatDamage:
    def test_robin_additional(self):
        # 倍率 1.44，攻击 2500，增伤 0.625，防御乘区 1000/2000，抗性 0，敌人已击破
        # 固定双暴：1 + 1.0 × 1.5 = 2.5
        dmg = flat_damage(1.44, 2500.0, 0.625, 1000 / 2000, 1.0, enemy_broken=True)
        assert dmg == pytest.approx(1.44 * 2500 * 1.625 * 2.5 * (1000 / 2000))


class TestBreakDamage:
    def test_level80_quantum(self):
        # 80 级量子击破（BE=0，韧性 480，def/res = 1.0）：
        # 3767.5533 × 0.5 × (0.5 + 480/120)
        assert break_damage("Quantum", 0.0, 480, 1.0, 1.0) == pytest.approx(
            BREAK_BASE_DAMAGE * 0.5 * (0.5 + 480 / 120))

    def test_break_effect_scales(self):
        assert break_damage("Quantum", 1.0, 480, 1.0, 1.0) == pytest.approx(
            BREAK_BASE_DAMAGE * 0.5 * (0.5 + 480 / 120) * 2.0)

    def test_toughness_multiplier_uses_max(self):
        # 韧性乘区基于最大韧性：韧性越高击破伤害越高
        hi = break_damage("Quantum", 0.0, 480, 1.0, 1.0)
        lo = break_damage("Quantum", 0.0, 240, 1.0, 1.0)
        assert hi > lo
        assert hi == pytest.approx(BREAK_BASE_DAMAGE * 0.5 * (0.5 + 480 / 120))
        assert lo == pytest.approx(BREAK_BASE_DAMAGE * 0.5 * (0.5 + 240 / 120))

    def test_def_res_multipliers(self):
        # 击破伤害吃防御/抗性乘区（mechanics-spec 1.5）
        d = break_damage("Quantum", 0.0, 480, 0.5, 0.8)
        assert d == pytest.approx(BREAK_BASE_DAMAGE * 0.5 * (0.5 + 480 / 120) * 0.5 * 0.8)

    def test_vuln_final_true(self):
        d = break_damage("Quantum", 0.0, 480, 1.0, 1.0, vuln=0.2, final_dmg=0.1, true_dmg=0.1)
        assert d == pytest.approx(BREAK_BASE_DAMAGE * 0.5 * (0.5 + 480 / 120) * 1.2 * 1.1 * 1.1)
