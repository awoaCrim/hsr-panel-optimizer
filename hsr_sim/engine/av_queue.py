"""行动队列（Action Value 模型）——排轴核心，通用多单位支持。

模型：行动距离（distance，初始 10000）与速度（speed），
剩余行动值 AV = distance / speed。速度变化时 distance 不变（时间重算），
拉条 = distance 按比例减少，推条 = 按比例增加。

单位类型不限（角色/忆灵/敌人走同一队列）——为忆灵召唤与后续
多波次敌人预留。参考 Honkai-Star-Rail-Simulator 的 AV 分段模型。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


ACTION_DISTANCE = 10000.0


@dataclass
class QueueEntry:
    unit_id: str
    distance: float = ACTION_DISTANCE
    speed: float = 100.0

    @property
    def av(self) -> float:
        return self.distance / self.speed


class ActionQueue:
    """按剩余行动值排序的通用行动队列。"""

    def __init__(self) -> None:
        self._entries: Dict[str, QueueEntry] = {}
        self._heap: List[Tuple[float, int, str]] = []
        self._seq = 0

    # ---- 单位管理 ----
    def add(self, unit_id: str, speed: float, distance: float = ACTION_DISTANCE) -> None:
        self._entries[unit_id] = QueueEntry(unit_id, distance, speed)
        self._push(unit_id)

    def remove(self, unit_id: str) -> None:
        self._entries.pop(unit_id, None)

    def has(self, unit_id: str) -> bool:
        return unit_id in self._entries

    def get_speed(self, unit_id: str) -> Optional[float]:
        e = self._entries.get(unit_id)
        return e.speed if e else None

    # ---- 速度 / 距离操作 ----
    def set_speed(self, unit_id: str, speed: float) -> None:
        e = self._entries.get(unit_id)
        if e:
            e.speed = speed
            self._push(unit_id)

    def advance(self, unit_id: str, pct: float) -> None:
        """拉条：剩余距离按比例减少（花火战技 0.5 / 知更鸟大招 1.0）。"""
        e = self._entries.get(unit_id)
        if e:
            e.distance = max(0.0, e.distance * (1.0 - pct))
            self._push(unit_id)

    def postpone(self, unit_id: str, pct: float) -> None:
        """推条/行动延后：剩余距离按比例增加（击破延后 0.25）。"""
        e = self._entries.get(unit_id)
        if e:
            e.distance = e.distance * (1.0 + pct)
            self._push(unit_id)

    def reset_after_action(self, unit_id: str) -> None:
        """行动后重置行动距离（进入下一轮行动）。"""
        e = self._entries.get(unit_id)
        if e:
            e.distance = ACTION_DISTANCE
            self._push(unit_id)

    def keep_acting(self, unit_id: str) -> None:
        """额外行动：不重置距离（distance 保持 0，立即再行动）。"""
        e = self._entries.get(unit_id)
        if e:
            e.distance = 0.0
            self._push(unit_id)

    # ---- 时间推进 ----
    def next(self) -> Optional[Tuple[str, float]]:
        """返回下一个行动单位及其剩余行动值（相对时间增量）。"""
        while self._heap:
            av, _seq, unit_id = self._heap[0]
            e = self._entries.get(unit_id)
            if e is None or abs(e.av - av) > 1e-9:
                heapq.heappop(self._heap)  # 过期条目
                continue
            return unit_id, av
        return None

    def advance_time(self, av: float) -> None:
        """全局推进 av：所有单位剩余距离 -= av × speed，并重建堆。"""
        for e in self._entries.values():
            e.distance = max(0.0, e.distance - av * e.speed)
        self._heap = []
        for unit_id in self._entries:
            self._push(unit_id)

    def _push(self, unit_id: str) -> None:
        e = self._entries.get(unit_id)
        if e is None:
            return
        self._seq += 1
        heapq.heappush(self._heap, (e.av, self._seq, unit_id))

    def __len__(self) -> int:
        return len(self._entries)

    def snapshot(self) -> Dict[str, float]:
        return {uid: e.av for uid, e in self._entries.items()}
