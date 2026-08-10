"""模拟器测试：SP 追踪 / 红A额外行动 / 拉条 / 击破 / 全队冒烟（验收标准①+②）。"""
import pytest

from hsr_sim.engine.damage import expected_damage
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemies, load_rotation, load_team
from hsr_sim.model import Action, CharacterPolicy, Enemy, Rotation, Stats
from hsr_sim.report import build_report


def _characters():
    from hsr_sim.loader import load_character
    return {cid: load_character(DATA_DIR / "characters" / f"{cid}.json")
            for cid in ("1015", "1306", "1309", "8007")}


def _stats():
    return {
        "1015": Stats(atk=2800, speed=134, crit_rate=0.75, crit_dmg=1.50),
        "1306": Stats(atk=1600, speed=161, crit_rate=0.20, crit_dmg=2.00, energy_regen=1.19),
        "1309": Stats(atk=2500, speed=121, crit_rate=0.05, crit_dmg=0.50, energy_regen=1.50),
        "8007": Stats(atk=1800, speed=145, crit_rate=0.05, crit_dmg=0.50, energy_regen=1.50),
    }


def _enemies():
    return {
        "e1": Enemy(id="e1", name="精英甲", element="Quantum", hp=1e9, atk=1000,
                    defense=1000, speed=100, toughness=480,
                    weaknesses=["Quantum", "Physical", "Ice"]),
    }


def _rot(actions):
    return Rotation(actions={k: [Action(unit_id=a[0], action=a[1], target=a[2] if len(a) > 2 else "")
                                 for a in seq] for k, seq in actions.items()})


class TestSpTracking:
    def test_basic_gains_sp_skill_loses(self):
        sim = Simulator(_characters(), _stats(), _enemies(),
                        _rot({"1015": [("1015", "basic"), ("1015", "skill")]}), target_av=500)
        # 基础 5 + 红A额外能力行迹「投影魔术」2 + 花火天赋 2 = 9。
        assert sim.sp_max == pytest.approx(9.0)
        # 动作级验证：开局 4 → 普攻 +1 → 5；战技 -1 → 4
        sim._character_act("1015")   # basic
        assert sim.sp == pytest.approx(5.0)
        sim._character_act("1015")   # skill
        assert sim.sp == pytest.approx(4.0)

    def test_archer_alone_adds_two_to_cap(self):
        """红A额外能力行迹「投影魔术」：在场时使战技点上限提高 2 点。"""
        chars = _characters()
        sim = Simulator({"1015": chars["1015"]}, {"1015": _stats()["1015"]},
                        _enemies(), Rotation(), target_av=500)
        assert sim.sp_max == pytest.approx(7.0)

    def test_sp_never_below_zero_with_basic_rotation(self):
        sim = Simulator(_characters(), _stats(), _enemies(),
                        _rot({"1015": [("1015", "basic")] * 10}), target_av=500)
        r = sim.run()
        assert r.sp_min >= 0.0


class TestExtraAction:
    def test_archer_skill_keeps_acting(self):
        chars = _characters()
        stats = _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1015": [("1015", "skill")] * 2}), target_av=500)
        sim.run()
        # 战技 extra_action：连发的战技发生在同一时刻（t 相同）；SP 耗尽后降级普攻恢复循环
        acts = [a for a in sim.log if a.unit_id == "1015" and a.action == "skill"]
        assert len(acts) >= 2
        assert acts[0].t == acts[1].t
        # SP 不足时战技降级普攻（游戏规则），避免无限行动
        degraded = [a for a in sim.log if a.unit_id == "1015" and "SP不足" in a.detail]
        assert len(degraded) > 0
        # 时间正常推进（没有卡在 dt=0 死循环）
        assert sim.t > 400.0


    def test_extra_actions_do_not_tick_owner_buffs(self):
        """红A额外行动链属于同一回合：链中间不掉持续回合，链结束时才 -1。"""
        chars = _characters()
        stats = _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1015": [("1015", "skill")] * 2}), target_av=500)
        sim.sp = 9.0
        sim.buffs.add("crit_dmg", 0.4, "1015", 3, target="1015")

        sim._character_act("1015")
        assert "1015" in sim.burst_chain
        assert sim.buffs.get("crit_dmg", "1015").duration == 2

        sim._character_act("1015")
        assert "1015" in sim.burst_chain
        assert sim.buffs.get("crit_dmg", "1015").duration == 2

        # 强制让下一次战技成为额外行动链的最后一次。
        sim.rotation.actions["1015"] = [Action(unit_id="1015", action="skill")]
        sim.rotation.policy["1015"] = CharacterPolicy(chain_max=3)
        sim._character_act("1015")
        assert "1015" not in sim.burst_chain
        assert sim.buffs.get("crit_dmg", "1015").duration == 2

        # 下一次正常回合开始后才再次递减。
        sim.rotation.actions["1015"] = [Action(unit_id="1015", action="basic")]
        sim._character_act("1015")
        assert sim.buffs.get("crit_dmg", "1015").duration == 1

