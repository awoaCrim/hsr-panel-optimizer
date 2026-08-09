"""行动队列对账测试：AV 计算、拉条、行动后重置（验收标准①）。"""
import pytest

from hsr_sim.engine.av_queue import ACTION_DISTANCE, ActionQueue


class TestAvBasics:
    def test_av_134_speed(self):
        # 10000 / 134 = 74.63
        q = ActionQueue()
        q.add("u1", 134.0)
        unit, av = q.next()
        assert unit == "u1"
        assert av == pytest.approx(10000 / 134)

    def test_order_by_av(self):
        q = ActionQueue()
        q.add("slow", 100.0)   # AV 100
        q.add("fast", 200.0)   # AV 50
        unit, av = q.next()
        assert unit == "fast"
        assert av == pytest.approx(50.0)

    def test_advance_time_reduces_distance(self):
        q = ActionQueue()
        q.add("u1", 100.0)
        q.advance_time(40.0)
        unit, av = q.next()
        assert av == pytest.approx(60.0)

    def test_ordered_matches_next_and_filters_stale_entries(self):
        """UI 投影必须与实际 next 顺序一致，且不暴露拉条前的过期堆项。"""
        q = ActionQueue()
        q.add("first", 100.0)
        q.add("second", 100.0)
        q.add("fast", 200.0)
        q.advance("second", 0.5)  # 与 fast 同 AV，但 second 的新有效堆项后入
        ordered = q.ordered()
        assert ordered == [("fast", 50.0), ("second", 50.0), ("first", 100.0)]
        assert q.next() == ordered[0]


class TestAdvanceAndReset:
    def test_advance_50pct(self):
        # 拉条 50%：剩余距离减半 → AV 减半
        q = ActionQueue()
        q.add("u1", 100.0)
        q.advance("u1", 0.5)
        _, av = q.next()
        assert av == pytest.approx(50.0)

    def test_advance_100pct(self):
        # 知更鸟大招：全队立即行动
        q = ActionQueue()
        q.add("u1", 100.0)
        q.advance("u1", 1.0)
        _, av = q.next()
        assert av == pytest.approx(0.0)

    def test_postpone_25pct(self):
        # 击破延后 25%：距离 ×1.25
        q = ActionQueue()
        q.add("u1", 100.0)
        q.postpone("u1", 0.25)
        _, av = q.next()
        assert av == pytest.approx(125.0)

    def test_reset_after_action(self):
        q = ActionQueue()
        q.add("u1", 100.0)
        q.advance_time(100.0)          # 到点
        q.reset_after_action("u1")
        _, av = q.next()
        assert av == pytest.approx(ACTION_DISTANCE / 100.0)

    def test_keep_acting(self):
        # 红A 战技额外行动：distance 保持 0
        q = ActionQueue()
        q.add("u1", 100.0)
        q.advance_time(100.0)
        q.keep_acting("u1")
        _, av = q.next()
        assert av == pytest.approx(0.0)

    def test_speed_change_keeps_distance(self):
        # 速度变化不影响距离，AV 重算（流萤 -60 速的分段模型）
        q = ActionQueue()
        q.add("u1", 200.0)
        q.advance_time(25.0)           # distance = 5000
        q.set_speed("u1", 100.0)
        _, av = q.next()
        assert av == pytest.approx(50.0)
