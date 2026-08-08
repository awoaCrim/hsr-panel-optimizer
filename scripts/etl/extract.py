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
        }
    return out


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
                      "advance_target", "extra_action", "sp_bonus"):
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

    NORM_DIR.mkdir(parents=True, exist_ok=True)
    for name, doc in [("characters", characters), ("skills", skills),
                      ("enemies", enemies), ("level_curves", level_curves)]:
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
