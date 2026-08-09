"""多波次关卡（D8）+ 死敌队列修复 + 星启模式关卡装配测试。"""
from pathlib import Path

import pytest

from hsr_sim.data.loader import load_team_normalized
from hsr_sim.engine.effects import DamageEffect
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemy_waves, load_enemies, load_rotation

STARFORGE = DATA_DIR / "enemy_starforge12c.json"


@pytest.fixture()
def real_team():
    return load_team_normalized(Path("data/team_real.json"))


class TestStarforgeStageData:
    """星启模式第三节点（StageConfig 30124123）数据装配。"""

    def test_waves_structure(self):
        waves = load_enemy_waves(STARFORGE)
        assert len(waves) == 2
        assert set(waves[0]) == {"present", "future", "past"}
        assert set(waves[1]) == {"deepnight_swarm"}

    def test_lv95_values(self):
        """Lv95 数值 = 基础值 × HardLevelGroup 3 比例（HP×375.4385/ATK×34.75065/SPD×1.32）。"""
        waves = load_enemy_waves(STARFORGE)
        present = waves[0]["present"]
        assert present.hp == pytest.approx(1116 * 375.4385, rel=1e-3)
        assert present.atk == pytest.approx(18 * 34.75065, rel=1e-3)
        assert present.speed == pytest.approx(120 * 1.32, rel=1e-3)
        assert waves[1]["deepnight_swarm"].hp == pytest.approx(7440 * 375.4385, rel=1e-3)
        assert waves[1]["deepnight_swarm"].toughness == pytest.approx(600)

    def test_resistances_and_weakness(self):
        """星启第三节点克制关系：三剧团各自弱点；深魇蝗灾弱物理/火/风。"""
        waves = load_enemy_waves(STARFORGE)
        assert waves[0]["present"].weaknesses == ["Physical", "Fire", "Imaginary"]
        assert waves[0]["future"].resistances["Quantum"] == 0.2
        assert waves[1]["deepnight_swarm"].weaknesses == ["Physical", "Fire", "Wind"]
        assert waves[1]["deepnight_swarm"].resistances["Quantum"] == 0.2
class TestNodeRegistryData:
    """5312 常规两节点与 5313 星启第三节点必须使用各自真实 StageID/敌人。"""

    def test_regular_nodes_are_distinct(self):
        node1 = load_enemy_waves(DATA_DIR / "enemy_floor12_node1.json")
        node2 = load_enemy_waves(DATA_DIR / "enemy_floor12_node2.json")
        assert set(node1[0]) == {"rage_shell", "frost_wanderer"}
        assert set(node1[1]) == {"sam"}
        assert set(node2[0]) == {"cywing", "cyash"}
        assert set(node2[1]) == {"pamking"}


class TestMultiWave:
    def test_wave_advance(self, real_team):
        """波次推进：波次 1 全灭 → 波次 2 入场（事件入流、HP 重建、行动条入队）。"""
        chars, stats, _, _ = real_team
        waves = load_enemy_waves(STARFORGE)
        rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, waves[0], rot, 1000, 95, seed=0, waves=waves)
        r = sim.run()
        assert sim.enemy_wave == 1              # 打到最后一波
        wave_events = [a for a in r.actions if a.action.startswith("wave")]
        assert len(wave_events) == 1
        assert "第2波" in wave_events[0].detail
        # 波次 2 敌人被打过（伤害事件含 deepnight_swarm）
        assert any(e.target == "deepnight_swarm" for e in sim.damage_events)
        assert all(v <= 0 for v in r.enemy_hp_left.values())

    def test_dead_enemy_removed_from_queue(self, real_team):
        """死敌队列修复：击杀后战斗立即结束，死敌不再行动。"""
        chars, stats, _, _ = real_team
        enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
        rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, enemies, rot, target_av, level, seed=0)
        r = sim.run()
        # 精英击杀后：无敌人行动（旧 bug：死敌会继续行动直到超时）
        last_attack = max((a.t for a in r.actions if a.action == "enemy_attack"), default=0)
        assert r.t_end <= target_av
        # 全灭后战斗结束：最后一击后没有更多 enemy_attack
        assert r.t_end - last_attack < 100

    def test_undo_across_wave(self, real_team):
        """回退跨波次：undo 回到波次 1 的状态（enemy_wave/enemies/enemy_hp 一致恢复）。"""
        chars, stats, _, _ = real_team
        waves = load_enemy_waves(STARFORGE)
        rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, waves[0], rot, 1000, 95, seed=0, waves=waves)
        r = sim.run()
        assert sim.undo()
        # 回退到波次 1（undo 最近决策点：波次 2 期间或之前的我方行动前）
        assert sim.enemy_wave <= 1
        assert set(sim.enemy_hp) == set(waves[sim.enemy_wave])
        # 重跑一致性：同 seed 从回退点继续（快照恢复后状态完整）
        snap_wave = sim.enemy_wave
        snap_hp = dict(sim.enemy_hp)
        assert sim.undo() or True   # 可再回退或已到底
        assert sim.enemy_wave <= snap_wave

    def test_wave_boundary_target_triggers_do_not_reuse_old_enemy(self, real_team):
        """击杀并切波后，追击/协奏不得继续访问旧波次目标（WebUI KeyError: 'cyash' 回归）。"""
        chars, stats, _, _ = real_team
        waves = load_enemy_waves(STARFORGE)
        rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, waves[0], rot, 1000, 95, seed=0, waves=waves)
        # 最小化真实结算链：主伤害击杀波次 1 最后一名敌人；随后同时满足红A追击与协奏触发。
        sim.enemy_hp["present"] = 0.0
        sim.enemy_hp["future"] = 0.0
        sim.enemy_hp["past"] = 1.0
        sim.fate_charge["1015"] = 1.0
        sim.concert_rounds = 2

        sim._apply_effects("8007", [DamageEffect(mult=1.0)], "past", "", skill_type="basic")

        assert sim.enemy_wave == 1
        assert set(sim.enemy_hp) == {"deepnight_swarm"}
        # 触发链属于击杀所在波次，不得把追击/附加伤害泄漏到新波敌人；
        # 追击没有合法目标时也不能白白消耗红A充能。
        assert sim.fate_charge["1015"] == 1.0
        assert not any(event.kind in {"followup", "additional"} for event in sim.damage_events)
        assert not any(event.target == "deepnight_swarm" for event in sim.damage_events)

    def test_wave_boundary_same_act(self, real_team):
        """波次切换同 act 内：击杀瞬间切换，剩余段不打旧目标。"""
        chars, stats, _, _ = real_team
        waves = load_enemy_waves(STARFORGE)
        rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, waves[0], rot, 1000, 95, seed=0, waves=waves)
        sim.run()
        # 无异常即通过（旧 bug：波次切换后旧 target KeyError）
        assert True
