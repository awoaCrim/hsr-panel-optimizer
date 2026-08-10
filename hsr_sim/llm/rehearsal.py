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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..rehearse import RehearseError, RehearsalSession, UndoBudgetExceeded
from .client import LLMClient

MECHANICS_RULES = """\
## 决策空间（官方玩家可控部分，其余由模拟器按官方规则自动执行）
1. 主动行动：只能从当前局面 `decision.skills` 选择 basic / skill；该列表已排除 SP 不足等非法动作。
   每个 `decision.skill_options[skill]` 都给出 `sp_delta`、`sp_cost`、`available`；
   `sp_delta=-1` 表示消耗 1 点。非攻击只表示不造成伤害，不表示免费。
2. 大招时机：能量满的角色可放可等——ults=null 放全部满能；ults={} 全 hold；
   ults={"<cid>": true/false} 逐角色指定。大招不占行动条，在行动连锁末尾即时释放。
3. 开战准备（秘技、攻击入战者、保留领域）属于 Setup，已在首个决策点前由模拟器结算；
   `battle_setup` 是实际生效真值。同类领域最多保留1个，禁止声称被 `skipped` 的秘技同时生效。
4. 自动执行：追击 / 协奏 / 真伤（声援） / 击破 / 忆灵（迷迷）行动 / 敌人 AI / 回能。
非攻击技能不会造成伤害/削韧，也不会触发协奏附加伤害、队友攻击追击或“队友攻击后”回能；
不得根据技能说明中的增益/治疗参数虚构攻击倍率或敌方目标。

## 生存（① 敌人 AI）
敌人会攻击我方（技能见敌人段），伤害 = 敌攻击×倍率×我方防御减免，受击回能。
我方 HP 归零 = 死亡（退出行动/决策）；死亡影响胜利条件，规划时考虑生存。

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
                is_attack = sk.mult > 0.0
                parts = [f"{k}:攻击力倍率{sk.mult:.0%}" if is_attack else f"{k}:非攻击"]
                if sk.toughness and is_attack:
                    parts.append(f"削韧{sk.toughness}")
                if sk.sp:
                    parts.append(f"SP{sk.sp:+.0f}")
                if sk.energy_cost:
                    parts.append(f"能量{sk.energy_cost}")
                if sk.advance_pct:
                    self_excl = "，不可自拉" if not sk.advance_self else ""
                    parts.append(f"拉条{sk.advance_pct:.0%}(目标={sk.advance_target or ('队友' + self_excl)})")
                if sk.extra_action:
                    parts.append("额外行动")
                skills.append("/".join(parts))
        te = c.talent_extra
        extra = []
        if te.get("sp_cap_bonus"):
            extra.append(f"在场使战技点上限+{te['sp_cap_bonus']:g}")
        if te.get("followup_on_ally_attack"):
            extra.append("队友攻击后消耗充能追击")
        if te.get("energy_on_ally_attack"):
            extra.append(f"队友攻击后仅自身回能{te['energy_on_ally_attack']}（不是全队回能）")
        if te.get("summon"):
            m = te.get("memosprite", {})
            extra.append(f"迷迷在进入战斗时已自动完成召唤；此后战技不会召唤/重新召唤迷迷"
                         f"(速{m.get('speed', 130)}；普攻{m.get('basic_hits', 4)}段"
                         f"×{m.get('basic_mult', 0.36):.0%}+全体{m.get('basic_aoe_mult', 0.9):.0%}；"
                         f"强化=拉条+声援真伤{m.get('support_true_dmg', 0.28):.0%})")
        se = te.get("skill_effects", {})
        if se.get("ult", {}).get("concert"):
            extra.append("协奏（每次我方攻击后附加固定双暴伤害）")
        if se.get("skill", {}).get("advance"):
            extra.append(f"战技拉条{se['skill']['advance'].get('pct', 0):.0%}")
        skill_buff = se.get("skill", {}).get("buff")
        if skill_buff:
            scope = "全队" if not skill_buff.get("target") else "指定队友"
            extra.append(f"战技{scope}{skill_buff.get('stat')}+{skill_buff.get('value', 0):.0%}"
                         f"/持续{skill_buff.get('duration', 1)}回合")
        mem_skill = se.get("skill", {})
        if mem_skill.get("mem_charge"):
            extra.append(f"战技消耗{abs(c.skills['skill'].sp):g} SP：为已在场迷迷充能"
                         f"{mem_skill['mem_charge']:.0%}"
                         + ("并治疗迷迷（治疗结算未实现）" if mem_skill.get("mem_heal") else ""))
        mem_ult = se.get("ult", {})
        if mem_ult.get("mem_charge"):
            extra.append(f"终结技为迷迷充能{mem_ult['mem_charge']:.0%}"
                         + ("并使迷迷立即行动" if mem_ult.get("mem_immediate") else ""))
        lines.append(f"- {cid} {c.name}（{c.element}）：速{s.speed} 攻{s.atk:.0f} "
                     f"双暴{s.crit_rate:.0%}/{s.crit_dmg:.0%} 充能{s.energy_regen:.0%} | "
                     + "; ".join(skills) + (f" | {', '.join(extra)}" if extra else ""))
    return "\n".join(lines)


def _enemy_summary(session: RehearsalSession) -> str:
    lines = []
    for eid, e in session.sim.enemies.items():
        lines.append(f"- {eid} {e.name}：HP{e.hp:,.0f} 防御{e.defense:.0f} 韧性{e.toughness:.0f} "
                     f"弱点{e.weaknesses} 抗性{e.resistances or '无'} 速{e.speed}")
        for sk in e.skills:
            lines.append(f"   技能 {sk.name}：倍率{sk.mult}（{sk.damage_type or '任意'}）"
                         f" 冷却{sk.ai_cd} 受击回能{sk.sp_hit}")
        if not e.skills:
            lines.append("   无攻击技能（不攻击我方）")
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


def _clean_desc(s: Optional[str]) -> str:
    """清理 wiki 富文本标签（<unbreak>/<color=...>），保留 #n[i] 参数占位符。"""
    import re
    if not s:
        return ""
    s = re.sub(r"<color=#[0-9a-fA-F]{8}>", "", s)
    s = s.replace("</color>", "").replace("<unbreak>", "").replace("</unbreak>", "")
    return s


