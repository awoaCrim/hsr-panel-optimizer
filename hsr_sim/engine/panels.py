"""战斗内面板投影：把模拟器当前状态压成前端/LLM 共用的真值视图。

该模块只做投影，不拥有战斗状态。基础面板、当前生效面板、增益/减益列表均从
Simulator 读取，因此 act、buff 到期、undo、restart 后会自然同步。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .buffs import Buff


STAT_LABELS = {
    "hp": "生命",
    "atk": "攻击力",
    "defense": "防御力",
    "speed": "速度",
    "crit_rate": "暴击率",
    "crit_dmg": "暴击伤害",
    "break_effect": "击破特攻",
    "energy_regen": "能量恢复效率",
    "dmg_bonus": "伤害提高",
    "heal_bonus": "治疗量加成",
    "atk_pct": "攻击力提高",
    "atk_flat": "攻击力固定提高",
    "speed_pct": "速度提高",
    "speed_flat": "速度提高",
    "def_pct": "防御力提高",
    "def_flat": "防御力固定提高",
    "true_dmg": "真实伤害",
    "concert_atk": "协奏攻击提高",
    "res_pen": "抗性穿透",
    "mems_support": "声援真伤",
    "equip_next_ally_dmg": "下一个队友伤害提高",
}


def _round_stats(stats) -> Dict[str, float]:
    return {key: round(float(value), 6) for key, value in asdict(stats).items()}


def _source_name(sim, source: str) -> str:
    if source in sim.chars:
        return sim.chars[source].name
    if source == "MEM":
        return "迷迷"
    return source


def _buff_kind(buff: Buff) -> str:
    if buff.stat.startswith(("enemy_", "debuff:")) or buff.value < 0.0:
        return "debuff"
    return "buff"


def _buff_label(buff: Buff) -> str:
    if buff.stat.startswith("enemy_res_pen:"):
        return f"{buff.stat.split(':', 1)[1]}抗性降低"
    if buff.stat.startswith("enemy_weakness_add:"):
        return f"添加{buff.stat.split(':', 1)[1]}弱点"
    return STAT_LABELS.get(buff.stat, buff.stat)


def _buff_projection(sim, buff: Buff) -> Dict[str, Any]:
    return {
        "kind": _buff_kind(buff),
        "stat": buff.stat,
        "label": _buff_label(buff),
        "value": round(buff.value, 6),
        "source": buff.source,
        "source_name": _source_name(sim, buff.source),
        "target": buff.target or "all",
        "remaining": buff.duration,
        "permanent": buff.duration == 0,
        "cap": round(buff.cap, 6),
    }


def _conditional_effects(sim, cid: str) -> List[Dict[str, Any]]:
    """返回不存于 BuffManager、但当前会改变面板的条件效果。"""
    effects: List[Dict[str, Any]] = []
    for ex in sim._equip_effects(cid):
        if ex["type"] == "sp_cap_ge_atk" and sim.sp_max >= ex["sp_cap_ge"]:
            effects.append({
                "kind": "buff", "stat": "atk_pct", "label": "攻击力提高",
                "value": round(ex["value"], 6), "source": ex.get("src", "equipment"),
                "source_name": ex.get("src", "装备条件"), "target": cid,
                "remaining": 0, "permanent": True, "cap": 0.0,
            })
        elif ex["type"] == "stat_conditional" and ex.get("cond") == "memosprite_present" \
                and sim.memosprite is not None and sim.memosprite.get("alive") \
                and ex["stat"] == "crit_dmg":
            effects.append({
                "kind": "buff", "stat": "crit_dmg", "label": "暴击伤害",
                "value": round(ex["value"], 6), "source": ex.get("src", "equipment"),
                "source_name": ex.get("src", "装备条件"), "target": cid,
                "remaining": 0, "permanent": True, "cap": 0.0,
            })
    return effects


def _team_derived_effects(sim, cid: str) -> List[Dict[str, Any]]:
    effects: List[Dict[str, Any]] = []
    talent_bonus = 0.03 * min(sim.sp_spent_count, 3)
    if talent_bonus:
        effects.append({
            "kind": "buff", "stat": "dmg_bonus", "label": "伤害提高",
            "value": round(talent_bonus, 6), "source": "team_talent:sparkle",
            "source_name": "花火天赋层数", "target": cid,
            "remaining": 0, "permanent": True, "cap": 0.09,
        })
    for owner, ch in sim.chars.items():
        for ex in ch.equipment_effects:
            if ex["type"] == "mem_team_dmg":
                effects.append({
                    "kind": "buff", "stat": "dmg_bonus", "label": "伤害提高",
                    "value": round(ex["value"], 6), "source": owner,
                    "source_name": ch.name, "target": cid,
                    "remaining": 0, "permanent": True, "cap": 0.0,
                })
                break
    if sim.concert_rounds > 0:
        for owner, ch in sim.chars.items():
            for ex in ch.equipment_effects:
                if ex["type"] == "concert_res_pen":
                    effects.append({
                        "kind": "buff", "stat": "res_pen", "label": "全属性抗性穿透",
                        "value": round(ex["value"], 6), "source": owner,
                        "source_name": ch.name, "target": cid,
                        "remaining": sim.concert_rounds, "permanent": False, "cap": 0.0,
                    })
    return effects


def character_panel(sim, cid: str) -> Dict[str, Any]:
    base = sim.stats[cid]
    effective = sim._effective_stats(cid)
    active = [
        _buff_projection(sim, buff)
        for buff in sim.buffs.for_target(cid)
        if not buff.stat.startswith("enemy_")
        and not (buff.stat == "equip_next_ally_dmg" and buff.source == cid)
    ]
    queue_speed = sim.queue.get_speed(cid) or effective.speed
    active.extend(_conditional_effects(sim, cid))
    active.extend(_team_derived_effects(sim, cid))
    for buff in sim.buffs._buffs:
        if buff.stat == "equip_next_ally_dmg" and buff.source != cid:
            projected = _buff_projection(sim, buff)
            projected["target"] = cid
            active.append(projected)
    effective_dict = _round_stats(effective)
    # 该效果仍由伤害条件乘区结算；面板显式显示给当前可受益角色，避免 LLM 漏读。
    effective_dict["dmg_bonus"] = round(
        effective_dict["dmg_bonus"] + sum(
            buff.value for buff in sim.buffs._buffs
            if buff.stat == "equip_next_ally_dmg" and buff.source != cid
        ), 6)
    return {
        "id": cid,
        "name": sim.chars[cid].name,
        "base": _round_stats(base),
        "effective": effective_dict,
        "speed": round(queue_speed, 6),
        "hp": round(sim.char_hp[cid], 1),
        "hp_max": round(sim.char_hp_max[cid], 1),
        "energy": round(sim.energy[cid], 3),
        "energy_cost": sim.chars[cid].skills["ult"].energy_cost,
        "alive": sim.char_hp[cid] > 0.0,
        "buffs": [x for x in active if x["kind"] == "buff"],
        "debuffs": [x for x in active if x["kind"] == "debuff"],
    }


def enemy_effects(sim, eid: str) -> Dict[str, List[Dict[str, Any]]]:
    active = [
        _buff_projection(sim, buff)
        for buff in sim.buffs.for_target(eid)
        if buff.target == eid or buff.stat.startswith("enemy_")
    ]
    return {
        "buffs": [x for x in active if x["kind"] == "buff"],
        "debuffs": [x for x in active if x["kind"] == "debuff"],
    }


def battle_panels(sim) -> Dict[str, Any]:
    return {
        "characters": {cid: character_panel(sim, cid) for cid in sim.chars},
        "enemies": {eid: enemy_effects(sim, eid) for eid in sim.enemies},
    }
