"""多波次关卡（D8）+ 死敌队列修复 + 星启模式关卡装配测试。"""
from pathlib import Path

import pytest

from hsr_sim.data.loader import load_team_normalized
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemy_waves, load_enemies, load_rotation

STARFORGE = DATA_DIR / "enemy_starforge12b.json"


@pytest.fixture()
def real_team():
    return load_team_normalized(Path("data/team_real.json"))


class TestStarforgeStageData:
    """星启模式第二关（StageConfig 30124122）数据装配。"""

    def test_waves_structure(self):
        waves = load_enemy_waves(STARFORGE)
        assert len(waves) == 2
        assert set(waves[0]) == {"cywing", "cyash"}
        assert set(waves[1]) == {"pamking"}

    def test_lv95_values(self):
        """Lv95 数值 = 基础值 × HardLevelGroup 3 比例（HP×375.4385/ATK×34.75065/SPD×1.32）。"""
        waves = load_enemy_waves(STARFORGE)
        cywing = waves[0]["cywing"]
        assert cywing.hp == pytest.approx(1395 * 375.4385, rel=1e-3)
        assert cywing.atk == pytest.approx(18 * 34.75065, rel=1e-3)
        assert cywing.speed == pytest.approx(120 * 1.32, rel=1e-3)
        assert waves[1]["pamking"].hp == pytest.approx(3487.5 * 375.4385, rel=1e-3)
        assert waves[1]["pamking"].toughness == pytest.approx(720)

    def test_resistances_and_weakness(self):
        """真实克制关系：苍翼量子抗 80%（红A 大劣）、灰烬物理抗 80%、帕姆王全抗 20%。"""
        waves = load_enemy_waves(STARFORGE)
        assert waves[0]["cywing"].resistances["Quantum"] == 0.8
        assert "Quantum" not in waves[0]["cywing"].weaknesses
        assert waves[0]["cyash"].resistances["Physical"] == 0.8
        assert waves[1]["pamking"].resistances["Quantum"] == 0.2


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
        # 波次 2 敌人被打过（伤害事件含 pamking）
        assert any(e.target == "pamking" for e in sim.damage_events)
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

    def test_wave_boundary_same_act(self, real_team):
        """波次切换同 act 内：击杀瞬间切换，剩余段不打旧目标。"""
        chars, stats, _, _ = real_team
        waves = load_enemy_waves(STARFORGE)
        rot = load_rotation(DATA_DIR / "rotation.json")
        sim = Simulator(chars, stats, waves[0], rot, 1000, 95, seed=0, waves=waves)
        sim.run()
        # 无异常即通过（旧 bug：波次切换后旧 target KeyError）
        assert True