def _gear_summary(session: RehearsalSession) -> str:
    """装备与星魂（光锥/遗器套装/星魂管理系统）：真实数据 + 接入状态标注。"""
    cfg = session._config_paths
    lines = ["光锥被动 / 套装效果已接入战斗模拟（面板类+条件增伤+无视防御+叠层+SP返还+开局提前）；",
             "星魂效果部分接入（红A E1/E2、花火 E4、知更鸟 E1、记忆主 E1；等级类 E3/E5 未接入）。装备明细："]
    if cfg is None:
        lines.append("  （会话未绑定队伍文件，装备配置不可查）")
        return "\n".join(lines)
    try:
        team = json.loads(Path(cfg[0]).read_text(encoding="utf-8"))
        from ..data.loader import load_equipment
        eq = load_equipment()
    except Exception:
        lines.append("  （队伍/装备数据读取失败）")
        return "\n".join(lines)
    lcs = eq.get("light_cones", {})
    rss = eq.get("relic_sets", {})
    eids = eq.get("eidolons", {})
    builds = team.get("builds", {})
    for cid in session.sim.chars:
        b = builds.get(cid) or {}
        parts = []
        # 光锥
        lc_id = b.get("light_cone", "")
        if lc_id and lc_id in lcs:
            lc = lcs[lc_id]
            parts.append(f"光锥[{lc_id}] {lc.get('name')}（80级白值 {lc.get('base_stats')}")
            eff = lc.get("effect")
            if eff:
                parts.append(f"精1效果[{eff.get('name')}] {_clean_desc(eff.get('desc'))[:100]} "
                             f"参数{eff.get('level_1_params')}")
            else:
                parts.append("效果未收录")
            parts.append("）")
        else:
            parts.append(f"光锥: 未配置（legacy 模板 atk+582）")
        # 套装
        sets = b.get("relic_sets", []) or []
        if sets:
            pieces = []
            for rs_cfg in sets:
                sid = rs_cfg.get("id") if isinstance(rs_cfg, dict) else rs_cfg
                need = int(rs_cfg.get("pieces", 4)) if isinstance(rs_cfg, dict) else 4
                rs = rss.get(str(sid)) or rss.get(sid)
                if rs:
                    descs = []
                    t2 = rs.get("two_piece") or {}
                    t4 = rs.get("four_piece") or {}
                    if t2:
                        descs.append(f"2件:{_clean_desc(t2.get('desc'))[:40]}")
                    if t4 and need >= 4:
                        descs.append(f"4件:{_clean_desc(t4.get('desc'))[:40]}")
                    pieces.append(f"{rs.get('name')}[{'，'.join(descs)}]×{need}")
            parts.append("套装: " + " + ".join(pieces))
        # 星魂
        el = b.get("eidolon", 0) or 0
        eid = eids.get(cid)
        if eid and el > 0:
            owned = []
            for rk, rv in list(eid.get("ranks", {}).items())[:el]:
                tag = "（已接入）" if rv.get("exec") else ("（未接入）" if rv.get("exec_skip") else "")
                owned.append(f"E{rk} {rv.get('name')}{tag}: {_clean_desc(rv.get('desc'))[:80]}")
            parts.append(f"星魂 {el} 命：" + " | ".join(owned))
        elif eid:
            parts.append("星魂 0 命")
        # 词条
        if b.get("main_stats") or b.get("substats"):
            parts.append(f"词条: 主{b.get('main_stats', {})} 副{b.get('substats', {})}")
        lines.append(f"  - {cid}：" + "; ".join(parts))
    return "\n".join(lines)


