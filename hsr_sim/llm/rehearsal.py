"""LLM 战斗指挥循环（ADR-0007 D10）：observe → 决策 → act → 自评回退 → 收敛。

协议：
1. 决策调用：user = 当前局面（精简状态）+ 决策契约 → LLM 输出 {skill, target, ults, note}
2. 自评调用：user = 上一步 act 结果 + 新局面 → LLM 输出 {verdict, k, reason}
   每步 act 一次自评；verdict ∈ continue / undo / undo_to / stop
3. 收敛：LLM 自主判断（D6 开放式目标）或物理边界（敌人全灭/行动值耗尽）
4. 回退预算由会话强制（D4：每步 3 次 / 全局 50 次），耗尽后 LLM 只能继续或 stop

知识包（system prompt）从模拟器数据动态生成（D10：机制规则 + 当前队伍技能/敌人
数据 + 信任度信封）——不硬编码角色数值，避免知识过期（如旧版迷迷模型）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..rehearse import RehearseError, RehearsalSession, UndoBudgetExceeded
from .client import LLMClient

MECHANICS_RULES = """\
## 决策空间（官方玩家可控部分，其余由模拟器按官方规则自动执行）
1. 主动行动：当前决策点角色的 basic / skill（target 可指定单体目标；留空 = 默认）
2. 大招时机：能量满的角色可放可等——ults=null 放全部满能；ults={} 全 hold；
   ults={"<cid>": true/false} 逐角色指定。大招不占行动条，在行动连锁末尾即时释放。
自动执行：追击 / 协奏 / 真伤（声援） / 击破 / 忆灵（迷迷）行动 / 敌人 AI / 回能。

## 目标（开放式）
由你自主定义（如：行动值耗尽前最大化总伤害 / 击杀 / 破韧），何时收敛由你判断。
物理终止：敌人全灭 / 行动值耗尽 / 队列空。

## 回退（分支探索）
可 undo（回退一步）或 undo_to k（回退到第 k 个 act 之后，任意步）重新决策。
被撤销的路线会归档进分支树报告（尝试过哪些路线、为什么放弃）。
预算：每步最多 3 次回退、全局 50 次；耗尽后系统强制你继续或 stop。

## 输出格式
每次只输出一个合法 JSON 对象，不要输出其他文本。
"""


def _char_summary(session: RehearsalSession) -> str:
    sim = session.sim
    lines = []
    for cid, c in sim.chars.items():
        s = sim.stats[cid]
        skills = []
        for k in ("basic", "skill", "ult"):
            sk = c.skills.get(k)
            if sk:
                parts = [f"{k}:倍率{sk.mult}"]
                if sk.sp:
                    parts.append(f"SP{sk.sp:+.0f}")
                if sk.energy_cost:
                    parts.append(f"能量{sk.energy_cost}")
                if sk.advance_pct:
                    parts.append(f"拉条{sk.advance_pct:.0%}({sk.advance_target or '自身'})")
                if sk.extra_action:
                    parts.append("额外行动")
                skills.append("/".join(parts))
        te = c.talent_extra
        extra = []
        if te.get("followup_on_ally_attack"):
            extra.append("队友攻击后消耗充能追击")
        if te.get("energy_on_ally_attack"):
            extra.append(f"队友攻击回能{te['energy_on_ally_attack']}")
        if te.get("summon"):
            m = te.get("memosprite", {})
            extra.append(f"召唤迷迷(速{m.get('speed', 130)}；普攻{m.get('basic_hits', 4)}段"
                         f"×{m.get('basic_mult', 0.36):.0%}+全体{m.get('basic_aoe_mult', 0.9):.0%}；"
                         f"强化=拉条+声援真伤{m.get('support_true_dmg', 0.28):.0%})")
        se = te.get("skill_effects", {})
        if se.get("ult", {}).get("concert"):
            extra.append("协奏（每次我方攻击后附加固定双暴伤害）")
        if se.get("skill", {}).get("advance"):
            extra.append(f"战技拉条{se['skill']['advance'].get('pct', 0):.0%}")
        lines.append(f"- {cid} {c.name}（{c.element}）：速{s.speed} 攻{s.atk:.0f} "
                     f"双暴{s.crit_rate:.0%}/{s.crit_dmg:.0%} 充能{s.energy_regen:.0%} | "
                     + "; ".join(skills) + (f" | {', '.join(extra)}" if extra else ""))
    return "\n".join(lines)


def _enemy_summary(session: RehearsalSession) -> str:
    lines = []
    for eid, e in session.sim.enemies.items():
        lines.append(f"- {eid} {e.name}：HP{e.hp:,.0f} 防御{e.defense:.0f} 韧性{e.toughness:.0f} "
                     f"弱点{e.weaknesses} 抗性{e.resistances or '无'} 速{e.speed}")
    return "\n".join(lines)


def _trust_pack(session: RehearsalSession) -> str:
    unv = session.sim._unverified
    if not unv:
        return "全部输入数值已验证或来自解包/社区多源交叉（trust=verified）。"
    enemies = [p for p in unv if p.startswith("enemies.")]
    others = [p for p in unv if not p.startswith("enemies.")]
    parts = [f"⚠ 共 {len(unv)} 处输入未验证（trust=unverified）："]
    if enemies:
        parts.append(f"  敌人模板 {len(enemies)} 处（HP/防御/韧性/抗性）：{enemies[:5]}{'…' if len(enemies) > 5 else ''}")
    if others:
        parts.append(f"  其他 {len(others)} 处：{others[:5]}{'…' if len(others) > 5 else ''}")
    parts.append("  未实测值存在近似误差，规划/汇报时不要把它们当精确真理。")
    return "\n".join(parts)


def build_knowledge_pack(session: RehearsalSession) -> str:
    """D10 知识精简包：机制规则 + 当前队伍/敌人数据 + 信任度信封（动态生成）。"""
    return f"""\
