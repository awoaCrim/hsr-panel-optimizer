"""面板装配器 —— 由 基础属性 + 遗器主词条 + 副词条 装配最终面板，并审计词条预算。

这是"面板方案"的正确形态（docs/game-knowledge.md 1.2 schema）：
LLM 输出 main_stats（4 件主词条）+ substats（副词条词条数），程序按标准词条价值
计算最终面板 —— 面板是否"可实现"由词条预算约束（默认 30 有效词条）保证。

标准值（5 星遗器满级）：
- 主词条：暴击率 32.4% / 暴伤 64.8% / 攻击 43.2% / 速度 25 / 充能 19.4% / 击破 64.8% / 属性伤 38.8%
- 副词条（每词条）：攻击 4.32% / 速度 2.4 / 暴击率 3.24% / 暴伤 6.48% / 击破 6.48% / 充能 3.24%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .model import Stats

SUBSTAT_BUDGET = 30  # 有效副词条预算（可配置）

# 光锥模板（v1 简化：5 星光锥固定基础攻击 + 攻击%；ETL 阶段按具体光锥精确化）
LIGHT_CONE_TEMPLATE = {"atk_base": 582.0, "atk_pct": 0.20}

MAIN_STAT_VALUES = {
    "crit_rate": 0.324,
    "crit_dmg": 0.648,
    "atk_pct": 0.432,
    "hp_pct": 0.432,
    "def_pct": 0.54,
    "speed": 25.0,
    "energy_regen": 0.194,
    "break_effect": 0.648,
    "heal_bonus": 0.346,
    "quantum_dmg": 0.388, "physical_dmg": 0.388, "fire_dmg": 0.388,
    "ice_dmg": 0.388, "thunder_dmg": 0.388, "wind_dmg": 0.388, "imaginary_dmg": 0.388,
}

SUBSTAT_VALUE = {
    "atk_pct": 0.0432,
    "speed": 2.4,
    "crit_rate": 0.0324,
    "crit_dmg": 0.0648,
    "break_effect": 0.0648,
    "energy_regen": 0.0324,
}

VALID_MAIN_STATS = {
    "body": ["crit_rate", "crit_dmg", "atk_pct", "hp_pct"],
    "feet": ["speed", "atk_pct"],
    "sphere": ["atk_pct", "quantum_dmg", "physical_dmg", "fire_dmg", "ice_dmg",
               "thunder_dmg", "wind_dmg", "imaginary_dmg"],
    "rope": ["energy_regen", "break_effect", "atk_pct"],
}

SLOT_NAMES = ["body", "feet", "sphere", "rope"]


@dataclass
class BuildConfig:
    """LLM 输出的装备配置。"""

    main_stats: Dict[str, str] = field(default_factory=dict)  # slot -> 主词条类型
    substats: Dict[str, float] = field(default_factory=dict)  # stat -> 词条数
    light_cone: Dict = field(default_factory=dict)            # 光锥（v1 模板）


def substat_count(config: BuildConfig) -> float:
    return sum(config.substats.values())


def assemble(base: Stats, element: str, config: BuildConfig) -> Stats:
    """装配最终面板：基础 + 光锥 + 主词条 + 副词条。攻击%为同乘区加算。"""
    out = base.copy()

    # 光锥（模板：基础攻击 + 攻击%）
    lc = config.light_cone or {}
    atk_flat = lc.get("atk_base", LIGHT_CONE_TEMPLATE["atk_base"])
    atk_pct_total = lc.get("atk_pct", LIGHT_CONE_TEMPLATE["atk_pct"])

    # 主词条
    body = config.main_stats.get("body")
    feet = config.main_stats.get("feet")
    sphere = config.main_stats.get("sphere")
    rope = config.main_stats.get("rope")

    if body == "crit_rate":
        out.crit_rate += MAIN_STAT_VALUES["crit_rate"]
    elif body == "crit_dmg":
        out.crit_dmg += MAIN_STAT_VALUES["crit_dmg"]
    elif body == "atk_pct":
        atk_pct_total += MAIN_STAT_VALUES["atk_pct"]
    if feet == "speed":
        out.speed += MAIN_STAT_VALUES["speed"]
    elif feet == "atk_pct":
        atk_pct_total += MAIN_STAT_VALUES["atk_pct"]
    if sphere in MAIN_STAT_VALUES:
        if sphere == "atk_pct":
            atk_pct_total += MAIN_STAT_VALUES["atk_pct"]
        elif sphere.endswith("_dmg"):
            out.dmg_bonus += MAIN_STAT_VALUES[sphere]
    if rope in MAIN_STAT_VALUES:
        if rope == "energy_regen":
            out.energy_regen += MAIN_STAT_VALUES["energy_regen"]
        elif rope == "break_effect":
            out.break_effect += MAIN_STAT_VALUES["break_effect"]
        elif rope == "atk_pct":
            atk_pct_total += MAIN_STAT_VALUES["atk_pct"]

    # 副词条（先收集攻击%再加算，避免顺序乘法）
    for stat, count in config.substats.items():
        v = SUBSTAT_VALUE.get(stat)
        if v is None:
            continue
        if stat == "atk_pct":
            atk_pct_total += v * count
        elif stat == "speed":
            out.speed += v * count
        elif stat == "crit_rate":
            out.crit_rate += v * count
        elif stat == "crit_dmg":
            out.crit_dmg += v * count
        elif stat == "break_effect":
            out.break_effect += v * count
        elif stat == "energy_regen":
            out.energy_regen += v * count

    out.atk = (base.atk + atk_flat) * (1.0 + atk_pct_total)
    return out


def validate_config(config: BuildConfig) -> List[str]:
    """配置合法性检查：主词条类型是否允许、词条数是否非负。"""
    errors: List[str] = []
    for slot in SLOT_NAMES:
        v = config.main_stats.get(slot)
        if v is None:
            errors.append(f"{slot} 未指定主词条")
        elif v not in VALID_MAIN_STATS[slot]:
            errors.append(f"{slot} 主词条 {v} 不合法（可选 {VALID_MAIN_STATS[slot]}）")
    for stat, count in config.substats.items():
        if count < 0:
            errors.append(f"副词条 {stat} 词条数为负")
    return errors
