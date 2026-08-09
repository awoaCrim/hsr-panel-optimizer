"""伤害公式 —— 全乘区实现。定值依据：docs/mechanics-spec.md（P0-2，fribbels/hsr-optimizer
damageCalculator.ts @67b8356 + HoYoLAB 机制帖）。

普通伤害 = Broken乘区 × 技能倍率 × 攻击力 × 增伤乘区 × 暴击期望 × 防御乘区 × 抗性乘区 × 易伤乘区 × 最终增伤
          + 真实伤害（追加乘区）
另含：附加伤害（知更鸟协奏，固定双暴）、击破伤害（80 级基础 3767.5533 × 属性倍率 × 韧性乘区 × …）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..model import Stats

# 击破基础伤害：3767.5533 = 角色 80 级值（官方 Lv1~80 = 54~3767；fribbels 固定 80 级常数）。
# 星铁角色等级上限 80 —— 不存在"90 级击破系数"（旧 BREAK_LEVEL_COEF 已废）。
BREAK_BASE_DAMAGE = 3767.5533
# 击破属性倍率（物理/火 2.0，风 1.5，冰/雷 1.0，量子/虚数 0.5）—— 与 fribbels ElementToBreakScaling 一致
BREAK_ELEMENT_MULT = {
    "Physical": 2.0, "Fire": 2.0, "Wind": 1.5,
    "Ice": 1.0, "Thunder": 1.0, "Quantum": 0.5, "Imaginary": 0.5,
}
# 攻击者等级默认值：星铁角色等级上限（mechanics-spec 1.2；敌人等级独立）
DEFAULT_ATTACKER_LEVEL = 80
# 韧性未破时受到的伤害减免（官方：未破韧敌人受伤 ×0.9，击破后 ×1.0；mechanics-spec 1.1）
BROKEN_MULT = 0.9


@dataclass
class Multipliers:
    """伤害乘区（buff 时间轴驱动为 P0-3 目标，当前由模拟器逐时刻计算）。"""

    dmg_bonus: float = 0.0          # 增伤（属性+全伤+技能类型，加算）
    vuln: float = 0.0               # 易伤（乘算区）
    def_ignore: float = 0.0         # 无视防御（0.2 = 20%）
    res_pen: float = 0.0            # 抗性穿透
    true_dmg: float = 0.0           # 真实伤害比例（追加已结算伤害的 X%）
    final_dmg: float = 0.0          # 最终增伤（独立乘区，mechanics-spec 1.1）
    break_effect: float = 0.0       # 击破特攻（击破伤害专用）
    extra_atk_pct: float = 0.0      # 攻击力百分比加成（如知更鸟协奏）
    extra_atk_flat: float = 0.0     # 攻击力固定加成


def defense_multiplier(attacker_level: int, enemy_defense: float, def_ignore: float = 0.0) -> float:
    """防御乘区：200 + 10×攻方等级 / (防御×(1-无视) + 200 + 10×攻方等级)。

    攻方 80 级、敌防 1100（90 级敌人 = 200+10×90）时 = 1000/2100。
    fribbels 形式 100/((eLv+20)(1-defPen)+100) 与之等价（mechanics-spec 1.2）。
    """
    eff_def = enemy_defense * (1.0 - def_ignore)
    base = 200.0 + 10.0 * attacker_level
    return base / (eff_def + base)


def resistance_multiplier(resistance: float, res_pen: float = 0.0) -> float:
    """抗性乘区：1 - (目标抗性 - 抗性穿透)。

    星铁无"负抗收益减半"（那是原神规则）；穿透溢出直接加收益（mechanics-spec 1.3）。
    """
    return 1.0 - (resistance - res_pen)


def crit_expectation(crit_rate: float, crit_dmg: float) -> float:
    """暴击期望 = 1 + 暴击率(≤100%) × 暴伤。"""
    return 1.0 + min(crit_rate, 1.0) * crit_dmg


def noncrit_damage(
    mult: float,
    atk: float,
    stats: Stats,
    multipliers: Multipliers,
    enemy_defense: float,
    resistance: float,
    attacker_level: int = DEFAULT_ATTACKER_LEVEL,
    enemy_broken: bool = False,
) -> float:
    """非暴击基础伤害（段级判定的基础值；暴击项按段 roll）。"""
    import dataclasses
    s = dataclasses.replace(stats, crit_rate=0.0)   # crit_expectation = 1
    return expected_damage(mult, atk, s, multipliers, enemy_defense, resistance,
                           attacker_level, enemy_broken)


def broken_multiplier(enemy_broken: bool) -> float:
    """韧性未破 ×0.9，击破后 ×1.0（mechanics-spec 1.1）。"""
    return 1.0 if enemy_broken else BROKEN_MULT


def expected_damage(
    mult: float,
    atk: float,
    stats: Stats,
    multipliers: Multipliers,
    enemy_defense: float,
    resistance: float,
    attacker_level: int = DEFAULT_ATTACKER_LEVEL,
    enemy_broken: bool = False,
) -> float:
    """单次攻击期望伤害（含真伤追加乘区）。"""
    atk_eff = atk * (1.0 + multipliers.extra_atk_pct) + multipliers.extra_atk_flat
    raw = mult * atk_eff * (1.0 + stats.dmg_bonus + multipliers.dmg_bonus)
    crit = crit_expectation(stats.crit_rate, stats.crit_dmg)
    def_m = defense_multiplier(attacker_level, enemy_defense, multipliers.def_ignore)
    res_m = resistance_multiplier(resistance, multipliers.res_pen)
    vuln_m = 1.0 + multipliers.vuln
    damage = broken_multiplier(enemy_broken) * raw * crit * def_m * res_m * vuln_m
    return damage * (1.0 + multipliers.final_dmg) * (1.0 + multipliers.true_dmg)


def flat_damage(
    mult: float,
    atk: float,
    dmg_bonus: float,
    def_m: float,
    res_m: float,
    fixed_crit_rate: float = 1.0,
    fixed_crit_dmg: float = 1.5,
    final_dmg: float = 0.0,
    enemy_broken: bool = False,
) -> float:
    """固定双暴的附加伤害（知更鸟协奏：暴击率固定 100%、暴伤固定 150%）。"""
    raw = mult * atk * (1.0 + dmg_bonus)
    crit = 1.0 + fixed_crit_rate * fixed_crit_dmg
    return broken_multiplier(enemy_broken) * raw * crit * def_m * res_m * (1.0 + final_dmg)


def break_damage(
    element: str,
    break_effect: float,
    enemy_toughness_max: float,
    def_m: float,
    res_m: float,
    vuln: float = 0.0,
    final_dmg: float = 0.0,
    true_dmg: float = 0.0,
    dmg_bonus: float = 0.0,
) -> float:
    """击破伤害（mechanics-spec 1.5，fribbels BreakDamageFunction）：

    = 3767.5533(80级) × 属性倍率 × (0.5 + 最大韧性/120) × (1+击破特攻)
      × 防御乘区 × 抗性乘区 × (1+易伤) × (1+最终增伤) × (1+hit级击破增伤) × (1+真伤)

    击破瞬间韧性已破 → 不乘 0.9。dmg_bonus 仅 hit 级（如击破特攻光锥附带的击破伤害提高），
    行动级增伤（属性/全伤）不适用。
    """
    elem = BREAK_ELEMENT_MULT.get(element, 1.0)
    base = BREAK_BASE_DAMAGE * elem * (0.5 + enemy_toughness_max / 120.0) * (1.0 + break_effect)
    return base * def_m * res_m * (1.0 + vuln) * (1.0 + final_dmg) * (1.0 + dmg_bonus) * (1.0 + true_dmg)
