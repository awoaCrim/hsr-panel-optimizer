"""normalized 数据加载 —— 读取 data/normalized/*.json，剥离溯源包装为纯值。

信任度信封（ADR-0006 6.2）：load() 同时返回 provenance 注册表，
`unverified_paths()` 给出 source_trust=D 或 validation=raw 的字段清单，
v2 模拟器报告据此标注"未验证"。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .provenance import Provenance

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
NORMALIZED_DIR = DATA_DIR / "normalized"

_META_KEYS = {"_source", "_upstream_ids", "_upstream_params", "_note"}


def _is_wrapper(o) -> bool:
    return isinstance(o, dict) and "value" in o and any(
        k in o for k in ("source_trust", "validation", "source", "version", "field", "override", "note")
    )


def _strip_provenance(o):
    """递归剥离溯源包装：{'value': x, 'source_trust': ...} → x；纯值原样。"""
    if isinstance(o, dict):
        if _is_wrapper(o):
            return o["value"]
        return {k: _strip_provenance(v) for k, v in o.items() if k not in _META_KEYS}
    if isinstance(o, list):
        return [_strip_provenance(v) for v in o]
    return o


def _collect_provenance(o, path: str, inherited: Dict, out: List[Tuple[str, Provenance]]):
    if isinstance(o, dict):
        if _is_wrapper(o):
            out.append((path, Provenance.from_dict(o, inherited)))
            return
        if "_source" in o:
            inherited = {**inherited, **(o["_source"] or {})}
        for k, v in o.items():
            if k in _META_KEYS:
                continue
            _collect_provenance(v, f"{path}.{k}" if path else k, inherited, out)
    elif isinstance(o, (int, float)):
        out.append((path, Provenance.from_dict({}, inherited)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            _collect_provenance(v, f"{path}[{i}]", inherited, out)


class NormalizedData:
    """data/normalized/ 的纯值视图 + 溯源注册表。"""

    def __init__(self, doc: dict, provenance: List[Tuple[str, Provenance]]):
        self.doc = doc
        self.provenance = provenance

    def get(self, *keys, default=None):
        cur = self.doc
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def unverified_paths(self) -> List[Tuple[str, str, str]]:
        """未验证值清单：(路径, source_trust, validation)。"""
        return [
            (p, prov.source_trust, prov.validation)
            for p, prov in self.provenance
            if not prov.is_trusted()
        ]


def load(normalized_dir: Path = NORMALIZED_DIR) -> NormalizedData:
    """加载全部 normalized JSON，返回纯值 + 溯源注册表。"""
    if not normalized_dir.exists():
        raise FileNotFoundError(f"缺 {normalized_dir}（先运行 python scripts/etl/extract.py）")
    doc: Dict = {}
    provenance: List[Tuple[str, Provenance]] = []
    for f in sorted(normalized_dir.glob("*.json")):
        if f.name == "VERSIONS.json":
            continue
        raw = json.loads(f.read_text(encoding="utf-8"))
        doc[f.name.replace(".json", "")] = _strip_provenance(raw)
        _collect_provenance(raw, f.name.replace(".json", ""), {}, provenance)
    return NormalizedData(doc, provenance)


# ---------- 模拟器数据层（P0-3 Step B：normalized 直驱，ADR-0006 5.4） ----------

from hsr_sim.loader import _stats_from_dict, assemble_team  # noqa: E402
from hsr_sim.model import CharacterData, Enemy, SkillData  # noqa: E402


def load_characters_normalized(normalized_dir: Path = NORMALIZED_DIR):
    """从 normalized 构建 CharacterData（技能字段 + mechanic → talent_extra）。

    返回 (characters: Dict[str, CharacterData], unverified_paths)。
    """
    nd = load(normalized_dir)
    chars_data = nd.get("characters") or {}
    skills_data = nd.get("skills") or {}
    characters: Dict[str, CharacterData] = {}
    for cid, cd in chars_data.items():
        skills = {}
        for slot, sd in (skills_data.get(cid) or {}).items():
            skills[slot] = SkillData(
                mult=sd.get("mult", 0.0), sp=sd.get("sp", 0),
                energy=sd.get("energy", 0.0), energy_cost=sd.get("energy_cost", 0.0),
                toughness=sd.get("toughness", 0.0), delay=sd.get("delay", 0.0),
                advance_pct=sd.get("advance_pct", 0.0),
                advance_target=sd.get("advance_target", ""),
                advance_self=sd.get("advance_self", True),
                extra_action=sd.get("extra_action", False),
                sp_bonus=sd.get("sp_bonus", 0),
                note=sd.get("_note", ""),
            )
        talent_extra = {
            "skill_effects": {
                slot: dict(s.get("mechanic")) if s.get("mechanic") else None
                for slot, s in (skills_data.get(cid) or {}).items()
            }
        }
        # 机制说明（note 在 ETL 中单独存放，重建时合并回 mechanic）
        for slot, s in (skills_data.get(cid) or {}).items():
            mech = talent_extra["skill_effects"].get(slot)
            if mech is not None and s.get("_mechanic_note"):
                mech["note"] = s["_mechanic_note"]
        talent_extra["skill_effects"] = {
            k: v for k, v in talent_extra["skill_effects"].items() if v is not None
        }
        talent_extra.update(cd.get("talent_extra") or {})
        characters[cid] = CharacterData(
            id=cid, name=cd["name"], element=cd["element"], path=cd["path"],
            base_stats=_stats_from_dict(cd.get("base_stats") or {}),
            skills=skills, talent_extra=talent_extra,
            max_energy=cd.get("max_energy", 0.0),
        )
    # 信任度信封：仅本层字段（characters.*/skills.*）
    unverified = [p for p, _, _ in nd.unverified_paths()
                  if p.startswith(("characters.", "skills."))]
    return characters, unverified


def load_team_normalized(team_path: Path, normalized_dir: Path = NORMALIZED_DIR):
    """队伍方案 + normalized 角色数据 → (characters, stats, speed_targets, unverified)。"""
    import json as _json

    characters, unverified = load_characters_normalized(normalized_dir)
    stats, speed_targets, build_errors = assemble_team(team_path, characters)
    if build_errors:
        raise ValueError(f"面板配置不合法：{_json.dumps(build_errors, ensure_ascii=False)}")
    return characters, stats, speed_targets, unverified


def load_enemies_normalized(normalized_dir: Path = NORMALIZED_DIR):
    """normalized 敌人 → (enemies, level, target_av, unverified)。"""
    nd = load(normalized_dir)
    ed = nd.get("enemies") or {}
    enemies = {
        eid: Enemy(
            id=e.get("id", eid), name=e["name"], element=e["element"],
            hp=e["hp"], atk=e["atk"], defense=e["defense"], speed=e["speed"],
            toughness=e["toughness"], weaknesses=e.get("weaknesses", []),
            resistances=e.get("resistances", {}),
            break_immune=e.get("break_immune", False),
        )
        for eid, e in (ed.get("enemies") or {}).items()
    }
    # 信任度信封：仅本层字段（enemies.*）
    unverified = [p for p, _, _ in nd.unverified_paths() if p.startswith("enemies.")]
    return enemies, ed.get("level", 90), ed.get("target_av", 250.0), unverified
