"""验证报告 —— LLM 迭代契约（主指标 + 约束清单 + 诊断）。

收敛条件：击杀且约束全达标；5 轮未收敛则输出最近方案 + 差距清单。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .engine.simulate import SimResult


@dataclass
class ConstraintCheck:
    name: str
    met: bool
    detail: str = ""


@dataclass
class ValidationReport:
    score: float                                  # 主指标：2T 总期望伤害
    enemy_total_hp: float
    constraints: List[ConstraintCheck] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    result: SimResult | None = None

    @property
    def all_met(self) -> bool:
        return all(c.met for c in self.constraints)


def build_report(
    result: SimResult,
    enemy_total_hp: float,
    speed_targets: Dict[str, float],
    actual_speeds: Dict[str, float],
    substat_counts: Dict[str, float] | None = None,
    substat_budget: float = 30.0,
) -> ValidationReport:
    constraints: List[ConstraintCheck] = []

    # 约束 1：2T 内击杀
    killed = result.total_damage >= enemy_total_hp
    constraints.append(ConstraintCheck(
        "2T击杀",
        killed,
        f"总伤害 {result.total_damage:,.0f} / 敌人总HP {enemy_total_hp:,.0f}"
        + (f"（缺口 {enemy_total_hp - result.total_damage:,.0f}）" if not killed else ""),
    ))

    # 约束 2：SP 全程非负
    sp_ok = result.sp_min >= 0.0
    constraints.append(ConstraintCheck(
        "SP非负",
        sp_ok,
        f"SP 最低点 {result.sp_min:.0f}" + ("（见底位置见诊断）" if not sp_ok else ""),
    ))

    # 约束 3：能量充足（所有大招均成功施放）
    energy_ok = len(result.energy_shortfalls) == 0
    constraints.append(ConstraintCheck(
        "能量充足",
        energy_ok,
        f"大招施放 {sum(result.ult_count.values())} 次"
        + (f"，能量不足 {len(result.energy_shortfalls)} 处" if not energy_ok else ""),
    ))

    # 约束 4：速度断点
    for cid, target in speed_targets.items():
        actual = actual_speeds.get(cid, 0.0)
        met = actual >= target - 1e-9
        constraints.append(ConstraintCheck(
            f"速度断点{cid}",
            met,
            f"目标 {target:g}，实际 {actual:g}" + (f"（差 {target - actual:.1f}）" if not met else ""),
        ))

    # 约束 5：词条预算（面板可实现性）
    if substat_counts:
        over = {cid: c - substat_budget for cid, c in substat_counts.items() if c > substat_budget}
        budget_ok = not over
        detail = "、".join(f"{cid} {c:g}/{substat_budget:g}" for cid, c in substat_counts.items())
        if over:
            detail += "；超预算：" + "、".join(f"{cid} +{v:.1f}" for cid, v in over.items())
        constraints.append(ConstraintCheck("词条预算", budget_ok, detail))

    # 诊断
    diag: List[str] = []
    if not sp_ok:
        low = [f"t={t:.1f} SP={sp:.0f}" for t, sp in result.sp_timeline if sp <= 0.0]
        diag.append("SP 见底：" + "、".join(low))
    for t, cid, have, need in result.energy_shortfalls:
        diag.append(f"t={t:.1f} {cid} 能量不足：{have:.0f}/{need:.0f}（大招被跳过，继续攒能）")
    for cid, target in speed_targets.items():
        actual = actual_speeds.get(cid, 0.0)
        if actual < target - 1e-9:
            diag.append(f"{cid} 速度差 {target - actual:.1f} 达断点（约需 {int((target - actual) / 2.4 + 0.5)} 个速度词条）")
    if result.damage_by_source:
        top = max(result.damage_by_source.items(), key=lambda kv: kv[1])
        share = top[1] / result.total_damage * 100 if result.total_damage > 0 else 0
        diag.append(f"伤害占比：{top[0]} {share:.0f}%（{top[1]:,.0f}）")
    if substat_counts:
        for cid, c in substat_counts.items():
            if c > substat_budget:
                diag.append(f"{cid} 词条超预算：{c:g} > {substat_budget:g}（需减 {c - substat_budget:.1f} 词条）")
    if not killed:
        diag.append(f"距离击杀还差 {enemy_total_hp - result.total_damage:,.0f} 伤害"
                    f"（约 {max(1, int((enemy_total_hp - result.total_damage) / max(result.total_damage, 1) * 100))}% 提升空间）")

    return ValidationReport(
        score=result.total_damage,
        enemy_total_hp=enemy_total_hp,
        constraints=constraints,
        diagnostics=diag,
        result=result,
    )


def format_report(report: ValidationReport, lang: str = "zh") -> str:
    """终端中文摘要。"""
    lines = [
        "=" * 52,
        f"2T 总期望伤害：{report.score:,.0f}  /  敌人总HP：{report.enemy_total_hp:,.0f}",
        f"击杀：{'[达标]' if report.constraints[0].met else '[未达]'}  SP：{'[达标]' if report.constraints[1].met else '[未达]'}  "
        f"能量：{'[达标]' if report.constraints[2].met else '[未达]'}",
        "-" * 52,
    ]
    # 信任度信封（ADR-0006 6.2）：输入含 D/raw 值 → 结果标注未验证
    if report.result is not None and report.result.trust_level == "unverified":
        lines.append(f"⚠ 未验证：{len(report.result.unverified_inputs)} 处输入为手填/待核对"
                     f"（示例：{report.result.unverified_inputs[0]}）——结果仅参考，不可作为游戏真值")
        lines.append("-" * 52)
    for c in report.constraints:
        lines.append(f"[{'达标' if c.met else '未达'}] {c.name}：{c.detail}")
    if report.diagnostics:
        lines.append("-" * 52)
        lines.append("诊断：")
        for d in report.diagnostics:
            lines.append(f"  · {d}")
    lines.append("=" * 52)
    return "\n".join(lines)
