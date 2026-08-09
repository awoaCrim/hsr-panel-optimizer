"""T2b Oracle 测试（P0-4）—— 按 mechanics-spec 定值公式独立手算核算。

与 T1 的区别：T1 验证"实现 == 自己写的公式"；T2b 的值来自**外部定值**
（fribbels 源码 / 官方规则，docs/mechanics-spec.md），从第一性原理手算，
不引用 v1.5 golden、不复制实现公式。

场景：单红A（1015）vs 90 级精英（防御 1100 = 200+10×90，抗性 0，韧性 30，速度 10 不插队）。
红A：atk=2800, speed=134, cr=75%, cd=150%，序列模式 basic 连打。
"""
import pytest

from hsr_sim.engine.damage import BREAK_BASE_DAMAGE
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Action, Enemy, Rotation, Stats

# ---- 手算常量（mechanics-spec 定值） ----
AV_BASIC = 10000.0 / 134.0                       # 74.626865...：134 速单次行动值
DEF_M_90 = 1000.0 / (1100.0 + 1000.0)             # 80 级攻方 vs 90 级敌防 1100（1.2）
CRIT = 1.0 + 0.75 * 1.5                           # 2.125（1.4）
RAW = 1.3 * 2800.0                                # basic 倍率 × 攻击（1.1）
DAMAGE_UNBROKEN = RAW * CRIT * DEF_M_90 * 0.9     # 韧性未破 ×0.9
DAMAGE_BROKEN = RAW * CRIT * DEF_M_90             # 击破后 ×1.0
BREAK_DMG = (BREAK_BASE_DAMAGE * 0.5 * (0.5 + 30.0 / 120.0)
             * DEF_M_90)                          # 量子击破：3767.5533×0.5×(0.5+30/120)×def（1.5）


def _make():
    chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json")}
    stats = {"1015": Stats(atk=2800.0, speed=134.0, crit_rate=0.75, crit_dmg=1.5)}
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Quantum",
        hp=1e9, atk=1000, defense=1100.0, speed=10.0, toughness=30.0,
        weaknesses=["Quantum"])}
    rot = Rotation(actions={"1015": [Action(unit_id="1015", action="basic")] * 12})
    return Simulator(chars, stats, enemies, rot, target_av=400.0)


class TestBasicAttackOracle:
    def test_action_av_timeline(self):
        """行动时间线：134 速 → 每次行动 AV = 10000/134（E/AV 定值）。"""
        sim = _make()
        for n in range(1, 5):
            assert sim.run_step() == "1015"
            assert sim.t == pytest.approx(AV_BASIC * n, rel=1e-9)

    def test_damage_sp_energy_unbroken(self):
        """前两次普攻：未破韧伤害 ×0.9、削韧 10、SP +1（触顶 5）、能量 +20。"""
        sim = _make()
        sim.run_step()   # 第 1 次
        e = sim.damage_events[-1]
        assert e.amount == pytest.approx(DAMAGE_UNBROKEN, rel=1e-9)
        assert sim.toughness["elite"] == pytest.approx(20.0)
        assert sim.sp == pytest.approx(5.0)          # 4 + 1
        assert sim.energy["1015"] == pytest.approx(20.0)

        sim.run_step()   # 第 2 次
        assert sim.damage_events[-1].amount == pytest.approx(DAMAGE_UNBROKEN, rel=1e-9)
        assert sim.toughness["elite"] == pytest.approx(10.0)
        assert sim.sp == pytest.approx(5.0)          # 触顶
        assert sim.energy["1015"] == pytest.approx(40.0)

    def test_break_sequence(self):
        """第 3 次普攻：伤害仍 ×0.9（削韧前韧性 10 > 0，E1 伤害先于削韧）
        → 削韧归零 → 击破：击破伤害（手算）+ 延后 25% + 韧性归零。"""
        sim = _make()
        sim.run_step(); sim.run_step(); sim.run_step()
        # 本次普攻伤害（削韧前未破 → ×0.9）
        assert sim.damage_events[-2].amount == pytest.approx(DAMAGE_UNBROKEN, rel=1e-9)
        # 击破伤害事件
        brk = sim.damage_events[-1]
        assert brk.kind == "break"
        assert brk.amount == pytest.approx(BREAK_DMG, rel=1e-9)
        # 击破记录 + 韧性归零
        assert len(sim.breaks) == 1
        assert sim.breaks[0][1] == "elite"
        assert sim.toughness["elite"] == 0.0
        # 行动延后 25%：基于当前剩余距离（advance_time 已累计扣减）
        # 第 3 次行动时敌人距离 = 10000 - 3×AV×10（speed 10）→ ×1.25
        expected_dist = (10000.0 - AV_BASIC * 3 * 10.0) * 1.25
        assert sim.queue._entries["elite"].distance == pytest.approx(expected_dist, rel=1e-9)

    def test_post_break_damage_full(self):
        """击破后普攻：×1.0 不再 ×0.9。"""
        sim = _make()
        for _ in range(4):
            sim.run_step()
        assert sim.damage_events[-1].amount == pytest.approx(DAMAGE_BROKEN, rel=1e-9)

    def test_no_more_toughness_damage_after_break(self):
        """破韧后不再削韧、不重复击破。"""
        sim = _make()
        for _ in range(6):
            sim.run_step()
        assert sim.toughness["elite"] == 0.0
        assert len(sim.breaks) == 1