class TestAdvance:
    def test_sparkle_advance_archer(self):
        chars, stats = _characters(), _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1306": [("1306", "skill", "1015")]}), target_av=500)
        sim.t = 0.0
        # 直接执行花火战技：拉条目标应为红A（rotation target），红A 剩余距离减半
        sim._character_act("1306")
        archer_av = sim.queue.snapshot()["1015"]
        assert archer_av == pytest.approx((10000 * 0.5) / 134.0)
        # 花火自己不受拉条影响
        sparkle_av = sim.queue.snapshot()["1306"]
        assert sparkle_av == pytest.approx(10000 / 161.0)

    def test_sparkle_buff_targets_archer(self):
        """花火战技的暴伤 buff 应加给红A（友方目标），而非敌人。"""
        chars, stats = _characters(), _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1306": [("1306", "skill", "1015")]}), target_av=500)
        sim._character_act("1306")
        assert sim.buffs.sum_for("crit_dmg", "1015") == pytest.approx(0.93)
        assert sim.buffs.sum_for("crit_dmg", "e1") == 0.0


class TestBreak:
    def test_break_logic_deterministic(self):
        """削韧归零 → 击破：break 事件 + 行动延后 25%（手动控韧性，不依赖循环）。"""
        chars, stats = _characters(), _stats()
        sim = Simulator(chars, stats, _enemies(), _rot({}), target_av=500)
        sim.toughness["e1"] = 40.0   # 削韧 30 后剩 10，未破
        sim._character_act("1015")   # 战技（rotation 为空 → 默认普攻，削韧 10）
        # 注：_rot({}) 时 next_action 返回 None → 默认 basic（削韧 10）
        assert sim.toughness["e1"] == pytest.approx(30.0)
        assert len(sim.breaks) == 0
        # 再来 3 次普攻（削韧 10×3）→ 归零破韧
        for _ in range(3):
            sim._character_act("1015")
        assert sim.toughness["e1"] == 0.0
        assert len(sim.breaks) == 1
        # 破韧后不再削韧、不再重复 break
        sim._character_act("1015")
        assert len(sim.breaks) == 1

    def test_weakness_required(self):
        """非弱点属性不削韧。"""
        chars, stats = _characters(), _stats()
        enemies = _enemies()
        enemies["e1"].weaknesses = ["Physical"]   # 红A 量子不在弱点
        sim = Simulator(chars, stats, enemies, _rot({}), target_av=500)
        sim.toughness["e1"] = 40.0
        sim._character_act("1015")
        assert sim.toughness["e1"] == pytest.approx(40.0)   # 不削韧
        assert len(sim.breaks) == 0


class TestUltImmediate:
    def test_ult_fires_when_energy_full_without_acting(self):
        """终结技不占行动条：能量够时即时释放，不推进行动条。"""
        chars, stats = _characters(), _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1306": [("1306", "skill"), ("1306", "basic"), ("1306", "ult")]}),
                        target_av=500)
        sim.t = 100.0
        sim.energy["1306"] = 110.0   # 花火能量已满
        av_before = sim.queue.snapshot()["1306"]
        sim._try_immediate_ults()
        assert sim.ult_count["1306"] == 1
        assert sim.sp == pytest.approx(8.0)          # 大招回 4 SP（开局 4+4=8，未触及上限 9）
        assert sim.energy["1306"] == pytest.approx(5 * 1.19)  # 释放后剩 5×充能
        # 行动条未被重置（大招不占行动条）
        assert sim.queue.snapshot()["1306"] == av_before

    def test_ult_not_fired_without_energy(self):
        chars, stats = _characters(), _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1306": [("1306", "skill"), ("1306", "basic"), ("1306", "ult")]}),
                        target_av=500)
        sim.energy["1306"] = 50.0
        sim._try_immediate_ults()
        assert sim.ult_count["1306"] == 0

    def test_ult_requires_ult_in_rotation(self):
        """rotation 未声明 ult 的角色即使能量满也不自动开大。"""
        chars, stats = _characters(), _stats()
        sim = Simulator(chars, stats, _enemies(),
                        _rot({"1306": [("1306", "skill"), ("1306", "basic")]}),
                        target_av=500)
        sim.energy["1306"] = 110.0
        sim._try_immediate_ults()
        assert sim.ult_count["1306"] == 0


class TestTeamSmoke:
    def test_full_team_2t_runs(self):
        """验收标准②：静态 JSON 全队 2T 冒烟。"""
        chars, stats, speed_targets = load_team(DATA_DIR / "team_reda.json",
                                                DATA_DIR / "characters")
        enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
        rotation = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, enemies, rotation, target_av, level)
        result = sim.run()
        report = build_report(result, sum(e.hp for e in enemies.values()),
                              speed_targets, {c: s.speed for c, s in stats.items()})
        assert result.total_damage > 0
        assert report.score == result.total_damage
        assert len(report.constraints) >= 4
        # 迷迷在行动队列中行动过
        mem_acts = [a for a in result.actions if a.unit_id == "MEM"]
        assert len(mem_acts) > 0
        # 各角色都有行动
        for cid in ("1015", "1306", "1309", "8007"):
            assert result.action_count.get(cid, 0) > 0, cid
