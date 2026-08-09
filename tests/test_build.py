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
        # 基础 1000 攻/100 速/5% 暴击/50% 暴伤 + 光锥模板(582 攻白值，被动不入面板) + 主词条 + 副词条
        base = Stats(atk=621.0, speed=105.0, crit_rate=0.05, crit_dmg=0.50)
        cfg = BuildConfig(
            main_stats={"body": "crit_dmg", "feet": "speed",
                        "sphere": "quantum_dmg", "rope": "atk_pct"},
            substats={"speed": 2, "crit_rate": 16, "crit_dmg": 5, "atk_pct": 7},
        )
        s = assemble(base, "Quantum", cfg)
        # 攻击：(621+582)×(1+0.432绳+7×0.0432) = 1203×1.7344（光锥被动不入面板）
        assert s.atk == pytest.approx(1203.0 * (1 + 0.432 + 7 * 0.0432))
        # 速度：105 + 25鞋 + 2×2.4
        assert s.speed == pytest.approx(105 + 25 + 2 * 2.4)
        # 暴击：5% + 16×3.24%；暴伤：50% + 64.8%衣 + 5×6.48%
        assert s.crit_rate == pytest.approx(0.05 + 16 * 0.0324)
        assert s.crit_dmg == pytest.approx(0.50 + 0.648 + 5 * 0.0648)
        # 属性伤：球
        assert s.dmg_bonus == pytest.approx(0.388)

    def test_real_light_cone_white_stats(self):
        """真实光锥数据（Nanoka/equipment）：80 级白值进面板，被动效果不进。"""
        from hsr_sim.data.loader import load_equipment
        eq = load_equipment()
        lc = eq["light_cones"]["23001"]
        assert lc["name"] == "于夜色中"
        # 80 级总值验证（列表 atk=582 与 promotions 计算一致）
        assert lc["base_stats"]["atk"] == pytest.approx(582.12, rel=1e-6)
        assert lc["effect"]["name"] == "花与蝶"
        assert len(lc["effect"]["level_1_params"]) == 5
        base = Stats(atk=621.0, speed=105.0, crit_rate=0.05, crit_dmg=0.50)
        cfg = BuildConfig(
            light_cone="23001",
            main_stats={"body": "crit_dmg", "feet": "speed",
                        "sphere": "quantum_dmg", "rope": "atk_pct"},
            substats={"atk_pct": 7},
        )
        s = assemble(base, "Quantum", cfg, eq)
        assert s.atk == pytest.approx((621.0 + 582.12) * (1 + 0.432 + 7 * 0.0432))

    def test_unknown_light_cone_falls_back(self):
        """未知光锥 id / 无 equipment 数据：回退模板（不崩）。"""
        base = Stats(atk=621.0, speed=105.0)
        cfg = BuildConfig(light_cone="99999",
                          main_stats={"body": "atk_pct", "feet": "atk_pct",
                                      "sphere": "atk_pct", "rope": "atk_pct"})
        s = assemble(base, "Quantum", cfg, {"light_cones": {}})
        assert s.atk == pytest.approx((621.0 + 582.0) * (1 + 4 * 0.432))

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
        # 红A：快枪手4（速度+6%）：(105+25+2×2.4)×1.06 = 142.888
        assert stats["1015"].speed == pytest.approx(142.888)
        assert stats["1015"].crit_dmg == pytest.approx(0.50 + 16 * 0.0648)   # 高暴伤词条 16
        counts = load_substat_counts(DATA_DIR / "team_reda.json")
        assert counts["1015"] == 30.0
        assert all(c <= 30 for c in counts.values())
