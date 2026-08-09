"""效果模型（ADR-0006 5.2 / ADR-0007 D2）—— 技能 = Effects[]，角色机制属于数据层。

执行顺序语义（E1）：effects 列表顺序 = v1.5 结算链顺序（伤害 → 天赋钩子 → SP → 能量
→ 削韧 → 拉条），翻译器保证与 legacy 行为逐项等价（T2a parity）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..model import SkillData


@dataclass
class Effect:
    """效果基类（不可变数据）。"""

    note: str = ""


# ---------- 主链效果（v1.5 SkillData 固有字段） ----------

@dataclass
class DamageEffect(Effect):
    """主伤害（行动级；触发 on_ally_attack 链）。"""
    mult: float = 0.0
    kind: str = "normal"            # normal / followup / additional / break


@dataclass
class SPChangeEffect(Effect):
    """SP 变化（普攻 +1 / 战技 -1 / 花火大招 +4 合并）。"""
    delta: int = 0
    counts_as_spent: bool = False   # 花火天赋：每耗 1 SP 全队增伤（v1.5 仅 skill.sp<0 计数）


@dataclass
class EnergyGainEffect(Effect):
    """回能（× 充能效率）。"""
    amount: float = 0.0


@dataclass
class EnergyCostEffect(Effect):
    """大招能量消耗（ult_count 在此 +1）。"""
    amount: float = 0.0


@dataclass
class ToughnessEffect(Effect):
    """削韧（仅弱点属性生效，量 = amount）。"""
    amount: float = 0.0


@dataclass
class AdvanceEffect(Effect):
    """拉条：目标 = 友方目标（ally_target）或自身。"""
    pct: float = 0.0


@dataclass
class ExtraActionEffect(Effect):
    """额外行动（红A 回路连接）：不结束回合；链上限由策略/天赋决定。"""
    max_chain: int = 0              # 0 = 用天赋默认（extra_action_max）


# ---------- 天赋钩子效果（v1.5 talent_extra.skill_effects） ----------

@dataclass
class AdvanceAllEffect(Effect):
    """全队拉条（知更鸟大招：除施放者外立即行动）。"""
    pct: float = 0.0


@dataclass
class FateChargeEffect(Effect):
    """充能资源（红A：上限 4，追击消耗 1）。"""
    amount: float = 0.0
    cap: float = 4.0


@dataclass
class ConcertEffect(Effect):
    """协奏（知更鸟）：附加伤害倍率 + 持续轮数（按行动递减）。"""
    rounds: int = 0
    additional_mult: float = 0.72


@dataclass
class MemospriteChargeEffect(Effect):
    """忆灵充能（迷迷：0-100）。"""
    amount: float = 0.0             # 比例（0.5 = 50）


@dataclass
class MemospriteImmediateEffect(Effect):
    """忆灵立即行动（迷迷：距离清零，插队到下一行动）。"""


@dataclass
class BuffEffect(Effect):
    """增益：stat/value/target/duration/cap（复用 BuffManager 语义）。

    target == "advance_target" 时由执行器解析为运行时友方目标。
    """
    stat: str = ""
    value: float = 0.0
    target: str = ""
    duration: int = 1
    cap: float = 0.0


@dataclass
class AuraEffect(Effect):
    """常驻光环（duration=0，如迷迷在场全队真伤）。"""
    stat: str = ""
    value: float = 0.0


# ---------- 翻译器（v1.5 SkillData/talent_extra → Effects[]） ----------

def skill_to_effects(action_name: str, skill: SkillData, talent_extra: Dict) -> List[Effect]:
    """把 legacy 技能数据翻译为效果列表。

    顺序 = v1.5 结算链（damage → 天赋钩子组 → SP → 能量 → 削韧 → 拉条 → 额外行动），
    与 _character_act / _execute_ult 的 legacy 行为逐项对应（T2a parity 前提）。
    """
    effs: List[Effect] = []

    # 1. 主伤害（触发 on_ally_attack）；kind 区分技能类型（ult 专属乘区/报告用）
    if skill.mult > 0.0:
        effs.append(DamageEffect(mult=skill.mult, kind="ult" if action_name == "ult" else "normal"))

    # 2. 天赋钩子（顺序与 v1.5 _apply_skill_effects 一致）
    se = talent_extra.get("skill_effects", {}).get(action_name, {})
    if se.get("advance_all"):
        effs.append(AdvanceAllEffect(pct=se["advance_all"]))
    if se.get("fate_charge"):
        effs.append(FateChargeEffect(amount=se["fate_charge"]))
    if se.get("concert"):
        effs.append(ConcertEffect(rounds=se["concert"],
                                  additional_mult=se.get("additional_mult", 0.72)))
    if se.get("mem_charge"):
        effs.append(MemospriteChargeEffect(amount=se["mem_charge"]))
    if se.get("mem_immediate"):
        effs.append(MemospriteImmediateEffect())
    if se.get("buff"):
        b = se["buff"]
        effs.append(BuffEffect(
            stat=b["stat"], value=b["value"],
            target=b.get("target", ""),           # "advance_target" 执行时解析
            duration=b.get("duration", 1), cap=b.get("cap", 0.0),
        ))
    if se.get("true_dmg_aura"):
        effs.append(AuraEffect(stat="true_dmg", value=se["true_dmg_aura"]))
    if se.get("atk_flat"):
        # v1.5：协奏 +200 攻击固定加成，duration 沿用 buff 的（默认 2）
        effs.append(BuffEffect(stat="atk_flat", value=se["atk_flat"],
                               duration=se.get("buff", {}).get("duration", 2)))

    # 3. SP（合并 skill.sp + sp_bonus，保持 v1.5 单点时间线）
    delta = skill.sp + getattr(skill, "sp_bonus", 0)
    if delta:
        effs.append(SPChangeEffect(delta=delta, counts_as_spent=skill.sp < 0))

    # 4. 能量
    if skill.energy:
        effs.append(EnergyGainEffect(amount=skill.energy))
    if skill.energy_cost:
        effs.append(EnergyCostEffect(amount=skill.energy_cost))

    # 5. 削韧：只有实际攻击技能可削韧；非攻击机制技的解包参数不得生成幽灵削韧。
    if skill.mult > 0.0 and skill.toughness > 0.0:
        effs.append(ToughnessEffect(amount=skill.toughness))

    # 6. 拉条
    if skill.advance_pct > 0.0:
        effs.append(AdvanceEffect(pct=skill.advance_pct))

    # 7. 额外行动
    if skill.extra_action:
        default_max = talent_extra.get("skill_effects", {}).get(action_name, {}).get("extra_action_max", 0)
        effs.append(ExtraActionEffect(max_chain=default_max))

    return effs
