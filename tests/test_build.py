"""面板装配器对账测试：词条 → 面板换算与预算审计（验收标准①）。"""
import pytest

from hsr_sim.build import (
    SUBSTAT_BUDGET,
    BuildConfig,
    assemble,
    substat_count,
    validate_config,
)
from hsr_sim.model import Stats


class TestAssemble:
    def test_main_stats_and_substats(self):
        # 基础 1000 攻/100 速/5% 暴击/50% 暴伤 + 光锥模板(582 攻 + 20%) + 主词条 + 副词条
        base = Stats(atk=621.0, speed=105.0, crit_rate=0.05, crit_dmg=0.50)
        cfg = BuildConfig(
            main_stats={"body": "crit_dmg", "feet": "speed",
                        "sphere": "quantum_dmg", "rope": "atk_pct"},
            substats={"speed": 2, "crit_rate": 16, "crit_dmg": 5, "atk_pct": 7},
        )
        s = assemble(base, "Quantum", cfg)
        # 攻击：(621+582)×(1+0.20光锥+0.432绳+7×0.0432) = 1203×1.9344
        assert s.atk == pytest.approx(1203.0 * (1 + 0.20 + 0.432 + 7 * 0.0432))
        # 速度：105 + 25鞋 + 2×2.4
        assert s.speed == pytest.approx(105 + 25 + 2 * 2.4)
        # 暴击：5% + 16×3.24%；暴伤：50% + 64.8%衣 + 5×6.48%
        assert s.crit_rate == pytest.approx(0.05 + 16 * 0.0324)
        assert s.crit_dmg == pytest.approx(0.50 + 0.648 + 5 * 0.0648)
        # 属性伤：球
        assert s.dmg_bonus == pytest.approx(0.388)

    def test_energy_regen_rope(self):
        base = Stats(atk=500.0, speed=100.0)
        cfg = BuildConfig(main_stats={"body": "atk_pct", "feet": "atk_pct",
                                      "sphere": "atk_pct", "rope": "energy_regen"},
                          substats={"energy_regen": 3})
        s = assemble(base, "Ice", cfg)
        assert s.energy_regen == pytest.approx(1.0 + 0.194 + 3 * 0.0324)

    def test_substat_count(self):
        cfg = BuildConfig(substats={"speed": 2, "crit_rate": 16, "crit_dmg": 5, "atk_pct": 7})
        assert substat_count(cfg) == 30.0


class TestBudget:
    def test_budget_constant(self):
        assert SUBSTAT_BUDGET == 30

    def test_validate_bad_main_stat(self):
        cfg = BuildConfig(main_stats={"body": "speed", "feet": "speed",
                                      "sphere": "quantum_dmg", "rope": "atk_pct"})
        errs = validate_config(cfg)
        assert any("body" in e for e in errs)  # 衣不能是速度

    def test_validate_negative_substat(self):
        cfg = BuildConfig(main_stats={"body": "crit_dmg", "feet": "speed",
                                      "sphere": "quantum_dmg", "rope": "atk_pct"},
                          substats={"speed": -1})
        assert validate_config(cfg)  # 返回错误列表


class TestLoadTeamWithBuilds:
    def test_loads_team_v2(self):
        """词条分配形态 → 装配面板（集成测试）。"""
        from pathlib import Path
        from hsr_sim.loader import DATA_DIR, load_substat_counts, load_team
        chars, stats, targets = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
        # 红A：105+25+2×2.4 = 134.8
        assert stats["1015"].speed == pytest.approx(134.8)
        assert stats["1015"].crit_dmg == pytest.approx(0.50 + 0.648 + 5 * 0.0648)
        counts = load_substat_counts(DATA_DIR / "team_reda.json")
        assert counts["1015"] == 30.0
        assert all(c <= 30 for c in counts.values())
