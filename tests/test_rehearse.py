"""推演会话（ADR-0007 3.3）接口测试：observe/act/undo/restart/propose_setup。

场景：单红A（1015）vs 单精英（HP 大、韧性 300、Ice 弱点），target_av=400。
决策点 = 1015 行动前；大招时机由 act 的 ults 参数控制（D2）。
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Action, Enemy, Rotation, Stats
from hsr_sim.rehearse import RehearseError, RehearsalSession, UndoBudgetExceeded


def _make_session(seed: int = 0, **kw) -> RehearsalSession:
    chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json")}
    stats = {"1015": Stats(atk=3000.0, speed=145.0, crit_rate=0.8, crit_dmg=1.5)}
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Ice", hp=1e9, atk=1000,
        defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Ice"])}
    rot = Rotation()
    sim = Simulator(chars, stats, enemies, rot, target_av=400.0, seed=seed)
    return RehearsalSession(sim, name="单红A测试", **kw)


class TestObserve:
    def test_stops_at_decision_point(self):
        s = _make_session()
        state = s.observe()
        assert state["phase"] == "decision"
        assert state["queue"]["next"] == "1015"
        assert state["decision"]["unit"] == "1015"
        assert "basic" in state["decision"]["skills"]
        # 结构完整性：能量/SP/敌人/伤害/信任信封
        assert "energy" in state and "1015" in state["energy"]
        assert state["sp"]["value"] == pytest.approx(4.0)
        assert state["enemies"]["elite"]["hp"] == pytest.approx(1e9)
        assert "trust" in state and "level" in state["trust"]

    def test_decision_after_auto_steps(self):
        """敌人/忆灵行动自动执行，决策点只在行动前。"""
        s = _make_session()
        s.observe()
        state = s.observe()      # 无 act 时再次 observe 不推进
        assert state["queue"]["next"] == "1015"
        assert state["t"] == 0.0


class TestAct:
    def test_act_executes_skill(self):
        s = _make_session()
        s.observe()
        res = s.act(skill="skill")
        assert res["damage_delta"] > 0
        assert len(s.acts) == 1
        assert s.acts[0].unit_id == "1015"
        assert s.acts[0].skill == "skill"
        state = s.observe()
        assert state["damage"]["total"] == pytest.approx(res["damage_delta"], abs=0.5)

    def test_act_rejects_invalid_skill_and_ult(self):
        s = _make_session()
        s.observe()
        with pytest.raises(RehearseError, match="无技能"):
            s.act(skill="nonsense")
        with pytest.raises(RehearseError, match="不占行动条"):
            s.act(skill="ult")

    def test_act_requires_decision_point(self):
        s = _make_session()
        s.act(skill="skill")
        # 无 observe 时 next 可能不是我方行动（被拉条/连锁）——act 应校验决策点
        # 此处单角色无自动行动：act 后 next 仍为 1015，决策点成立
        res = s.act(skill="basic")
        assert res["damage_delta"] > 0
        assert len(s.acts) == 2


class TestUltTiming:
    def test_ult_hold_via_ults_dict(self):
        """ults={} 或 {cid: False}：能量满也不放大招（官方规则：可等）。"""
        s = _make_session()
        s.observe()
        s.sim.energy["1015"] = s.sim.chars["1015"].skills["ult"].energy_cost  # 刚好满
        s.act(skill="basic", ults={})          # 全 hold
        assert s.sim.ult_count["1015"] == 0
        s.act(skill="basic", ults={"1015": False})
        assert s.sim.ult_count["1015"] == 0
        s.act(skill="basic", ults=None)        # 默认全放
        assert s.sim.ult_count["1015"] == 1

    def test_ult_used_reported(self):
        s = _make_session()
        s.observe()
        s.sim.energy["1015"] = s.sim.chars["1015"].skills["ult"].energy_cost
        res = s.act(skill="basic", ults={"1015": True})
        assert res["ult_used"] == ["1015"]


class TestUndo:
    def test_undo_restores_and_archives(self):
        s = _make_session()
        s.observe()
        s.act(skill="skill")
        a1 = s.sim.action_count["1015"]
        t1 = s.sim.t
        s.act(skill="skill")
        a2 = s.sim.action_count["1015"]
        assert a2 > a1      # 连锁中 t 可能相同（红A 回路），行动数必然增加
        state = s.undo(reason="试试别的")
        assert s.sim.action_count["1015"] == a1
        assert s.sim.t == pytest.approx(t1)
        assert len(s.acts) == 1
        assert len(s.abandoned) == 1
        assert s.abandoned[0].reason == "试试别的"
        assert len(s.abandoned[0].acts) == 1
        assert s.abandoned[0].fork_after == 0
        assert state["progression"]["undo_used"] == 1

    def test_undo_to_arbitrary(self):
        """回退任意行动：undo_to(0) 回到初始。"""
        s = _make_session()
        for _ in range(3):
            s.observe()
            s.act(skill="skill")
        assert len(s.acts) == 3
        s.undo_to(0, reason="整段重来")
        assert s.sim.t == pytest.approx(0.0)
        assert s.acts == []
        assert len(s.abandoned) == 1
        assert len(s.abandoned[0].acts) == 3
        assert s.abandoned[0].fork_after == -1

    def test_undo_empty_raises(self):
        s = _make_session()
        with pytest.raises(RehearseError, match="无 act"):
            s.undo()

    def test_per_step_budget(self):
        """每步回退预算（D4）：连续 undo 上限 3，act 后重置。"""
        s = _make_session(per_step_budget=3)
        for _ in range(3):
            s.observe()
            s.act(skill="skill")
        for _ in range(3):
            s.undo()
        with pytest.raises(UndoBudgetExceeded, match="每步"):
            s.undo()
        s.observe()
        s.act(skill="skill")     # 新 act：重置每步预算
        s.undo()
        assert s.total_undo == 4

    def test_global_budget(self):
        """全局回退预算（D4）：耗尽后强制收敛。"""
        s = _make_session(undo_budget=2, per_step_budget=10)
        for _ in range(2):
            s.observe()
            s.act(skill="skill")
            s.undo()             # 每步 1 次，全局 2 次
        with pytest.raises(UndoBudgetExceeded, match="全局"):
            s.observe()
            s.act(skill="skill")
            s.undo()

    def test_determinism_after_undo(self):
        """同 seed 同决策重放：结果一致（D1 固定 seed 随机）。"""
        def run_twice():
            s = _make_session(seed=7)
            s.observe()
            s.act(skill="skill", ults={})
            s.act(skill="basic", ults={})
            total = s._total_damage()
            s.undo_to(0)
            s.observe()
            s.act(skill="skill", ults={})
            s.act(skill="basic", ults={})
            return total, s._total_damage()
        a, b = run_twice()
        assert a == pytest.approx(b, abs=1e-6)


class TestRestartAndSetup:
    def test_restart(self):
        s = _make_session()
        s.observe()
        s.act(skill="skill")
        s.act(skill="skill")
        state = s.restart(reason="重开一局")
        assert s.sim.t == pytest.approx(0.0)
        assert s.acts == []
        assert len(s.abandoned) == 1
        assert len(s.abandoned[0].acts) == 2
        assert state["queue"]["next"] == "1015"

    def test_propose_setup_new_session(self):
        """配置变更 = 新会话（D5）：undo 栈清空，旧会话冻结为历史。"""
        s = _make_session()
        s.observe()
        s.act(skill="skill")
        s2 = s.propose_setup(team=DATA_DIR / "team_reda.json",
                             enemy=DATA_DIR / "enemy_elite90.json",
                             rotation=DATA_DIR / "rotation.json", name="换队伍")
        assert s2.name == "换队伍"
        assert s2.acts == []
        assert s2.total_undo == 0
        assert len(s2.history) == 1
        assert s2.history[0]["setup"]["name"] == "单红A测试"
        # 旧会话不受影响
        assert len(s.acts) == 1

    def test_terminal_av_exhausted(self):
        s = _make_session()
        # 缩短 target_av 触发边界：重建 sim 不可行（sim 引用），直接改 sim 属性
        s.sim.target_av = 20.0
        s.observe()
        assert s.observe()["phase"] == "terminal"
        with pytest.raises(RehearseError, match="已终止"):
            s.act(skill="basic")


class TestAdvanceTargetRules:
    """官方目标选择器规则：花火战技不可自拉、目标必须为队友。"""

    def _make_sparkle_session(self, seed: int = 0) -> RehearsalSession:
        chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json"),
                 "1306": load_character(DATA_DIR / "characters" / "1306.json")}
        stats = {"1015": Stats(atk=3000.0, speed=145.0, crit_rate=0.8, crit_dmg=1.5),
                 "1306": Stats(atk=2000.0, speed=162.0, crit_rate=0.05, crit_dmg=0.5)}
        enemies = {"elite": Enemy(
            id="elite", name="精英", element="Ice", hp=1e9, atk=1000,
            defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Ice"])}
        sim = Simulator(chars, stats, enemies, Rotation(), target_av=400.0, seed=seed)
        return RehearsalSession(sim, name="花火规则测试")

    def test_ally_targets_in_decision(self):
        """有拉条技能的角色：decision.ally_targets 给出可拉队友（排除自己）。"""
        s = self._make_sparkle_session()
        state = s.observe()
        while state["decision"]["unit"] != "1306":
            s.act(skill="basic", ults={})
            state = s.observe()
        assert state["decision"]["ally_targets"] == ["1015"]

    def test_act_rejects_self_advance(self):
        """花火战技 target=自己：拒绝（官方目标选择器排除自身）。"""
        s = self._make_sparkle_session()
        state = s.observe()
        while state["decision"]["unit"] != "1306":
            s.act(skill="basic", ults={})
            state = s.observe()
        with pytest.raises(RehearseError, match="不可选择自己"):
            s.act(skill="skill", target="1306")

    def test_act_rejects_enemy_as_advance_target(self):
        """花火战技 target=敌人：拒绝（拉条目标必须是队友）。"""
        s = self._make_sparkle_session()
        state = s.observe()
        while state["decision"]["unit"] != "1306":
            s.act(skill="basic", ults={})
            state = s.observe()
        with pytest.raises(RehearseError, match="必须是我方队友"):
            s.act(skill="skill", target="elite")

    def test_advance_target_ally_works(self):
        """花火拉队友：拉条生效（目标距离归零），自身不被拉。"""
        s = self._make_sparkle_session()
        state = s.observe()
        while state["decision"]["unit"] != "1306":
            s.act(skill="basic", ults={})
            state = s.observe()
        av_self = s.sim.queue.snapshot()["1306"]
        s.act(skill="skill", target="1015", ults={})
        av_ally = s.sim.queue.snapshot()["1015"]
        # 1015 被拉 50%：剩余距离 = (初始 av - 1306 行动耗时) × 0.5
        av_1015_init = 10000.0 / 145.0
        assert av_ally == pytest.approx((av_1015_init - av_self) * 0.5, rel=1e-6)
        # 花火自己未被拉（距离变化 = 时间推进后的正常重置，非 50% 跳变）
        assert s.sim.queue.snapshot()["1306"] == pytest.approx(av_self, rel=1e-9)


class TestSerialization:
    def test_state_roundtrip(self):
        """会话持久化往返：状态、决策、放弃路线、预算计数完整恢复。"""
        s = RehearsalSession.from_files(seed=3, name="roundtrip")
        s.observe()
        s.act(skill="skill", ults={})
        s.act(skill="basic", ults={})
        s.undo(reason="试一下")
        state = s.state_dict()
        s2 = RehearsalSession.from_state(state, base_dir=DATA_DIR)
        assert s2.sim.t == pytest.approx(s.sim.t)
        assert s2._total_damage() == pytest.approx(s._total_damage())
        assert len(s2.acts) == len(s.acts)
        assert len(s2.abandoned) == len(s.abandoned)
        assert s2.abandoned[0].reason == "试一下"
        assert s2.total_undo == s.total_undo
        assert s2.undo_since_act == s.undo_since_act
        # 恢复后可继续推演
        s2.observe()
        s2.act(skill="basic")
        assert len(s2.acts) == len(s.acts) + 1
