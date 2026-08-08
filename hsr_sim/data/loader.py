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