你是星穹铁道战斗指挥。模拟器高度模拟官方战斗逻辑（公式经 fribbels 交叉验证、
事件溯源支持任意回退与分支探索）。

{MECHANICS_RULES}
## 队伍（当前面板）
{_char_summary(session)}

## 敌人
{_enemy_summary(session)}

## 信任度信封
{_trust_pack(session)}
"""


def _compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """精简状态给 LLM（控制 token 成本，保留决策所需全量信息）。"""
    return {
        "phase": state["phase"],
        "terminal_reason": state.get("terminal_reason"),
        "t": state["t"],
        "queue": state["queue"],
        "energy": {c: v["value"] for c, v in state["energy"].items()},
        "sp": state["sp"]["value"],
        "enemies": {eid: {k: e[k] for k in ("hp_pct", "toughness", "broken")}
                    for eid, e in state["enemies"].items()},
        "memosprite": state.get("memosprite"),
        "buffs": state["buffs"],
        "decision": state.get("decision"),
        "damage": state["damage"],
        "progression": {k: state["progression"][k]
                        for k in ("acts", "undo_left", "undo_used")},
    }


DECISION_CONTRACT = """\
## 当前局面
{state}

## 你的决策（决策点：{unit}）
输出 JSON：{{"skill": "<basic|skill>", "target": "<敌人id|留空>", "ults": <null|{{"cid": bool}}>, "note": "<一句话理由>"}}
- skill 必须来自局面 decision.skills；target 从 decision.targets 选，可留空
- ults=null 表示放全部满能大招；{{}} 全 hold；{{"cid": true/false}} 逐角色指定
- note 会进入推演报告决策轨迹，请说明战术意图
"""

EVAL_CONTRACT = """\
## 上一步 act 结果
{result}

## 当前局面
{state}