def build_knowledge_pack(session: RehearsalSession) -> str:
    """D10 知识精简包：机制规则 + 当前队伍/敌人数据 + 信任度信封（动态生成）。"""
    return f"""\
你是星穹铁道战斗指挥。模拟器高度模拟官方战斗逻辑（公式经 fribbels 交叉验证、
事件溯源支持任意回退与分支探索）。

{MECHANICS_RULES}
## 队伍（当前面板）
{_char_summary(session)}

## 队伍资源
- 战技点：当前 {session.sim.sp:g} / 上限 {session.sim.sp_max:g}（基础上限 5；角色天赋/星魂可提高上限）。
  当前值与上限是不同概念，规划时必须使用本局上限，不能自行按常规 5 点推断。

## 开战准备（已结算的模拟器真值）
{json.dumps(session.sim.setup_state, ensure_ascii=False)}

## 装备与星魂
{_gear_summary(session)}

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
        "sp": {"value": state["sp"]["value"], "max": state["sp"]["max"]},
        "enemies": {eid: {k: e[k] for k in ("hp_pct", "toughness", "broken")}
                    for eid, e in state["enemies"].items()},
        "memosprite": state.get("memosprite"),
        "panels": state.get("panels", {}),
        "decision": state.get("decision"),
        "damage": state["damage"],
        "progression": {k: state["progression"][k]
                        for k in ("acts", "undo_left", "undo_used")},
    }


def _decision_history(session: RehearsalSession) -> Dict[str, Any]:
    """给下一次 LLM 调用的路线记忆；undo 后当前路线自动缩短，放弃路线仍保留。"""
    return {
        "current_route": [
            {
                "index": a.index,
                "unit": a.unit_id,
                "skill": a.skill,
                "target": a.target,
                "ults": a.ults,
                "note": a.note,
                "result": {
                    "t": a.result.get("t"),
                    "damage_delta": a.result.get("damage_delta"),
                    "sp_delta": a.result.get("sp_delta"),
                    "ult_used": a.result.get("ult_used", []),
                    "wave": a.result.get("wave"),
                },
            }
            for a in session.acts
        ],
        "abandoned_routes": [
            {
                "index": route.index,
                "fork_after": route.fork_after,
                "reason": route.reason,
                "choices": [
                    {"unit": a.unit_id, "skill": a.skill, "target": a.target,
                     "ults": a.ults, "note": a.note}
                    for a in route.acts
                ],
            }
            for route in session.abandoned
        ],
    }


DECISION_CONTRACT = """\
## 之前的选择与分支记忆
{history}

