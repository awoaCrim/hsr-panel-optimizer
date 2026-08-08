"""LLM 指挥循环测试（hsr_sim/llm/rehearsal.py）——FakeClient 注入，不依赖网络。

协议验证：决策 → act → 自评（continue/undo/undo_to/stop）→ 收敛；
非法决策重试（错误反馈给 LLM 再决策）。
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.llm.rehearsal import build_knowledge_pack, run_rehearsal
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Enemy, Rotation, Stats
from hsr_sim.rehearse import RehearsalSession


class FakeClient:
    """按调用顺序弹出预置响应（决策/自评交替）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_json(self, messages, temperature=0.2):
        assert self.responses, "FakeClient 响应耗尽"
        self.calls += 1
        return self.responses.pop(0)


def _session() -> RehearsalSession:
    chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json")}
    stats = {"1015": Stats(atk=3000.0, speed=145.0, crit_rate=0.8, crit_dmg=1.5)}
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Ice", hp=1e9, atk=1000,
        defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Ice"])}
    sim = Simulator(chars, stats, enemies, Rotation(), target_av=400.0, seed=0)
    return RehearsalSession(sim, name="LLM测试")


def test_knowledge_pack_dynamic():
    """知识包从模拟器数据动态生成：含队伍技能/敌人/信任信封，无硬编码错误模型。"""
    s = _session()
    pack = build_knowledge_pack(s)
    assert "1015" in pack and "红A" in pack
    assert "精英" in pack and "弱点" in pack
    assert "信任度信封" in pack
    assert "1.6" not in pack      # 旧版迷迷模型（×1.6 全体伤害）必须不存在
    assert "声援" in pack or "迷迷" in pack


def test_knowledge_pack_advance_and_gear():
    """知识包含：拉条不可自拉规则 + 装备管理系统（光锥/套装/星魂 + 未接入标注）。"""
    from hsr_sim.rehearse import RehearsalSession as RS
    s = RS.from_files()   # 默认红A队（含花火 1306）
    pack = build_knowledge_pack(s)
    assert "不可自拉" in pack          # 花火战技目标选择器排除自身
    assert "已接入战斗模拟" in pack      # 光锥/套装效果已接入
    assert "E2" in pack and "已接入" in pack   # 红A 2 命（星魂效果已接入）
    assert "等级类 E3/E5 未接入" in pack        # 星魂诚实标注
    assert "于夜色中" in pack and "花与蝶" in pack   # 红A 光锥 + 精炼效果
    assert "繁星璀璨的天才" in pack    # 红A 量子套
    assert "星魂 2 命" in pack
    assert "词条" in pack             # builds 词条构成


def test_full_run_to_terminal():
    """continue 自评直到物理终止（av_exhausted）：act 数与调用数对应。"""
    decisions = [{"skill": "skill", "ults": {}, "note": "攒能"} for _ in range(30)]
    verdicts = [{"verdict": "continue"} for _ in range(30)]
    fake = FakeClient([x for pair in zip(decisions, verdicts) for x in pair])
    s = _session()
    r = run_rehearsal(fake, s, max_acts=30)
    assert "物理终止" in r.stop_reason
    assert r.acts > 0
    assert r.llm_calls == r.acts * 2        # 每步决策 + 自评
    assert r.retries == 0
    assert "决策轨迹" in r.report
    assert "分支树摘要" in r.report


def test_self_eval_undo():
    """自评 undo：状态回退，路线归档，报告含放弃路线。"""
    fake = FakeClient([
        {"skill": "skill", "ults": {}},
        {"verdict": "undo", "reason": "测试回退"},
        {"skill": "skill", "ults": {}},
        {"verdict": "continue"},
        {"skill": "basic", "ults": {}},
        {"verdict": "continue"},
        {"skill": "basic", "ults": {}},
        {"verdict": "stop"},
    ])
    s = _session()
    r = run_rehearsal(fake, s, max_acts=6)
    assert r.undos == 1
    assert r.stop_reason != ""
    assert len(s.abandoned) == 1
    assert s.abandoned[0].reason == "测试回退"
    assert "放弃路线 1" in r.report


def test_undo_to_arbitrary():
    """自评 undo_to k：回到第 k 个 act 之后。"""
    fake = FakeClient([
        {"skill": "skill", "ults": {}},
        {"verdict": "continue"},
        {"skill": "skill", "ults": {}},
        {"verdict": "undo_to", "k": 0, "reason": "从头再来"},
        {"skill": "basic", "ults": {}},
        {"verdict": "stop"},
    ])
    s = _session()
    r = run_rehearsal(fake, s, max_acts=5)
    assert r.undos == 1
    # undo_to(0)：第 3 个 act 从初始状态开始
    assert s.acts[-1].index == 3
    assert s.abandoned[0].fork_after == -1


def test_stop_early():
    """LLM 自评 stop：提前收敛。"""
    fake = FakeClient([
        {"skill": "skill", "ults": {}},
        {"verdict": "stop", "reason": "已达成目标"},
    ])
    s = _session()
    r = run_rehearsal(fake, s, max_acts=10)
    assert r.acts == 1
    assert r.stop_reason == "已达成目标"


def test_invalid_decision_retry():
    """非法决策（技能不存在）：错误反馈重试，不中断流程。"""
    fake = FakeClient([
        {"skill": "nonexistent"},          # 非法
        {"skill": "skill", "ults": {}},    # 修正后
        {"verdict": "continue"},
        {"skill": "basic", "ults": {}},
        {"verdict": "stop"},
    ])
    s = _session()
    r = run_rehearsal(fake, s, max_acts=5)
    assert r.retries == 1
    assert r.acts == 2
    assert r.llm_calls == 5                # 决策1+重试1+自评1+决策2+自评2


def test_max_acts_cap():
    """步数上限：LLM 一直不收敛时强制停止。"""
    decisions = [{"skill": "skill", "ults": {}} for _ in range(4)]
    verdicts = [{"verdict": "continue"} for _ in range(4)]
    fake = FakeClient([x for pair in zip(decisions, verdicts) for x in pair])
    s = _session()
    r = run_rehearsal(fake, s, max_acts=3)
    assert r.acts == 3
    assert "步数上限" in r.stop_reason
