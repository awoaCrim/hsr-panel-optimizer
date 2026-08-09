"""推演会话（ADR-0007 3.3 / D7）——LLM 指挥接口 + 完整推演报告。

决策模型（D2/D3）：
- 决策点 = 我方主动行动前（observe 推进到此处停下；敌人/忆灵行动自动执行）
- act = 决策单元：主动行动（skill/target）+ 该步大招指令（ults）+ 全部自动连锁
- undo 到任意 act 边界（"回退任意行动"）；被撤销路线归档为放弃路线（D7 报告素材）
- 大招时机（D2）：能量满可不立即放。ults=None = 全放；ults={} = 全 hold；
  ults={cid: True/False} = 逐角色指定。指令为 act 级（下一 act 重新指定）。

配置（D5）：Setup 不可 undo 触及；propose_setup = 冻结旧会话开新会话，undo 栈清空。
随机性（D1）：固定 seed；RNG 状态随事件流快照，undo 后重放同决策结果一致。
预算（D4）：每步最多 3 次回退、全局 50 次；耗尽后强制收敛。

用法：
  python -m hsr_sim.rehearse --demo [--brief]      # 演示决策器全流程 + D7 报告
  python -m hsr_sim.rehearse                        # REPL（stdin 逐行 JSON 指令）
  python -m hsr_sim.rehearse observe --state s.json # 单指令模式（--state 持久化会话）
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .engine.simulate import Simulator
from .engine.snapshot import BattleSnapshot
from .loader import DATA_DIR, load_rotation
from .model import Action


class RehearseError(Exception):
    """推演会话使用错误（决策点/参数/预算）。"""


class UndoBudgetExceeded(RehearseError):
    """回退预算耗尽（D4：强制收敛）。"""


# ---------------------------------------------------------------------------
# 记录结构
# ---------------------------------------------------------------------------

@dataclass
class ActRecord:
    """一次 act（决策单元）：主动行动 + 大招指令 + 可观察结果摘要。"""

    index: int
    unit_id: str
    skill: str                 # 实际执行技能
    target: str                # 实际结算目标（默认目标已解析）
    requested_skill: str = ""  # LLM/调用方原请求；正常应与 skill 相同
    requested_target: str = ""
    ults: Optional[Dict[str, bool]] = None
    note: str = ""
    result: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AbandonedRoute:
    """放弃路线（分支树摘要素材，D7）：被撤销/重开的 act 段。"""

    index: int
    fork_after: int              # 从第几个 act 之后分叉（-1 = 初始）
    acts: List[ActRecord] = field(default_factory=list)
    reason: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

class RehearsalSession:
    """一次推演会话：配置层（Setup，不可 undo）+ 决策层（act 序列 + 放弃路线）。"""

    def __init__(self, sim: Simulator, *, name: str = "rehearsal",
                 undo_budget: int = 50, per_step_budget: int = 3,
                 history: Optional[List[Dict]] = None) -> None:
        self.sim = sim
        # 推演会话的初始边界必须已完成“进入战斗”自动阶段：忆灵开局召唤、
        # 翁瓦克等开局拉条先结算，再向 LLM 暴露首个决策点并建立 undo 初始快照。
        sim._ensure_memosprite_summon()
        sim._apply_start_effects()
        self.name = name
        self.undo_budget = undo_budget
        self.per_step_budget = per_step_budget
        self.history = list(history or [])          # 冻结的旧会话（D5 参考）
        self.initial = sim.snapshot()
        self.snapshots: List[BattleSnapshot] = [self.initial]   # [i] = i 个 act 后状态
        self.acts: List[ActRecord] = []
        self.abandoned: List[AbandonedRoute] = []
        self.total_undo = 0
        self.undo_since_act = 0
        self._act_seq = 0
        self._config_paths = None   # 持久化用（from_files 设置）

    # ---------- 工厂 / 配置层（D5） ----------

    @classmethod
    def from_files(cls, team: Path = DATA_DIR / "team_reda.json",
                   enemy: Path = DATA_DIR / "enemy_elite90.json",
                   rotation: Path = DATA_DIR / "rotation.json",
                   seed: int = 0, name: str = "rehearsal", legacy: bool = False,
                   undo_budget: int = 50, per_step_budget: int = 3,
                   history: Optional[List[Dict]] = None) -> "RehearsalSession":
        team = Path(team)
        enemy = Path(enemy)
        rotation_path = Path(rotation)
        # 队伍数据可走 normalized；关卡始终以调用方传入的 enemy 文件为准。
        # 旧实现的 non-legacy 路径会忽略 enemy 参数、固定加载 normalized 默认靶场，
        # 导致 WebUI/CLI 看似切关，实际仍在打旧敌人。
        from .loader import load_enemies, load_enemy_waves
        _ed = json.loads(enemy.read_text(encoding="utf-8"))
        enemies, level, target_av = load_enemies(enemy)
        waves = load_enemy_waves(enemy)
        if legacy:
            from .loader import load_team
            char_dir = team.parent / "characters"
            characters, stats, _speed_targets = load_team(team, char_dir)
            unverified: List[str] = []
        else:
            from .data.loader import load_enemies_normalized, load_team_normalized
            characters, stats, _speed_targets, unverified = load_team_normalized(team)
            # 默认精英靶场保留 normalized 信任度信封；其他关卡使用文件内显式清单。
            if enemy.resolve() == (DATA_DIR / "enemy_elite90.json").resolve():
                _, _, _, unv2 = load_enemies_normalized()
            else:
                unv2 = list(_ed.get("unverified_inputs", []))
            unverified = unverified + unv2
        # 开局状态（标准规则 SP 4/能量 0；关卡配置可覆盖——末日幻影/模拟宇宙等）
        initial_sp = _ed.get("initial_sp", 4.0)
        initial_energy = _ed.get("initial_energy", {})
        rotation = load_rotation(rotation_path)
        mem_speed = 130.0
        for c in characters.values():
            mem = c.talent_extra.get("memosprite")
            if mem:
                mem_speed = mem.get("speed", mem_speed)
        sim = Simulator(characters, stats, enemies, rotation, target_av, level, mem_speed,
                        unverified_inputs=unverified, seed=seed,
                        initial_sp=initial_sp, initial_energy=initial_energy,
                        waves=waves)
        session = cls(sim, name=name, undo_budget=undo_budget, per_step_budget=per_step_budget,
                      history=history)
        session._config_paths = (team, enemy, rotation_path, legacy)
        return session

    def propose_setup(self, *, name: Optional[str] = None, **kwargs: Any) -> "RehearsalSession":
        """配置变更 = 冻结旧树开新会话（D5）：undo 栈清空，旧会话归档为历史参考。"""
        new = RehearsalSession.from_files(
            **kwargs, name=name or f"{self.name}#{len(self.history) + 1}",
            undo_budget=self.undo_budget, per_step_budget=self.per_step_budget,
            history=[self.frozen_record()] + self.history)
        return new

    def frozen_record(self) -> Dict[str, Any]:
        """冻结摘要（配置变更后作为历史参考，D5/D7）。"""
        return {
            "setup": self._setup_meta(),
            "decision_trail": [_act_dict(a) for a in self.acts],
            "abandoned": [_route_dict(r) for r in self.abandoned],
            "final": self._state_summary(),
        }

    def _setup_meta(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "seed": self.sim.seed,
            "team": [f"{cid} {self.sim.chars[cid].name}" for cid in self.sim.chars],
            "enemies": [e.name for wave in self.sim._waves for e in wave.values()],
            "waves": [[e.name for e in wave.values()] for wave in self.sim._waves],
            "target_av": self.sim.target_av,
            "level": self.sim.attacker_level,
        }

    # ---------- 决策循环 ----------

    def observe(self) -> Dict[str, Any]:
        """推进到下一个决策点（自动执行敌人/忆灵行动；大招全 hold 等 LLM 决定）并返回完整状态。

        自动终止（D6 物理边界）：敌人全灭 / 行动值耗尽 / 队列空。
        """
        self.sim.ult_hold = set(self.sim.chars)
        try:
            while True:
                if self._terminal_reason() is not None:
                    break
                nxt = self.sim.queue.next()
                if nxt is not None and nxt[0] in self.sim.chars:
                    break                        # 决策点：我方主动行动前
                self.sim.run_step()
        finally:
            self.sim.ult_hold = set()
        return self._state()

    def act(self, skill: str = "basic", target: str = "", ults: Optional[Dict[str, bool]] = None,
            note: str = "") -> Dict[str, Any]:
        """执行一个决策单元：主动行动 + 该步大招指令 + 全部自动连锁（D3）。

        skill：basic/skill（大招不占行动条，由 ults 指定时机）；
        ults：None = 放全部满能大招，{} = 全 hold，{cid: bool} = 逐角色。
        """
        term = self._terminal_reason()
        if term is not None:
            raise RehearseError(f"推演已终止：{term}（observe 查看最终状态）")
        nxt = self.sim.queue.next()
        if nxt is None or nxt[0] not in self.sim.chars:
            raise RehearseError("当前无待决策的我方行动：先 observe() 推进到决策点")
        cid = nxt[0]
        if skill == "ult":
            raise RehearseError("大招不占行动条：用 ults 参数指定大招时机（D2），skill 只能为 basic/skill")
        if skill not in self.sim.chars[cid].skills:
            raise RehearseError(f"角色 {cid} 无技能 {skill!r}（可选：{list(self.sim.chars[cid].skills)}）")
        skill_obj = self.sim.chars[cid].skills[skill]
        if skill_obj.sp < 0 and self.sim.sp < -skill_obj.sp:
            raise RehearseError(
                f"{cid} 的 {skill} 需要 {-skill_obj.sp:g} 点战技点，"
                f"当前仅 {self.sim.sp:g} 点（战技点不足；请选择 basic）")
        target_type = self._skill_target_type(skill_obj)
        requested_target = target
        if target_type == "none" and target:
            raise RehearseError(
                f"{cid} 的 {skill} 是非攻击/无目标技能，无需选择目标（target 必须留空）")
        if target_type == "enemy":
            alive_enemies = [eid for eid, hp in self.sim.enemy_hp.items() if hp > 0.0]
            if target and target not in alive_enemies:
                raise RehearseError(
                    f"敌方目标 {target!r} 当前不可选（可选：{alive_enemies}）")
            target = target or (alive_enemies[0] if alive_enemies else "")
            if not target:
                raise RehearseError("当前没有可选的存活敌方目标")
        # 拉条目标校验（官方目标选择器规则）：advance 技能目标必须是存活队友且不可自拉
        if skill_obj.advance_pct:
            allies = [c for c in self.sim.chars if c != cid and
                      (self.sim.char_hp_max.get(c, 0.0) <= 0.0 or
                       self.sim.char_hp.get(c, 1.0) > 0.0)]
            if not target and not skill_obj.advance_self:
                raise RehearseError(
                    f"{cid} 的 {skill} 必须选择我方目标（可选队友：{allies}）")
            if not skill_obj.advance_self and target == cid:
                raise RehearseError(
                    f"{cid} 的技能不可选择自己为拉条目标（官方目标选择器排除自身），"
                    f"可选队友：{allies}")
            if target and target not in allies and not (skill_obj.advance_self and target == cid):
                raise RehearseError(
                    f"拉条目标 {target!r} 必须是我方队友（可选：{allies}）")
            if not target and skill_obj.advance_self:
                target = cid
        if ults is None:
            allowed = set(self.sim.chars)        # 默认：满能即放（与旧行为一致）
        elif isinstance(ults, dict):
            unknown = set(ults) - set(self.sim.chars)
            if unknown:
                raise RehearseError(f"ults 含未知角色：{sorted(unknown)}")
            allowed = {c for c, flag in ults.items() if flag}
            for ult_cid in allowed:
                ult = self.sim.chars[ult_cid].skills.get("ult")
                if ult is None:
                    raise RehearseError(f"角色 {ult_cid} 没有终结技")
                if self.sim.energy[ult_cid] < ult.energy_cost:
                    raise RehearseError(
                        f"{ult_cid} 终结技能量未满："
                        f"{self.sim.energy[ult_cid]:g}/{ult.energy_cost:g}")
                if self.sim.char_hp_max.get(ult_cid, 0.0) > 0.0 and \
                        self.sim.char_hp.get(ult_cid, 1.0) <= 0.0:
                    raise RehearseError(f"{ult_cid} 已倒下，不能释放终结技")
        else:
            raise RehearseError("ults 应为 dict {cid: bool} 或 None")
        dmg_before = self._total_damage()
        sp_before = self.sim.sp
        ult_before = dict(self.sim.ult_count)
        breaks_before = len(self.sim.breaks)
        log_before = len(self.sim.log)
        self.sim.external_action = Action(unit_id=cid, action=skill, target=target)
        self.sim.ult_hold = set(self.sim.chars) - allowed
        self.sim.ult_override = bool(allowed)
        try:
            self.sim.run_step()
        finally:
            self.sim.ult_hold = set()
            self.sim.ult_override = None
        actual_skill = next(
            (entry.action for entry in self.sim.log[log_before:]
             if entry.unit_id == cid and entry.action in ("basic", "skill")),
            skill,
        )
        self._act_seq += 1
        rec = ActRecord(index=self._act_seq, unit_id=cid,
                        skill=actual_skill, target=target,
                        requested_skill=skill, requested_target=requested_target,
                        ults=ults, note=note,
                        result=self._act_summary(dmg_before, sp_before, ult_before,
                                                breaks_before, actual_skill, target))
        self.acts.append(rec)
        self.snapshots.append(self.sim.snapshot())
        self.undo_since_act = 0
        return rec.result

    # ---------- 回退 / 重开 ----------

    def undo(self, reason: str = "") -> Dict[str, Any]:
        """撤销最近一次 act（LLM 自评用，D4）。"""
        return self.undo_to(len(self.acts) - 1, reason)

    def undo_to(self, k: int, reason: str = "") -> Dict[str, Any]:
        """回退到任意 act 边界（"回退任意行动"）：第 k 个 act 之后（k=0 = 初始）。

        被撤销的 act 段归档为放弃路线（D7：尝试过哪些路线、为什么放弃）。
        预算检查优先（D4）：预算耗尽时强制收敛，不因操作本身非法而绕过。
        """
        if self.undo_since_act >= self.per_step_budget:
            raise UndoBudgetExceeded(
                f"每步回退预算 {self.per_step_budget} 次已用尽（D4：先 act 再回退会重置）")
        if self.total_undo >= self.undo_budget:
            raise UndoBudgetExceeded(
                f"全局回退预算 {self.undo_budget} 次已用尽（D4：强制从现有分支收敛）")
        if k < 0 or k >= len(self.acts):
            if not self.acts:
                raise RehearseError("无 act 可撤销")
            raise RehearseError(f"k 越界：0 ≤ k ≤ {len(self.acts) - 1}（当前 {len(self.acts)} 个 act）")
        removed = self.acts[k:]
        self._archive_route(removed, reason, fork_after=k - 1)
        self.sim.restore(self.snapshots[k])
        self.acts = self.acts[:k]
        self.snapshots = self.snapshots[:k + 1]
        self.total_undo += 1
        self.undo_since_act += 1
        return self._state()

    def restart(self, reason: str = "") -> Dict[str, Any]:
        """回到初始状态；当前路线归档为放弃路线（配置不变，D5）。"""
        self._archive_route(self.acts, reason, fork_after=-1)
        self.sim.restore(self.initial)
        self.acts = []
        self.snapshots = [self.sim.snapshot()]
        self.undo_since_act = 0
        return self._state()

    def _archive_route(self, acts: List[ActRecord], reason: str, fork_after: int) -> None:
        if not acts:
            return
        self.abandoned.append(AbandonedRoute(
            index=len(self.abandoned) + 1, fork_after=fork_after, acts=list(acts),
            reason=reason or "", summary=self._state_summary()))

    # ---------- 状态 / 摘要 ----------

    def _terminal_reason(self) -> Optional[str]:
        sim = self.sim
        if all(hp <= 0.0 for hp in sim.enemy_hp.values()):
            return "enemies_defeated"
        nxt = sim.queue.next()
        if nxt is None:
            return "queue_empty"
        if sim.t + nxt[1] > sim.target_av:
            return "av_exhausted"
        return None

    def _total_damage(self) -> float:
        return sum(e.amount for e in self.sim.damage_events)

    def _act_summary(self, dmg_before: float, sp_before: float, ult_before: Dict[str, int],
                     breaks_before: int, actual_skill: str, actual_target: str) -> Dict[str, Any]:
        sim = self.sim
        return {
            "t": round(sim.t, 4),
            "skill": actual_skill,
            "target": actual_target,
            "damage_delta": round(self._total_damage() - dmg_before, 1),
            "ult_used": [c for c in sim.chars if sim.ult_count.get(c, 0) > ult_before.get(c, 0)],
            "new_breaks": [eid for _t, eid in self.sim.breaks[breaks_before:]],
            "sp_before": round(sp_before, 3),
            "sp_delta": round(sim.sp - sp_before, 3),
            "sp": round(sim.sp, 3),
            "energy": {c: round(sim.energy[c], 3) for c in sim.chars},
            "kills": [eid for eid, hp in sim.enemy_hp.items() if hp <= 0.0],
            "wave": sim.enemy_wave + 1,
            "wave_count": len(sim._waves),
        }

    @staticmethod
    def _skill_target_type(skill_obj) -> str:
        """把现有技能结构映射为官方选择器类别：敌方 / 我方 / 无目标。"""
        if skill_obj.advance_pct:
            return "ally"
        if skill_obj.mult > 0.0:
            return "enemy"
        return "none"

    def _decision_point(self) -> Optional[Dict[str, Any]]:
        sim = self.sim
        nxt = sim.queue.next()
        if nxt is None or nxt[0] not in sim.chars:
            return None
        cid = nxt[0]
        if sim.char_hp_max.get(cid, 0.0) > 0.0 and sim.char_hp.get(cid, 1.0) <= 0.0:
            return None    # ① 死亡角色不进入决策点（观察者跳过）
        all_skill_names = [k for k in ("basic", "skill") if k in sim.chars[cid].skills]
        skill_options = {}
        for k in all_skill_names:
            sk = sim.chars[cid].skills[k]
            sp_cost = max(0.0, -float(sk.sp))
            available = sim.sp >= sp_cost
            mechanics = dict(
                sim.chars[cid].talent_extra.get("skill_effects", {}).get(k) or {})
            mechanics.pop("note", None)
            skill_options[k] = {
                "is_attack": sk.mult > 0.0,
                "target_type": self._skill_target_type(sk),
                "sp_delta": sk.sp,
                "sp_cost": sp_cost,
                "mechanics": mechanics,
                "available": available,
                "unavailable_reason": "" if available else
                f"战技点不足：需要 {sp_cost:g}，当前 {sim.sp:g}",
            }
        skills = [k for k in all_skill_names if skill_options[k]["available"]]
        ult_ready = [c for c in sim.chars
                     if not (sim.char_hp_max.get(c, 0.0) > 0.0 and sim.char_hp.get(c, 1.0) <= 0.0)
                     and sim.energy[c] >= sim.chars[c].skills["ult"].energy_cost]
        has_ally_target = any(o["target_type"] == "ally" and o["available"]
                              for o in skill_options.values())
        return {
            "unit": cid,
            "skills": skills,
            "skill_options": skill_options,
            "default": "skill" if "skill" in skills else "basic",
            "targets": [eid for eid, hp in sim.enemy_hp.items() if hp > 0.0],
            "ally_targets": [c for c in sim.chars if c != cid and
                             (sim.char_hp_max.get(c, 0.0) <= 0.0 or
                              sim.char_hp.get(c, 1.0) > 0.0)] if has_ally_target else [],
            "memosprite_present": bool(sim.memosprite and sim.memosprite.get("alive")),
            "ult_ready": ult_ready,
            "energy_status": "full" if cid in ult_ready else "charging",
        }

    def _state(self) -> Dict[str, Any]:
        """完整结构化状态（D9：不预设指标，LLM 自己解读）。"""
        sim = self.sim
        nxt = sim.queue.next()
        term = self._terminal_reason()
        result = sim._result()
        return {
            "setup": self._setup_meta(),
            "phase": "terminal" if term else "decision",
            "terminal_reason": term,
            "t": round(sim.t, 4),
            "steps": sim._steps,
            "queue": {
                "next": nxt[0] if nxt else None,
                "next_in": round(nxt[1], 4) if nxt else None,
                "entries": {uid: {"av": round(av, 4),
                                    "speed": self.sim.queue._entries[uid].speed}
                            for uid, av in sim.queue.snapshot().items()},
            },
            "energy": {cid: {"value": round(sim.energy[cid], 3),
                             "cost": sim.chars[cid].skills["ult"].energy_cost,
                             "full": sim.energy[cid] >= sim.chars[cid].skills["ult"].energy_cost}
                       for cid in sim.chars},
            "allies": {cid: {
                "name": sim.chars[cid].name,
                "hp": round(sim.char_hp[cid], 1),
                "hp_max": round(sim.char_hp_max[cid], 1),
                "hp_pct": round(sim.char_hp[cid] / sim.char_hp_max[cid] * 100, 1)
                if sim.char_hp_max[cid] > 0.0 else 0.0,
                "energy": round(sim.energy[cid], 3),
                "energy_cost": sim.chars[cid].skills["ult"].energy_cost,
                "energy_full": sim.energy[cid] >= sim.chars[cid].skills["ult"].energy_cost,
                "alive": sim.char_hp[cid] > 0.0,
            } for cid in sim.chars},
            "sp": {"value": round(sim.sp, 3), "max": sim.sp_max,
                   "timeline_tail": [[round(t, 3), sp] for t, sp in sim.sp_timeline[-5:]]},
            "wave": {"index": sim.enemy_wave + 1, "total": len(sim._waves)},
            "enemies": {eid: {
                "name": sim.enemies[eid].name,
                "hp": round(sim.enemy_hp[eid], 1),
                "hp_max": round(sim.enemies[eid].hp, 1),
                "hp_pct": round(sim.enemy_hp[eid] / sim.enemies[eid].hp * 100, 1),
                "toughness": round(sim.toughness[eid], 1),
                "toughness_max": round(sim.enemies[eid].toughness, 1),
                "broken": sim.toughness[eid] <= 0.0,
            } for eid in sim.enemies},
            "memosprite": ({"charge": round(sim.memosprite["charge"], 1),
                            "alive": sim.memosprite["alive"],
                            "owner": sim.memosprite_owner}
                           if sim.memosprite else None),
            "buffs": [{"stat": b.stat, "value": b.value, "source": b.source,
                       "left": b.duration, "target": b.target or "all"}
                      for b in sim.buffs._buffs],
            "decision": self._decision_point() if term is None else None,
            "damage": {"total": round(self._total_damage(), 1),
                       "by_kind": {k: round(v, 1) for k, v in result.damage_by_kind.items()},
                       "by_source": {k: round(v, 1) for k, v in result.damage_by_source.items()},
                       "recent": [[e.t, e.source, e.target, round(e.amount, 1), e.kind]
                                  for e in sim.damage_events[-8:]]},
            "progression": {"acts": len(self.acts),
                            "abandoned_routes": len(self.abandoned),
                            "undo_used": self.total_undo,
                            "undo_left": self.undo_budget - self.total_undo},
            "trust": {"level": "unverified" if sim._unverified else "verified",
                      "unverified_inputs": list(sim._unverified)},
        }

    def _state_summary(self) -> Dict[str, Any]:
        """结果摘要（放弃路线/最终状态共用，D7）。"""
        sim = self.sim
        result = sim._result()
        return {
            "t": round(sim.t, 4),
            "total_damage": round(result.total_damage, 1),
            "damage_by_kind": {k: round(v, 1) for k, v in result.damage_by_kind.items()},
            "kills": [eid for eid, hp in sim.enemy_hp.items() if hp <= 0.0],
            "enemy_hp_left": {eid: round(hp, 1) for eid, hp in sim.enemy_hp.items()},
            "char_hp_left": {cid: round(hp, 1) for cid, hp in sim.char_hp.items()},
            "char_deaths": [cid for cid, hp in sim.char_hp.items() if hp <= 0.0],
            "wave": sim.enemy_wave + 1,
            "wave_count": len(sim._waves),
            "sp": round(sim.sp, 3),
            "ult_count": dict(sim.ult_count),
            "action_count": dict(sim.action_count),
            "breaks": [[round(t, 3), eid] for t, eid in sim.breaks],
        }

    # ---------- 报告（D7） ----------

    def report_dict(self, stop_reason: str = "") -> Dict[str, Any]:
        if not stop_reason:
            terminal = self._terminal_reason()
            if terminal:
                stop_reason = f"物理终止：{terminal}"
        return {
            "setup": self._setup_meta(),
            "termination": {"reason": stop_reason},
            "decision_trail": [_act_dict(a) for a in self.acts],
            "branch_summary": {
                "current": {"acts": len(self.acts),
                            "undo_used": self.total_undo},
                "abandoned": [_route_dict(r) for r in self.abandoned],
                "history": list(self.history),
            },
            "final_state": self._state_summary(),
            "trust": {"level": "unverified" if self.sim._unverified else "verified",
                      "unverified_count": len(self.sim._unverified),
                      "unverified_inputs": list(self.sim._unverified)},
        }

    def report(self, brief: bool = False, stop_reason: str = "") -> str:
        """完整推演报告：决策轨迹 + 分支树摘要 + 最终状态（--brief 只给结论）。"""
        r = self.report_dict(stop_reason=stop_reason)
        lines = ["===== 推演报告 ====="]
        s = r["setup"]
        lines.append(f"配置：{s['name']}（seed={s['seed']}）队伍 {s['team']} "
                     f"vs {s['enemies']} 目标行动值 {s['target_av']}")
        if r["termination"]["reason"]:
            lines.append(f"[终止原因] {r['termination']['reason']}")
        if brief:
            f = r["final_state"]
            lines.append(f"最终：总伤害 {f['total_damage']:,.0f} / 击杀 {len(f['kills'])} "
                         f"/ SP {f['sp']} / 大招 {sum(f['ult_count'].values())} "
                         f"/ 行动 {sum(f['action_count'].values())} / 用时 {f['t']} AV / "
                         f"放弃路线 {len(r['branch_summary']['abandoned'])} 条")
        else:
            lines.append(f"\n[决策轨迹] {len(r['decision_trail'])} 个 act")
            for a in r["decision_trail"]:
                res = a["result"]
                sp_before = res.get("sp_before", res["sp"] - res.get("sp_delta", 0.0))
                sp_delta = res.get("sp_delta", res["sp"] - sp_before)
                requested = ""
                if a.get("requested_skill") and (a["requested_skill"] != a["skill"] or
                                                   (a.get("requested_target") and
                                                    a["requested_target"] != a["target"])):
                    requested = (f"  请求 {a['requested_skill']}"
                                 f"→{a.get('requested_target') or '-'}")
                lines.append(
                    f"  #{a['index']} t={res['t']:>7.2f}  {a['unit_id']} {a['skill']}"
                    f"→{a['target'] or '-'}  | 伤害 {res['damage_delta']:>10,.0f}"
                    f"  SP {sp_before:.1f}→{res['sp']:.1f} (Δ{sp_delta:+.1f})"
                    f"  大招 {res['ult_used'] or '-'}{requested}"
                    + (f"  [LLM理由（未经规则验证）：{a['note']}]" if a["note"] else ""))
            lines.append(f"\n[分支树摘要]")
            lines.append(f"  当前路线：{len(r['decision_trail'])} 个 act（撤销 {r['branch_summary']['current']['undo_used']} 次）")
            for ab in r["branch_summary"]["abandoned"]:
                acts = ab["acts"]
                last = acts[-1]["result"] if acts else {}
                fork = "初始" if ab["fork_after"] < 0 else f"act#{ab['fork_after']} 后"
                lines.append(f"  放弃路线 {ab['index']}：{fork} 分叉，{len(acts)} 个 act，"
                             f"放弃时伤害 {ab['summary']['total_damage']:,.0f}"
                             + (f"；原因：{ab['reason']}" if ab["reason"] else ""))
            if r["branch_summary"]["history"]:
                lines.append(f"  历史会话 {len(r['branch_summary']['history'])} 个（配置变更冻结）")
            f = r["final_state"]
            lines.append(f"\n[最终状态]")
            lines.append(f"  总伤害 {f['total_damage']:,.0f} / 击杀 {len(f['kills'])} / "
                         f"剩余 HP {f['enemy_hp_left']}")
            lines.append(f"  分伤害 {f['damage_by_kind']} / 大招 {f['ult_count']} / "
                         f"行动 {f['action_count']} / 破韧 {f['breaks']}")
            lines.append(f"  用时 {f['t']} AV / SP {f['sp']} / "
                         f"波次 {f['wave']}/{f['wave_count']}")
        t = r["trust"]
        if t["level"] == "unverified":
            lines.append(f"⚠ 信任度：unverified（{t['unverified_count']} 处未验证输入，"
                         f"见 unverified_inputs；未实测值不可当真理，D10）")
        return "\n".join(lines)

    # ---------- 持久化（单指令 CLI 模式） ----------

    def state_dict(self) -> Dict[str, Any]:
        cfg = self._config_paths or (DATA_DIR / "team_reda.json", DATA_DIR / "enemy_elite90.json",
                                     DATA_DIR / "rotation.json", False)
        return {
            "name": self.name,
            "undo_budget": self.undo_budget,
            "per_step_budget": self.per_step_budget,
            "history": self.history,
            "acts": [_act_dict(a) for a in self.acts],
            "abandoned": [_route_dict(r) for r in self.abandoned],
            "total_undo": self.total_undo,
            "undo_since_act": self.undo_since_act,
            "act_seq": self._act_seq,
            "snapshots": [_snap_to_dict(s) for s in self.snapshots],
            "current": _snap_to_dict(self.sim.snapshot()),
            "config": {"team": str(cfg[0]), "enemy": str(cfg[1]),
                       "rotation": str(cfg[2]), "seed": self.sim.seed,
                       "legacy": cfg[3], "name": self.name},
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any], base_dir: Path = DATA_DIR) -> "RehearsalSession":
        cfg = state["config"]
        paths = [Path(p) if not Path(p).is_absolute() else Path(p) for p in
                 (cfg["team"], cfg["enemy"], cfg["rotation"])]
        paths = [p if p.is_absolute() else base_dir / p for p in paths]
        session = cls.from_files(
            team=paths[0], enemy=paths[1], rotation=paths[2], seed=cfg["seed"],
            legacy=cfg.get("legacy", False), name=cfg["name"],
            undo_budget=state["undo_budget"], per_step_budget=state["per_step_budget"],
            history=state.get("history"))
        session.total_undo = state["total_undo"]
        session.undo_since_act = state["undo_since_act"]
        session._act_seq = state["act_seq"]
        session.acts = [_act_from_dict(a) for a in state["acts"]]
        session.abandoned = [_route_from_dict(r) for r in state["abandoned"]]
        session.snapshots = [_snap_from_dict(s) for s in state["snapshots"]]
        session.initial = session.snapshots[0]
        session.sim.restore(_snap_from_dict(state["current"]))
        return session

    def _config_paths_set(self, team, enemy, rotation, legacy) -> None:
        self._config_paths = (Path(team), Path(enemy), Path(rotation), legacy)


def _act_dict(a: ActRecord) -> Dict[str, Any]:
    return {"index": a.index, "unit_id": a.unit_id,
            "skill": a.skill, "target": a.target,
            "requested_skill": a.requested_skill or a.skill,
            "requested_target": a.requested_target,
            "ults": a.ults, "note": a.note, "result": a.result}


def _act_from_dict(d: Dict[str, Any]) -> ActRecord:
    return ActRecord(index=d["index"], unit_id=d["unit_id"], skill=d["skill"],
                     target=d.get("target", ""),
                     requested_skill=d.get("requested_skill", d["skill"]),
                     requested_target=d.get("requested_target", d.get("target", "")),
                     ults=d.get("ults"), note=d.get("note", ""),
                     result=d.get("result", {}))


def _route_dict(r: AbandonedRoute) -> Dict[str, Any]:
    return {"index": r.index, "fork_after": r.fork_after,
            "acts": [_act_dict(a) for a in r.acts],
            "reason": r.reason, "summary": r.summary}


def _route_from_dict(d: Dict[str, Any]) -> AbandonedRoute:
    return AbandonedRoute(index=d["index"], fork_after=d["fork_after"],
                          acts=[_act_from_dict(a) for a in d["acts"]],
                          reason=d.get("reason", ""), summary=d.get("summary", {}))


def _rng_to_list(state: Any) -> Any:
    """random.getstate() 返回嵌套 tuple（(version, (ints...))），递归转 JSON 可序列化。"""
    if isinstance(state, tuple):
        return [_rng_to_list(x) for x in state]
    return state


def _rng_from_list(state: Any) -> Any:
    if isinstance(state, list):
        return tuple(_rng_from_list(x) for x in state)
    return state


def _snap_to_dict(snap: BattleSnapshot) -> Dict[str, Any]:
    """BattleSnapshot → JSON dict（Buff/DamageEvent/ActionLog/Action 逐字段展开）。"""
    def action_to_dict(a: Action) -> Dict[str, Any]:
        return {"unit_id": a.unit_id, "action": a.action, "target": a.target, "note": a.note}
    return {
        "t": snap.t, "steps": snap.steps, "sp": snap.sp, "sp_max": snap.sp_max,
        "energy": snap.energy, "toughness": snap.toughness, "enemy_hp": snap.enemy_hp,
        "buffs": [{"stat": b.stat, "value": b.value, "source": b.source,
                   "duration": b.duration, "target": b.target, "cap": b.cap}
                  for b in snap.buffs],
        "fate_charge": snap.fate_charge, "skill_used": snap.skill_used,
        "burst_chain": snap.burst_chain, "sp_spent_count": snap.sp_spent_count,
        "concert_rounds": snap.concert_rounds,
        "concert_additional_mult": snap.concert_additional_mult,
        "memosprite": snap.memosprite, "memosprite_owner": snap.memosprite_owner,
        "enemy_wave": snap.enemy_wave,
        "skill_streak": snap.skill_streak,
        "char_hp": snap.char_hp, "char_hp_max": snap.char_hp_max,
        "enemy_cd": {eid: dict(c) for eid, c in snap.enemy_cd.items()},
        "queue_entries": {uid: [d, s] for uid, (d, s) in snap.queue_entries.items()},
        "sp_timeline": [list(x) for x in snap.sp_timeline],
        "damage_events": [[e.t, e.source, e.target, e.amount, e.kind]
                          for e in snap.damage_events],
        "log": [[l.t, l.unit_id, l.action, l.detail] for l in snap.log],
        "breaks": [list(x) for x in snap.breaks],
        "ult_count": snap.ult_count, "action_count": snap.action_count,
        "rotation_actions": {uid: [action_to_dict(a) for a in seq]
                             for uid, seq in snap.rotation_actions.items()},
        "rng_state": [snap.rng_state[0], _rng_to_list(snap.rng_state[1])],
    }


def _snap_from_dict(d: Dict[str, Any]) -> BattleSnapshot:
    from .engine.buffs import Buff
    from .engine.simulate import ActionLog, DamageEvent
    return BattleSnapshot(
        t=d["t"], steps=d["steps"], sp=d["sp"], sp_max=d["sp_max"],
        energy=dict(d["energy"]), toughness=dict(d["toughness"]),
        enemy_hp=dict(d["enemy_hp"]),
        buffs=[Buff(stat=b["stat"], value=b["value"], source=b["source"],
                    duration=b["duration"], target=b.get("target", ""),
                    cap=b.get("cap", 0.0)) for b in d["buffs"]],
        fate_charge=dict(d["fate_charge"]), skill_used=dict(d["skill_used"]),
        burst_chain=dict(d["burst_chain"]), sp_spent_count=d["sp_spent_count"],
        concert_rounds=d["concert_rounds"],
        concert_additional_mult=d["concert_additional_mult"],
        memosprite=dict(d["memosprite"]) if d["memosprite"] else None,
        memosprite_owner=d["memosprite_owner"], enemy_wave=int(d.get("enemy_wave", 0)),
        skill_streak=dict(d.get("skill_streak", {})),
        char_hp=dict(d.get("char_hp", {})), char_hp_max=dict(d.get("char_hp_max", {})),
        enemy_cd={eid: dict(c) for eid, c in d.get("enemy_cd", {}).items()},
        queue_entries={uid: (float(v[0]), float(v[1]))
                       for uid, v in d["queue_entries"].items()},
        sp_timeline=[(float(x[0]), float(x[1])) for x in d["sp_timeline"]],
        damage_events=[DamageEvent(t=float(e[0]), source=e[1], target=e[2],
                                   amount=float(e[3]), kind=e[4])
                       for e in d["damage_events"]],
        log=[ActionLog(t=float(l[0]), unit_id=l[1], action=l[2], detail=l[3] if len(l) > 3 else "")
             for l in d["log"]],
        breaks=[(float(x[0]), x[1]) for x in d["breaks"]],
        ult_count=dict(d["ult_count"]), action_count=dict(d["action_count"]),
        rotation_actions={uid: [Action(unit_id=a["unit_id"], action=a["action"],
                                       target=a.get("target", ""), note=a.get("note", ""))
                                for a in seq]
                          for uid, seq in d["rotation_actions"].items()},
        rng_state=(d["rng_state"][0], _rng_from_list(d["rng_state"][1])),
    )


# ---------------------------------------------------------------------------
# 演示决策器 / CLI
# ---------------------------------------------------------------------------

def _demo_pilot(session: RehearsalSession, max_acts: int = 200) -> RehearsalSession:
    """演示策略：从当前合法动作中优先战技；SP 不足时显式选择普攻。"""
    state = session.observe()
    acts = 0
    while state["phase"] == "decision" and acts < max_acts:
        d = state["decision"]
        option = d["skill_options"][d["default"]]
        if option["target_type"] == "ally":
            target = d["ally_targets"][0] if d["ally_targets"] else ""
        elif option["target_type"] == "enemy":
            target = d["targets"][0] if d["targets"] else ""
        else:
            target = ""
        session.act(skill=d["default"], target=target, note="demo")
        acts += 1
        state = session.observe()
    return session


def _run_repl(session: RehearsalSession, state_file: Optional[str]) -> int:
    """逐行 JSON 指令：{"op": "observe"|"act"|"undo"|"undo_to"|"restart"|"report"|...}。"""
    def save():
        if state_file:
            Path(state_file).write_text(
                json.dumps(session.state_dict(), ensure_ascii=False), encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"JSON 解析失败：{e}"}, ensure_ascii=False))
            continue
        op = cmd.get("op", "observe")
        try:
            if op == "observe":
                out = session.observe()
            elif op == "act":
                out = session.act(skill=cmd.get("skill", "basic"), target=cmd.get("target", ""),
                                  ults=cmd.get("ults"), note=cmd.get("note", ""))
                save()
            elif op == "undo":
                out = session.undo(reason=cmd.get("reason", ""))
                save()
            elif op == "undo_to":
                out = session.undo_to(int(cmd["k"]), reason=cmd.get("reason", ""))
                save()
            elif op == "restart":
                out = session.restart(reason=cmd.get("reason", ""))
                save()
            elif op == "report":
                out = {"report": session.report(brief=cmd.get("brief", False))}
            elif op == "branch_stats":
                out = session.report_dict()["branch_summary"]
            elif op == "propose_setup":
                session = session.propose_setup(
                    team=cmd.get("team", DATA_DIR / "team_reda.json"),
                    enemy=cmd.get("enemy", DATA_DIR / "enemy_elite90.json"),
                    rotation=cmd.get("rotation", DATA_DIR / "rotation.json"),
                    seed=cmd.get("seed", 0), name=cmd.get("name"))
                save()
                out = {"new_session": session.name, "history": len(session.history)}
            elif op == "quit":
                break
            else:
                raise RehearseError(f"未知指令 {op!r}")
            print(json.dumps({"ok": True, "data": out}, ensure_ascii=False))
        except (RehearseError, UndoBudgetExceeded) as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="hsr-sim-rehearse",
                                     description="推演会话（ADR-0007）：LLM 指挥接口 + 完整推演报告")
    parser.add_argument("--team", default=str(DATA_DIR / "team_reda.json"))
    parser.add_argument("--enemy", default=str(DATA_DIR / "enemy_elite90.json"))
    parser.add_argument("--rotation", default=str(DATA_DIR / "rotation.json"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--legacy", action="store_true", help="v1.5 手填数据层")
    parser.add_argument("--state", default=None, help="会话持久化文件（单指令模式共享状态）")
    parser.add_argument("--demo", action="store_true", help="演示决策器全流程 + 完整报告")
    parser.add_argument("--brief", action="store_true", help="报告只给结论")
    parser.add_argument("--llm", action="store_true", help="LLM 指挥整局推演（OpenAI 兼容接口）")
    parser.add_argument("--llm-config", default=None, help="LLM 配置 JSON（可选，覆盖环境变量）")
    parser.add_argument("--max-acts", type=int, default=40, help="LLM 推演 act 上限（默认 40）")
    parser.add_argument("--quiet", action="store_true", help="LLM 推演不打印逐步轨迹")
    args = parser.parse_args(argv)

    if args.state and Path(args.state).exists():
        state = json.loads(Path(args.state).read_text(encoding="utf-8"))
        session = RehearsalSession.from_state(state, base_dir=DATA_DIR)
    else:
        session = RehearsalSession.from_files(
            team=Path(args.team), enemy=Path(args.enemy), rotation=Path(args.rotation),
            seed=args.seed, legacy=args.legacy)

    if args.demo:
        _demo_pilot(session)
        print(session.report(brief=args.brief))
        return 0
    if args.llm:
        from .llm.client import LLMClient, default_config
        from .llm.rehearsal import run_rehearsal
        cfg = default_config()
        if args.llm_config:
            cfg.update(json.loads(Path(args.llm_config).read_text(encoding="utf-8")))
        try:
            client = LLMClient(cfg["base_url"], cfg["api_key"], cfg["model"],
                               disable_thinking=bool(cfg.get("no_thinking")))
            if not cfg["api_key"]:
                raise RuntimeError("未配置 API Key")
        except RuntimeError as e:
            print(f"❌ LLM 配置错误：{e}\n"
                  "请设置环境变量 HSR_LLM_BASE_URL / HSR_LLM_API_KEY / HSR_LLM_MODEL，"
                  "或 --llm-config 指定 JSON", file=sys.stderr)
            return 2
        result = run_rehearsal(client, session, max_acts=args.max_acts,
                               verbose=not args.quiet)
        if args.brief:
            print(session.report(brief=True))
        else:
            print(result.report)
        return 0
    return _run_repl(session, args.state)


if __name__ == "__main__":
    sys.exit(main())
