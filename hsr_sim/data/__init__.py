"""数据层（ADR-0006 L1）：溯源模型 + audit 门禁 + normalized 加载。

用法：
    python -m hsr_sim.data audit     # 审计 data/normalized/（P0-1 门禁）
    python -m hsr_sim.data paths     # 列出全部未验证字段（信任度信封预览）
"""
from .audit import audit
from .loader import NormalizedData, load
from .provenance import Provenance, TRUSTED_SOURCES, VALIDATED_STATES

__all__ = ["audit", "load", "NormalizedData", "Provenance", "TRUSTED_SOURCES", "VALIDATED_STATES"]
