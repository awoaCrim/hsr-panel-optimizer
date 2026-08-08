"""战斗状态快照（ADR-0007 3.1 / mechanics-spec E11）—— 回退任意行动的基础。

快照 = 模拟器全部可变状态的可序列化拷贝（act 边界粒度）。
undo 语义（D3）：回到最近一次我方主动行动前（敌人/忆灵行动自动回退，不单独成决策点）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class BattleSnapshot:
    """模拟器完整状态（字段与 Simulator 可变状态一一对应）。"""

    t: float = 0.0
    steps: int = 0
    sp: float = 4.0
    sp_max: float = 5.0
    energy: Dict[str, float] = field(default_factory=dict)
    toughness: Dict[str, float] = field(default_factory=dict)
    enemy_hp: Dict[str, float] = field(default_factory=dict)
    buffs: List = field(default_factory=list)              # Buff dataclass 列表
    fate_charge: Dict[str, float] = field(default_factory=dict)
    skill_used: Dict[str, int] = field(default_factory=dict)
    burst_chain: Dict[str, int] = field(default_factory=dict)
    sp_spent_count: int = 0
    concert_rounds: int = 0
    concert_additional_mult: float = 0.72
    memosprite: Optional[dict] = None
    memosprite_owner: str = ""
    skill_streak: Dict[str, int] = field(default_factory=dict)  # 连续战技计数（星魂 E1）
    queue_entries: Dict[str, Tuple[float, float]] = field(default_factory=dict)  # unit -> (distance, speed)
    sp_timeline: List[Tuple[float, float]] = field(default_factory=list)
    damage_events: List = field(default_factory=list)
    log: List = field(default_factory=list)
    breaks: List[Tuple[float, str]] = field(default_factory=list)
    ult_count: Dict[str, int] = field(default_factory=dict)
    action_count: Dict[str, int] = field(default_factory=dict)
    rotation_actions: Dict[str, list] = field(default_factory=dict)  # 序列模式推进状态
    rng_state: Tuple = (0, ())                            # (seed, state)；E12：RNG 入状态
