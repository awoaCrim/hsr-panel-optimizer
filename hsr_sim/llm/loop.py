"""LLM 迭代循环：提方案 → 程序验证 → 反馈 → 收敛（最多 5 轮）。

协议（docs/game-knowledge.md 1.4 节）：
1. 首轮：LLM 输出完整方案（面板 + 配速 + 循环）
2. 迭代：程序验证 → 主指标/约束/诊断 → LLM 依据反馈调整
3. 收敛：击杀且约束全达标；5 轮未收敛输出最近方案 + 差距清单
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine.policy_search import search_policy, search_summary
from ..engine.simulate import Simulator
from ..loader import DATA_DIR, load_enemies, load_rotation, load_substat_counts, load_team
from ..model import Rotation
from ..report import ValidationReport, build_report, format_report
from .client import LLMClient

MAX_ROUNDS = 5

SYSTEM_PROMPT = """你是《崩坏：星穹铁道》资深数值策划，任务是为指定队伍输出全队最佳面板方案。

## 队伍角色基础属性（80 级白值，不含光锥/遗器）
- 1015 红A（量子巡猎）：攻击 621、速度 105、基础双暴 5%/50%
- 1306 花火（量子同谐）：攻击 524、速度 101、基础双暴 5%/50%
- 1309 知更鸟（物理同谐）：攻击 640、速度 102、基础双暴 5%/50%
- 8007 记忆主（冰记忆）：攻击 543、速度 103、基础双暴 5%/50%
光锥模板：攻击 +582、攻击 +20%。

## 词条规则（关键约束）
- 副词条预算：每人 ≤ 30 有效词条，超预算=方案无效
- 每词条价值：攻击 4.32% / 速度 2.4 / 暴击率 3.24% / 暴伤 6.48% / 击破 6.48% / 充能 3.24%
- 主词条（4 件，不计入预算）：衣 暴击32.4%/暴伤64.8%/攻击43.2%；鞋 速度25/攻击43.2%；
  球 属性伤38.8%/攻击43.2%；绳 充能19.4%/击破64.8%/攻击43.2%
- 最终面板 = 白值 + 光锥 + 主词条 + 副词条（程序装配，你只分配词条）

## 游戏规则摘要（关键公式）
- 期望伤害 = 技能倍率 × 攻击力 × (1+增伤) × (1+暴击率×暴伤) × 防御乘区 × 抗性乘区 × (1+真伤)
- 防御乘区 = (200+10×攻方等级) / (敌人防御×(1-无视防御) + 200+10×攻方等级)
- 行动值 AV = 10000/速度；混沌回忆 2T = 250 AV（首轮 150 + 次轮 100）
- 能量：普攻 20 / 战技 30 / 大招 5（×充能效率）；终结技能量满时即时释放（不占行动条）
- SP：上限 5（花火在场 +2 = 7）、开局 4；普攻 +1、战技 -1；SP 不能为负
- 拉条：花火战技 50%（目标=行动指令 target）；知更鸟大招全队 100% 立即行动
- 红A：战技【回路连接】后本回合不结束（可连打，主动 5 次后退出），战技伤害 +100%/层叠 2 层；
  大招 220 能量，给 2 点充能；天赋：队友攻击后消耗 1 充能追击（200% 攻击）+ 回 1 SP + 回 5 能量
- 记忆主：迷迷独立行动（速度 130）；战技迷迷充能 10%、大招充能 40% + 迷迷全体 240% 伤害；
  迷迷在场全队真伤 +10%；充能满 100% 时迷迷行动强化（×1.6）
- 知更鸟：战技全队增伤 50%（3 回合）；大招全队立即行动 + 协奏 2 回合
  （每次我方攻击后附加知更鸟攻击 120% 的固定双暴伤害）+ 全队攻击 +22.8%+200
- 花火：战技拉条 50% + 目标暴伤 +93%（1 回合）；大招回 4 SP；天赋 SP 上限 +2、每耗 1 SP 全队增伤 6% 叠 3 层

