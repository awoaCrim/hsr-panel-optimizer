"""简化 buff 系统 —— 按"施加者行动次数"计时的增益。

v1 不实现完整 buff 时间轴（覆盖率模型留待后续），仅支持：
- 乘区加成（增伤/暴伤/攻击/真伤），target 为空 = 全队
- cap：同类增益叠加上限（红A 回路层数上限）
- duration 以施加者行动次数递减（0 = 常驻）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Buff:
    stat: str          # dmg_bonus / crit_dmg / atk_pct / true_dmg / concert_atk
    value: float
    source: str        # 施加者 unit_id
    duration: int      # 剩余次数（施加者行动后 -1；0 = 常驻）
    target: str = ""   # 生效目标 unit_id（空 = 全队）
    cap: float = 0.0   # 该 stat 同类叠加上限（0 = 无）


class BuffManager:
    def __init__(self) -> None:
        self._buffs: List[Buff] = []

    def add(self, stat: str, value: float, source: str, duration: int,
            target: str = "", cap: float = 0.0) -> None:
        if cap > 0.0:
            total = self.sum_for(stat, target)
            if total >= cap:
                return
            value = min(value, cap - total)
        self._buffs.append(Buff(stat, value, source, duration, target, cap))

    def tick_owner(self, source: str) -> None:
        """施加者行动一次：计时 buff 剩余次数 -1 并移除过期。"""
        keep: List[Buff] = []
        for b in self._buffs:
            if b.source == source and b.duration > 0:
                b.duration -= 1
                if b.duration > 0:
                    keep.append(b)
                continue
            keep.append(b)
        self._buffs = keep

    def sum_for(self, stat: str, target: str = "") -> float:
        return sum(
            b.value for b in self._buffs
            if b.stat == stat and (b.target == "" or b.target == target)
        )

    def get(self, stat: str, target: str = "") -> Optional[Buff]:
        for b in self._buffs:
            if b.stat == stat and (b.target == "" or b.target == target):
                return b
        return None