class TestUltOracle:
    def test_ult_immediate_release(self):
        """能量满 220 → 下一次行动结算后即时释放：ult 伤害手算、能量 220-220+5。

        序列模式需在序列中声明 ult（v1.5 语义：声明才自动释放）。
        """
        sim = _make()
        sim.rotation.actions = {"1015": [Action(unit_id="1015", action="basic")] * 12
                                + [Action(unit_id="1015", action="ult")]}
        sim.energy["1015"] = 220.0
        sim.run_step()   # basic（未破韧伤害 ×0.9）→ 结算后大招即时释放
        # basic 伤害 + ult 伤害 + ult 削韧 30 触发击破伤害（韧性 30 → 0）
        assert len(sim.damage_events) == 3
        ult = sim.damage_events[-2]
        assert ult.kind == "ult"     # ult 专属 kind（ult 乘区/报告可识别）
        assert ult.amount == pytest.approx(10.0 * 2800.0 * CRIT * DEF_M_90 * 0.9, rel=1e-9)
        assert sim.damage_events[-1].kind == "break"
        assert sim.ult_count["1015"] == 1
        # 能量：220（预设）+ 20（basic 回能）+ 5（ult 回能） - 220（ult 消耗）
        assert sim.energy["1015"] == pytest.approx(25.0)
        # 大招不占行动条：t 仍是一次普攻的 AV
        assert sim.t == pytest.approx(AV_BASIC, rel=1e-9)


class TestEnergyCostOracle:
    def test_skill_sp_energy(self):
        """战技：SP -1、能量 +30、伤害 mult 3.6（未破 ×0.9）。"""
        chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json")}
        stats = {"1015": Stats(atk=2800.0, speed=134.0, crit_rate=0.75, crit_dmg=1.5)}
        enemies = {"elite": Enemy(
            id="elite", name="精英", element="Quantum",
            hp=1e9, atk=1000, defense=1100.0, speed=10.0, toughness=30.0,
            weaknesses=["Quantum"])}
        rot = Rotation(actions={"1015": [Action(unit_id="1015", action="skill")] * 5})
        sim = Simulator(chars, stats, enemies, rot, target_av=400.0)
        sim.run_step()
        e = sim.damage_events[-1]
        assert e.amount == pytest.approx(3.6 * 2800.0 * CRIT * DEF_M_90 * 0.9, rel=1e-9)
        assert sim.sp == pytest.approx(3.0)            # 4 - 1
        assert sim.energy["1015"] == pytest.approx(30.0)
        assert sim.toughness["elite"] == pytest.approx(10.0)   # 削韧 20