## 自评
输出 JSON：{{"verdict": "continue|undo|undo_to|stop", "k": <undo_to 时填>, "reason": "<一句话>"}}
- continue：接受这一步，继续
- undo：回退一步重新决策；undo_to：回退到第 k 个 act 之后（k=0 = 从头再来）
- stop：已达成你自定的目标（或认定无更好路线），结束推演
- 注意回退预算（局面 progression.undo_left）；频繁回退会被系统强制收敛
"""


@dataclass
class RehearsalResult:
    acts: int = 0
    llm_calls: int = 0
    undos: int = 0
    retries: int = 0
    stop_reason: str = ""
    report: str = ""
    report_dict: Dict[str, Any] = field(default_factory=dict)


def run_rehearsal(client: LLMClient, session: RehearsalSession,
                  max_acts: int = 40, verbose: bool = False) -> RehearsalResult:
    """LLM 自主指挥一整局推演（D10：每步 act + 自评）。

    非法决策自动重试（≤3 次/步，错误反馈给 LLM）；回退预算由会话强制。
    """
    knowledge = build_knowledge_pack(session)
    result = RehearsalResult()
    state = session.observe()
    if verbose:
        print(f"开局：t={state['t']} 队列={state['queue']['entries']}")

    while state["phase"] != "terminal" and result.acts < max_acts:
        decision = state["decision"]
        # ---- 决策调用 ----
        msgs = [
            {"role": "system", "content": knowledge},
            {"role": "user", "content": DECISION_CONTRACT.format(
                state=json.dumps(_compact_state(state), ensure_ascii=False),
                unit=decision["unit"])},
        ]
        d = client.chat_json(msgs)
        result.llm_calls += 1
        skill = d.get("skill", decision["default"])
        target = d.get("target", "")
        ults = d.get("ults")
        note = d.get("note", "")
        # ---- 执行（非法决策重试 ≤3 次） ----
        for attempt in range(3):
            try:
                act_result = session.act(skill=skill, target=target, ults=ults, note=note)
                break
            except RehearseError as e:
                result.retries += 1
                if attempt == 2 or isinstance(e, UndoBudgetExceeded):
                    raise
                msgs = msgs + [
                    {"role": "assistant", "content": json.dumps(d, ensure_ascii=False)},
                    {"role": "user", "content": f"你的决策被拒绝：{e}\n"
                                                f"重新输出合法决策 JSON（决策点 {decision['unit']}）。"},
                ]
                d = client.chat_json(msgs)
                result.llm_calls += 1
                skill = d.get("skill", decision["default"])
                target = d.get("target", "")
                ults = d.get("ults")
        result.acts += 1
        if verbose:
            print(f"  act#{result.acts} t={act_result['t']:>7.2f} {decision['unit']} {skill}"
                  f"→{target or '-'} 伤害{act_result['damage_delta']:>10,.0f} 大招{act_result['ult_used'] or '-'}"
                  + (f" [{note}]" if note else ""))
        # ---- 自评调用 ----
        state = session.observe()
        ev_msgs = [
            {"role": "system", "content": knowledge},
            {"role": "user", "content": EVAL_CONTRACT.format(
                result=json.dumps(act_result, ensure_ascii=False),
                state=json.dumps(_compact_state(state), ensure_ascii=False))},
        ]
        ev = client.chat_json(ev_msgs)
        result.llm_calls += 1
        verdict = ev.get("verdict", "continue")
        reason = ev.get("reason", "")
        try:
            if verdict == "undo":
                session.undo(reason=reason)
                result.undos += 1
                if verbose:
                    print(f"  ↺ 自评回退：{reason}")
            elif verdict == "undo_to":
                session.undo_to(int(ev.get("k", 0)), reason=reason)
                result.undos += 1
                if verbose:
                    print(f"  ↺ 自评回退到 act#{ev.get('k')}：{reason}")
            elif verdict == "stop":
                result.stop_reason = reason or "LLM 自评收敛"
                if verbose:
                    print(f"  ■ 停止：{result.stop_reason}")
                break
        except UndoBudgetExceeded as e:
            if verbose:
                print(f"  ⚠ {e}")
            # 预算耗尽：LLM 只能继续（verdict 视为 continue），直到物理终止
        state = session.observe()

    if result.acts >= max_acts:
        result.stop_reason = f"达到步数上限 {max_acts}"
    elif state["phase"] == "terminal":
        result.stop_reason = f"物理终止：{state['terminal_reason']}"
    result.report = session.report()
    result.report_dict = session.report_dict()
    return result
