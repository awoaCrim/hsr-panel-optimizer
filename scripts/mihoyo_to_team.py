"""米游社战绩 JSON → 队伍配置（team_real.json 格式）自动转换。

输入：米游社·星穹铁道战绩导出（角色战绩汇总 JSON）
输出：data/team_real.json（面板直填 + 装备/星魂/行迹等级）

面板计算：
- ATK/SPD/HP/DEF = 战绩总计（直填）
- 暴击率 = 5%（基础）+ 光锥 stat + 套装 stat + 躯干主词条 + 副词条
- 暴伤 = 50%（基础）+ 光锥 stat + 套装 stat + 躯干主词条 + 副词条
- 充能 = 1.0 + 连结绳主词条 + 翁瓦克 2 件（若装备）
- 行迹属性加成（天赋树节点）未计入——战绩截图未含，标注待确认

用法：python scripts/mihoyo_to_team.py <战绩.json> [--out data/team_real.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHAR_IDS = {"Archer": "1015", "开拓者": "8007", "花火": "1306", "知更鸟": "1309"}
# 光锥名 → id（真实队伍）
LC_IDS = {"理想燃烧的地狱": "23046", "回到大地的飞行": "23034",
          "飞向粉色的明天": "22006", "夜色流光溢彩": "23026"}
# 遗器部位名前缀 → 套装 id
SET_IDS = {"天才": "108", "直播间": "324", "司铎": "121", "翁瓦克": "308",
           "救世主": "127", "快枪手": "102", "勇烈": "120", "繁星璀璨的天才": "108"}
SKILL_IDS = {"普攻": "basic", "战技": "skill", "终结技": "ult", "天赋": "talent",
             "忆灵技": "mem_skill", "忆灵天赋": "mem_talent"}
MAIN_STAT_KEYS = {"暴击伤害": "crit_dmg", "暴击率": "crit_rate", "速度": "speed",
                  "攻击力百分比": "atk_pct", "量子属性伤害提高": "quantum_dmg",
                  "能量恢复效率": "energy_regen", "生命值": "hp", "生命值百分比": "hp_pct"}


def piece_stat(rel: dict) -> dict:
    """单件遗器对暴击/暴伤的贡献。"""
    out = {"crit_rate": 0.0, "crit_dmg": 0.0}
    main = rel.get("主属性") or {}
    if main.get("属性") == "暴击率":
        out["crit_rate"] += main.get("数值", 0) / 100.0
    elif main.get("属性") == "暴击伤害":
        out["crit_dmg"] += main.get("数值", 0) / 100.0
    for s in rel.get("副属性") or []:
        if s.get("属性") == "暴击率":
            out["crit_rate"] += s.get("数值", 0) / 100.0
        elif s.get("属性") == "暴击伤害":
            out["crit_dmg"] += s.get("数值", 0) / 100.0
    return out


def lc_stat(cid: str, lc_name: str) -> dict:
    """光锥面板类效果（stat exec 提取：暴击率/暴伤）。"""
    from hsr_sim.data.loader import load_equipment
    eq = load_equipment()
    lc = (eq.get("light_cones") or {}).get(LC_IDS.get(lc_name, "")) or {}
    eff = lc.get("effect") or {}
    v = eff.get("value", eff) if isinstance(eff, dict) and "value" in eff else eff
    out = {"crit_rate": 0.0, "crit_dmg": 0.0}
    for ex in (v.get("exec") or []):
        if ex["type"] == "stat" and ex["stat"] in out:
            out[ex["stat"]] += ex["value"]
    return out


def set_stat(sets: list) -> dict:
    """套装面板类效果（2 件套 stat exec：暴击率/暴伤；含叠影光锥非面板项忽略）。"""
    out = {"crit_rate": 0.0, "crit_dmg": 0.0}
    for sid in sets:
        if sid == "324":      # 直播间：暴伤 16%（基础部分）
            out["crit_dmg"] += 0.16
        elif sid == "127":    # 救世主：暴击 8%
            out["crit_rate"] += 0.08
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "team_real.json")
    args = ap.parse_args()

    data = json.loads(args.src.read_text(encoding="utf-8"))
    builds = {}
    for c in data["角色"]:
        cid = CHAR_IDS.get(c["角色名"])
        if not cid:
            print(f"跳过未知角色: {c['角色名']}")
            continue
        attr = c["角色属性"]
        # 套装 id（按部位前缀）
        set_ids, counts = [], {}
        for rel in c["遗器"]:
            name = rel["名称"]
            sid = next((v for k, v in SET_IDS.items() if k in name), None)
            if sid:
                counts[sid] = counts.get(sid, 0) + 1
        for sid, n in counts.items():
            set_ids.append({"id": sid, "pieces": n})
        # 暴击/暴伤：有忆灵属性时用忆灵值（官方继承语义：忆灵 = 忆师面板，含行迹加成）
        mem = c.get("忆灵属性") or {}
        if mem.get("暴击率") and mem.get("暴击伤害"):
            cr = mem["暴击率"]["数值"] / 100.0
            cd = mem["暴击伤害"]["数值"] / 100.0
        else:
            cr, cd = 0.05, 0.50
            for rel in c["遗器"]:
                ps = piece_stat(rel)
                cr += ps["crit_rate"]
                cd += ps["crit_dmg"]
            ls = lc_stat(cid, c["光锥"]["名称"])
            ss = set_stat(counts)
            cr += ls["crit_rate"] + ss["crit_rate"]
            cd += ls["crit_dmg"] + ss["crit_dmg"]
        # 充能：绳 + 翁瓦克
        regen = 1.0
        for rel in c["遗器"]:
            if rel["部位"] == "连结绳" and (rel["主属性"] or {}).get("属性") == "能量恢复效率":
                regen += rel["主属性"]["数值"] / 100.0
        if counts.get("308"):
            regen += 0.05
        skill_levels = {}
        for k, v in (c.get("行迹") or {}).items():
            slot = SKILL_IDS.get(k)
            if slot and slot in ("basic", "skill", "ult", "talent"):
                skill_levels[slot] = v
        builds[cid] = {
            "note": f"{c['角色名']} 星魂{c['星魂解锁数']} 光锥 {c['光锥']['名称']}(精{c['光锥']['叠影']})；"
                    f"暴击/暴伤=基础+遗器+光锥+套装反推（行迹属性加成未计入）",
            "stats": {
                "atk": attr["攻击力"]["总计"],
                "speed": attr["速度"]["总计"],
                "crit_rate": round(cr, 4),
                "crit_dmg": round(cd, 4),
                "hp": attr["生命值"]["总计"],
                "defense": attr["防御力"]["总计"],
                "energy_regen": round(regen, 4),
            },
            "light_cone": LC_IDS.get(c["光锥"]["名称"], ""),
            "light_cone_rank": c["光锥"]["叠影"],
            "relic_sets": set_ids,
            "eidolon": c["星魂解锁数"],
            "skill_levels": skill_levels,
        }
    out = {"_note": "米游社战绩自动转换（scripts/mihoyo_to_team.py）；行迹属性加成未计入（待截图）",
           "builds": builds}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {args.out}：{list(builds)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
