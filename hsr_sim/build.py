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

# 光锥模板（legacy 兜底：未配置光锥 id 时；ETL/Nanoka 提供真实光锥数据）
LIGHT_CONE_TEMPLATE = {"atk_base": 582.0}

# 光锥主属性加成（80 级，5 星：基础攻击 + 无被动；被动由 equipment 数据提供，未接入面板）
LIGHT_CONE_ATK_BASE = {  # id -> 80 级基础攻击（缺失时用模板）
}

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
    light_cone: str = ""               # 光锥 id（equipment 数据；空 = legacy 模板）
    relic_sets: List[str] = field(default_factory=list)  # 套装配置：{id, pieces} 或旧格式 str
    eidolon: int = 0                   # 星魂等级（0-6）
    cid: str = ""                      # 角色 id（星魂效果按角色查 equipment.eidolons）


def substat_count(config: BuildConfig) -> float:
    return sum(config.substats.values())


def resolve_equipment(build: Dict, equipment: Optional[Dict]) -> Dict:
    """解析装备配置 → 生效效果：{stat_bonus: {stat: value}, effects: [exec...]}。

    - stat/stat_conditional：面板类（stat_conditional 的 cond 由模拟器判定，不进面板）
    - 其余类型：机制效果（模拟器执行，src 标注来源）
    relic_sets 格式：{"id": str, "pieces": int}（2 件生效 two_piece，4 件叠加 four_piece）；
    兼容旧格式（str = 4 件）。
    """
    stat_bonus: Dict[str, float] = {}
    effects: List[Dict] = []
    if not equipment:
        return {"stat_bonus": stat_bonus, "effects": effects}
    lc_id = build.get("light_cone", "")
    if lc_id:
        lc = equipment.get("light_cones", {}).get(lc_id) or {}
        for ex in (lc.get("effect") or {}).get("exec", []) or []:
            if ex["type"] == "stat":
                stat_bonus[ex["stat"]] = stat_bonus.get(ex["stat"], 0.0) + ex["value"]
            else:
                effects.append({**ex, "src": f"lc:{lc_id}"})
    for rs_cfg in build.get("relic_sets", []) or []:
        if isinstance(rs_cfg, str):
            rs_cfg = {"id": rs_cfg, "pieces": 4}
        sid = str(rs_cfg.get("id", ""))
        pieces = int(rs_cfg.get("pieces", 4))
        rs = equipment.get("relic_sets", {}).get(sid) or {}
        for key, need in (("two_piece", 2), ("four_piece", 4)):
            if pieces >= need:
                for ex in (rs.get(key) or {}).get("exec", []) or []:
                    if ex["type"] == "stat":
                        stat_bonus[ex["stat"]] = stat_bonus.get(ex["stat"], 0.0) + ex["value"]
                    else:
                        effects.append({**ex, "src": f"rs:{sid}:{key}"})
    # 星魂（0-6 命，逐命收集；等级类 E3/E5 无 exec 自动跳过）
    el = int(build.get("eidolon", 0) or 0)
    if el > 0:
        cid = build.get("cid", "")
        eid = (equipment.get("eidolons") or {}).get(cid)
        if eid:
            for rk in range(1, el + 1):
                rv = eid.get("ranks", {}).get(str(rk)) or {}
                for ex in rv.get("exec", []) or []:
                    effects.append({**ex, "src": f"rank:{rk}"})
    return {"stat_bonus": stat_bonus, "effects": effects}


def assemble(base: Stats, element: str, config: BuildConfig,
             equipment: Optional[Dict] = None) -> Stats:
    """装配最终面板：基础 + 光锥白值 + 主词条 + 副词条。

    光锥被动/套装效果/星魂效果不进入面板（未接入战斗模拟，知识包诚实标注）；
    equipment = load_equipment() 的原始 dict（strip 溯源后）。
    """
    out = base.copy()

    atk_pct_total = 0.0

    # 装备 stat 效果（光锥被动/套装面板类；速度% 乘算单独处理）
    eq = resolve_equipment({"light_cone": config.light_cone,
                            "relic_sets": config.relic_sets,
                            "eidolon": config.eidolon, "cid": config.cid}, equipment)
    sb = eq["stat_bonus"]
    speed_pct = 0.0
    speed_flat = 0.0
    for stat, v in sb.items():
        if stat == "crit_rate":
            out.crit_rate += v
        elif stat == "crit_dmg":
            out.crit_dmg += v
        elif stat == "energy_regen":
            out.energy_regen += v
        elif stat == "speed_pct":
            speed_pct += v
        elif stat == "atk_pct":
            atk_pct_total += v
        else:
            speed_flat += v  # speed 常量（当前无，预留）

    # 光锥（真实数据：80 级基础攻击；legacy 兜底模板）
    atk_flat = LIGHT_CONE_TEMPLATE["atk_base"]
    lc_id = config.light_cone
    if lc_id and equipment:
        lc = equipment.get("light_cones", {}).get(lc_id)
        if lc and lc.get("base_stats"):
            bs = lc["base_stats"]
            if bs.get("atk"):
                atk_flat = bs["atk"]

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
    if speed_pct:
        out.speed = out.speed * (1.0 + speed_pct)
    out.speed += speed_flat
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
