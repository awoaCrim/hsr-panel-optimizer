"""溯源数据模型 —— ADR-0006 4.2 双维信任模型。

- source_trust：来源信任（A datamine / B 社区整理 / C wiki / D 手填假设）
- validation：验证状态（raw / mapped / cross_checked / game_verified）

原始数据可信 ≠ 对原始数据的解释可信：一个值必须两维都达标
才能进入"可信结果"（source_trust ∈ {A,B,C} 且 validation ∈ {mapped, cross_checked, game_verified}）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

TRUST_LEVELS = ("A", "B", "C", "D")
VALIDATION_STATES = ("raw", "mapped", "cross_checked", "game_verified")

TRUSTED_SOURCES = {"A", "B", "C"}          # D = 手填/模板/假设，不可信
VALIDATED_STATES = {"mapped", "cross_checked", "game_verified"}  # raw = 语义未核对


@dataclass
class Provenance:
    """单值溯源。字段与 data/normalized 中包装对象的 key 一一对应。"""

    source: str = ""                 # datamine / starrailres / biligame / handfill
    source_trust: str = "D"          # A/B/C/D
    validation: str = "raw"          # raw/mapped/cross_checked/game_verified
    version: str = ""                # 上游固定版本（srr@<sha> / tbgd@<sha>）
    field: str = ""                  # 上游原始字段路径
    override: bool = False           # 人工修正/覆盖了上游值
    note: str = ""

    def is_trusted(self) -> bool:
        """两维都达标才算可信。"""
        return self.source_trust in TRUSTED_SOURCES and self.validation in VALIDATED_STATES

    def to_dict(self) -> Dict:
        return {
            "source_trust": self.source_trust,
            "validation": self.validation,
            "source": self.source,
            "version": self.version,
            "field": self.field,
            "override": self.override,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict, defaults: Optional[dict] = None) -> "Provenance":
        d = d or {}
        if defaults is None:
            defaults = {}
        return cls(
            source=d.get("source", defaults.get("source", "")),
            source_trust=d.get("source_trust", defaults.get("source_trust", "D")),
            validation=d.get("validation", defaults.get("validation", "raw")),
            version=d.get("version", defaults.get("version", "")),
            field=d.get("field", defaults.get("field", "")),
            override=d.get("override", defaults.get("override", False)),
            note=d.get("note", defaults.get("note", "")),
        )


def validate_provenance(p: Provenance, path: str) -> List[str]:
    """单值溯源合法性检查。返回违规清单（空 = 合法）。"""
    errs: List[str] = []
    if p.source_trust not in TRUST_LEVELS:
        errs.append(f"{path}: source_trust={p.source_trust!r} 非法（可选 A/B/C/D）")
    if p.validation not in VALIDATION_STATES:
        errs.append(f"{path}: validation={p.validation!r} 非法（可选 raw/mapped/cross_checked/game_verified）")
    if p.source_trust == "D" and p.validation in ("cross_checked", "game_verified"):
        errs.append(f"{path}: source_trust=D 不应带 {p.validation}（手填值无法交叉验证/实测）")
    return errs
