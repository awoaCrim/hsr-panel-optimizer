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
from typing import Dict, List, Optional, Set, Tuple

from ..model import Action, CharacterData, CharacterPolicy, Enemy, Rotation, Stats
from .av_queue import ActionQueue
from .buffs import BuffManager
from .damage import Multipliers, break_damage, expected_damage, flat_damage, noncrit_damage
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
    MemospriteImmediateEffect,
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
    # ④ 对账：非暴击基准（暴击判定不可复现，实战值应匹配 {noncrit, noncrit×(1+暴伤)} 端点）
    noncrit: float = 0.0
    crit_dmg_mult: float = 0.0   # 1+暴伤（暴击端点倍率；0 = 无暴击概念）


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
    setup: Dict[str, object] = field(default_factory=dict)
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
        initial_sp: Optional[float] = None,
        initial_energy: Optional[Dict[str, float]] = None,
        waves: Optional[List[Dict[str, Enemy]]] = None,
        battle_setup: Optional[Dict] = None,
    ) -> None:
        self.chars = characters
        self.stats = char_stats
        self._initial_sp = 4.0 if initial_sp is None else initial_sp
        self._initial_energy = dict(initial_energy or {})
        self.battle_setup = copy.deepcopy(battle_setup or {})
        # 多波次（D8，混沌回忆结构）：waves 缺省 = 单波次
        self._waves = list(waves) if waves else [enemies]
        self.enemy_wave = 0
        self.enemies = self._waves[0]
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
        # 多波次（D8）：重启回到第 1 波
        self.enemy_wave = 0
        self.enemies = self._waves[0]

        self.queue = ActionQueue()
        for cid in self.chars:
            self.queue.add(cid, self.stats[cid].speed)
        for eid, e in self.enemies.items():
            self.queue.add(eid, e.speed)

        # 运行时状态
        self.t = 0.0
        self._steps = 0
        self.sp = self._initial_sp
        self.sp_max = 5.0
        for cid, c in self.chars.items():
            self.sp_max += c.talent_extra.get("sp_cap_bonus", 0)
            # 星魂 SP 上限加成（花火 E4）
            for ex in c.equipment_effects:
                if ex["type"] == "ult_sp_refund_extra":
                    self.sp_max += ex.get("sp_cap_bonus", 0)
        self.energy: Dict[str, float] = {
            cid: self._initial_energy.get(cid, 0.0) for cid in self.chars}
        self.toughness: Dict[str, float] = {eid: e.toughness for eid, e in self.enemies.items()}
        # ① 生存：我方 HP（面板 HP，死亡 = 移除行动）与敌人技能冷却
        self.char_hp: Dict[str, float] = {
            cid: self.stats[cid].hp for cid in self.chars}
        self.char_hp_max: Dict[str, float] = {
            cid: self.stats[cid].hp for cid in self.chars}
        self.enemy_cd: Dict[str, Dict[int, int]] = {
            eid: {i: 0 for i in range(len(e.skills))} for eid, e in self.enemies.items()}
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
        # 装备效果运行时（光锥被动/套装；exec DSL，见 build.resolve_equipment）
        self.equip_stacks: Dict[str, Dict[str, int]] = {}   # cid -> effect_id -> 层数
        self.equip_ult_count: Dict[str, int] = {}           # cid -> 大招计数（ult_sp_refund）
        self.equip_hit_target: Dict[str, str] = {}          # cid -> 上次攻击目标（论剑叠层）
        self.skill_streak: Dict[str, int] = {}              # 连续战技计数（星魂 E1）
        self.setup_state: Dict[str, object] = {
            "requested": [], "applied": [], "skipped": [], "field_owner": "", "engage_by": "",
        }
        self._wave_energy_effects: List[Tuple[str, float, str]] = []
        self._start_effects_applied = False
        # 装备效果运行时状态随 _reset 清理（restart 一致性）
        for _attr in ("equip_stacks", "equip_ult_count", "equip_hit_target", "skill_streak"):
            getattr(self, _attr).clear()
        self._start_effects_applied = False
        # LLM 指挥通道（ADR-0007 3.3）：
        self.external_action: Optional[Action] = None    # 决策注入（优先于 policy/序列；消费后清空）
        self.ult_hold: Set[str] = set()                  # 大招抑制：成员即使能量满也不自动释放
        self.ult_override: Optional[bool] = None         # True = 忽略 rotation 声明，非 hold 者满能即放

    def _equip_effects(self, cid: str) -> List[Dict]:
        """角色生效的装备机制效果（exec DSL，含来源标注）。"""
        return self.chars[cid].equipment_effects

    def _energy_regen(self, cid: str) -> float:
        """充能效率：基础面板 + 战斗内增减益 + 装备叠层。"""
        v = self.stats[cid].energy_regen + self.buffs.sum_for("energy_regen", cid)
        for ex in self._equip_effects(cid):
            if ex["type"] == "stack_energy_regen":
                n = self.equip_stacks.get(cid, {}).get(ex["src"], 0)
                v += n * ex["per_stack"]
        return v

    def _apply_start_effects(self) -> None:
        """开战自动阶段：显式秘技 Setup → 每波开始效果 → 装备开局拉条。"""
        if self._start_effects_applied:
            return
        self._start_effects_applied = True
        self._apply_battle_setup()
        self._apply_wave_start_effects(0)
        for cid, ch in self.chars.items():
            for ex in self._equip_effects(cid):
                if ex["type"] == "start_advance" and self.stats[cid].speed >= ex["speed_ge"]:
                    self.queue.advance(cid, ex["pct"])

    def _apply_battle_setup(self) -> None:
        """应用显式开战准备；同一 field_group 最多保留一个领域。"""
        requested = [str(cid) for cid in self.battle_setup.get("techniques", [])]
        field_owner = str(self.battle_setup.get("field_owner", "") or "")
        state = {"requested": requested, "applied": [], "skipped": [], "field_owner": "",
                 "engage_by": str(self.battle_setup.get("engage_by", "") or "")}

        is_attack = self.battle_setup.get("engage_by")
        if is_attack and str(is_attack) not in requested:
            raise ValueError("battle_setup.engage_by 必须包含在 techniques 中")
        if is_attack:
            owner = self.chars.get(str(is_attack))
            if owner is None or owner.technique.get("category") != "attack":
                raise ValueError("battle_setup.engage_by 必须指向队伍内的攻击型秘技")

        field_groups: Dict[str, List[str]] = {}
        for cid in requested:
            ch = self.chars.get(cid)
            if ch is None or not ch.technique:
                state["skipped"].append({"unit_id": cid, "reason": "角色不在队伍或无秘技数据"})
                continue
            group = ch.technique.get("field_group", "")
            if group:
                field_groups.setdefault(group, []).append(cid)
        for group, owners in field_groups.items():
            if len(owners) > 1 and field_owner not in owners:
                names = [self.chars[c].name for c in owners]
                raise ValueError(
                    f"开战准备存在互斥领域 {names}；field_owner 必须从 {owners} 中显式选择")
        if field_owner and not any(field_owner in owners for owners in field_groups.values()):
            raise ValueError("battle_setup.field_owner 必须指向 techniques 中的领域秘技")

        for cid in requested:
            ch = self.chars.get(cid)
            if ch is None or not ch.technique:
                continue
            tech = ch.technique
            group = tech.get("field_group", "")
            if group and len(field_groups.get(group, [])) > 1 and cid != field_owner:
                state["skipped"].append({
                    "unit_id": cid, "name": ch.name, "technique": tech.get("name", "秘技"),
                    "reason": f"同类领域互斥；保留 {self.chars[field_owner].name} 领域",
                })
                self.log.append(ActionLog(
                    self.t, cid, "setup_skip",
                    detail=f"秘技 {tech.get('name', '秘技')} 未生效：同类领域互斥，保留 {self.chars[field_owner].name} 领域"))
                continue
            self._apply_technique(cid, tech)
            state["applied"].append({
                "unit_id": cid, "name": ch.name, "technique": tech.get("name", "秘技"),
                "category": tech.get("category", ""),
            })
            if group:
                state["field_owner"] = cid
        self.setup_state = state

    def _apply_technique(self, cid: str, technique: Dict) -> None:
        details = []
        for effect in technique.get("effects", []):
            effect_type = effect.get("type")
            if effect_type == "sp":
                before = self.sp
                self.sp = min(self.sp_max, self.sp + float(effect.get("amount", 0.0)))
                self.sp_timeline.append((self.t, self.sp))
                details.append(f"SP {before:g}→{self.sp:g}")
            elif effect_type == "fate_charge":
                amount = float(effect.get("amount", 0.0))
                cap = float(effect.get("cap", 4.0))
                self.fate_charge[cid] = min(cap, self.fate_charge.get(cid, 0.0) + amount)
                details.append(f"充能+{amount:g}")
            elif effect_type == "energy_each_wave":
                amount = float(effect.get("amount", 0.0))
                self._wave_energy_effects.append((cid, amount, technique.get("name", "秘技")))
                details.append(f"每波能量+{amount:g}")
            elif effect_type == "postpone_all":
                pct = float(effect.get("pct", 0.0))
                for eid in list(self.enemies):
                    self.queue.postpone(eid, pct)
                details.append(f"敌方全体行动延后{pct:.0%}")
            elif effect_type == "damage_all":
                mult = float(effect.get("mult", 0.0))
                for eid in list(self.enemies):
                    if self.enemy_hp.get(eid, 0.0) <= 0.0:
                        continue
                    dmg, noncrit, crit_dmg_mult = self._technique_damage(cid, eid, mult)
                    self._record_damage(cid, eid, dmg, "technique",
                                        noncrit=noncrit, crit_dmg_mult=crit_dmg_mult)
                details.append(f"敌方全体{mult:.0%}攻击力伤害")
        self.log.append(ActionLog(
            self.t, cid, "technique",
            detail=f"{technique.get('name', '秘技')}：" + "；".join(details)))

    def _technique_damage(self, cid: str, target: str, mult: float) -> float:
        """攻击型秘技伤害；按普通角色伤害公式结算，不触发战斗内“攻击后”链。"""
        stats = self._effective_stats(cid)
        enemy = self.enemies[target]
        res = enemy.resistances.get(self.chars[cid].element, 0.0)
        m = self._current_multipliers(damager=cid)
        m.res_pen += self.buffs.sum_for("res_pen", cid)
        noncrit = noncrit_damage(mult, stats.atk, stats, m, enemy.defense, res,
                                 self.attacker_level,
                                 enemy_broken=self.toughness[target] <= 0.0)
        crit = self.rng.random() < min(stats.crit_rate, 1.0)
        amount = noncrit * (1.0 + stats.crit_dmg) if crit else noncrit
        return amount, noncrit, 1.0 + stats.crit_dmg

    def _apply_wave_start_effects(self, wave_idx: int) -> None:
        for cid, amount, technique_name in self._wave_energy_effects:
            before = self.energy[cid]
            self.energy[cid] += amount
            self._memosprite_charge_from_energy(amount)
            self.log.append(ActionLog(
                self.t, cid, "wave_start_effect",
                detail=f"第{wave_idx + 1}波 {technique_name}：能量 {before:g}→{self.energy[cid]:g}"))

    # ---------- 主循环 ----------
    def run(self) -> SimResult:
        self._ensure_memosprite_summon()
        self._apply_start_effects()
        while self.run_step() is not None:
            pass
        return self._result()

    def run_step(self) -> Optional[str]:
        """执行一个行动边界：下一个单位行动 + 即时大招结算（含全部连锁）。

        返回行动单位 id；None = 推演结束（队列空 / AV 耗尽 / 超步熔断）。
        我方角色行动前自动压快照（undo 的决策点，ADR-0007 D3）。
        """
        self._ensure_memosprite_summon()
        self._apply_start_effects()
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
        self._act_sp_consumed = 0   # 324：同回合消耗 SP 计数（每次行动清零）
        if self.external_action is not None and self.external_action.unit_id == cid:
            # LLM 指挥注入的决策（ADR-0007 3.3）：优先于策略/序列，消费后清空
            action = self.external_action
            self.external_action = None
            pol = self.rotation.policy.get(cid)
        else:
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
        if action.action == "ult":            # 终结技由即时释放机制负责（_try_immediate_ults）：轮到该槽时能量必然不足，跳过
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

        # 同一完整回合只结算一次持续时间：首个正常行动扣一次，后续额外行动不扣。
        # 在行动开始时结算可确保本次行动中新获得的 buff 不会被当前回合立即消耗。
        in_extra_chain = cid in self.burst_chain
        if not in_extra_chain:
            self.buffs.tick_owner(cid)
        # 目标解析：只有实际伤害技能才绑定敌人；非攻击技能不得携带幽灵敌方目标。
        dmg_target = self._resolve_target(action.target, skill) if skill.mult > 0.0 else None
        ally_target = action.target if action.target in self.chars else (skill.advance_target or "")
        if not skill.advance_self and ally_target == cid:
            # 官方规则：目标选择器排除自身（花火战技不可自拉）
            ally_target = ""

        # 效果执行（E1 结算链：伤害 → 天赋钩子 → SP → 能量 → 削韧 → 拉条；顺序与 v1.5 等价）
        effects = skill_to_effects(action.action, skill, self.chars[cid].talent_extra)
        self._apply_effects(cid, effects, dmg_target, ally_target,
                            advance_self=skill.advance_self, skill_type=action.action)
        self._after_skill_equipment(cid, action.action, ally_target)
        extra_effect = next((e for e in effects if isinstance(e, ExtraActionEffect)), None)

        # 行动日志与队列推进
        self.action_count[cid] = self.action_count.get(cid, 0) + 1
        self.log.append(ActionLog(self.t, cid, action.action))
        self.rotation.advance(cid)
        self._track_skill_streak(cid, action.action)

        # 324 直播间：同回合消耗 ≥N SP → 暴伤 buff（持续 duration 回合）
        if self._act_sp_consumed > 0:
            for ex in self._equip_effects(cid):
                if ex["type"] == "sp_consume_cd" and self._act_sp_consumed >= ex["sp_ge"]:
                    self.buffs.add("crit_dmg", ex["value"], cid, ex["duration"], target=cid)
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

    def _track_skill_streak(self, cid: str, action: str) -> None:
        """星魂 E1（红A）：单个回合内连续 3 次战技 → 回 2 SP（普攻/其他动作打断计数）。"""
        # 如泥酣眠 CD 递减（装备者行动 = 1 回合）
        for ex in self._equip_effects(cid):
            if ex["type"] == "no_crit_crit":
                cd = self.equip_stacks.get(cid, {}).get(ex["src"], 0)
                if cd > 0:
                    self.equip_stacks.setdefault(cid, {})[ex["src"]] = cd - 1
        streak = self.skill_streak.get(cid, 0)
        if action == "skill":
            streak += 1
            self.skill_streak[cid] = streak
            for ex in self._equip_effects(cid):
                if ex["type"] == "skill_count_sp_refund" and streak == ex["count"]:
                    self.sp = min(self.sp_max, self.sp + ex["amount"])
                    self.sp_timeline.append((self.t, self.sp))
                    self.skill_streak[cid] = 0
        else:
            self.skill_streak.pop(cid, None)

    def _after_skill_equipment(self, cid: str, action: str, ally_target: str) -> None:
        """装备战技后效果：23003 下一个队友增伤 / 24005 全队增伤 / 121-4 目标暴伤。"""
        for ex in self._equip_effects(cid):
            if action == "skill":
                if ex["type"] == "skill_next_ally_dmg":
                    self.buffs.add("equip_next_ally_dmg", ex["value"], cid, ex["duration"])
                elif ex["type"] == "skill_team_dmg":
                    self.buffs.add("dmg_bonus", ex["value"], cid, ex["duration"])
                elif ex["type"] == "skill_stack_atk":
                    # 23046：战技后攻击 +X% 叠层（常驻，cap 上限）
                    self.buffs.add("atk_pct", ex["per_stack"], cid, 0,
                                   target=cid, cap=ex["per_stack"] * ex["max"])
                elif ex["type"] == "single_skill_energy" and ally_target in self.chars:
                    # 23034：对我方单体施放战技/终结技后回能 6
                    gained = ex["value"] * self._energy_regen(cid)
                    self.energy[cid] += gained
                    self._memosprite_charge_from_energy(gained)
                elif ex["type"] == "target_dmg_stack" and ally_target in self.chars:
                    # 23034：【圣咏】目标增伤叠层（3 层上限）
                    self.buffs.add("dmg_bonus", ex["per_stack"], cid, ex["duration"],
                                   target=ally_target, cap=ex["per_stack"] * ex["max"])
                elif ex["type"] == "every_n_skill_sp" and ally_target in self.chars:
                    # 23034：每 2 次单体战技/终结技回 1 SP
                    n = self.equip_stacks.get(cid, {}).get(ex["src"], 0) + 1
                    self.equip_stacks.setdefault(cid, {})[ex["src"]] = n
                    if n >= ex["every"]:
                        self.equip_stacks[cid][ex["src"]] = 0
                        self.sp = min(self.sp_max, self.sp + ex["amount"])
                        self.sp_timeline.append((self.t, self.sp))
                elif ex["type"] == "mem_present_team_dmg" and \
                        self.memosprite is not None and self.memosprite["alive"]:
                    # 127-4：普攻/战技后忆灵在场 → 全队增伤（持续 1 行动）
                    self.buffs.add("dmg_bonus", ex["value"], cid, ex["duration"])
            if ex["type"] == "target_cd_buff" and ally_target and ally_target in self.chars:
                # 121-4：对单体目标施放战技/大招 → 目标暴伤（可叠 2 层）
                self.buffs.add("crit_dmg", ex["value"], cid, ex["duration"],
                               target=ally_target, cap=ex["value"] * ex.get("max_stacks", 2))

    # ---------- 终结技即时释放（不占行动条） ----------
    def _try_immediate_ults(self) -> None:
        """能量满足即释放大招：策略 ult=on_full 或序列含 ult 的角色启用。"""
        for cid in list(self.chars):
            if cid in self.ult_hold:
                continue    # LLM 指挥：该角色本 act 内 hold（ADR-0007 D2 大招时机）
            if self.ult_override:
                enabled = True   # LLM 显式指令：非 hold 者满能即放（覆盖 rotation 声明）
            else:
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
        dmg_target = self._resolve_target("", skill) if skill.mult > 0.0 else None
        ally_target = ""
        # v1.5 顺序保留：ult 的削韧在行动日志之后（toughness_in_effects=False，log 后补）
        effects = skill_to_effects("ult", skill, self.chars[cid].talent_extra)
        self._apply_effects(cid, effects, dmg_target, ally_target, toughness_in_effects=False,
                            skill_type="ult")
        self.action_count[cid] = self.action_count.get(cid, 0) + 1
        self.log.append(ActionLog(self.t, cid, "ult", detail="即时释放"))
        if dmg_target:
            self._apply_effect_toughness(cid, dmg_target, effects)
        self._after_ult_equipment(cid)

    def _after_ult_equipment(self, cid: str) -> None:
        """装备大招后效果：23003 每 2 次大招回 1 SP；23026 【歌咏】→【华彩】；
        红A E2 量子抗性+弱点；花火 E4 额外回 SP。"""
        dmg_target = self._resolve_target("", self.chars[cid].skills["ult"])
        for ex in self._equip_effects(cid):
            if ex["type"] == "ult_sp_refund":
                n = self.equip_ult_count.get(cid, 0) + 1
                self.equip_ult_count[cid] = n
                if n % ex["every"] == 0:
                    self.sp = min(self.sp_max, self.sp + ex["amount"])
                    self.sp_timeline.append((self.t, self.sp))
            elif ex["type"] == "ult_sp_refund_extra":
                # 花火 E4：终结技额外恢复 1 点战技点
                self.sp = min(self.sp_max, self.sp + ex["amount"])
                self.sp_timeline.append((self.t, self.sp))
            elif ex["type"] == "ult_quantum_pen" and dmg_target:
                # 红A E2：终结技使目标量子抗性 -20% + 添加量子弱点，持续 2 回合
                self.buffs.add(f"enemy_res_pen:{ex['element']}", ex["res_pen"],
                               cid, ex["duration"], target=dmg_target)
                if ex.get("add_weakness"):
                    self.buffs.add(f"enemy_weakness_add:{ex['element']}", 1.0,
                                   cid, ex["duration"], target=dmg_target)
            elif ex["type"] == "ult_convert":
                # 23026：大招移除【歌咏】，获得【华彩】：攻击 +48%、全队增伤 +24% 1 回合
                self.equip_stacks.get(cid, {}).pop(ex["src"], None)
                self.buffs.add("atk_pct", ex["stack_value"], cid, ex["duration"], target=cid)
                self.buffs.add("dmg_bonus", ex["team_dmg"], cid, ex["duration"])

    def _apply_effects(self, cid: str, effects: List[Effect], dmg_target: Optional[str],
                       ally_target: str, toughness_in_effects: bool = True,
                       advance_self: bool = True, skill_type: str = "") -> None:
        """通用效果执行器（E1 结算链）。效果类型 = 数据，无角色特判（ADR-0007 D2）。"""
        for eff in effects:
            if isinstance(eff, DamageEffect) and dmg_target:
                self._deal_damage(cid, dmg_target, eff.mult, kind=eff.kind,
                                  skill_type=skill_type)
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
            elif isinstance(eff, MemospriteImmediateEffect):
                if self.memosprite is not None:
                    # 迷迷立即行动（距离清零，插队）；行动内容轮到它时执行
                    self.queue.advance("MEM", 1.0)
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
                    self._act_sp_consumed = self._act_sp_consumed + 1  # 324：同回合消耗 SP 计数
                self.sp = min(self.sp_max, self.sp + eff.delta)
                self.sp_timeline.append((self.t, self.sp))
            elif isinstance(eff, EnergyGainEffect):
                gained = eff.amount * self._energy_regen(cid)
                self.energy[cid] += gained
                self._memosprite_charge_from_energy(gained)
            elif isinstance(eff, EnergyCostEffect):
                self.energy[cid] -= eff.amount
                self.ult_count[cid] = self.ult_count.get(cid, 0) + 1
            elif isinstance(eff, ToughnessEffect):
                if toughness_in_effects and dmg_target:
                    self._apply_toughness(cid, dmg_target, eff.amount)
            elif isinstance(eff, AdvanceEffect):
                adv_target = ally_target or cid
                if not advance_self and adv_target == cid:
                    adv_target = ""    # 官方规则：不可自拉（花火战技目标选择器排除自身）
                if adv_target:
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
        for eid, hp in list(self.enemy_hp.items()):
            if hp > 0.0:
                dmg, nc, cdm = self._mem_deal(eid, mult)
                self._record_damage("MEM", eid, dmg, "normal",
                                    noncrit=nc, crit_dmg_mult=cdm)

    def _enemy_is_current(self, target: Optional[str]) -> bool:
        """目标仍属于当前波次；结算链跨波次后旧 id 必须失效。"""
        return bool(target) and target in self.enemies and target in self.enemy_hp

    def _resolve_target(self, target_id: str, skill) -> Optional[str]:
        # 保持既有决策语义：显式 id 只要仍在当前 HP 状态中就接受；
        # 跨波切换后旧 id 已不在 enemy_hp，会回退到当前波默认目标。
        if target_id and target_id in self.enemy_hp:
            return target_id
        alive = [eid for eid, hp in self.enemy_hp.items() if hp > 0.0]
        return alive[0] if alive else None

    def _deal_damage(self, cid: str, target: str, mult: float, kind: str = "normal",
                     skill_type: str = "", hits: int = 1) -> float:
        """段级伤害结算（②段级判定）：每段独立 roll 暴击（RNG 入事件流）。

        hits = 段数（默认 1；忆灵普攻 4 段、AOE 每目标 1 段）。
        每段：非暴击基础 ×（暴击 ? 1+暴伤 : 1）；段事件逐条记录；
        条件触发（论剑叠层/如泥酣眠/击杀）按段判定（官方语义）。
        技能总削韧仍按技能结算一次（官方削韧不按段）。
        """
        # 主伤害可能已在同一效果链中触发波次切换；旧目标不可继续结算。
        if not self._enemy_is_current(target):
            return 0.0
        stats = self._effective_stats(cid)
        # 装备条件暴击（目标血量条件）——作用于本次技能全部段
        for ex in self._equip_effects(cid):
            if ex["type"] == "hp_le_crit" and \
                    self.enemy_hp[target] <= ex["hp_le"] * self.enemies[target].hp:
                stats.crit_rate += ex["crit_rate"]
        enemy = self.enemies[target]
        res = enemy.resistances.get(self.chars[cid].element, 0.0)
        # 星魂 E2（红A）：终结技降低目标元素抗性
        res -= self.buffs.sum_for(f"enemy_res_pen:{self.chars[cid].element}", target)
        res_pen = self.buffs.sum_for("res_pen", cid)
        m = self._current_multipliers(damager=cid)
        m.res_pen += res_pen
        bonus, def_ignore = self._equip_damage(cid, kind, skill_type, target, stats)
        m.dmg_bonus += bonus
        m.def_ignore += def_ignore
        noncrit = noncrit_damage(mult, stats.atk, stats, m, enemy.defense, res,
                                 self.attacker_level,
                                 enemy_broken=self.toughness[target] <= 0.0)
        total = 0.0
        for _ in range(hits):
            # 目标死亡/波次切换后，剩余段不再执行（官方多段技能目标死亡中断语义）
            if target not in self.enemy_hp or self.enemy_hp[target] <= 0.0:
                break
            # 暴击判定（E12：RNG 入事件流，段级独立 roll）
            crit = self.rng.random() < min(stats.crit_rate, 1.0)
            dmg = noncrit * (1.0 + stats.crit_dmg) if crit else noncrit
            total += dmg
            prev_hp = self.enemy_hp[target]
            self._record_damage(cid, target, dmg, kind,
                                noncrit=noncrit, crit_dmg_mult=1.0 + stats.crit_dmg)
            # 论剑叠层（同目标每次命中 +1 层；换目标清零）——段级精确（原按 act 计层近似）
            for ex in self._equip_effects(cid):
                if ex["type"] == "hit_stack_dmg":
                    if self.equip_hit_target.get(cid) != target:
                        self.equip_hit_target[cid] = target
                        self.equip_stacks.setdefault(cid, {})[ex["src"]] = 0
                    else:
                        n = self.equip_stacks.get(cid, {}).get(ex["src"], 0)
                        self.equip_stacks.setdefault(cid, {})[ex["src"]] = min(n + 1, ex["max"])
                elif ex["type"] == "no_crit_crit":
                    # 如泥酣眠：段未暴击 → 暴击率 +value 持续 1 回合（CD cooldown 回合）
                    cd = self.equip_stacks.get(cid, {}).get(ex["src"], 0)
                    if not crit and cd <= 0:
                        self.buffs.add("crit_rate", ex["value"], cid,
                                       ex.get("duration", 1), target=cid)
                        self.equip_stacks.setdefault(cid, {})[ex["src"]] = ex.get("cooldown", 3)
            # 击杀触发（星海巡航：击杀后攻击+20% 2回合）
            # .get：击杀瞬间波次已切换，目标不在当前 enemy_hp（多波次 D8）
            if prev_hp > 0.0 and self.enemy_hp.get(target, 0.0) <= 0.0:
                for ex in self._equip_effects(cid):
                    if ex["type"] == "on_kill_atk":
                        self.buffs.add("atk_pct", ex["value"], cid, ex["duration"], target=cid)
        return total

    def _equip_damage(self, cid: str, kind: str, skill_type: str, target: str,
                      stats) -> Tuple[float, float]:
        """装备伤害乘区（per-attacker）：条件增伤 + 无视防御 + 元素伤。

        返回 (dmg_bonus 增量, def_ignore)。技能条件用 (skill_type or kind) 匹配
        （basic/skill → skill_type；ult/追击 → kind）。speed_over_100 层数取整。
        """
        bonus = 0.0
        def_ignore = 0.0
        elem = self.chars[cid].element
        for ex in self._equip_effects(cid):
            t = ex["type"]
            if t == "element_dmg" and elem == ex["element"]:
                bonus += ex["value"]
            elif t == "basic_dmg" and kind == "normal" and skill_type == "basic":
                bonus += ex["value"]
            elif t == "crit_ge_dmg" and (skill_type or kind) in ex.get("skills", []) \
                    and stats.crit_rate >= ex["crit_ge"]:
                bonus += ex["value"]
            elif t == "speed_over_100_dmg":
                stacks = max(0, int((stats.speed - 100.0) / ex["speed_step"]))
                stacks = min(stacks, ex["max_stacks"])
                if (skill_type or kind) in ex.get("skills", []):
                    bonus += stacks * ex["mult"]
                elif kind == "ult" and ex.get("ult_crit_dmg"):
                    stats.crit_dmg += stacks * ex["ult_crit_dmg"]
            elif t == "def_ignore":
                def_ignore += ex["value"]
                if ex.get("weakness_extra") and self.chars[cid].element in \
                        self.enemies[target].weaknesses:
                    def_ignore += ex["weakness_extra"]
            elif t == "hit_stack_dmg":
                # 论剑：同目标命中叠层（近似：按 act 计层，战技多段未逐段模拟）
                n = self.equip_stacks.get(cid, {}).get(ex["src"], 0)
                bonus += n * ex["per_stack"]
            elif t == "ult_dmg" and kind == "ult":
                bonus += ex["value"]     # 红A E4：终结技伤害提高 150%
        # 23003 战技后增伤（下一个行动的队友，buff 存在期间其他角色攻击都吃——近似）
        for b in self.buffs._buffs:
            if b.stat == "equip_next_ally_dmg" and b.source != cid:
                bonus += b.value
        # 312-4 同属性队友增伤（装备者效果，作用于元素相同的其他角色）
        for owner, ch in self.chars.items():
            if owner == cid or self.chars[owner].element != elem:
                continue
            for ex in ch.equipment_effects:
                if ex["type"] == "same_element_team_dmg":
                    bonus += ex["value"]
        return bonus, def_ignore

    def _dynamic_dmg_bonus(self, cid: str) -> float:
        """战斗内动态增伤面板（BuffManager + 花火层数 + 常驻队伍效果）。"""
        value = self.buffs.sum_for("dmg_bonus", cid)
        value += 0.03 * min(self.sp_spent_count, 3)
        for owner in self.chars.values():
            if any(ex["type"] == "mem_team_dmg" for ex in owner.equipment_effects):
                value += next(ex["value"] for ex in owner.equipment_effects
                              if ex["type"] == "mem_team_dmg")
        return value

    def _effective_stats(self, cid: str) -> Stats:
        s = replace(self.stats[cid])
        s.crit_dmg += self.buffs.sum_for("crit_dmg", cid)
        s.crit_rate += self.buffs.sum_for("crit_rate", cid)   # 记忆主 E1：声援暴击
        s.atk = s.atk * (1.0 + self.buffs.sum_for("atk_pct", cid)) + self.buffs.sum_for("atk_flat", cid)
        # 速度/防御效果通过面板与对应系统读取；行动顺序速度以 ActionQueue 为真值。
        s.speed = max(0.0, self.queue.get_speed(cid) or s.speed)
        s.defense = max(0.0, s.defense * (1.0 + self.buffs.sum_for("def_pct", cid))
                        + self.buffs.sum_for("def_flat", cid))
        s.break_effect += self.buffs.sum_for("break_effect", cid)
        s.energy_regen += self.buffs.sum_for("energy_regen", cid)
        s.dmg_bonus += self._dynamic_dmg_bonus(cid)
        s.heal_bonus += self.buffs.sum_for("heal_bonus", cid)
        # 装备条件面板：sp_cap_ge_atk（23046：SP 上限≥6 才触发——红A E2 上限 5 不触发）
        for ex in self._equip_effects(cid):
            if ex["type"] == "sp_cap_ge_atk" and self.sp_max >= ex["sp_cap_ge"]:
                s.atk *= (1.0 + ex["value"])
        # 装备条件面板（stat_conditional）：如蕉乐园召唤在场暴伤
        for ex in self._equip_effects(cid):
            if ex["type"] == "stat_conditional" and ex.get("cond") == "memosprite_present":
                if self.memosprite is not None and self.memosprite["alive"]:
                    if ex["stat"] == "crit_dmg":
                        s.crit_dmg += ex["value"]
        return s

    def _current_multipliers(self, damager: str = "") -> Multipliers:
        m = Multipliers()
        # stats.dmg_bonus 由 _effective_stats 汇总；此处仅保留非面板伤害乘区，避免重复计算。
        m.dmg_bonus = 0.0
        # 22006 飞向粉色的明天：常驻全队增伤已由 _effective_stats 汇总。
        # 花火天赋：每耗 1 SP 全队增伤 3%（叠 3 层）——进入当前面板，由 _effective_stats 统一汇总。
        # 花火 E2：天赋每层额外全队无视防御（谜诡 3 层）
        for cid, ch in self.chars.items():
            for ex in ch.equipment_effects:
                if ex["type"] == "talent_def_ignore":
                    m.def_ignore += ex["per_layer"] * min(self.sp_spent_count, 3)
                    break
        # 知更鸟 E1：协奏期间全属性抗性穿透 24%
        if self.concert_rounds > 0:
            for c in self.chars.values():
                for ex in c.equipment_effects:
                    if ex["type"] == "concert_res_pen":
                        m.res_pen += ex["value"]
        m.true_dmg = self.buffs.sum_for("true_dmg")
        m.extra_atk_pct = self.buffs.sum_for("concert_atk")
        return m

    # ---------- 天赋触发 ----------
    def _on_ally_attack(self, cid: str, target: str) -> None:
        """队友攻击后触发：红A 追击 / 知更鸟回能 / 装备叠层（如【歌咏】）。"""
        # 装备叠层：我方角色每次攻击 → 装备者 +1 层（23026 歌咏）
        for other_id, ch in self.chars.items():
            for ex in ch.equipment_effects:
                if ex["type"] == "stack_energy_regen" and ex.get("trigger") == "ally_attack":
                    stacks = self.equip_stacks.setdefault(other_id, {})
                    key = ex["src"]
                    if stacks.get(key, 0) < ex["max"]:
                        stacks[key] = stacks.get(key, 0) + 1
        for other_id in self.chars:
            if other_id == cid:
                continue
            talent = self.chars[other_id].talent_extra
            if talent.get("followup_on_ally_attack"):
                self._archer_followup(other_id, target)
            if talent.get("energy_on_ally_attack"):
                gained = talent["energy_on_ally_attack"] * self._energy_regen(other_id)
                # 知更鸟 E2：天赋回能额外 +1
                for ex in self._equip_effects(other_id):
                    if ex["type"] == "talent_energy_bonus":
                        gained += ex["value"] * self._energy_regen(other_id)
                self.energy[other_id] += gained
        # 知更鸟协奏附加伤害：任何我方攻击后
        if self.concert_rounds > 0:
            robin = next(
                (c for c in self.chars.values() if c.talent_extra.get("skill_effects", {}).get("ult", {}).get("concert")),
                None,
            )
            if robin:
                self._additional_damage(robin, target)

    def _archer_followup(self, cid: str, target: str) -> None:
        # 目标已不属于当前波次时不触发：不消耗充能，也不虚假恢复 SP/能量。
        if not self._enemy_is_current(target):
            return
        charge = self.fate_charge.get(cid, 0.0)
        if charge < 1.0:
            return
        self.fate_charge[cid] = charge - 1.0
        mult = self.chars[cid].skills["talent"].mult
        self._deal_damage(cid, target, mult, kind="followup")
        # 追击恢复 1 个战技点 + 天赋回能（wiki：心眼真 回 5 能量，× 充能效率）
        self.sp = min(self.sp_max, self.sp + 1.0)
        self.sp_timeline.append((self.t, self.sp))
        self.energy[cid] += self.chars[cid].talent_extra.get("followup_energy", 0.0) * self._energy_regen(cid)
        self._memosprite_charge_from_energy(self.chars[cid].talent_extra.get("followup_energy", 0.0) * self._energy_regen(cid))

    def _additional_damage(self, robin: CharacterData, target: str) -> None:
        """知更鸟协奏附加伤害：固定双暴（100%/150%）。"""
        # 原攻击已触发切波时，附伤不能沿用旧 id 或穿透到下一波。
        if not self._enemy_is_current(target):
            return
        enemy = self.enemies[target]
        robin_stats = self._effective_stats(robin.id)
        m = self._current_multipliers(damager=robin.id)
        dmg = flat_damage(
            getattr(self, "_concert_additional_mult", 0.72),
            robin_stats.atk,
            robin_stats.dmg_bonus + m.dmg_bonus,
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
        # 多波次（D8）：目标已死亡/波次切换后不再削韧
        if target not in self.enemy_hp or self.enemy_hp[target] <= 0.0:
            return
        elem = self.chars[cid].element
        enemy = self.enemies[target]
        has_weakness = elem in enemy.weaknesses or \
            self.buffs.sum_for(f"enemy_weakness_add:{elem}", target) > 0.0  # 星魂 E2 动态弱点
        if not has_weakness:
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
            m = self._current_multipliers(damager=cid)
            dmg = break_damage(
                self.chars[cid].element, stats.break_effect,
                self.enemies[target].toughness,
                self._def_m(target), self._res_m(target, self.chars[cid].element),
                vuln=m.vuln, final_dmg=m.final_dmg, true_dmg=m.true_dmg,
            )
            self._record_damage(cid, target, dmg, "break")

    # ---------- 忆灵 / 敌人 ----------
    def _memosprite_act(self) -> None:
        """迷迷行动（docs/research/memory-trailblazer-mem.md 定值）：
        - 充能未满：普通行动 = 4 段随机单体 + 全体（迷迷攻击），行动后充能 +5%
        - 充能已满：强化行动 = 100% 拉条声援目标 + 施加【声援】（per-hit 真伤，3 次行动）
        """
        if self.memosprite is None or not self.memosprite["alive"]:
            self.queue.remove("MEM")
            return
        cfg = self.chars[self.memosprite_owner].talent_extra.get("memosprite", {})
        enhanced = self.memosprite["charge"] >= 100.0
        if enhanced:
            self.memosprite["charge"] = 0.0
            # 强化：拉条声援目标 + 声援 buff（目标选择 = 玩家决策，v1.5 自动默认主C）
            target = self._mems_support_target()
            self.queue.advance(target, cfg.get("enhanced_advance_pct", 1.0))
            self.buffs.add("mems_support", cfg.get("support_true_dmg", 0.28),
                           "MEM", cfg.get("support_rounds", 3), target=target)
            # 记忆主 E1：声援目标暴击率 +10%
            for ex in self._equip_effects(self.memosprite_owner):
                if ex["type"] == "mems_support_crit":
                    self.buffs.add("crit_rate", ex["value"], "MEM",
                                   cfg.get("support_rounds", 3), target=target)
            self.log.append(ActionLog(self.t, "MEM", "memosprite_ult",
                                      detail=f"强化：拉条+声援 {target}"))
        else:
            # 普通：4 段随机单体（每段独立随机目标，E12 RNG）+ 全体
            hits = int(cfg.get("basic_hits", 4))
            mult = cfg.get("basic_mult", 0.36)
            alive = [eid for eid, hp in self.enemy_hp.items() if hp > 0.0]
            for _ in range(hits):
                if not alive:
                    break
                target = self.rng.choice(alive)
                dmg, nc, cdm = self._mem_deal(target, mult)
                if dmg > 0.0:
                    self._record_damage("MEM", target, dmg, "normal",
                                        noncrit=nc, crit_dmg_mult=cdm)
            if alive:
                self._mem_damage_all(cfg.get("basic_aoe_mult", 0.90))
            # 123-4：忆灵攻击时装备者暴伤提升（迷迷共享装备者面板）
            for ex in self._equip_effects(self.memosprite_owner):
                if ex["type"] == "mem_cd_buff":
                    self.buffs.add("crit_dmg", ex["value"], self.memosprite_owner,
                                   ex["duration"], target=self.memosprite_owner)
            # 行动充能 +5%（充能未满时）
            self.memosprite["charge"] = min(100.0, self.memosprite["charge"]
                                             + cfg.get("act_charge", 0.05) * 100.0)
            self.log.append(ActionLog(self.t, "MEM", "memosprite_skill"))
        self.queue.reset_after_action("MEM")

    def _mem_deal(self, eid: str, mult: float) -> float:
        """迷迷单段伤害（攻击 = 忆师攻击，继承比例待实测；段级暴击判定）。"""
        owner_stats = self._effective_stats(self.memosprite_owner)
        m = self._current_multipliers(damager=self.memosprite_owner)
        noncrit = noncrit_damage(
            mult, owner_stats.atk, owner_stats, m,
            self.enemies[eid].defense,
            self.enemies[eid].resistances.get("Ice", 0.0),
            self.attacker_level,
            enemy_broken=self.toughness[eid] <= 0.0,
        )
        cd_mult = 1.0 + owner_stats.crit_dmg
        if self.rng.random() < min(owner_stats.crit_rate, 1.0):
            return noncrit * cd_mult, noncrit, cd_mult
        return noncrit, noncrit, cd_mult

    def _mems_support_target(self) -> str:
        """声援目标：官方为玩家决策；v1.5 自动默认主C（队伍第一个角色），P1 LLM 指挥时由决策指定。"""
        return next(iter(self.chars))

    def _memosprite_charge_from_energy(self, amount: float) -> None:
        """全队每恢复 10 点能量 → 迷迷充能 +1%（research 定值）。"""
        if self.memosprite is not None and amount > 0.0:
            self.memosprite["charge"] = min(100.0, self.memosprite["charge"] + amount / 10.0)

    def _enemy_act(self, eid: str) -> None:
        """敌人行动（①敌人 AI）：破韧恢复 → 技能循环 → 攻击我方 → 受击回能 → 死亡。

        技能选择：按列表序取第一个冷却就绪的技能（官方行为树未解包，近似：
        AI_CD 冷却 + 列表序；无 skills 的敌人保持 v1 行为——只回韧性不攻击）。
        目标选择：随机存活角色（官方仇恨机制未模拟，标注）。
        """
        enemy = self.enemies[eid]
        # 破韧恢复（保留 v1）
        if self.toughness[eid] <= 0.0:
            self.toughness[eid] = enemy.toughness
        # 技能冷却递减
        cd = self.enemy_cd.setdefault(eid, {})
        for i in cd:
            if cd[i] > 0:
                cd[i] -= 1
        # 技能选择：列表序第一个冷却就绪（伤害技能）
        skill = None
        if enemy.skills:
            for i, sk in enumerate(enemy.skills):
                if cd.get(i, 0) <= 0:
                    skill = sk
                    cd[i] = sk.ai_cd
                    break
        if skill is not None:
            # 只攻击参与生存的角色（面板配置了 HP；未配置 = 生存未启用，v1 兼容）
            alive = [cid for cid in self.chars
                     if self.char_hp_max.get(cid, 0.0) > 0.0 and self.char_hp[cid] > 0.0]
            if alive:
                target = self.rng.choice(alive)
                # 伤害 = 敌人攻击 × 倍率 × 我方防御减免（80 级攻方 vs 我方防御）
                from .damage import defense_multiplier
                res = 0.0  # 我方元素抗性未模拟（我方无抗性概念，标注）
                dmg = enemy.atk * skill.mult * defense_multiplier(
                    self.attacker_level, self.stats[target].defense) * (1.0 - res)
                self.char_hp[target] = max(0.0, self.char_hp[target] - dmg)
                # 受击回能（官方 SPHitBase × 充能效率；死亡不回能）
                if self.char_hp[target] > 0.0:
                    gained = skill.sp_hit * self._energy_regen(target)
                    self.energy[target] += gained
                    self._memosprite_charge_from_energy(gained)
                self.log.append(ActionLog(
                    self.t, eid, "enemy_attack",
                    detail=f"{skill.name}→{target} 伤害{dmg:.0f}"))
                if self.char_hp[target] <= 0.0:
                    self.queue.remove(target)
                    self.log.append(ActionLog(self.t, eid, "enemy_kill",
                                              detail=f"击杀 {target}"))
            else:
                self.log.append(ActionLog(self.t, eid, "enemy_action"))
        else:
            self.log.append(ActionLog(self.t, eid, "enemy_action"))
        self.queue.reset_after_action(eid)

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
            enemy_wave=self.enemy_wave,
            skill_streak=dict(self.skill_streak),
            char_hp=dict(self.char_hp), char_hp_max=dict(self.char_hp_max),
            enemy_cd={eid: dict(c) for eid, c in self.enemy_cd.items()},
            setup_state=copy.deepcopy(self.setup_state),
            wave_energy_effects=list(self._wave_energy_effects),
            start_effects_applied=self._start_effects_applied,
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
        # 多波次：敌人引用随波次恢复（enemy_hp/toughness/enemy_cd 由快照重建）
        self.enemy_wave = snap.enemy_wave
        self.enemies = self._waves[self.enemy_wave]
        self.skill_streak = dict(snap.skill_streak)
        self.char_hp = dict(snap.char_hp)
        self.char_hp_max = dict(snap.char_hp_max)
        self.enemy_cd = {eid: dict(c) for eid, c in snap.enemy_cd.items()}
        self.setup_state = copy.deepcopy(snap.setup_state)
        self._wave_energy_effects = list(snap.wave_energy_effects)
        self._start_effects_applied = snap.start_effects_applied
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
    def _record_damage(self, source: str, target: str, amount: float, kind: str,
                      noncrit: float = 0.0, crit_dmg_mult: float = 0.0) -> None:
        """记录伤害事件；段级伤害携带非暴击基准（④ 对账端点）。"""
        self.damage_events.append(DamageEvent(self.t, source, target, amount, kind,
                                              noncrit=noncrit, crit_dmg_mult=crit_dmg_mult))
        self.enemy_hp[target] = max(0.0, self.enemy_hp[target] - amount)
        # 声援真伤（research 定值）：声援目标每段伤害后附加真伤 = 该段伤害 × 比例
        # 真伤本身不再触发真伤；不削韧、不算行动、独立乘区
        if kind != "true" and amount > 0.0:
            pct = self.buffs.sum_for("mems_support", source)
            if pct > 0.0:
                true = amount * pct
                self.damage_events.append(DamageEvent(self.t, source, target, true, "true"))
                self.enemy_hp[target] = max(0.0, self.enemy_hp[target] - true)
        # 死亡移除 + 多波次推进（D8）：目标死亡即移出行动队列；全灭 → 下一波入场
        if self.enemy_hp[target] <= 0.0:
            self.queue.remove(target)
        if not any(hp > 0.0 for hp in self.enemy_hp.values()):
            for eid in list(self.enemies):
                self.queue.remove(eid)
            if self.enemy_wave + 1 < len(self._waves):
                self._spawn_wave(self.enemy_wave + 1)

    def _spawn_wave(self, idx: int) -> None:
        """多波次推进：新一波敌人入场（HP/韧性/冷却重置、行动条满速入队）。"""
        self.enemy_wave = idx
        self.enemies = self._waves[idx]
        self.enemy_hp = {eid: e.hp for eid, e in self.enemies.items()}
        self.toughness = {eid: e.toughness for eid, e in self.enemies.items()}
        self.enemy_cd = {eid: {i: 0 for i in range(len(e.skills))}
                         for eid, e in self.enemies.items()}
        for eid, e in self.enemies.items():
            self.queue.add(eid, e.speed)
        self.log.append(ActionLog(self.t, "WAVE", f"wave_{idx + 1}",
                                  detail=f"第{idx + 1}波敌人入场"))
        self._apply_wave_start_effects(idx)

    def _result(self) -> SimResult:
        r = SimResult(t_end=self.t)
        r.total_damage = sum(e.amount for e in self.damage_events)
        for e in self.damage_events:
            r.damage_by_source[e.source] = r.damage_by_source.get(e.source, 0.0) + e.amount
            r.damage_by_kind[e.kind] = r.damage_by_kind.get(e.kind, 0.0) + e.amount
        r.setup = copy.deepcopy(self.setup_state)
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
