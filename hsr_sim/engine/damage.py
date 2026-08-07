"""伤害公式 —— 全乘区实现，乘区按名累乘，后续新增乘区只需加参数。

伤害 = 技能倍率 × 攻击力 × 增伤乘区 × 暴击期望 × 防御乘区 × 抗性乘区 × 易伤乘区
     + 真实伤害（追加，不经过防御/抗性）
另含：附加伤害（知更鸟协奏，固定双暴）、击破伤害（等级系数 × 属性倍率 × 击破特攻）。
参考 fribbels/hsr-optimizer 伤害公式与社区 wiki。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from ..model import Stats


# 击破基础伤害（等级系数）：90 级 ≈ 3767.5（社区 wiki 值，待实测核对）
BREAK_LEVEL_COEF = {
    80: 2704.0,
    85: 3195.0,
    90: 3767.5,
}
# 击破属性倍率（物理/火 2.0，风 1.5，冰/雷 1.0，量子/虚数 0.5）
BREAK_ELEMENT_MULT = {
    "Physical": 2.0, "Fire": 2.0, "Wind": 1.5,
    "Ice": 1.0, "Thunder": 1.0, "Quantum": 0.5, "Imaginary": 0.5,
}


@dataclass
class Multipliers:
    """伤害乘区（v1 静态值，后续可改为 buff 时间轴驱动）。"""

    dmg_bonus: float = 0.0          # 增伤（属性+全伤+技能类型，加算）
    vuln: float = 0.0               # 易伤（乘算区）
    def_ignore: float = 0.0         # 无视防御（0.2 = 20%）
    res_pen: float = 0.0            # 抗性穿透
    true_dmg: float = 0.0           # 真实伤害比例（追加已结算伤害的 X%）
    break_effect: float = 0.0       # 击破特攻（击破伤害专用）
    extra_atk_pct: float = 0.0      # 攻击力百分比加成（如知更鸟协奏）
    extra_atk_flat: float = 0.0     # 攻击力固定加成


def defense_multiplier(attacker_level: int, enemy_defense: float, def_ignore: float = 0.0) -> float:
    """防御乘区：200 + 10×攻方等级 / (防御×(1-无视) + 200 + 10×攻方等级)。"""
    eff_def = enemy_defense * (1.0 - def_ignore)
    base = 200.0 + 10.0 * attacker_level
    return base / (eff_def + base)


def resistance_multiplier(resistance: float, res_pen: float = 0.0) -> float:
    """抗性乘区：抗性 ≥ 0 时 1 - res；抗性 < 0 时 1 - res/2（穿透溢出减半收益）。"""
    res = resistance - res_pen
    return 1.0 - res if res >= 0.0 else 1.0 - res / 2.0


def crit_expectation(crit_rate: float, crit_dmg: float) -> float:
    """暴击期望 = 1 + 暴击率(≤100%) × 暴伤。"""
    return 1.0 + min(crit_rate, 1.0) * crit_dmg


def expected_damage(
    mult: float,
    atk: float,
    stats: Stats,
    multipliers: Multipliers,
    enemy_defense: float,
    resistance: float,
    attacker_level: int = 90,
) -> float:
    """单次攻击期望伤害（含真伤追加）。"""
    atk_eff = atk * (1.0 + multipliers.extra_atk_pct) + multipliers.extra_atk_flat
    raw = mult * atk_eff * (1.0 + stats.dmg_bonus + multipliers.dmg_bonus)
    crit = crit_expectation(stats.crit_rate, stats.crit_dmg)
    def_m = defense_multiplier(attacker_level, enemy_defense, multipliers.def_ignore)
    res_m = resistance_multiplier(resistance, multipliers.res_pen)
    vuln_m = 1.0 + multipliers.vuln
    damage = raw * crit * def_m * res_m * vuln_m
    return damage * (1.0 + multipliers.true_dmg)


def flat_damage(
    mult: float,
    atk: float,
    dmg_bonus: float,
    def_m: float,
    res_m: float,
    fixed_crit_rate: float = 1.0,
    fixed_crit_dmg: float = 1.5,
) -> float:
    """固定双暴的附加伤害（知更鸟协奏：暴击率固定 100%、暴伤固定 150%）。"""
    raw = mult * atk * (1.0 + dmg_bonus)
    crit = 1.0 + fixed_crit_rate * fixed_crit_dmg
    return raw * crit * def_m * res_m


def break_damage(level: int, element: str, break_effect: float, enemy_toughness_max: float) -> float:
    """击破伤害：等级系数 × 属性倍率 × (1 + 击破特攻) × 韧性修正（韧性越低伤害越高，v1 取满韧性系数 1.0）。"""
    coef = BREAK_LEVEL_COEF.get(level, BREAK_LEVEL_COEF[90])
    elem = BREAK_ELEMENT_MULT.get(element, 1.0)
    return coef * elem * (1.0 + break_effect)
