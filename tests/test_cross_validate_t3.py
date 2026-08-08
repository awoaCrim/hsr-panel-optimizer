"""T3 交叉对账（P0-5）—— 与 fribbels 参考实现逐项网格对比。

验收（ADR-0006 8 表）：首次差异定位到具体机制与字段。
网格覆盖全部乘区组合（普通伤害 23040 组、击破 9216 组）；
任何一组不一致 → 报出输入与差异，定位到字段。

参考实现：tests/t3_reference.py（移植自 fribbels damageCalculator.ts @67b8356）。
"""
import itertools

import pytest

from hsr_sim.engine.damage import (
    Multipliers,
    break_damage,
    crit_expectation,
    defense_multiplier,
    expected_damage,
    resistance_multiplier,
)
from hsr_sim.model import Stats
from t3_reference import (
    fb_break_damage,
    fb_crit_mult,
    fb_def_mult,
    fb_normal_damage,
    fb_res_mult,
)

ATK = 2800.0
CR_CD = [(0.5, 1.0), (0.75, 1.5), (1.0, 2.0), (1.2, 2.5)]          # 含超 100% 暴击率
BOOSTS = [(0.0, 0.0), (0.5, 0.388), (1.2, 0.0)]                     # (action级, 属性级)
DEF_PENS = [0.0, 0.2, 0.5]
RES_PENS = [0.0, 0.3, 0.6]
RES = [-0.4, 0.0, 0.2, 0.4]
VULNS = [0.0, 0.3]
FINALS = [0.0, 0.2]
TRUE_DMGS = [0.0, 0.1]
BROKENS = [False, True]
ENEMY_LEVELS = [40, 60, 80, 90, 100]


class TestPrimitiveEquivalence:
    def test_def_mult(self):
        """防御乘区：本项目 defense_multiplier(80, 10×(eLv+20)) == fribbels defMulti。"""
        for e_lv in range(40, 101, 5):
            d = 10.0 * (e_lv + 20)
            for pen in DEF_PENS:
                assert defense_multiplier(80, d, pen) == pytest.approx(
                    fb_def_mult(e_lv, pen), rel=1e-9)

    def test_res_mult(self):
        for res, pen in itertools.product(RES, RES_PENS):
            assert resistance_multiplier(res, pen) == pytest.approx(
                fb_res_mult(res, pen), rel=1e-9)

    def test_crit_mult(self):
        for cr, cd in CR_CD:
            assert crit_expectation(cr, cd) == pytest.approx(
                fb_crit_mult(cr, cd), rel=1e-9)


class TestNormalDamageGrid:
    def test_grid_matches_fribbels(self):
        """普通伤害全乘区网格：23040 组输入逐组对比。"""
        for (cr, cd), (boost, elem_boost), def_pen, res_pen, res, vuln, final, true_dmg, broken, e_lv in \
                itertools.product(CR_CD, BOOSTS, DEF_PENS, RES_PENS, RES, VULNS, FINALS, TRUE_DMGS, BROKENS, ENEMY_LEVELS):
            stats = Stats(atk=ATK, crit_rate=cr, crit_dmg=cd, dmg_bonus=boost)
            m = Multipliers(dmg_bonus=elem_boost, vuln=vuln, def_ignore=def_pen,
                            res_pen=res_pen, true_dmg=true_dmg, final_dmg=final)
            ours = expected_damage(
                1.0, ATK, stats, m,
                enemy_defense=10.0 * (e_lv + 20), resistance=res,
                attacker_level=80, enemy_broken=broken,
            )
            fb = fb_normal_damage(
                atk=ATK, atk_scaling=1.0, boost=boost, element_boost=elem_boost,
                cr=cr, cd=cd, enemy_level=e_lv, res=res,
                def_pen=def_pen, res_pen=res_pen, vuln=vuln,
                final_dmg=final, broken=broken, true_dmg=true_dmg,
            )
            assert ours == pytest.approx(fb, rel=1e-9), (
                f"普通伤害不一致：cr={cr} cd={cd} boost={boost} elem_boost={elem_boost} "
                f"def_pen={def_pen} res_pen={res_pen} res={res} vuln={vuln} final={final} "
                f"true={true_dmg} broken={broken} e_lv={e_lv}：ours={ours} fb={fb}")


class TestBreakDamageGrid:
    def test_grid_matches_fribbels(self):
        """击破伤害全乘区网格：9216 组输入逐组对比。"""
        elements = ["Quantum", "Physical", "Wind", "Ice"]
        bes = [0.0, 0.5, 2.0]
        toughnesses = [30.0, 120.0, 480.0, 1000.0]
        hit_boosts = [0.0, 0.3]
        for element, be, tough, def_pen, res_pen, res, vuln, final, true_dmg, hit_boost, e_lv in \
                itertools.product(elements, bes, toughnesses, DEF_PENS, RES_PENS, RES, VULNS, FINALS, TRUE_DMGS, hit_boosts, ENEMY_LEVELS):
            def_m = defense_multiplier(80, 10.0 * (e_lv + 20), def_pen)
            res_m = resistance_multiplier(res, res_pen)
            ours = break_damage(element, be, tough, def_m, res_m,
                                vuln=vuln, final_dmg=final, true_dmg=true_dmg, dmg_bonus=hit_boost)
            fb = fb_break_damage(
                element=element, be=be, enemy_max_toughness=tough,
                enemy_level=e_lv, res=res, def_pen=def_pen, res_pen=res_pen,
                vuln=vuln, final_dmg=final, hit_boost=hit_boost, true_dmg=true_dmg,
            )
            assert ours == pytest.approx(fb, rel=1e-9), (
                f"击破伤害不一致：element={element} be={be} tough={tough} def_pen={def_pen} "
                f"res_pen={res_pen} res={res} vuln={vuln} final={final} true={true_dmg} "
                f"hit_boost={hit_boost} e_lv={e_lv}：ours={ours} fb={fb}")
