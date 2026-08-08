"""输入加载层 —— JSON → 模型。输入层解耦点（ADR-0002）：
手填 JSON 与未来的米游社导入适配器都产出同一套结构。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .build import BuildConfig, assemble, validate_config
from .model import Action, CharacterData, CharacterPolicy, Enemy, Rotation, SkillData, Stats

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _stats_from_dict(d: dict) -> Stats:
    return Stats(
        hp=d.get("hp", 0.0), atk=d.get("atk", 0.0), defense=d.get("defense", 0.0),
        speed=d.get("speed", 0.0), crit_rate=d.get("crit_rate", 0.0),
        crit_dmg=d.get("crit_dmg", 0.0), break_effect=d.get("break_effect", 0.0),
        energy_regen=d.get("energy_regen", 1.0), dmg_bonus=d.get("dmg_bonus", 0.0),
        heal_bonus=d.get("heal_bonus", 0.0),
    )


def _skill_from_dict(d: dict) -> SkillData:
    return SkillData(
        mult=d.get("mult", 0.0), sp=d.get("sp", 0), energy=d.get("energy", 0.0),
        energy_cost=d.get("energy_cost", 0.0), toughness=d.get("toughness", 0.0),
        delay=d.get("delay", 0.0), advance_pct=d.get("advance_pct", 0.0),
        advance_target=d.get("advance_target", ""), extra_action=d.get("extra_action", False),
        sp_bonus=d.get("sp_bonus", 0), note=d.get("note", ""),
    )


def load_character(path: Path) -> CharacterData:
    d = json.loads(path.read_text(encoding="utf-8"))
    return CharacterData(
        id=d["id"], name=d["name"], element=d["element"], path=d["path"],
        base_stats=_stats_from_dict(d.get("base_stats", {})),
        skills={k: _skill_from_dict(v) for k, v in d.get("skills", {}).items()},
        talent_extra=d.get("talent_extra", {}),
        max_energy=d.get("max_energy", 0.0),
        note=d.get("note", ""),
    )


def assemble_team(team_path: Path, characters: Dict[str, CharacterData]) -> Tuple[Dict[str, Stats], Dict[str, float], Dict[str, List[str]]]:
    """从队伍方案 JSON 装配面板：返回 (stats, speed_targets, build_errors)。

    builds 支持两种形态：
    - {main_stats, substats}：装备配置 → 由 build.py 装配面板（词条预算可审计）
    - {stats}：直接给定最终面板（兼容/调试用）
    """
    d = json.loads(team_path.read_text(encoding="utf-8"))
    stats: Dict[str, Stats] = {}
    build_errors: Dict[str, List[str]] = {}
    for cid, build in d["builds"].items():
        ch = characters[cid]
        if "stats" in build:
            stats[cid] = _stats_from_dict(build["stats"])
        elif "main_stats" in build or "substats" in build:
            cfg = BuildConfig(main_stats=build.get("main_stats", {}),
                              substats=build.get("substats", {}),
                              light_cone=build.get("light_cone", {}))
            errs = validate_config(cfg)
            if errs:
                build_errors[cid] = errs
                stats[cid] = ch.base_stats
            else:
                stats[cid] = assemble(ch.base_stats, ch.element, cfg)
        else:
            stats[cid] = ch.base_stats
    return stats, d.get("speed_targets", {}), build_errors


def load_team(team_path: Path, char_dir: Path) -> Tuple[Dict[str, CharacterData], Dict[str, Stats], Dict[str, float]]:
    """加载队伍方案：返回 (角色数据, 面板, 速度断点目标)。"""
    d = json.loads(team_path.read_text(encoding="utf-8"))
    characters: Dict[str, CharacterData] = {}
    for cid in d["builds"]:
        cpath = char_dir / f"{cid}.json"
        characters[cid] = load_character(cpath)
    stats, speed_targets, build_errors = assemble_team(team_path, characters)
    if build_errors:
        raise ValueError(f"面板配置不合法：{json.dumps(build_errors, ensure_ascii=False)}")
    return characters, stats, speed_targets


def load_enemies(path: Path) -> Tuple[Dict[str, Enemy], int, float]:
    d = json.loads(path.read_text(encoding="utf-8"))
    enemies = {
        eid: Enemy(
            id=e.get("id", eid), name=e["name"], element=e["element"],
            hp=e["hp"], atk=e["atk"], defense=e["defense"], speed=e["speed"],
            toughness=e["toughness"], weaknesses=e.get("weaknesses", []),
            resistances=e.get("resistances", {}),
            break_immune=e.get("break_immune", False),
        )
        for eid, e in d["enemies"].items()
    }
    return enemies, d.get("level", 90), d.get("target_av", 250.0)


def load_substat_counts(team_path: Path) -> Dict[str, float]:
    """每个角色的副词条总词条数（词条预算审计用）。"""
    d = json.loads(team_path.read_text(encoding="utf-8"))
    out = {}
    for cid, b in d.get("builds", {}).items():
        if "substats" in b:
            out[cid] = float(sum(b["substats"].values()))
    return out


def load_rotation(path: Path) -> Rotation:
    d = json.loads(path.read_text(encoding="utf-8"))
    # v3 策略形态：{policy: {cid: {ult, chain_max, fallback, skill_budget, pull_target}}}
    policy = {}
    for cid, p in d.get("policy", {}).items():
        policy[cid] = CharacterPolicy(
            ult=p.get("ult", "on_full"),
            chain_max=int(p.get("chain_max", 0)),
            fallback=p.get("fallback", "basic"),
            skill_budget=int(p.get("skill_budget", 999)),
            pull_target=p.get("pull_target", ""),
        )
    # v2 序列形态（兼容）
    actions: Dict[str, list] = {}
    for unit_id, seq in d.get("actions", {}).items():
        actions[unit_id] = [
            Action(unit_id=a.get("unit_id", unit_id), action=a["action"], target=a.get("target", ""))
            for a in seq
        ]
    return Rotation(policy=policy, actions=actions)
