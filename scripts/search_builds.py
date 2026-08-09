"""装备组合搜索 —— 红A 主 C 的光锥 × 套装 × 主词条 × 副词条穷举。

评估：demo pilot 决策跑完整局（seed=0 双精英 90），指标 = 击杀数 → 总伤害 → 用时。
数据：normalized（equipment.json 全部 exec 接入）。

用法：python scripts/search_builds.py [--top N] [--enemy <文件>] [--seed N] [--quiet]
   --enemy：关卡文件（默认 enemy_elite90.json；④ 多关卡排序稳定性验证）
"""
from __future__ import annotations

import copy
import itertools
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hsr_sim.build import BuildConfig, assemble, resolve_equipment  # noqa: E402
from hsr_sim.data.loader import (NORMALIZED_DIR, load_characters_normalized,  # noqa: E402
                                 load_enemies_normalized, load_equipment)
from hsr_sim.engine.simulate import Simulator  # noqa: E402
from hsr_sim.loader import DATA_DIR, _apply_rank_levels  # noqa: E402
from hsr_sim.model import Rotation  # noqa: E402
from hsr_sim.rehearse import RehearsalSession, _demo_pilot  # noqa: E402

RED = "1015"

# 候选空间（红A；其余角色固定当前配置）
LIGHT_CONES = [
    ("23001", "于夜色中"),
    ("24001", "星海巡航"),
    ("23012", "如泥酣眠"),
    ("21010", "论剑"),
]
SETS = [
    ([{"id": "108", "pieces": 4}, {"id": "306", "pieces": 2}], "天才4+萨尔索图2"),
    ([{"id": "108", "pieces": 4}, {"id": "308", "pieces": 2}], "天才4+翁瓦克2"),
    ([{"id": "102", "pieces": 4}, {"id": "306", "pieces": 2}], "快枪手4+萨尔索图2"),
    ([{"id": "102", "pieces": 4}, {"id": "308", "pieces": 2}], "快枪手4+翁瓦克2"),
    ([{"id": "108", "pieces": 2}, {"id": "102", "pieces": 2}, {"id": "306", "pieces": 2}], "天才2+快枪手2+萨尔索图2"),
]
BODY = [("crit_dmg", "暴伤衣"), ("crit_rate", "暴击衣")]
FEET = [("speed", "速度鞋"), ("atk_pct", "攻击鞋")]
SPHERE = [("quantum_dmg", "量子球"), ("atk_pct", "攻击球")]
SUBS = [
    ({"speed": 2, "crit_rate": 16, "crit_dmg": 5, "atk_pct": 7}, "模板16/5/7"),
    ({"speed": 2, "crit_rate": 10, "crit_dmg": 10, "atk_pct": 8}, "均衡10/10/8"),
    ({"speed": 2, "crit_rate": 8, "crit_dmg": 16, "atk_pct": 4}, "高暴伤8/16/4"),
]


