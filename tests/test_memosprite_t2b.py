"""忆灵（迷迷）机制 T2b 测试——research 定值（docs/research/memory-trailblazer-mem.md）。

- 普通行动：4 段随机单体（每段 36%）+ 全体（90%），行动后充能 +5%
- 强化行动：无伤害——100% 拉条 + 声援（per-hit 真伤 28%，3 次行动）
- 声援真伤：目标每段伤害后附加（独立乘区，kind=true）
- 充能联动：全队每恢复 10 点能量 → 迷迷 +1%
- 大招：充能 40% + 迷迷立即行动

场景：单记忆主（8007）vs 90 级精英（def 1100，Ice 弱点，韧性 300）。
记忆主：atk=1800, speed=145, cr=0%（段级判定：非暴击确定性），cd=50%（元素增伤 0）。
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Action, Enemy, Rotation, Stats

DEF_M = 1000.0 / 2100.0          # 80 级 vs 1100 防
CRIT = 1.0                       # cr=0%（非暴击确定性）


def _make():
    chars = {"8007": load_character(DATA_DIR / "characters" / "8007.json")}
    stats = {"8007": Stats(atk=1800.0, speed=145.0, crit_rate=0.0, crit_dmg=0.5)}
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Ice",
        hp=1e9, atk=1000, defense=1100.0, speed=10.0, toughness=300.0,
        weaknesses=["Ice"])}
    rot = Rotation(actions={"8007": [Action(unit_id="8007", action="basic")] * 20})
    return Simulator(chars, stats, enemies, rot, target_av=400.0)


def _run_until_mem(sim, max_steps=20):
    """跑到下一个行动是 MEM（不消费 MEM 行动）。"""
    for _ in range(max_steps):
        nxt = sim.queue.next()
        if nxt is not None and nxt[0] == "MEM":
            return True
        if sim.run_step() is None:
            return False
    return False


class TestMemospriteBasic:
    def test_basic_action_multihit(self):
        """普通行动：4 段随机单体 + 全体；单敌场景伤害可手算。"""
        sim = _make()
        assert _run_until_mem(sim)
        charge_before = sim.memosprite["charge"]
        n_before = len(sim.damage_events)
        sim.run_step()  # MEM 行动
        events = sim.damage_events[n_before:]
        # 4 段单体（kind=normal，每段 0.36 倍率）+ 1 次全体（0.90）
        assert len(events) == 5
        hit_dmg = 1800.0 * 0.36 * CRIT * DEF_M * 0.9
        aoe_dmg = 1800.0 * 0.90 * CRIT * DEF_M * 0.9
        assert events[0].amount == pytest.approx(hit_dmg, rel=1e-9)
        assert events[-1].amount == pytest.approx(aoe_dmg, rel=1e-9)
        total = sum(e.amount for e in events)
        assert total == pytest.approx(1800.0 * 2.34 * CRIT * DEF_M * 0.9, rel=1e-9)
        # 行动后充能 +5%（此前 8007 已行动回能 +2%，取增量）
        assert sim.memosprite["charge"] == pytest.approx(charge_before + 5.0)

    def test_rng_targets_randomized(self):
        """多段随机单体目标由 RNG 决定（E12）：不同 seed 目标序列不同（双敌场景）。"""
        def run(seed):
            chars = {"8007": load_character(DATA_DIR / "characters" / "8007.json")}
            stats = {"8007": Stats(atk=1800.0, speed=145.0, crit_rate=0.0, crit_dmg=0.5)}
            enemies = {
                "a": Enemy(id="a", name="甲", element="Ice", hp=1e9, atk=1000,
                           defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Ice"]),
                "b": Enemy(id="b", name="乙", element="Ice", hp=1e9, atk=1000,
                           defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Ice"]),
            }
            rot = Rotation(actions={"8007": [Action(unit_id="8007", action="basic")] * 20})
            sim = Simulator(chars, stats, enemies, rot, target_av=400.0, seed=seed)
            assert _run_until_mem(sim)
            n0 = len(sim.damage_events)
            sim.run_step()
            return [e.target for e in sim.damage_events[n0:] if e.kind == "normal"][:4]
        seqs = {tuple(run(s)) for s in range(8)}
        assert all(len(q) == 4 and set(q) <= {"a", "b"} for q in seqs)
        assert len(seqs) > 1  # 存在不同 seed 目标序列（随机性生效）


class TestMemospriteEnhanced:
    def test_enhanced_advances_and_buffs_not_damage(self):
        """强化行动：无伤害——100% 拉条目标 + 声援 buff。"""
        sim = _make()
        assert _run_until_mem(sim)
        sim.memosprite["charge"] = 100.0
        n_before = len(sim.damage_events)
        av_before = sim.queue.snapshot()["8007"]
        sim.run_step()  # MEM 强化行动
        # 无伤害事件
        assert len(sim.damage_events) == n_before
        # 拉条 100%：目标 8007 距离清零
        assert sim.queue.snapshot()["8007"] == pytest.approx(0.0, abs=1e-6)
        # 声援 buff（28%，目标 = 主C 8007）
        assert sim.buffs.sum_for("mems_support", "8007") == pytest.approx(0.28)
        # 充能清零
        assert sim.memosprite["charge"] == 0.0
        assert av_before > 0.0

    def test_mems_support_true_damage(self):
        """声援目标每段伤害后附加真伤 = 该段伤害 × 28%（独立乘区，kind=true）。"""
        sim = _make()
        assert _run_until_mem(sim)
        sim.memosprite["charge"] = 100.0
        sim.run_step()  # 强化：声援 8007
        # 8007 行动打一下（basic 1.4 倍率）
        n_before = len(sim.damage_events)
        sim.run_step()  # 8007 被拉条到 0 → 立即行动
        events = sim.damage_events[n_before:]
        # basic 伤害 + 真伤（同一来源 8007）
        normal = [e for e in events if e.kind == "normal"]
        true = [e for e in events if e.kind == "true"]
        assert len(normal) == 1 and len(true) == 1
        assert true[0].amount == pytest.approx(normal[0].amount * 0.28, rel=1e-9)
        # 真伤不触发真伤（无递归）
        assert sum(1 for e in events if e.kind == "true") == 1


class TestMemospriteCharge:
    def test_energy_gain_charges_mem(self):
        """全队每恢复 10 点能量 → 迷迷充能 +1%。"""
        sim = _make()
        sim._ensure_memosprite_summon()
        sim.memosprite["charge"] = 0.0
        sim.energy["8007"] = 0.0
        sim._character_act("8007")   # 普攻：回能 20 → 迷迷 +2%
        assert sim.memosprite["charge"] == pytest.approx(2.0)

    def test_ult_charges_and_immediate(self):
        """大招：充能 40% + 迷迷立即行动（距离清零）。"""
        sim = _make()
        sim._ensure_memosprite_summon()
        sim.energy["8007"] = 160.0
        sim.rotation.actions = {"8007": [Action(unit_id="8007", action="basic")] * 10
                                + [Action(unit_id="8007", action="ult")]}
        sim._character_act("8007")      # 普攻（回能 20 → 充能 +2%）
        sim._try_immediate_ults()        # 能量满 → 大招即时释放
        assert sim.ult_count["8007"] == 1
        # 充能：+40%（大招）+ 0.5%（大招回能 5）+ 2%（普攻回能 20）
        assert sim.memosprite["charge"] == pytest.approx(42.5)
        # 迷迷立即行动：距离清零
        assert sim.queue.snapshot()["MEM"] == pytest.approx(0.0, abs=1e-6)