## 当前局面
{state}

## 你的决策（决策点：{unit}）
输出 JSON：{{"skill": "<basic|skill>", "target": "<敌人id|队友id|留空>", "ults": <null|{{"cid": bool}}>, "note": "<一句话理由>"}}
- 结合“之前的选择与分支记忆”保持策略连贯；若改选，说明与既有路线相比改变了什么
- skill 必须来自局面 decision.skills；不要选择 skill_options 中 available=false 的动作
- 必须读取 decision.skill_options[skill].multiplier / multiplier_pct；攻击技能按该攻击力倍率结算，辅助技能 multiplier=0
- 必须读取 decision.skill_options[skill].sp_delta / sp_cost：非攻击不等于免费，sp_delta=-1 仍会消耗 1 点战技点
- `panels.characters` 是当前战斗内有效面板；buff 获得、到期、debuff 与 undo 后均由模拟器重新投影，禁止继续使用已失效效果
- 根据所选 skill 的 decision.skill_options[skill].target_type 决定 target：
  enemy → 从 decision.targets 选敌人（可留空让模拟器默认）；
  ally → 从 decision.ally_targets 选队友并遵守不可自拉；
  none → target 必须留空，禁止给非攻击/无目标技能虚构敌方目标
- 必须按 decision.skill_options[skill].mechanics 描述本次技能效果；字段未出现的效果视为不存在，不得自行补全
- decision.memosprite_present=true 表示忆灵已在场；只有 mechanics.summon=true 才能声称本次技能召唤忆灵
- decision.skill_options[skill].is_attack=false 时，该技能不造成伤害/削韧，且不触发任何“攻击后”链
- ults=null 表示放全部满能大招；{{}} 全 hold；{{"cid": true/false}} 逐角色指定
- 只有 decision.ult_ready 中的角色可设为 true；不得声称未实际出现在 act_result.ult_used 的大招已释放
- note 会进入推演报告，但只作为“LLM理由（未经规则验证）”；不得把推测写成已发生的结算事实
"""

EVAL_CONTRACT = """\
## 之前的选择与分支记忆
{history}

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
                  max_acts: int = 40, verbose: bool = False,
                  on_progress: Optional[Callable[[str, RehearsalSession, Dict[str, Any]], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None) -> RehearsalResult:
    """LLM 自主指挥一整局推演（D10：每步 act + 自评）。

    后续每次模型调用都会带上此前已接受的决策/自评消息，并附当前路线与放弃路线摘要；
    undo 之后 LLM 仍能看到被放弃选择及其原因。非法重试只保留最终被模拟器接受的选择。
    on_progress 在模型请求/动作执行/自评边界发布实时状态（WebUI 不再等整局结束）。
    """
    def publish(activity: str, **detail: Any) -> None:
        if on_progress is not None:
            on_progress(activity, session, detail)

    knowledge = build_knowledge_pack(session)
    conversation: List[Dict[str, str]] = [{"role": "system", "content": knowledge}]
    result = RehearsalResult()
    state = session.observe()
    decision_turn = 0
    if verbose:
        print(f"开局：t={state['t']} 队列={state['queue']['entries']}")

    while state["phase"] != "terminal" and result.acts < max_acts:
        if should_stop is not None and should_stop():
            result.stop_reason = "用户停止"
            break
        decision = state["decision"]
        decision_turn += 1
        publish("waiting_llm_decision", state=state, unit=decision["unit"],
                act=decision_turn, llm_calls=result.llm_calls)
        # ---- 决策调用 ----
        msgs = conversation + [
            {"role": "user", "content": DECISION_CONTRACT.format(
                history=json.dumps(_decision_history(session), ensure_ascii=False),
                state=json.dumps(_compact_state(state), ensure_ascii=False),
                unit=decision["unit"])},
        ]
        decision_user_message = msgs[-1]
        d = client.chat_json(msgs)
        result.llm_calls += 1
        if should_stop is not None and should_stop():
            result.stop_reason = "用户停止"
            break
        skill = d.get("skill", decision["default"])
        target = d.get("target", "")
        ults = d.get("ults")
        note = d.get("note", "")
        publish("executing_action", state=state, unit=decision["unit"], skill=skill,
                target=target, ults=ults, note=note, llm_calls=result.llm_calls)
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
                if should_stop is not None and should_stop():
                    result.stop_reason = "用户停止"
                    break
                skill = d.get("skill", decision["default"])
                target = d.get("target", "")
                ults = d.get("ults")
                note = d.get("note", "")
                publish("executing_action", state=state, unit=decision["unit"], skill=skill,
                        target=target, ults=ults, note=note, retry=attempt + 1,
                        llm_calls=result.llm_calls)
        if result.stop_reason == "用户停止":
            break
        accepted_decision = dict(d)
        actual = session.acts[-1]
        result.acts += 1
        if verbose:
            print(f"  act#{result.acts} t={act_result['t']:>7.2f} {actual.unit_id} {actual.skill}"
                  f"→{actual.target or '-'} 伤害{act_result['damage_delta']:>10,.0f} 大招{act_result['ult_used'] or '-'}"
                  + (f" [LLM理由：{note}]" if note else ""))
        # ---- 自评调用 ----
        state = session.observe()
        publish("waiting_llm_evaluation", state=state, act=result.acts,
                decision={"unit": actual.unit_id, "skill": actual.skill,
                          "target": actual.target, "requested_skill": skill,
                          "requested_target": target, "ults": ults, "note": note},
                act_result=act_result, llm_calls=result.llm_calls)
        ev_msgs = conversation + [
            decision_user_message,
            {"role": "assistant", "content": json.dumps(accepted_decision, ensure_ascii=False)},
            {"role": "user", "content": EVAL_CONTRACT.format(
                history=json.dumps(_decision_history(session), ensure_ascii=False),
                result=json.dumps(act_result, ensure_ascii=False),
                state=json.dumps(_compact_state(state), ensure_ascii=False))},
        ]
        ev = client.chat_json(ev_msgs)
        result.llm_calls += 1
        conversation.extend([
            decision_user_message,
            {"role": "assistant", "content": json.dumps(accepted_decision, ensure_ascii=False)},
            ev_msgs[-1], {"role": "assistant", "content": json.dumps(ev, ensure_ascii=False)},
        ])
        if should_stop is not None and should_stop():
            result.stop_reason = "用户停止"
            break
        verdict = ev.get("verdict", "continue")
        reason = ev.get("reason", "")
        publish("applying_evaluation", state=state, act=result.acts,
                verdict=verdict, reason=reason, llm_calls=result.llm_calls)
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

    if not result.stop_reason and should_stop is not None and should_stop():
        result.stop_reason = "用户停止"
    elif result.acts >= max_acts:
        result.stop_reason = f"达到步数上限 {max_acts}"
    elif state["phase"] == "terminal":
        result.stop_reason = f"物理终止：{state['terminal_reason']}"
    result.report = session.report(stop_reason=result.stop_reason)
    result.report_dict = session.report_dict(stop_reason=result.stop_reason)
    publish("finished", state=state, stop_reason=result.stop_reason,
            acts=result.acts, llm_calls=result.llm_calls)
    return result
