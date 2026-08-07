"""策略搜索器 —— 在当前面板下枚举战斗决策参数空间，找 2T 总伤害最优策略。

背景（ADR-0005）：战斗决策（大招时机/普攻战技/连打次数/SP 分配）是真实优化空间，
写死的循环序列会让面板优化基于次优打法。策略搜索把决策参数化并枚举：

决策维度（每维都是真实战斗决策）：
- 红A 回路连打上限 chain_max（连打越多 SP/能量压力越大）
- 辅助战技预算 skill_budget（SP 分配给谁：花火拉条 vs 知更鸟 buff vs 记忆主充能）
- 大招开关 ult（开大占能量与行动优先级，但即时释放不占行动条）

全枚举量：chain(4) × 花火(3×2) × 知更鸟(3×2) × 记忆主(3×2) = 864 次模拟（~2 秒）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..model import CharacterData, CharacterPolicy, Enemy, Rotation, Stats
from .simulate import Simulator

DEFAULT_SPACE = {
    "1015": {"chain_max": [3, 4, 5, 6]},
    "1306": {"skill_budget": [1, 2, 3], "ult": ["on_full", "off"]},
    "1309": {"skill_budget": [0, 1, 2], "ult": ["on_full", "off"]},
    "8007": {"skill_budget": [0, 1, 2], "ult": ["on_full", "off"]},
}


@dataclass
class SearchResult:
    best_policy: Dict[str, CharacterPolicy] = field(default_factory=dict)
    best_score: float = 0.0
    best_sp_min: float = 0.0
    best_ult_count: Dict[str, int] = field(default_factory=dict)
    evaluated: int = 0
    valid: int = 0
    score_by_key: Dict[str, float] = field(default_factory=dict)


def _policy_key(policy: Dict[str, CharacterPolicy]) -> str:
    return ";".join(
        f"{cid}:u{p.ult[0]},c{p.chain_max},b{p.skill_budget}" for cid, p in sorted(policy.items())
    )


def _iter_combinations(
    characters: Dict[str, CharacterData], space: Dict
) -> List[Dict[str, CharacterPolicy]]:
    """展开参数空间 → 策略组合列表。"""
    cids = list(characters)
    buckets: List[List[Tuple[str, CharacterPolicy]]] = []
    for cid in cids:
        opts = space.get(cid, {})
        choices: List[Tuple[str, CharacterPolicy]] = []
        chain_maxes = opts.get("chain_max", [0])
        budgets = opts.get("skill_budget", [999])
        ults = opts.get("ult", ["on_full"])
        for chain in chain_maxes:
            for budget in budgets:
                for ult in ults:
                    choices.append((cid, CharacterPolicy(
                        ult=ult, chain_max=chain, skill_budget=budget,
                    )))
        buckets.append(choices)
    import itertools
    combos = []
    for combo in itertools.product(*buckets):
        combos.append({cid: p for cid, p in combo})
    return combos


def _simulate_once(
    characters, stats, enemies, level, target_av, mem_speed, policy
):
    rot = Rotation(policy=policy)
    sim = Simulator(characters, stats, enemies, rot, target_av, level, mem_speed)
    res = sim.run()
    return res


def search_policy(
    characters: Dict[str, CharacterData],
    stats: Dict[str, Stats],
    enemies: Dict[str, Enemy],
    level: int = 90,
    target_av: float = 250.0,
    memosprite_speed: float = 130.0,
    space: Optional[Dict] = None,
    require_sp_nonneg: bool = True,
) -> SearchResult:
    """枚举策略参数空间，返回 2T 总伤害最优且约束达标的策略。"""
    space = space or DEFAULT_SPACE
    result = SearchResult()
    for policy in _iter_combinations(characters, space):
        res = _simulate_once(characters, stats, enemies, level, target_av,
                             memosprite_speed, policy)
        result.evaluated += 1
        sp_ok = res.sp_min >= -1e-9 if require_sp_nonneg else True
        # 硬过滤：SP 非负（SP 预算真实可行）；能量不达标不淘汰（伤害优先，由报告约束展示）
        if not sp_ok:
            continue
        result.valid += 1
        key = _policy_key(policy)
        result.score_by_key[key] = res.total_damage
        if res.total_damage > result.best_score:
            result.best_score = res.total_damage
            result.best_policy = policy
            result.best_sp_min = res.sp_min
            result.best_ult_count = res.ult_count
    return result


def search_summary(result: SearchResult) -> str:
    """搜索结果的诊断文本（反馈给 LLM 的策略参考）。"""
    if not result.best_policy:
        return "策略搜索：参数空间内无 SP 达标策略（SP 约束过紧）"
    parts = [f"{cid}:ult={'开' if p.ult == 'on_full' else '关'}" +
             (f",连打{p.chain_max}" if p.chain_max else "") +
             (f",战技预算{p.skill_budget}" if p.skill_budget < 999 else "")
             for cid, p in sorted(result.best_policy.items())]
    return (
        f"策略搜索：程序枚举 {result.evaluated} 个策略（SP 达标 {result.valid} 个），"
        f"最优 [{'; '.join(parts)}] → 2T 伤害 {result.best_score:,.0f}（SP 最低 {result.best_sp_min:g}）"
    )
