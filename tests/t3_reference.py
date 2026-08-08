"""T3 交叉对账参考实现（P0-5）—— 仅测试基准，非运行代码。

移植自 fribbels/hsr-optimizer：
  src/lib/optimization/engine/damage/damageCalculator.ts
  @ 67b8356812e02f3eef32aa0273f283528b224c60（2026-08-07）
  （CritDamageFunction / BreakDamageFunction 的 wgsl 段，数学公式逐项移植）

约定：fribbels 固定攻方 80 级（defMulti 常数 100 = (200+10×80)/10），
敌人防御 = 10×(敌人等级+20)。与本项目 damage.py 的映射：
  - 本项目 defense_multiplier(80, d) == fb_def_mult(e_lv)  当 d = 10×(e_lv+20)
  - 本项目 (1+stats.dmg_bonus+m.dmg_bonus) == fb 的 (1+BOOST+elementalBoost)
  - 本项目 expected_damage(enemy_broken=...) == fb 的 baseUniversalMulti
"""
from __future__ import annotations

ELEMENT_BREAK_SCALING = {
    "Physical": 2.0, "Fire": 2.0, "Ice": 1.0, "Thunder": 1.0,
    "Wind": 1.5, "Quantum": 0.5, "Imaginary": 0.5,
}

# 击破基础伤害（80 级角色；Lv1~80 = 54~3767，fribbels 固定 80 级常数）
FB_BREAK_BASE = 3767.5533


def fb_def_mult(enemy_level: float, def_pen: float = 0.0) -> float:
    """defMulti = 100 / ((eLv+20) × max(0, 1-defPen) + 100)。"""
    return 100.0 / ((enemy_level + 20.0) * max(0.0, 1.0 - def_pen) + 100.0)


def fb_res_mult(res: float, res_pen: float = 0.0) -> float:
    """resMulti = 1 - (目标抗性 - 抗性穿透)；无负抗减半。"""
    return 1.0 - (res - res_pen)


def fb_crit_mult(cr: float, cd: float) -> float:
    """critMulti = cr×(1+cd) + (1-cr)，cr ≤ 100%。"""
    cr = min(1.0, cr)
    return cr * (1.0 + cd) + (1.0 - cr)


def fb_normal_damage(
    *,
    atk: float,
    atk_scaling: float = 1.0,
    boost: float = 0.0,
    element_boost: float = 0.0,
    cr: float,
    cd: float,
    enemy_level: float,
    res: float,
    def_pen: float = 0.0,
    res_pen: float = 0.0,
    vuln: float = 0.0,
    final_dmg: float = 0.0,
    broken: bool = True,
    true_dmg: float = 0.0,
) -> float:
    """普通伤害（CritDamageFunction）：baseUniversal × def × res × vuln × final
    × dmgBoost × ability × crit × trueDmg。"""
    base_universal = 1.0 if broken else 0.9
    def_m = fb_def_mult(enemy_level, def_pen)
    res_m = fb_res_mult(res, res_pen)
    vuln_m = 1.0 + vuln
    final_m = 1.0 + final_dmg
    dmg_boost = 1.0 + boost + element_boost
    ability = atk_scaling * atk
    crit = fb_crit_mult(cr, cd)
    return base_universal * def_m * res_m * vuln_m * final_m * dmg_boost * ability * crit * (1.0 + true_dmg)


def fb_break_damage(
    *,
    element: str,
    be: float,
    enemy_max_toughness: float,
    enemy_level: float,
    res: float,
    def_pen: float = 0.0,
    res_pen: float = 0.0,
    vuln: float = 0.0,
    final_dmg: float = 0.0,
    hit_boost: float = 0.0,
    true_dmg: float = 0.0,
    special_scaling: float = 1.0,
) -> float:
    """击破伤害（BreakDamageFunction）：baseUniversal(=1.0 击破瞬间) × def × res × vuln
    × final × dmgBoost(hit 级) × breakBase × (1+BE) × trueDmg。"""
    base_universal = 1.0  # 击破瞬间韧性已破
    def_m = fb_def_mult(enemy_level, def_pen)
    res_m = fb_res_mult(res, res_pen)
    vuln_m = 1.0 + vuln
    final_m = 1.0 + final_dmg
    dmg_boost = 1.0 + hit_boost
    break_base = FB_BREAK_BASE * ELEMENT_BREAK_SCALING[element] \
        * (0.5 + enemy_max_toughness / 120.0) * special_scaling
    be_m = 1.0 + be
    return base_universal * def_m * res_m * vuln_m * final_m * dmg_boost * break_base * be_m * (1.0 + true_dmg)
