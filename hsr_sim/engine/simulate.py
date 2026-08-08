"""前向模拟器：执行循环 → 输出 2T 内的伤害/SP/能量/行动记录。

机制覆盖（v1 全量，见 docs/adr/0004）：
- 多单位行动队列（角色/忆灵/敌人）
- 拉条：花火战技 50%、知更鸟大招全队 100%
- 忆灵：迷迷独立行动条 + 充能强化
- 真实伤害（记忆主）与附加伤害（知更鸟协奏）
- 击破：削韧 → 击破伤害 + 行动延后 25%
- 红A：战技【回路连接】额外行动、天赋追击（消耗充能立即攻击+回 SP）
- 花火：SP 上限 +2、每耗 1 SP 全队增伤
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from ..model import Action, CharacterData, CharacterPolicy, Enemy, Rotation, Stats
from .av_queue import ActionQueue
from .buffs import BuffManager
from .damage import Multipliers, break_damage, expected_damage, flat_damage
from .snapshot import BattleSnapshot
from .effects import (
    AdvanceAllEffect,
    AdvanceEffect,
    AuraEffect,
    BuffEffect,
    ConcertEffect,
    DamageEffect,
    Effect,
    EnergyCostEffect,
    EnergyGainEffect,
    ExtraActionEffect,
    FateChargeEffect,
    MemospriteChargeEffect,
    MemospriteDamageEffect,
    SPChangeEffect,
    ToughnessEffect,
    skill_to_effects,
)

DEFAULT_TARGET_AV = 250.0  # 2T = 首轮 150 + 次轮 100
BREAK_POSTPONE_PCT = 0.25


@dataclass
class DamageEvent:
    t: float
    source: str
    target: str
    amount: float
    kind: str  # normal / followup / additional / break


@dataclass
class ActionLog:
    t: float
    unit_id: str
    action: str
    detail: str = ""


@dataclass
class SimResult:
    t_end: float
    total_damage: float = 0.0
    damage_by_source: Dict[str, float] = field(default_factory=dict)
    damage_by_kind: Dict[str, float] = field(default_factory=dict)
    sp_timeline: List[Tuple[float, float]] = field(default_factory=list)
    sp_min: float = 0.0
    energy_shortfalls: List[Tuple[float, str, float, float]] = field(default_factory=list)
    actions: List[ActionLog] = field(default_factory=list)
    breaks: List[Tuple[float, str]] = field(default_factory=list)
    enemy_hp_left: Dict[str, float] = field(default_factory=dict)
    ult_count: Dict[str, int] = field(default_factory=dict)
    action_count: Dict[str, int] = field(default_factory=dict)
    trust_level: str = "trusted"             # trusted / unverified（ADR-0006 6.2）
    unverified_inputs: List[str] = field(default_factory=list)  # 参与计算的 D/raw 字段路径

    @property
    def enemies_killed(self) -> int:
        return sum(1 for hp in self.enemy_hp_left.values() if hp <= 0.0)


class Simulator:
    def __init__(
        self,
        characters: Dict[str, CharacterData],
        char_stats: Dict[str, Stats],
        enemies: Dict[str, Enemy],
        rotation: Rotation,
        target_av: float = DEFAULT_TARGET_AV,
        attacker_level: int = 80,
        memosprite_speed: float = 130.0,
        seed: int = 0,
        unverified_inputs: Optional[List[str]] = None,
    ) -> None:
        self.chars = characters
        self.stats = char_stats
        self.enemies = enemies
        self.rotation = rotation
        self.target_av = target_av
        self.attacker_level = attacker_level
        self.memosprite_speed = memosprite_speed
        self._unverified = list(unverified_inputs or [])   # 信任度信封（ADR-0006 6.2）
        self._reset(seed)

    def _reset(self, seed: int = 0) -> None:
        """初始化/重置全部可变状态（__init__ 与 restart 共用）。"""
        self.seed = seed
        self.rng = random.Random(seed)          # E12：RNG 入状态（暴击判定等随机源）
        self._snapshots: List[BattleSnapshot] = []

        self.queue = ActionQueue()
        for cid in self.chars:
            self.queue.add(cid, self.stats[cid].speed)
        for eid, e in self.enemies.items():
            self.queue.add(eid, e.speed)

        # 运行时状态
        self.t = 0.0
        self._steps = 0
        self.sp = 4.0
        self.sp_max = 5.0
        for cid, c in self.chars.items():
            self.sp_max += c.talent_extra.get("sp_cap_bonus", 0)
        self.energy: Dict[str, float] = {cid: 0.0 for cid in self.chars}
        self.toughness: Dict[str, float] = {eid: e.toughness for eid, e in self.enemies.items()}
        self.enemy_hp: Dict[str, float] = {eid: e.hp for eid, e in self.enemies.items()}
        self.buffs = BuffManager()
        self.damage_events: List[DamageEvent] = []
        self.sp_timeline: List[Tuple[float, float]] = [(0.0, self.sp)]
        self.shortfalls: List[Tuple[float, str, float, float]] = []
        self.log: List[ActionLog] = []
        self.breaks: List[Tuple[float, str]] = []
        self.ult_count: Dict[str, int] = {cid: 0 for cid in self.chars}
        self.action_count: Dict[str, int] = {cid: 0 for cid in self.chars}

        # 天赋运行时
        self.fate_charge: Dict[str, float] = {}          # 红A充能
        self.skill_used: Dict[str, int] = {cid: 0 for cid in self.chars}  # 战技使用计数（策略 skill_budget）
        self.burst_chain: Dict[str, int] = {}            # 额外行动链计数（红A 回路）
        self.sp_spent_count: int = 0                     # 花火天赋层数
        self.concert_rounds: int = 0                     # 知更鸟协奏剩余回合
        self.memosprite: Optional[dict] = None           # {charge, alive}
        self.memosprite_owner: str = ""

    # ---------- 主循环 ----------
    def run(self) -> SimResult:
        self._ensure_memosprite_summon()
        while self.run_step() is not None:
            pass
        return self._result()

    def run_step(self) -> Optional[str]:
        """执行一个行动边界：下一个单位行动 + 即时大招结算（含全部连锁）。

        返回行动单位 id；None = 推演结束（队列空 / AV 耗尽 / 超步熔断）。
        我方角色行动前自动压快照（undo 的决策点，ADR-0007 D3）。
        """
        self._ensure_memosprite_summon()
        nxt = self.queue.next()
        if nxt is None:
            return None
        unit_id, dt = nxt
        if self.t + dt > self.target_av:
            return None
        self._steps += 1
        if self._steps > 50000:
            raise RuntimeError(
                f"模拟超过 {self._steps} 步未结束：t={self.t:.1f} 单位={unit_id} dt={dt:.3f} "
                f"队列={self.queue.snapshot()} SP={self.sp:.0f}"
            )
        # 决策点：我方主动行动前压栈（undo 回到此处，敌人/忆灵行动自动回退）
        if unit_id in self.chars:
            self.push_act_snapshot()
        self.t += dt
        self.queue.advance_time(dt)

        if unit_id == "MEM":
            self._memosprite_act()
        elif unit_id in self.enemies:
            self._enemy_act(unit_id)
        elif unit_id in self.chars:
            self._character_act(unit_id)
        else:
            self.queue.reset_after_action(unit_id)

        # 终结技不占行动条：行动结算后检查能量，够则立即释放（星铁真实规则）
        self._try_immediate_ults()
        return unit_id

    def _ensure_memosprite_summon(self) -> None:
        """开局召唤忆灵（幂等：已召唤或队伍无召唤者则跳过）。"""
        if self.memosprite is None and self.memosprite_speed and any(
            c.talent_extra.get("summon") for c in self.chars.values()
        ):
            # 记忆主开局召唤迷迷
            owner = next(cid for cid, c in self.chars.items() if c.talent_extra.get("summon"))
            self.memosprite_owner = owner
            self.memosprite = {"charge": 0.0, "alive": True}
            self.queue.add("MEM", self.memosprite_speed)

    # ---------- 角色行动 ----------
    def _policy_decide(self, cid: str, pol: CharacterPolicy) -> Action:
        """策略模式决策：连打状态/战技预算/SP 阈值 → 动作。

        决策点（可被程序策略搜索优化）：
        - 回路连打中（burst_chain）→ 继续战技（SP 不足由主流程降级打断）
        - 战技预算用尽 → 普攻
        - SP 不足 → fallback 动作
        - 否则 → 战技（拉条目标取策略 pull_target）
        """
        if cid in self.burst_chain:
            return Action(unit_id=cid, action="skill")
        skill = self.chars[cid].skills.get("skill")
        if skill is None:
            return Action(unit_id=cid, action="basic")
        if self.skill_used.get(cid, 0) >= pol.skill_budget:
            return Action(unit_id=cid, action="basic")
        if skill.sp < 0 and self.sp < -skill.sp:
            return Action(unit_id=cid, action=pol.fallback)
        return Action(unit_id=cid, action="skill", target=pol.pull_target)

    def _character_act(self, cid: str) -> None:
        pol = self.rotation.policy.get(cid)
        if pol is not None:
            action = self._policy_decide(cid, pol)
        else:
            # 序列模式（v2 兼容）：消费写死的行动序列
            action = self.rotation.next_action(cid)
            if action is None:
                action = Action(unit_id=cid, action="basic")
        skill = self.chars[cid].skills.get(action.action)
        if skill is None:
            skill = self.chars[cid].skills["basic"]
            action = Action(unit_id=cid, action="basic")

        # 大招能量检查：能量不足则跳过该动作（保持行动链，继续打战技攒能），不降级普攻
        if action.action == "ult":
            # 终结技由即时释放机制负责（_try_immediate_ults）：轮到该槽时能量必然不足，跳过
            self.log.append(ActionLog(self.t, cid, "ult", detail="能量不足，跳过（攒能后即时释放）"))
            self.rotation.advance(cid)
            return

        # SP 检查：战技（sp<0）需足够 SP，不足则降级为普攻（游戏规则：SP 不足不能施放战技）
        if skill.sp < 0 and self.sp < -skill.sp:
            self.log.append(ActionLog(self.t, cid, "basic", detail="SP不足，战技降级为普攻"))
            action = Action(unit_id=cid, action="basic")
            skill = self.chars[cid].skills["basic"]
        elif action.action == "skill":
            self.skill_used[cid] = self.skill_used.get(cid, 0) + 1

        self.buffs.tick_owner(cid)
        # 目标解析：伤害目标（敌人）与友方目标（拉条/buff 对象，如花火拉红A）分离
        dmg_target = self._resolve_target(action.target, skill)
        ally_target = action.target if action.target in self.chars else (skill.advance_target or "")

        # 效果执行（E1 结算链：伤害 → 天赋钩子 → SP → 能量 → 削韧 → 拉条；顺序与 v1.5 等价）
        effects = skill_to_effects(action.action, skill, self.chars[cid].talent_extra)
        self._apply_effects(cid, effects, dmg_target, ally_target)
        extra_effect = next((e for e in effects if isinstance(e, ExtraActionEffect)), None)

        # 行动日志与队列推进
        self.action_count[cid] = self.action_count.get(cid, 0) + 1
        self.log.append(ActionLog(self.t, cid, action.action))
        self.rotation.advance(cid)

        if extra_effect is not None:
            # 额外行动链（红A 回路连接）：链内战技次数达上限后强制结束回合
            self.burst_chain[cid] = self.burst_chain.get(cid, 0) + 1
            max_chain = pol.chain_max if (pol is not None and pol.chain_max > 0) else extra_effect.max_chain
            if max_chain and self.burst_chain[cid] >= max_chain:
                self.burst_chain.pop(cid, None)
                self.queue.reset_after_action(cid)
            else:
                self.queue.keep_acting(cid)
        else:
            self.burst_chain.pop(cid, None)
            self.queue.reset_after_action(cid)

    # ---------- 终结技即时释放（不占行动条） ----------
    def _try_immediate_ults(self) -> None:
        """能量满足即释放大招：策略 ult=on_full 或序列含 ult 的角色启用。"""
        for cid in list(self.chars):
            pol = self.rotation.policy.get(cid)
            if pol is not None:
                enabled = pol.ult == "on_full"
            else:
                seq = self.rotation.actions.get(cid)
                enabled = bool(seq) and any(a.action == "ult" for a in seq)
            if not enabled:
                continue
            skill = self.chars[cid].skills["ult"]
            while self.energy[cid] >= skill.energy_cost:
                self._execute_ult(cid, skill)

    def _execute_ult(self, cid: str, skill) -> None:
        """执行大招：伤害/特效/能量扣除/SP，不占用行动条（不 reset、不推进时间）。"""
        dmg_target = self._resolve_target("", skill)
        ally_target = ""
        # v1.5 顺序保留：ult 的削韧在行动日志之后（toughness_in_effects=False，log 后补）
        effects = skill_to_effects("ult", skill, self.chars[cid].talent_extra)
        self._apply_effects(cid, effects, dmg_target, ally_target, toughness_in_effects=False)
        self.action_count[cid] = self.action_count.get(cid, 0) + 1
        self.log.append(ActionLog(self.t, cid, "ult", detail="即时释放"))
        if dmg_target:
            self._apply_effect_toughness(cid, dmg_target, effects)

    def _apply_effects(self, cid: str, effects: List[Effect], dmg_target: Optional[str],
                       ally_target: str, toughness_in_effects: bool = True) -> None:
        """通用效果执行器（E1 结算链）。效果类型 = 数据，无角色特判（ADR-0007 D2）。"""
        for eff in effects:
            if isinstance(eff, DamageEffect) and dmg_target:
                self._deal_damage(cid, dmg_target, eff.mult, kind=eff.kind)
                self._on_ally_attack(cid, dmg_target)
            elif isinstance(eff, AdvanceAllEffect):
                for other in self.chars:
                    if other != cid:
                        self.queue.advance(other, eff.pct)
            elif isinstance(eff, FateChargeEffect):
                self.fate_charge[cid] = min(eff.cap, self.fate_charge.get(cid, 0.0) + eff.amount)
            elif isinstance(eff, ConcertEffect):
                self.concert_rounds = eff.rounds
                self._concert_additional_mult = eff.additional_mult
            elif isinstance(eff, MemospriteChargeEffect):
                if self.memosprite is not None:
                    self.memosprite["charge"] = min(100.0, self.memosprite["charge"] + eff.amount * 100.0)
            elif isinstance(eff, MemospriteDamageEffect):
                if self.memosprite is not None:
                    self._mem_damage_all(eff.mult)
            elif isinstance(eff, BuffEffect):
                buff_target = ""
                if eff.target == "advance_target":
                    buff_target = ally_target or ""
                elif eff.target:
                    buff_target = eff.target
                self.buffs.add(eff.stat, eff.value, cid, eff.duration,
                               target=buff_target, cap=eff.cap)
            elif isinstance(eff, AuraEffect):
                self.buffs.add(eff.stat, eff.value, cid, 0)
            elif isinstance(eff, SPChangeEffect):
                if eff.counts_as_spent:
                    self.sp_spent_count += 1  # 花火天赋：每耗 1 SP 全队增伤
                self.sp = min(self.sp_max, self.sp + eff.delta)
                self.sp_timeline.append((self.t, self.sp))
            elif isinstance(eff, EnergyGainEffect):
                self.energy[cid] += eff.amount * self.stats[cid].energy_regen
            elif isinstance(eff, EnergyCostEffect):
                self.energy[cid] -= eff.amount
                self.ult_count[cid] = self.ult_count.get(cid, 0) + 1
            elif isinstance(eff, ToughnessEffect):
                if toughness_in_effects and dmg_target:
                    self._apply_toughness(cid, dmg_target, eff.amount)
            elif isinstance(eff, AdvanceEffect):
                adv_target = ally_target or cid
                self.queue.advance(adv_target, eff.pct)
        # 协奏轮数递减（v1.5：每次行动结算后 -1）
        if self.concert_rounds > 0:
            self.concert_rounds -= 1

    def _apply_effect_toughness(self, cid: str, dmg_target: str, effects: List[Effect]) -> None:
        """ult 补执行削韧（v1.5 顺序：ult 削韧在行动日志之后）。"""
        for eff in effects:
            if isinstance(eff, ToughnessEffect):
                self._apply_toughness(cid, dmg_target, eff.amount)

    def _mem_damage_all(self, mult: float) -> None:
        owner = self.chars[self.memosprite_owner]
        owner_stats = self._effective_stats(self.memosprite_owner)
        mem_atk = owner_stats.atk
        for eid, hp in list(self.enemy_hp.items()):
            if hp > 0.0:
                dmg = expected_damage(
                    mult, mem_atk, owner_stats, self._current_multipliers(),
                    self.enemies[eid].defense,
                    self.enemies[eid].resistances.get("Ice", 0.0),
                    self.attacker_level,
                    enemy_broken=self.toughness[eid] <= 0.0,
                )
                self._record_damage("MEM", eid, dmg, "normal")

    def _resolve_target(self, target_id: str, skill) -> Optional[str]:
        if target_id and target_id in self.enemy_hp:
            return target_id
        alive = [eid for eid, hp in self.enemy_hp.items() if hp > 0.0]
        return alive[0] if alive else None

    def _deal_damage(self, cid: str, target: str, mult: float, kind: str = "normal") -> float:
        stats = self._effective_stats(cid)
        enemy = self.enemies[target]
        res = enemy.resistances.get(self.chars[cid].element, 0.0)
        m = self._current_multipliers()
        dmg = expected_damage(mult, stats.atk, stats, m, enemy.defense, res, self.attacker_level,
                              enemy_broken=self.toughness[target] <= 0.0)
        self._record_damage(cid, target, dmg, kind)
        return dmg

    def _effective_stats(self, cid: str) -> Stats:
        s = replace(self.stats[cid])
        s.crit_dmg += self.buffs.sum_for("crit_dmg", cid)
        s.atk = s.atk * (1.0 + self.buffs.sum_for("atk_pct", cid)) + self.buffs.sum_for("atk_flat", cid)
        return s

    def _current_multipliers(self) -> Multipliers:
        m = Multipliers()
        m.dmg_bonus = self.buffs.sum_for("dmg_bonus")
        # 花火天赋：每耗 1 SP 全队增伤 3%（叠 3 层）
        m.dmg_bonus += 0.03 * min(self.sp_spent_count, 3)
        m.true_dmg = self.buffs.sum_for("true_dmg")
        m.extra_atk_pct = self.buffs.sum_for("concert_atk")
        return m

    # ---------- 天赋触发 ----------
    def _on_ally_attack(self, cid: str, target: str) -> None:
        """队友攻击后触发：红A 追击 / 知更鸟回能。"""
        for other_id in self.chars:
            if other_id == cid:
                continue
            talent = self.chars[other_id].talent_extra
            if talent.get("followup_on_ally_attack"):
                self._archer_followup(other_id, target)
            if talent.get("energy_on_ally_attack"):
                self.energy[other_id] += talent["energy_on_ally_attack"] * self.stats[other_id].energy_regen
        # 知更鸟协奏附加伤害：任何我方攻击后
        if self.concert_rounds > 0:
            robin = next(
                (c for c in self.chars.values() if c.talent_extra.get("skill_effects", {}).get("ult", {}).get("concert")),
                None,
            )
            if robin:
                self._additional_damage(robin, target)

    def _archer_followup(self, cid: str, target: str) -> None:
        charge = self.fate_charge.get(cid, 0.0)
        if charge < 1.0:
            return
        self.fate_charge[cid] = charge - 1.0
        mult = self.chars[cid].skills["talent"].mult
        self._deal_damage(cid, target, mult, kind="followup")
        # 追击恢复 1 个战技点 + 天赋回能（wiki：心眼真 回 5 能量，× 充能效率）
        self.sp = min(self.sp_max, self.sp + 1.0)
        self.sp_timeline.append((self.t, self.sp))
        self.energy[cid] += self.chars[cid].talent_extra.get("followup_energy", 0.0) * self.stats[cid].energy_regen

    def _additional_damage(self, robin: CharacterData, target: str) -> None:
        """知更鸟协奏附加伤害：固定双暴（100%/150%）。"""
        enemy = self.enemies[target]
        robin_stats = self._effective_stats(robin.id)
        m = self._current_multipliers()
        dmg = flat_damage(
            getattr(self, "_concert_additional_mult", 0.72),
            robin_stats.atk,
            m.dmg_bonus,
            self._def_m(target),
            self._res_m(target, robin.element),
            enemy_broken=self.toughness[target] <= 0.0,
        )
        self._record_damage(robin.id, target, dmg, "additional")

    def _def_m(self, target: str) -> float:
        from .damage import defense_multiplier
        return defense_multiplier(self.attacker_level, self.enemies[target].defense)

    def _res_m(self, target: str, element: str) -> float:
        from .damage import resistance_multiplier
        return resistance_multiplier(self.enemies[target].resistances.get(element, 0.0))

    # ---------- 削韧 / 击破 ----------
    def _apply_toughness(self, cid: str, target: str, amount: float) -> None:
        elem = self.chars[cid].element
        enemy = self.enemies[target]
        if elem not in enemy.weaknesses:
            return
        if self.toughness[target] <= 0.0:
            return
        self.toughness[target] = max(0.0, self.toughness[target] - amount)
        if self.toughness[target] <= 0.0:
            self._on_break(cid, target)

    def _on_break(self, cid: str, target: str) -> None:
        self.breaks.append((self.t, target))
        self.queue.postpone(target, BREAK_POSTPONE_PCT)
        if not self.enemies[target].break_immune:
            stats = self._effective_stats(cid)
            m = self._current_multipliers()
            dmg = break_damage(
                self.chars[cid].element, stats.break_effect,
                self.enemies[target].toughness,
                self._def_m(target), self._res_m(target, self.chars[cid].element),
                vuln=m.vuln, final_dmg=m.final_dmg, true_dmg=m.true_dmg,
            )
            self._record_damage(cid, target, dmg, "break")

    # ---------- 忆灵 / 敌人 ----------
    def _memosprite_act(self) -> None:
        if self.memosprite is None or not self.memosprite["alive"]:
            self.queue.remove("MEM")
            return
        owner = self.chars[self.memosprite_owner]
        owner_stats = self._effective_stats(self.memosprite_owner)
        mem_atk = owner_stats.atk  # v1：迷迷攻击 = 记忆主攻击（待验证）
        enhanced = self.memosprite["charge"] >= 100.0
        mult = 1.0 if not enhanced else 1.6  # 迷迷行动倍率（待验证）；强化 ×1.6（待验证）
        for eid, hp in list(self.enemy_hp.items()):
            if hp > 0.0:
                dmg = expected_damage(
                    mult, mem_atk, owner_stats, self._current_multipliers(),
                    self.enemies[eid].defense,
                    self.enemies[eid].resistances.get("Ice", 0.0),
                    self.attacker_level,
                    enemy_broken=self.toughness[eid] <= 0.0,
                )
                self._record_damage("MEM", eid, dmg, "normal")
        if enhanced:
            self.memosprite["charge"] = 0.0
        self.queue.reset_after_action("MEM")
        self.log.append(ActionLog(self.t, "MEM", "memosprite_skill"))

    def _enemy_act(self, eid: str) -> None:
        # v1：敌人行动仅恢复韧性（破韧后），不结算对角色伤害
        if self.toughness[eid] <= 0.0:
            self.toughness[eid] = self.enemies[eid].toughness
        self.queue.reset_after_action(eid)
        self.log.append(ActionLog(self.t, eid, "enemy_action"))

    # ---------- 快照 / 回退（ADR-0007 3.1，E11/E12） ----------
    def snapshot(self) -> BattleSnapshot:
        """捕获当前完整状态（可序列化）。"""
        return BattleSnapshot(
            t=self.t, steps=self._steps, sp=self.sp, sp_max=self.sp_max,
            energy=dict(self.energy), toughness=dict(self.toughness),
            enemy_hp=dict(self.enemy_hp), buffs=copy.deepcopy(self.buffs._buffs),
            fate_charge=dict(self.fate_charge), skill_used=dict(self.skill_used),
            burst_chain=dict(self.burst_chain), sp_spent_count=self.sp_spent_count,
            concert_rounds=self.concert_rounds,
            concert_additional_mult=getattr(self, "_concert_additional_mult", 0.72),
            memosprite=dict(self.memosprite) if self.memosprite else None,
            memosprite_owner=self.memosprite_owner,
            queue_entries={uid: (e.distance, e.speed) for uid, e in self.queue._entries.items()},
            sp_timeline=list(self.sp_timeline), damage_events=list(self.damage_events),
            log=list(self.log), breaks=list(self.breaks),
            ult_count=dict(self.ult_count), action_count=dict(self.action_count),
            rotation_actions={uid: list(seq) for uid, seq in self.rotation.actions.items()},
            rng_state=(self.seed, self.rng.getstate()),
        )

    def restore(self, snap: BattleSnapshot) -> None:
        """恢复快照（回退/分支探索共用；配置未变前提，D5）。"""
        self.seed = snap.rng_state[0]
        self.rng = random.Random()
        self.rng.setstate(snap.rng_state[1])
        self.t = snap.t
        self._steps = snap.steps
        self.sp = snap.sp
        self.sp_max = snap.sp_max
        self.energy = dict(snap.energy)
        self.toughness = dict(snap.toughness)
        self.enemy_hp = dict(snap.enemy_hp)
        self.buffs = BuffManager()
        self.buffs._buffs = copy.deepcopy(snap.buffs)
        self.fate_charge = dict(snap.fate_charge)
        self.skill_used = dict(snap.skill_used)
        self.burst_chain = dict(snap.burst_chain)
        self.sp_spent_count = snap.sp_spent_count
        self.concert_rounds = snap.concert_rounds
        self._concert_additional_mult = snap.concert_additional_mult
        self.memosprite = dict(snap.memosprite) if snap.memosprite else None
        self.memosprite_owner = snap.memosprite_owner
        self.queue = ActionQueue()
        for uid, (distance, speed) in snap.queue_entries.items():
            self.queue.add(uid, speed, distance)
        self.sp_timeline = list(snap.sp_timeline)
        self.damage_events = list(snap.damage_events)
        self.log = list(snap.log)
        self.breaks = list(snap.breaks)
        self.ult_count = dict(snap.ult_count)
        self.action_count = dict(snap.action_count)
        self.rotation.actions = {uid: list(seq) for uid, seq in snap.rotation_actions.items()}

    def push_act_snapshot(self) -> None:
        """决策点压栈：undo 回到最近一次我方主动行动前。"""
        self._snapshots.append(self.snapshot())

    def undo(self) -> bool:
        """撤销最近一次我方主动行动（含其全部自动连锁）。返回是否成功。"""
        if not self._snapshots:
            return False
        self.restore(self._snapshots.pop())
        return True

    def restart(self) -> None:
        """回到初始状态（推演配置不变时；配置变更 = 新会话，见 ADR-0007 D5）。"""
        self._reset(self.seed)

    # ---------- 记录与结果 ----------
    def _record_damage(self, source: str, target: str, amount: float, kind: str) -> None:
        self.damage_events.append(DamageEvent(self.t, source, target, amount, kind))
        self.enemy_hp[target] = max(0.0, self.enemy_hp[target] - amount)

    def _result(self) -> SimResult:
        r = SimResult(t_end=self.t)
        r.total_damage = sum(e.amount for e in self.damage_events)
        for e in self.damage_events:
            r.damage_by_source[e.source] = r.damage_by_source.get(e.source, 0.0) + e.amount
            r.damage_by_kind[e.kind] = r.damage_by_kind.get(e.kind, 0.0) + e.amount
        r.sp_timeline = self.sp_timeline
        r.sp_min = min(sp for _, sp in self.sp_timeline)
        r.energy_shortfalls = self.shortfalls
        r.actions = self.log
        r.breaks = self.breaks
        r.enemy_hp_left = dict(self.enemy_hp)
        r.ult_count = self.ult_count
        r.action_count = self.action_count
        # 信任度信封：任何 D/raw 输入参与计算 → 结果标注未验证（ADR-0006 6.2）
        r.unverified_inputs = list(self._unverified)
        r.trust_level = "unverified" if self._unverified else "trusted"
        return r
