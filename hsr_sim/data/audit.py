"""数据审计 —— ADR-0006 P0-1 门禁：`python -m hsr_sim.data audit`。

遍历 data/normalized/，校验：
1. 每个叶子值有溯源（显式包装，或继承块的 _source 默认）
2. 溯源两维合法（source_trust A/B/C/D，validation raw/mapped/cross_checked/game_verified）
3. version 与 data/raw/VERSIONS.json 一致
4. 引用完整（角色技能的上游 SkillID 前缀 = 角色 ID）
5. 输出"未验证"清单：source_trust=D 或 validation=raw 的值（信任度信封的数据源）

退出码：违规 >0 时 1，否则 0。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from .provenance import Provenance, validate_provenance

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
NORMALIZED_DIR = DATA_DIR / "normalized"
RAW_DIR = DATA_DIR / "raw"
VERSIONS_FILE = RAW_DIR / "VERSIONS.json"

# 跳过审计的元字段（非数据值）
META_KEYS = {"_source", "_upstream_ids", "_note", "id", "name"}

Unverified = Tuple[str, str, str]  # (path, source_trust, validation)


def _is_wrapper(o) -> bool:
    """溯源包装：{'value': x, source_trust/validation/...} —— 值 + 自带溯源。"""
    return isinstance(o, dict) and "value" in o and any(
        k in o for k in ("source_trust", "validation", "source", "version", "field", "override", "note")
    )


def _leaf_paths(obj, prefix: str) -> List[Tuple[str, object, Provenance]]:
    """展平嵌套结构：返回 (路径, 值, 生效溯源)。纯值继承最近一层块的 _source。"""
    out: List[Tuple[str, object, Provenance]] = []
    default: Dict = {}

    def walk(o, path: str, inherited: Dict):
        if isinstance(o, dict):
            if _is_wrapper(o):
                out.append((path, o["value"], Provenance.from_dict(o, inherited)))
                return
            if "_source" in o:
                inherited = {**inherited, **(o["_source"] or {})}
            for k, v in o.items():
                if k in META_KEYS and k != "name":
                    continue
                walk(v, f"{path}.{k}" if path else k, inherited)
        elif isinstance(o, (int, float)):
            out.append((path, o, Provenance.from_dict({}, inherited)))
        elif isinstance(o, str):
            # 字符串键（名称/元素/备注）：不做溯源强制，但记录
            pass
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]", inherited)
        else:  # bool / None
            pass

    walk(obj, prefix, default)
    return out


def audit() -> Tuple[List[str], List[Unverified]]:
    """返回 (违规清单, 未验证值清单)。"""
    errors: List[str] = []
    unverified: List[Unverified] = []

    if not NORMALIZED_DIR.exists():
        return [f"缺 data/normalized/（先运行 python scripts/etl/extract.py）"], []

    # VERSIONS.json
    versions: Dict = {}
    if VERSIONS_FILE.exists():
        versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    else:
        errors.append(f"缺 {VERSIONS_FILE}（先运行 python scripts/etl/fetch.py）")

    for f in sorted(NORMALIZED_DIR.glob("*.json")):
        if f.name == "VERSIONS.json":
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        for path, value, prov in _leaf_paths(doc, f.name.replace(".json", "")):
            # 溯源合法性
            errors += validate_provenance(prov, path)
            # 版本一致性：srr@/tbgd@ 须命中 VERSIONS.json 的 sha 前缀；biligame- 为 wiki 版本号
            if prov.version:
                if prov.version.startswith(("srr@", "tbgd@")):
                    known = []
                    for k, v in versions.items():
                        tag = "srr" if "StarRailRes" in k else ("tbgd" if "TurnBasedGameData" in k else k)
                        known.append(f"{tag}@{v.get('sha', '')[:7]}")
                    if prov.version not in known:
                        errors.append(f"{path}: version={prov.version!r} 不在 VERSIONS.json 中（{known}）")
                elif not prov.version.startswith("biligame-"):
                    errors.append(f"{path}: version={prov.version!r} 格式未知（应 srr@/tbgd@<sha 前 7 位> 或 biligame-<日期>）")
            # 未验证值收集
            if not prov.is_trusted():
                unverified.append((path, prov.source_trust, prov.validation))

    # 引用完整：skills.json 的 _upstream_ids 前缀 = 角色 ID
    skills_file = NORMALIZED_DIR / "skills.json"
    if skills_file.exists():
        skills = json.loads(skills_file.read_text(encoding="utf-8"))
        for cid, slots in skills.items():
            for slot, s in slots.items():
                if slot.startswith("_"):
                    continue
                for sid in (s.get("_upstream_ids") or []):
                    if not str(sid).startswith(str(cid)):
                        errors.append(f"skills.{cid}.{slot}: 上游 SkillID {sid} 前缀 ≠ 角色 ID {cid}")

    return errors, unverified


def main(argv: List[str]) -> int:
    errors, unverified = audit()
    if errors:
        print(f"AUDIT FAIL：{len(errors)} 处违规")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print(f"AUDIT PASS：溯源合法")
    if unverified:
        print(f"\n未验证值（source_trust=D 或 validation=raw）：{len(unverified)} 处")
        for path, trust, val in unverified:
            print(f"  ~ {path}  [{trust}/{val}]")
    else:
        print("\n未验证值：0 处 —— 全部输入可信")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
