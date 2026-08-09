"""extract —— raw 原始数据 → data/normalized/（带双维溯源，ADR-0006 L1）。

首批范围（P0-1）：4 角色（1015/1306/1309/8007）基础面板与技能数值、
敌人模板（D 标记）、HardLevelGroup 等级系数。

溯源策略：
- 基础面板：StarRailRes promotions（L80 = base + step×79），与 v1.5 手填一致 → cross_checked
- 技能倍率：StarRailRes character_skills L10（params[9]），ADR-0003 已与 biligame 交叉验证 → cross_checked
- 削韧：AvatarSkillConfig.StanceDamageDisplay（显式字段）→ A/mapped
- SP/能量/延时/拉条：解包字段语义未锁定（SPMultipleRatio/BPNeed/DelayRatio/回能参数位
  留待 P0-2 Mechanics Spec），维持 ADR-0003 wiki 核对值 → C/cross_checked
- 红A（1015）：AvatarSkillConfig 无联动数据（0 条）→ 全部 wiki + override 标记
- 敌人：v1 模板值 → D（P1 由 StageConfig/MonsterConfig 替换）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT / "data" / "raw"
NORM_DIR = ROOT / "data" / "normalized"
LEGACY_DIR = ROOT / "data" / "characters"

CHARS = ["1015", "1306", "1309", "8007"]
TYPE_TEXT_TO_SLOT = {"普攻": "basic", "战技": "skill", "终结技": "ult", "天赋": "talent"}
L80_LEVEL = 80  # 星铁角色等级上限（评审修正：攻击者等级 = 80，非 90）


def load_raw() -> Dict:
    """按 VERSIONS.json 定位 raw 文件并加载。"""
    versions = json.loads((RAW_DIR / "VERSIONS.json").read_text(encoding="utf-8"))
    out: Dict = {}
    for name, cfg in versions.items():
        tag = f"{name}@{cfg['sha']}"
        base = RAW_DIR / tag
        if not base.exists():
            raise FileNotFoundError(f"缺 {base}（先运行 python scripts/etl/fetch.py）")
        for f in base.rglob("*.json"):
            rel = f.relative_to(base).as_posix()
            out[f"{name}/{rel}"] = json.loads(f.read_text(encoding="utf-8"))
        out[f"{name}/VERSIONS"] = cfg
    return out


def prov(source: str, trust: str, version: str, validation: str,
         field: str = "", override: bool = False, note: str = "") -> Dict:
    d = {
        "source_trust": trust, "validation": validation,
        "source": source, "version": version, "field": field,
        "override": override, "note": note,
    }
    return {k: v for k, v in d.items() if v}


def wrapper(value, p: Dict) -> Dict:
    return {"value": value, **p}


def sr_ver(raw: Dict) -> str:
    return f"srr@{raw['StarRailRes/VERSIONS']['sha'][:7]}"


def tb_ver(raw: Dict) -> str:
    return f"tbgd@{raw['TurnBasedGameData/VERSIONS']['sha'][:7]}"


# ---------------- 角色基础面板 ----------------

def build_characters(raw: Dict) -> Dict:
    chars = raw["StarRailRes/index_min/cn/characters.json"]
    promos = raw["StarRailRes/index_min/cn/character_promotions.json"]
    sr_v = sr_ver(raw)
    out: Dict = {}
    for cid in CHARS:
        c = chars[cid]
        p = promos[cid]
        last = p["values"][-1]
        stats = {}
        for stat, key in [("hp", "hp"), ("atk", "atk"), ("defense", "def"),
                          ("speed", "spd"), ("crit_rate", "crit_rate"), ("crit_dmg", "crit_dmg")]:
            v = round(last[key]["base"] + last[key]["step"] * (L80_LEVEL - 1), 4)
            stats[stat] = wrapper(v, prov(
                "starrailres", "B", sr_v, "mapped",
                field=f"character_promotions.{cid}.values[6].{key} = base+step*{L80_LEVEL - 1}",
            ))
        # 非成长默认值（恒等）：break_effect=0 / energy_regen=1
        stats["break_effect"] = wrapper(0.0, prov(
            "handfill", "D", "", "raw", field="(default)",
            note="非成长属性默认值 0，无上游字段",
        ))
        stats["energy_regen"] = wrapper(1.0, prov(
            "handfill", "D", "", "raw", field="(default)",
            note="非成长属性默认值 1，无上游字段",
        ))
        out[cid] = {
            "id": cid,
            "name": c["name"],
            "element": c["element"],
            "path": c["path"],
            "rarity": c["rarity"],
            "_source": prov("starrailres", "B", sr_v, "mapped",
                            field=f"characters.{cid}"),
            "max_energy": wrapper(float(c["max_sp"]), prov(
                "starrailres", "B", sr_v, "cross_checked",
                field=f"characters.{cid}.max_sp",
                note="与 v1.5 手填/ADR-0003 wiki 核对一致",
            )),
            "base_stats": stats,
            # 角色级机制（技能级钩子在 skills.json 的 mechanic；wiki 核对，溯源 C）
            "talent_extra": _char_talent_extra(cid, raw),
        }
    return out


def _char_talent_extra(cid: str, raw: Dict) -> Dict:
    """角色级天赋钩子（followup/summon/sp_cap/memosprite）。"""
    legacy = json.loads((LEGACY_DIR / f"{cid}.json").read_text(encoding="utf-8"))
    te = legacy.get("talent_extra", {})
    # skill_effects 已并入 skills.json 的 mechanic，这里只保留角色级字段
    te = {k: v for k, v in te.items() if k != "skill_effects"}
    # 忆灵数值溯源（docs/research/memory-trailblazer-mem.md 定值）：
    # speed/HP 继承 = 解包（AvatarSkillConfig 800704 params）；倍率 = StarRailRes 忆灵技 params（1800701）+ HoneyHunter/fribbels 交叉；
    # 充能/声援 = 米游社/游民星空实测帖（C）；等级基准（0 命忆灵技 L6）待实测
    if "memosprite" in te:
        m = te["memosprite"]
        tb_v = tb_ver(raw)
        sr_v = sr_ver(raw)
        te["memosprite"] = {}
        for k, v in m.items():
            if k == "note":
                te["memosprite"][k] = v
            elif k in ("speed", "hp_inherit", "hp_flat"):
                te["memosprite"][k] = wrapper(v, prov(
                    "datamine", "A", tb_v, "mapped",
                    field=f"AvatarSkillConfig[800704@L10].ParamList",
                    note="research 定值：与实测帖一致；等级基准待实测",
                ))
            elif k in ("basic_hits", "basic_mult", "basic_aoe_mult"):
                te["memosprite"][k] = wrapper(v, prov(
                    "starrailres", "B", sr_v, "cross_checked",
                    field=f"character_skills.1800701.params[6]",
                    note="L6 基准（fribbels 同款）；HoneyHunter 等级表交叉一致；等级基准待实测",
                ))
            else:
                te["memosprite"][k] = wrapper(v, prov(
                    "community-guide", "C", "miyoushe-2026-01", "cross_checked",
                    note="米游社/游民星空实测帖多源一致；等级基准待实测",
                ))
        # 忆灵技等级表（星魂 E5 忆灵技+1 用）：1800701 普攻多段/全体、1800707 真伤
        srr_skills = raw["StarRailRes/index_min/cn/character_skills.json"]
        m1800701 = srr_skills.get("1800701") or {}
        m1800707 = srr_skills.get("1800707") or {}
        if "memosprite" in te and m1800701.get("params"):
            ps = m1800701["params"]
            te["memosprite"]["basic_mult_levels"] = wrapper(
                [p[0] for p in ps], prov("starrailres", "B", sr_v, "mapped",
                                          field="character_skills.1800701.params[*][0]（L1-L10）",
                                          note="星魂 E5 忆灵技+1 用"))
            if ps and len(ps[0]) > 2:
                te["memosprite"]["basic_aoe_mult_levels"] = wrapper(
                    [p[2] for p in ps], prov("starrailres", "B", sr_v, "mapped",
                                              field="character_skills.1800701.params[*][2]（L1-L10）",
                                              note="星魂 E5 忆灵技+1 用"))
        if "memosprite" in te and m1800707.get("params"):
            te["memosprite"]["support_true_dmg_levels"] = wrapper(
                [p[0] for p in m1800707["params"]],
                prov("starrailres", "B", sr_v, "mapped",
                     field="character_skills.1800707.params[*][0]（L1-L10）",
                     note="星魂 E5 忆灵技+1 用"))
    return te


# ---------------- 技能数值 ----------------

def _asc_entry(raw: Dict, sid: int, level: int = 10) -> Dict:
    asc = raw["TurnBasedGameData/ExcelOutput/AvatarSkillConfig.json"]
    for e in asc:
        if e.get("SkillID") == sid and e.get("Level") == level:
            return e
    return {}


def build_skills(raw: Dict, characters: Dict) -> Dict:
    srr_skills = raw["StarRailRes/index_min/cn/character_skills.json"]
    chars = raw["StarRailRes/index_min/cn/characters.json"]
    sr_v = sr_ver(raw)
    tb_v = tb_ver(raw)
    out: Dict = {}
    for cid in CHARS:
        # StarRailRes 技能 ID → slot
        slot_of: Dict[str, str] = {}
        for sid in chars[cid]["skills"]:
            s = srr_skills.get(str(sid))
            if s is None:
                continue
            slot = TYPE_TEXT_TO_SLOT.get(s.get("type_text", ""))
            if slot and slot not in slot_of.values():
                slot_of[str(sid)] = slot
        slots: Dict[str, Dict] = {}
        for sid, slot in slot_of.items():
            s = srr_skills[str(sid)]
            # L10 倍率（params 按等级排列，L10 = params[9]）
            params = s.get("params", [])
            l10 = params[9] if len(params) >= 10 else (params[-1] if params else [])
            asc = _asc_entry(raw, int(sid))
            # 削韧（显式字段，语义明确）
            toughness = asc.get("StanceDamageDisplay")
            slots[slot] = {
                "_source": prov("biligame", "C", "biligame-2026-08", "cross_checked",
                                note=f"ADR-0003 wiki 核对 L10（上游 SkillID {sid}）"),
                "_upstream_ids": [int(sid)],
                "_note": s.get("name", ""),
            }
            # 倍率：仅 basic 槽位 params[0] 按通用约定提取（已与 wiki 交叉验证）；
            # 其余槽位参数位语义因角色而异（如花火战技 [0]=0.24 是暴伤而非倍率），
            # 不猜测——保留 wiki 值，上游参数整体记入备注，P0-2 Mechanics Spec 锁。
            if slot == "basic" and isinstance(l10, list) and l10:
                slots[slot]["mult"] = wrapper(l10[0], prov(
                    "starrailres", "B", sr_v, "mapped",
                    field=f"character_skills.{sid}.params[9][0]",
                    note="普攻倍率参数位 [0] 为通用约定，P0-2 交叉验证后提升为 cross_checked",
                ))
            # 等级表（等级类星魂 E3/E5 用）：params[0] 每级（L1 起）；应用时校验 L10 与
            # 当前 mult 一致才生效（防参数位错位；等级类星魂对无表技能静默跳过）
            if params and all(isinstance(p, list) and p for p in params):
                slots[slot]["mult_levels"] = wrapper([p[0] for p in params], prov(
                    "starrailres", "B", sr_v, "mapped",
                    field=f"character_skills.{sid}.params[*][0]（等级表）",
                    note="等级类星魂用；应用时校验 L10 与 mult 一致",
                ))
            elif l10:
                slots[slot]["_upstream_params"] = l10
            if toughness is not None:
                slots[slot]["toughness"] = wrapper(float(toughness), prov(
                    "datamine", "A", tb_v, "mapped",
                    field=f"AvatarSkillConfig[{sid}@L10].StanceDamageDisplay",
                ))
        # 合并 v1.5 手填的机制字段（wiki 核对，解包语义 P0-2 锁）
        legacy = json.loads((LEGACY_DIR / f"{cid}.json").read_text(encoding="utf-8"))
        no_datamine = "（联动角色：AvatarSkillConfig 无数据，ADR-0003）" if cid == "1015" else ""
        for slot_name, lslot in legacy.get("skills", {}).items():
            s = slots.setdefault(slot_name, {
                "_source": prov("biligame", "C", "biligame-2026-08", "cross_checked",
                                note="v1.5 手填机制字段"),
                "_upstream_ids": [],
                "_note": lslot.get("note", ""),
            })
            for f in ("sp", "energy", "energy_cost", "delay", "advance_pct",
                      "advance_target", "advance_self", "extra_action", "sp_bonus", "toughness"):
                if f in lslot and f not in s:
                    s[f] = wrapper(lslot[f], prov(
                        "biligame", "C", "biligame-2026-08", "cross_checked",
                        note=f"wiki 核对（ADR-0003）{no_datamine}；解包字段语义 P0-2 锁",
                    ))
            if "mult" not in s and "mult" in lslot:
                s["mult"] = wrapper(lslot["mult"], prov(
                    "biligame", "C", "biligame-2026-08", "cross_checked",
                    note="wiki 核对（ADR-0003）",
                ))
        # 天赋钩子机制（talent_extra.skill_effects[action] → mechanic；溯源继承 slot 的 C/wiki）
        for slot_name in slots:
            se = legacy.get("talent_extra", {}).get("skill_effects", {}).get(slot_name)
            if se:
                slots[slot_name]["mechanic"] = {k: v for k, v in se.items() if k != "note"}
                if se.get("note"):
                    slots[slot_name]["_mechanic_note"] = se["note"]
        out[cid] = slots
    return out


# ---------------- 敌人（模板，D 标记） ----------------

def build_enemies(raw: Dict) -> Dict:
    legacy = json.loads((ROOT / "data" / "enemy_elite90.json").read_text(encoding="utf-8"))
    enemies = {}
    for eid, e in legacy["enemies"].items():
        enemies[eid] = {
            "id": e.get("id", eid),
            "name": e["name"],
            "element": e["element"],
            "_source": prov("handfill", "D", "", "raw",
                            note="v1 模板值；P1 由 StageConfig/MonsterConfig 真实敌人替换"),
            "hp": e["hp"], "atk": e["atk"], "defense": e["defense"],
            "speed": e["speed"], "toughness": e["toughness"],
            "weaknesses": e.get("weaknesses", []),
            "resistances": e.get("resistances", {}),
            "break_immune": e.get("break_immune", False),
        }
    return {"_source": prov("handfill", "D", "", "raw",
                            note="90 级双精英靶场（v1 手填模板）"),
            "level": legacy.get("level", 90),
            "target_av": legacy.get("target_av", 250.0),
            "enemies": enemies}


# ---------------- 等级系数（HardLevelGroup） ----------------

def build_level_curves(raw: Dict) -> Dict:
    hlg = raw["TurnBasedGameData/ExcelOutput/HardLevelGroup.json"]
    tb_v = tb_ver(raw)
    groups: Dict[str, Dict] = {}
    for e in hlg:
        g = str(e["HardLevelGroup"])
        entry: Dict = {}
        for key, field in [("hp_ratio", "HPRatio"), ("atk_ratio", "AttackRatio"),
                           ("def_ratio", "DefenceRatio"), ("speed_ratio", "SpeedRatio"),
                           ("stance_ratio", "StanceRatio")]:
            if field in e:
                entry[key] = e[field]["Value"]
        groups.setdefault(g, {})[str(e["Level"])] = entry
    return {
        "_source": prov("datamine", "A", tb_v, "mapped",
                        field="HardLevelGroup.json"),
        "groups": groups,
    }


# ---------------- 主流程 ----------------

# 装备效果可执行映射（手工：desc 参数位语义 → exec DSL；队伍配置装备全覆盖）
# 类型：stat(面板常驻) / element_dmg / speed_over_100_dmg / crit_ge_dmg / basic_dmg /
#       def_ignore / stack_energy_regen / ult_sp_refund / skill_next_ally_dmg /
#       skill_team_dmg / start_advance / ult_convert / target_cd_buff / mem_cd_buff /
#       same_element_team_dmg / stat_conditional(memosprite_present)
LC_EXEC = {
    "23001": [  # 于夜色中：暴击+18%；超速每10点普攻战技+6%（6层）；大招暴伤+12%/层
        {"type": "stat", "stat": "crit_rate", "value": 0.18},
        {"type": "speed_over_100_dmg", "speed_step": 10, "mult": 0.06,
         "max_stacks": 6, "skills": ["basic", "skill"], "ult_crit_dmg": 0.12},
    ],
    "23003": [  # 但战斗还未结束：充能+10%；每2次大招回1 SP；战技后下一个行动队友增伤30%
        {"type": "stat", "stat": "energy_regen", "value": 0.10},
        {"type": "ult_sp_refund", "every": 2, "amount": 1},
        {"type": "skill_next_ally_dmg", "value": 0.30, "duration": 1},
    ],
    "23026": [  # 夜色流光溢彩：我方攻击→【歌咏】层（充能+3%/层，5层）；大招转化【华彩】
        {"type": "stack_energy_regen", "per_stack": 0.03, "max": 5, "trigger": "ally_attack"},
        {"type": "ult_convert", "stack_stat": "atk_pct", "stack_value": 0.48,
         "team_dmg": 0.24, "duration": 1},
    ],
    "24005": [  # 记忆永不落幕：速度+6%；战技后全队增伤8% 3回合
        {"type": "stat", "stat": "speed_pct", "value": 0.06},
        {"type": "skill_team_dmg", "value": 0.08, "duration": 3},
    ],
}
RS_EXEC = {
    "108": {"2": [{"type": "element_dmg", "element": "Quantum", "value": 0.10}],
             "4": [{"type": "def_ignore", "value": 0.10, "weakness_extra": 0.10,
                     "element": "Quantum"}]},
    # 内圈套装（球/绳）只有 2 件套效果，2 件描述含多段效果
    "306": {"2": [{"type": "stat", "stat": "crit_rate", "value": 0.08},
                    {"type": "crit_ge_dmg", "crit_ge": 0.50, "value": 0.15,
                     "skills": ["ult", "followup"]}]},
    "121": {"2": [{"type": "stat", "stat": "speed_pct", "value": 0.06}],
             "4": [{"type": "target_cd_buff", "value": 0.18, "duration": 2,
                     "max_stacks": 2}]},
    "308": {"2": [{"type": "stat", "stat": "energy_regen", "value": 0.05},
                    {"type": "start_advance", "speed_ge": 120.0, "pct": 0.40}]},
    "102": {"2": [{"type": "stat", "stat": "atk_pct", "value": 0.12}],
             "4": [{"type": "stat", "stat": "speed_pct", "value": 0.06},
                    {"type": "basic_dmg", "value": 0.10}]},
    "312": {"2": [{"type": "stat", "stat": "energy_regen", "value": 0.05},
                    {"type": "same_element_team_dmg", "value": 0.10}]},
    "123": {"2": [{"type": "stat", "stat": "atk_pct", "value": 0.12}],
             "4": [{"type": "mem_cd_buff", "value": 0.30, "duration": 2}]},
    "318": {"2": [{"type": "stat", "stat": "crit_dmg", "value": 0.16},
                    {"type": "stat_conditional", "stat": "crit_dmg", "value": 0.32,
                     "cond": "memosprite_present"}]},
}


# 星魂效果可执行映射（等级类 E3/E5 不接入——模拟器技能等级固定，标注跳过）
# 类型：skill_count_sp_refund（单回合 3 战技回 SP）/ ult_quantum_pen（终结技抗性+弱点）/\
#       ult_sp_refund_extra（大招额外回 SP+上限）/ concert_res_pen（协奏抗穿）/
#       mems_support_crit（声援目标暴击）
RANK_EXEC = {
    "1015": {  # 红A
        "1": [{"type": "skill_count_sp_refund", "count": 3, "amount": 2}],
        "2": [{"type": "ult_quantum_pen", "element": "Quantum", "res_pen": 0.20,
                "add_weakness": True, "duration": 2}],
        "3": [{"type": "skill_level", "skill": "skill", "delta": 2, "cap": 15},
               {"type": "skill_level", "skill": "basic", "delta": 1, "cap": 10}],
        "4": [{"type": "ult_dmg", "value": 1.50}],
        "5": [{"type": "skill_level", "skill": "ult", "delta": 2, "cap": 15},
               {"type": "skill_level", "skill": "talent", "delta": 2, "cap": 15}],
    },
    "1306": {  # 花火
        "4": [{"type": "ult_sp_refund_extra", "amount": 1, "sp_cap_bonus": 1}],
    },
    "1309": {  # 知更鸟
        "1": [{"type": "concert_res_pen", "value": 0.24}],
    },
    "8007": {  # 记忆主
        "1": [{"type": "mems_support_crit", "value": 0.10}],
        "3": [{"type": "skill_level", "skill": "skill", "delta": 2, "cap": 15},
               {"type": "skill_level", "skill": "talent", "delta": 2, "cap": 15}],
        "5": [{"type": "skill_level", "skill": "ult", "delta": 2, "cap": 15},
               {"type": "skill_level", "skill": "basic", "delta": 1, "cap": 10},
               {"type": "memo_level", "skill_delta": 1}],
    },
}


def build_equipment(raw: Dict) -> Dict:
    """光锥/遗器套装/星魂（Nanoka wiki 接口：中文描述+数值合一，用户提供）。

    - 光锥：全部 169 个（列表白值）；已 fetch 详情的含精炼 L1 效果（refinements）
    - 套装：全部 60 个（2/4 件效果中文描述 + ParamList 数值）
    - 星魂：队伍 4 角色 1-6 命（desc + param_list）
    与 TBGD 解包（EquipmentSkillConfig/AvatarRankConfig/RelicSetSkillConfig）交叉一致。
    """
    nv = f"nanoka-{raw['Nanoka/VERSIONS']['sha']}"
    p_lc = prov("nanoka-wiki", "B", nv, "cross_checked",
                note="与 TBGD EquipmentConfig/StarRailRes promotions 交叉一致")
    p_eff = prov("nanoka-wiki", "B", nv, "cross_checked",
                 note="精炼 1 数值（与 TBGD EquipmentSkillConfig ParamList 交叉一致）")
    p_rs = prov("nanoka-wiki", "B", nv, "cross_checked",
                note="与 TBGD RelicSetSkillConfig AbilityParamList 交叉一致")
    p_rank = prov("nanoka-wiki", "B", nv, "cross_checked",
                  note="与 TBGD AvatarRankConfig Param 交叉一致")

    out: Dict = {"light_cones": {}, "relic_sets": {}, "eidolons": {}}

    # ---- 光锥 ----
    lc_list = raw["Nanoka/lightcone.json"]
    for cid, meta in lc_list.items():
        detail = raw.get(f"Nanoka/zh/lightcone/{cid}.json")
        base: Dict = {}
        if detail:
            for s in detail.get("stats", []):
                if s.get("promotion") == 6:
                    # 80 级总值 = 突破段起始值 + 每级增量 × 79（验证：23001 = 582.1 = 列表 atk）
                    base = {"hp": s["base_hp"] + s["base_hp_add"] * 79,
                            "atk": s["base_attack"] + s["base_attack_add"] * 79,
                            "def": s["base_defence"] + s["base_defence_add"] * 79}
                    break
        elif meta.get("atk"):
            base = {"atk": meta["atk"]}   # 列表含 80 级攻击（未 fetch 详情时）
        ref: Optional[Dict] = None
        if detail and detail.get("refinements"):
            rf = detail["refinements"]
            ref = {"name": rf.get("name"), "desc": rf.get("desc"),
                   "level_1_params": rf.get("level", {}).get("1", {}).get("param_list", [])}
        rank_str = meta.get("rank", "")
        rarity = 0
        if rank_str:
            rarity = int("".join(c for c in rank_str if c.isdigit())[:1] or 0)
        entry = {
            "id": cid, "name": meta.get("zh"), "path": meta.get("baseType"),
            "rarity": wrapper(rarity, prov("nanoka-wiki", "B", nv, "mapped",
                                            field=f"lightcone.json.{cid}.rank")),
            "base_stats": wrapper(base, p_lc),
        }
        if ref:
            ref["exec"] = LC_EXEC.get(cid, [])
            entry["effect"] = wrapper(ref, p_eff)
        # 无详情（未 fetch）：不写 effect 字段（数据缺失 ≠ 未验证输入，不污染信任信封）
        out["light_cones"][cid] = entry

    # ---- 遗器套装 ----
    for sid, rs in raw["Nanoka/relicset.json"].items():
        def piece(key: str) -> Optional[Dict]:
            p = rs.get("set", {}).get(key)
            if not p:
                return None
            d = {"desc": p.get("zh"), "params": p.get("ParamList") or []}
            execs = RS_EXEC.get(sid, {}).get(key)
            if execs:
                d["exec"] = execs
            return d
        out["relic_sets"][sid] = {
            "id": sid, "name": rs.get("zh"),
            "two_piece": wrapper(piece("2"), p_rs),
            "four_piece": wrapper(piece("4"), p_rs),
        }

    # ---- 星魂（队伍 4 角色）----
    for cid in CHARS:
        ch = raw.get(f"Nanoka/zh/character/{cid}.json")
        if not ch or not ch.get("ranks"):
            continue
        ranks = {}
        for rk, rv in sorted(ch["ranks"].items(), key=lambda kv: int(kv[0])):
            d = {"name": rv.get("name"), "desc": rv.get("desc"),
                 "param_list": rv.get("param_list") or []}
            execs = RANK_EXEC.get(cid, {}).get(rk)
            if execs:
                d["exec"] = execs
            elif rk in ("3", "5"):
                d["exec_skip"] = "等级类星魂（技能等级+2），模拟器技能等级固定，未接入"
            ranks[rk] = wrapper(d, p_rank)
        out["eidolons"][cid] = {"id": cid, "name": ch.get("zh"), "ranks": ranks}
    return out


def cross_check(characters: Dict, skills: Dict) -> List[str]:
    """与 v1.5 手填交叉核对，返回差异报告。"""
    report: List[str] = []
    for cid in CHARS:
        legacy = json.loads((LEGACY_DIR / f"{cid}.json").read_text(encoding="utf-8"))
        lb = legacy.get("base_stats", {})
        nb = characters[cid]["base_stats"]
        for stat, key in [("hp", "hp"), ("atk", "atk"), ("defense", "defense"),
                          ("speed", "speed"), ("crit_rate", "crit_rate"), ("crit_dmg", "crit_dmg")]:
            if key in lb:
                diff = lb[key] - nb[stat]["value"]
                if abs(diff) > 1e-6:
                    suffix = "（仅四舍五入）" if abs(diff) < 0.5 else ""
                    report.append(f"{cid}.base_stats.{stat}: 手填 {lb[key]} ≠ 上游 {nb[stat]['value']}{suffix}")
        for slot, ls in legacy.get("skills", {}).items():
            ns = skills[cid].get(slot)
            if ns and "mult" in ls and "mult" in ns and abs(ls["mult"] - ns["mult"]["value"]) > 1e-6:
                report.append(f"{cid}.{slot}.mult: 手填 {ls['mult']} ≠ 上游 {ns['mult']['value']}")
            if ns and "toughness" in ls and "toughness" in ns and abs(ls["toughness"] - ns["toughness"]["value"]) > 1e-6:
                report.append(f"{cid}.{slot}.toughness: 手填 {ls['toughness']} ≠ 解包 {ns['toughness']['value']}")
    return report


def main(argv) -> int:
    raw = load_raw()
    characters = build_characters(raw)
    skills = build_skills(raw, characters)
    enemies = build_enemies(raw)
    level_curves = build_level_curves(raw)
    equipment = build_equipment(raw)

    NORM_DIR.mkdir(parents=True, exist_ok=True)
    for name, doc in [("characters", characters), ("skills", skills),
                      ("enemies", enemies), ("level_curves", level_curves),
                      ("equipment", equipment)]:
        (NORM_DIR / f"{name}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ data/normalized/{name}.json")

    report = cross_check(characters, skills)
    if report:
        print("\n⚠ 与 v1.5 手填不一致（需人工确认）：")
        for r in report:
            print(f"  ✗ {r}")
    else:
        print("\n✓ 与 v1.5 手填交叉核对：全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
