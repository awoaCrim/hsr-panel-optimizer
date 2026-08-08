"""回放一致性 / 回退测试（ADR-0007 3.1 + mechanics-spec E11/E12）。

- 回放一致性：从快照恢复后重跑 == 原执行（bit 级，RNG 状态随事件流）
- undo：恢复到压栈时快照（逐字段）；restart：回到初始状态
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemies, load_rotation, load_team


def _make_sim(seed: int = 0):
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    rot = load_rotation(DATA_DIR / "rotation.json")
    return Simulator(chars, stats, enemies, rot, target_av, level, seed=seed)


def _trace(sim) -> dict:
    """可序列化轨迹（与 T2a golden 格式一致）。"""
    r = sim._result()
    return {
        "t_end": r.t_end,
        "total_damage": r.total_damage,
        "actions": [[a.t, a.unit_id, a.action, a.detail] for a in r.actions],
        "damage_events": [[e.t, e.source, e.target, round(e.amount, 6), e.kind]
                          for e in sim.damage_events],
        "sp_timeline": [[round(t, 4), sp] for t, sp in r.sp_timeline],
        "breaks": [[round(t, 4), e] for t, e in r.breaks],
        "enemy_hp_left": {k: round(v, 4) for k, v in r.enemy_hp_left.items()},
        "ult_count": r.ult_count,
        "action_count": r.action_count,
    }


class TestReplayConsistency:
    def test_replay_from_initial_snapshot_identical(self):
        """从初始快照恢复后重跑 == 原执行（bit 级）。"""
        sim1 = _make_sim(seed=9)
        initial = sim1.snapshot()
        sim1.run()
        trace1 = _trace(sim1)

        sim2 = _make_sim(seed=9)
        sim2.restore(initial)
        sim2.run()
        assert _trace(sim2) == trace1

    def test_replay_from_mid_snapshot_identical(self):
        """从任意中间快照恢复后重跑 == 直接从该点跑。"""
        sim = _make_sim(seed=11)
        for _ in range(7):
            if sim.run_step() is None:
                break
        mid = sim.snapshot()
        sim.run()
        trace_continue = _trace(sim)

        sim2 = _make_sim(seed=11)
        for _ in range(7):
            if sim2.run_step() is None:
                break
        sim2.restore(mid)
        sim2.run()
        assert _trace(sim2) == trace_continue


class TestUndo:
    def test_undo_restores_exact_snapshot(self):
        """连续 undo：状态逐字段 == 压栈时快照（决策点 = 我方行动前）。"""
        sim = _make_sim(seed=3)
        expected = []
        for _ in range(12):
            u = sim.run_step()
            if u is None:
                break
            if u in sim.chars:
                expected.append(sim._snapshots[-1])
        assert len(expected) >= 3, "应至少有 3 个我方行动决策点"
        for snap in reversed(expected):
            assert sim.undo() is True
            cur = sim.snapshot()
            assert cur.t == snap.t
            assert cur.sp == snap.sp
            assert cur.energy == snap.energy
            assert cur.toughness == snap.toughness
            assert cur.queue_entries == snap.queue_entries
            assert cur.enemy_hp == snap.enemy_hp
            assert len(cur.log) == len(snap.log)
            assert len(cur.damage_events) == len(snap.damage_events)
            assert cur.rng_state[1] == snap.rng_state[1]

    def test_undo_empty_stack_returns_false(self):
        sim = _make_sim()
        assert sim.undo() is False

    def test_undo_then_different_decision_yields_different_trace(self):
        """undo 后走不同决策 → 轨迹确实不同（分支探索前提）。

        注意：当前自动决策（policy）是确定性的，undo 后重跑必然得到同一轨迹；
        分支差异来自外部改变决策——此处模拟 LLM act 换序列（切到序列模式 basic 连打）。
        """
        from hsr_sim.model import Action

        sim_a = _make_sim(seed=21)
        for _ in range(4):
            if sim_a.run_step() is None:
                break
        assert sim_a.undo()
        # 换决策：红A 改打 basic（替代 policy 自动决策）
        sim_a.rotation.policy = {}
        sim_a.rotation.actions = {"1015": [Action(unit_id="1015", action="basic")] * 50}
        sim_a.run()

        sim_b = _make_sim(seed=21)
        for _ in range(4):
            if sim_b.run_step() is None:
                break
        assert sim_b.undo()
        sim_b.run()  # 保持原决策
        assert _trace(sim_a) != _trace(sim_b)


class TestRestart:
    def test_restart_resets_to_initial(self):
        sim = _make_sim(seed=5)
        sim.run()
        assert sim.t > 0.0 and len(sim.log) > 0
        sim.restart()
        assert sim.t == 0.0
        assert sim.sp == 4.0
        assert len(sim.log) == 0
        assert len(sim.damage_events) == 0
        assert sim.ult_count == {cid: 0 for cid in sim.chars}
        sim.run()  # 能重跑（确定性）
        assert sim.t > 0.0

    def test_restart_clears_undo_stack(self):
        sim = _make_sim(seed=2)
        sim.run_step()
        assert len(sim._snapshots) >= 1
        sim.restart()
        assert len(sim._snapshots) == 0
        assert sim.undo() is False


class TestRng:
    def test_rng_state_serialized_in_snapshot(self):
        """RNG 状态随快照序列化：恢复后随机序列与同 seed 同消耗一致（E12）。"""
        sim = _make_sim(seed=123)
        sim.rng.random()                     # 消耗 1
        snap = sim.snapshot()
        sim.rng.random()
        sim.rng.random()                     # 消耗 3
        sim.restore(snap)                    # 回到消耗 1
        a = sim.rng.random()                 # 消耗 2

        sim2 = _make_sim(seed=123)
        sim2.rng.random()                    # 消耗 1
        b = sim2.rng.random()                # 消耗 2
        assert a == b