def main() -> int:
    top_n = 20
    if "--top" in sys.argv:
        top_n = int(sys.argv[sys.argv.index("--top") + 1])
    enemy_file = "enemy_elite90.json"
    if "--enemy" in sys.argv:
        enemy_file = sys.argv[sys.argv.index("--enemy") + 1]
    seed = 0
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])

    chars, unverified = load_characters_normalized(NORMALIZED_DIR)
    equipment = load_equipment(NORMALIZED_DIR)
    enemies, level, target_av, unv2 = load_enemies_normalized()
    # ④ 多关卡：legacy 敌人文件直接加载（技能/速度/开局状态）
    from hsr_sim.loader import load_enemies
    enemies, level, target_av = load_enemies(DATA_DIR / enemy_file)
    _ed = json.loads((DATA_DIR / enemy_file).read_text(encoding="utf-8"))
    initial_sp = _ed.get("initial_sp", 4.0)
    initial_energy = _ed.get("initial_energy", {})

    # 其他角色（花火/知更鸟/记忆主）：固定当前配置，装配一次
    team = json.loads((DATA_DIR / "team_reda.json").read_text(encoding="utf-8"))
    other_stats: dict = {}
    for cid, b in team["builds"].items():
        if cid == RED:
            continue
        c = chars[cid]
        cfg = BuildConfig(main_stats=b.get("main_stats", {}), substats=b.get("substats", {}),
                          light_cone=b.get("light_cone", ""), relic_sets=b.get("relic_sets", []),
                          eidolon=b.get("eidolon", 0), cid=cid)
        other_stats[cid] = assemble(c.base_stats, c.element, cfg, equipment)
        r2 = resolve_equipment(
            {"light_cone": b.get("light_cone", ""), "relic_sets": b.get("relic_sets", []),
             "eidolon": b.get("eidolon", 0), "cid": cid}, equipment)
        c.equipment_effects = r2["effects"]
        _apply_rank_levels(c)
    red_base = chars[RED]

    results = []
    total = len(LIGHT_CONES) * len(SETS) * len(BODY) * len(FEET) * len(SPHERE) * len(SUBS)
    t0 = time.time()
    i = 0
    for (lc_id, lc_name), (sets, set_name), (body, body_n), (feet, feet_n), \
            (sphere, sphere_n), (subs, sub_n) in itertools.product(
                LIGHT_CONES, SETS, BODY, FEET, SPHERE, SUBS):
        i += 1
        ch = copy.deepcopy(red_base)          # 重载红A（_apply_rank_levels 改 mult，防残留）
        chars[RED] = ch
        cfg = BuildConfig(
            main_stats={"body": body, "feet": feet, "sphere": sphere, "rope": "atk_pct"},
            substats=subs, light_cone=lc_id, relic_sets=sets, eidolon=5, cid=RED)
        stats = dict(other_stats)
        stats[RED] = assemble(ch.base_stats, ch.element, cfg, equipment)
        ch.equipment_effects = resolve_equipment(
            {"light_cone": lc_id, "relic_sets": sets, "eidolon": 5, "cid": RED},
            equipment)["effects"]
        _apply_rank_levels(ch)

        sim = Simulator(chars, stats, enemies, Rotation(), target_av, level, 130.0,
                        unverified_inputs=unverified + unv2, seed=seed,
                        initial_sp=initial_sp, initial_energy=initial_energy)
        session = RehearsalSession(sim)
        _demo_pilot(session, max_acts=200)
        fs = session.report_dict()["final_state"]
        kills = sum(1 for hp in fs["enemy_hp_left"].values() if hp <= 0.0)
        results.append({
            "rank": 0, "kills": kills, "damage": fs["total_damage"],
            "time": fs["t"],
            "lc": lc_name, "sets": set_name, "body": body_n, "feet": feet_n,
            "sphere": sphere_n, "subs": sub_n,
            "ult": sum(fs["ult_count"].values()),
            "acts": sum(fs["action_count"].values()),
        })
        if i % 60 == 0 or i == total:
            print(f"  {i}/{total}  ({time.time()-t0:.0f}s)", flush=True)

    results.sort(key=lambda x: (-x["kills"], -x["damage"], x["time"]))
    for idx, r in enumerate(results):
        r["rank"] = idx + 1

    print(f"\n===== 红A 装备组合搜索：{total} 组，{time.time()-t0:.0f}s =====")
    print(f"{'#':>3} {'击杀':>3} {'总伤害':>10} {'用时AV':>8} {'大招':>3} {'光锥':<8} {'套装':<22} {'衣':<5} {'鞋':<5} {'球':<5} {'副词条':<10}")
    cur = {"lc": "于夜色中", "sets": "天才4+萨尔索图2", "body": "暴伤衣", "feet": "速度鞋",
           "sphere": "量子球", "subs": "模板16/5/7"}
    for r in results[:top_n]:
        mark = " ◀当前" if all(r[k] == v for k, v in cur.items()) else ""
        print(f"{r['rank']:>3} {r['kills']:>3} {r['damage']:>10,.0f} {r['time']:>8.1f} {r['ult']:>3} "
              f"{r['lc']:<8} {r['sets']:<22} {r['body']:<5} {r['feet']:<5} {r['sphere']:<5} {r['subs']:<10}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