## 输出契约
只输出一个合法 JSON 对象，格式：
{
  "builds": { "<角色ID>": {
      "main_stats": {"body": "crit_dmg|crit_rate|atk_pct", "feet": "speed|atk_pct",
                     "sphere": "<属性>_dmg|atk_pct", "rope": "energy_regen|break_effect|atk_pct"},
      "substats": {"atk_pct": 8, "speed": 2, "crit_rate": 10, "crit_dmg": 8, "energy_regen": 2}
  } },
  "speed_targets": { "<角色ID>": 134.0 },
  "rotation": { "policy": { "<角色ID>": {
      "ult": "on_full|off", "chain_max": 5, "skill_budget": 2, "pull_target": "<拉条目标ID>"
  } } },
  "reason": "中文调整思路"
}
规则：
- substats 是副词条词条数（每角色总和 ≤ 30）；速度断点靠词条+主词条实现
- rotation.policy 是战斗决策规则（可省略，程序会搜索最优策略并反馈）：
  chain_max=红A回路连打上限（连打中自动重复战技）；skill_budget=整场战技次数预算（辅助 SP 分配）；
  ult=on_full 能量满即时释放（不占行动条）
- 大招不占行动条：能量满自动放，无需排入行动序列
- speed_targets 是验证速度断点的目标值
"""


@dataclass
class IterationResult:
    rounds: int = 0
    converged: bool = False
    reports: List[ValidationReport] = field(default_factory=list)
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    final_team: Optional[Dict[str, Any]] = None
    final_rotation: Optional[Dict[str, Any]] = None


def _snapshot_team(team_path: Path, rotation_path: Path) -> Dict[str, Any]:
    return {
        "team": json.loads(team_path.read_text(encoding="utf-8")),
        "rotation": json.loads(rotation_path.read_text(encoding="utf-8")),
    }


def _restore_team(team_path: Path, rotation_path: Path, snap: Dict[str, Any]) -> None:
    team_path.write_text(json.dumps(snap["team"], ensure_ascii=False, indent=2), encoding="utf-8")
    rotation_path.write_text(json.dumps(snap["rotation"], ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_proposal(team_path: Path, rotation_path: Path, proposal: Dict[str, Any]) -> None:
    team = json.loads(team_path.read_text(encoding="utf-8"))
    rot = json.loads(rotation_path.read_text(encoding="utf-8"))
    if "builds" in proposal:
        team["builds"] = proposal["builds"]
    if "speed_targets" in proposal:
        team["speed_targets"] = proposal["speed_targets"]
    if "rotation" in proposal:
        if "policy" in proposal["rotation"]:
            rot["policy"] = proposal["rotation"]["policy"]
        if "actions" in proposal["rotation"]:
            rot["actions"] = proposal["rotation"]["actions"]
    team_path.write_text(json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")
    rotation_path.write_text(json.dumps(rot, ensure_ascii=False, indent=2), encoding="utf-8")


def _feedback_text(report: ValidationReport) -> str:
    lines = [f"验证结果（第 {len(report.diagnostics) + 1} 项诊断）：", f"主指标：2T 总伤害 {report.score:,.0f} / 敌人 HP {report.enemy_total_hp:,.0f}"]
    for c in report.constraints:
        lines.append(f"- [{'达标' if c.met else '未达'}] {c.name}：{c.detail}")
    for d in report.diagnostics:
        lines.append(f"- 诊断：{d}")
    return "\n".join(lines)


def _compact_team(snap: Dict[str, Any]) -> Dict[str, Any]:
    """精简方案：只传面板/断点/策略，去掉 note 等噪音（控制 prompt 体积）。"""
    team = snap.get("team", {})
    rot = snap.get("rotation", {})
    return {
        "builds": {
            k: {kk: vv for kk, vv in v.items() if kk != "note"}
            for k, v in team.get("builds", {}).items()
        },
        "speed_targets": team.get("speed_targets", {}),
        "rotation": {"policy": rot.get("policy", {})},
    }


def _mem_speed_for(characters) -> float:
    for c in characters.values():
        mem = c.talent_extra.get("memosprite")
        if mem:
            return float(mem.get("speed", 130.0))
    return 130.0


def run_iteration(
    client: LLMClient,
    team_path: Path,
    enemy_path: Path,
    rotation_path: Path,
    max_rounds: int = MAX_ROUNDS,
    verbose: bool = True,
    policy_search: bool = True,
) -> IterationResult:
    snapshot = _snapshot_team(team_path, rotation_path)
    result = IterationResult()
    enemy_text = json.dumps(
        {k: {kk: vv for kk, vv in v.items() if kk != "note"}
         for k, v in json.loads(enemy_path.read_text(encoding="utf-8")).get("enemies", {}).items()},
        ensure_ascii=False,
    )
    system_msg = {"role": "system", "content": SYSTEM_PROMPT}

    try:
        for rnd in range(1, max_rounds + 1):
            characters, stats, speed_targets = load_team(team_path, DATA_DIR / "characters")
            enemies, level, target_av = load_enemies(enemy_path)
            rotation = load_rotation(rotation_path)
            mem_speed = _mem_speed_for(characters)

            # 程序策略搜索：当前面板下最优战斗策略（面板验证基于最优打法，ADR-0005）
            if policy_search:
                search = search_policy(characters, stats, enemies, level, target_av, mem_speed)
                best_rot = Rotation(policy=search.best_policy)
            else:
                search = None
                best_rot = rotation
            sim = Simulator(characters, stats, enemies, best_rot, target_av, level, mem_speed)
            sim_result = sim.run()
            report = build_report(
                sim_result, sum(e.hp for e in enemies.values()),
                speed_targets, {c: s.speed for c, s in stats.items()},
                substat_counts=load_substat_counts(team_path),
                substat_budget=json.loads(team_path.read_text(encoding="utf-8")).get("substat_budget", 30.0),
            )
            # LLM 自定义策略对比：若磁盘策略与程序最优不同，额外验证并附诊断
            if search is not None and rotation.policy and search.best_policy:
                if any(
                    rotation.policy[cid] != search.best_policy.get(cid)
                    for cid in set(rotation.policy) | set(search.best_policy)
                ):
                    sim2 = Simulator(characters, stats, enemies, rotation, target_av, level, mem_speed)
                    r2 = build_report(
                        sim2.run(), sum(e.hp for e in enemies.values()),
                        speed_targets, {c: s.speed for c, s in stats.items()},
                        substat_counts=load_substat_counts(team_path),
                        substat_budget=json.loads(team_path.read_text(encoding="utf-8")).get("substat_budget", 30.0),
                    )
                    report.diagnostics.append(
                        f"LLM 策略伤害 {r2.score:,.0f} vs 程序最优策略 {report.score:,.0f}"
                    )
            result.reports.append(report)

            if verbose:
                print(f"\n===== 第 {rnd} 轮验证 =====")
                print(format_report(report))

            if report.all_met:
                result.converged = True
                result.rounds = rnd
                if verbose:
                    print(f"\n✅ 第 {rnd} 轮收敛：击杀且约束全达标")
                break

            search_text = search_summary(search) if search is not None else "（策略搜索未启用）"

            current = _compact_team(_snapshot_team(team_path, rotation_path))
            # 每轮独立上下文（不累积历史）：当前方案 + 反馈已含全部信息，控制 prompt 体积
            history = [
                system_msg,
                {
                    "role": "user",
                    "content": (
                        f"当前靶场：{enemy_text}\n"
                        f"当前方案：{json.dumps(current, ensure_ascii=False)}\n"
                        f"上一轮验证反馈：\n{_feedback_text(report)}\n"
                        f"策略参考：\n{search_text}\n"
                        "请输出调整后的完整方案 JSON（修正未达标项；本轮可继续优化伤害）。"
                    ),
                },
            ]
            proposal = client.chat_json(history)
            result.proposals.append(proposal)
            _apply_proposal(team_path, rotation_path, proposal)
            if verbose:
                print(f"   LLM 第 {rnd} 轮方案：{json.dumps(proposal.get('reason', {}), ensure_ascii=False)}")
        else:
            result.rounds = max_rounds
    finally:
        # 恢复磁盘上的基线（迭代结果是内存态，避免污染手填基线）
        _restore_team(team_path, rotation_path, snapshot)

    if not result.converged:
        last = result.reports[-1] if result.reports else None
        if verbose and last is not None:
            print("\n⚠️  5 轮未收敛。最近方案差距：")
            print(format_report(last))
        result.final_team = snapshot["team"]
    else:
        result.final_team = result.proposals[-1] if result.proposals else snapshot["team"]
    result.final_rotation = snapshot["rotation"]
    return result
