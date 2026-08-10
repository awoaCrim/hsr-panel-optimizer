"""战斗内 buff/debuff 与动态面板投影测试。"""
import pytest

from hsr_sim.engine.buffs import BuffManager
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Enemy, Rotation, Stats
from hsr_sim.rehearse import RehearsalSession


def _session() -> RehearsalSession:
    chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json")}
    stats = {"1015": Stats(
        hp=3000.0, atk=2000.0, defense=1000.0, speed=100.0,
        crit_rate=0.5, crit_dmg=1.0, break_effect=0.2,
        energy_regen=1.0, dmg_bonus=0.1,
    )}
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Ice", hp=1e8, atk=1000.0,
        defense=1100.0, speed=10.0, toughness=300.0,
        weaknesses=["Quantum"], resistances={"Quantum": 0.2},
    )}
    return RehearsalSession(Simulator(chars, stats, enemies, Rotation(), target_av=400.0))


def test_buff_manager_target_projection():
    buffs = BuffManager()
    buffs.add("dmg_bonus", 0.2, "a", 2)
    buffs.add("crit_dmg", 0.3, "b", 1, target="c")
    assert [b.stat for b in buffs.for_target("c")] == ["dmg_bonus", "crit_dmg"]
    assert [b.stat for b in buffs.for_target("other")] == ["dmg_bonus"]


def test_effective_panel_applies_buffs_and_debuffs():
    s = _session()
    sim = s.sim
    sim.buffs.add("atk_pct", 0.2, "1015", 2, target="1015")
    sim.buffs.add("atk_flat", 100.0, "1015", 2, target="1015")
    sim.buffs.add("crit_rate", 0.1, "1015", 2, target="1015")
    sim.buffs.add("crit_dmg", -0.2, "enemy", 2, target="1015")
    sim.buffs.add("dmg_bonus", 0.5, "1015", 2)
    sim.queue._entries["1015"].speed = 112.0
    sim.buffs.add("def_pct", -0.1, "enemy", 2, target="1015")
    sim.buffs.add("break_effect", 0.3, "1015", 2, target="1015")
    sim.buffs.add("energy_regen", 0.2, "1015", 2, target="1015")

    panel = s.observe()["panels"]["characters"]["1015"]
    effective = panel["effective"]
    assert effective["atk"] == pytest.approx(2500.0)
    assert effective["crit_rate"] == pytest.approx(0.6)
    assert effective["crit_dmg"] == pytest.approx(0.8)
    assert effective["dmg_bonus"] == pytest.approx(0.6)
    assert effective["speed"] == pytest.approx(112.0)
    assert effective["defense"] == pytest.approx(900.0)
    assert effective["break_effect"] == pytest.approx(0.5)
    assert effective["energy_regen"] == pytest.approx(1.2)
    assert any(x["stat"] == "crit_dmg" for x in panel["debuffs"])
    assert any(x["stat"] == "dmg_bonus" for x in panel["buffs"])


def test_dynamic_panel_matches_damage_multiplier_source():
    s = _session()
    sim = s.sim
    sim.buffs.add("dmg_bonus", 0.5, "1015", 2, target="1015")
    sim.sp_spent_count = 2
    panel = s.observe()["panels"]["characters"]["1015"]
    assert panel["effective"]["dmg_bonus"] == pytest.approx(
        sim.stats["1015"].dmg_bonus + sim._dynamic_dmg_bonus("1015"))


def test_buff_expiry_removes_panel_effect():
    s = _session()
    sim = s.sim
    sim.buffs.add("crit_dmg", 0.4, "1015", 1, target="1015")
    before = s.observe()["panels"]["characters"]["1015"]
    assert before["effective"]["crit_dmg"] == pytest.approx(1.4)
    sim.buffs.tick_owner("1015")
    after = s.observe()["panels"]["characters"]["1015"]
    assert after["effective"]["crit_dmg"] == pytest.approx(1.0)
    assert after["buffs"] == []


def test_conditional_next_ally_damage_enters_recipient_panel_only():
    from hsr_sim.data.loader import load_team_normalized
    from hsr_sim.loader import load_enemies
    chars, stats, _, _ = load_team_normalized(DATA_DIR / "team_real.json")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    sim = Simulator(chars, stats, enemies, Rotation(), target_av, level)
    session = RehearsalSession(sim)
    sim.buffs.add("equip_next_ally_dmg", 0.3, "1306", 1)
    panels = session.observe()["panels"]["characters"]
    assert panels["1015"]["effective"]["dmg_bonus"] == pytest.approx(
        sim._effective_stats("1015").dmg_bonus + 0.3)
    assert panels["1306"]["effective"]["dmg_bonus"] == pytest.approx(
        sim._effective_stats("1306").dmg_bonus)
    assert not any(x["stat"] == "equip_next_ally_dmg" for x in panels["1306"]["buffs"])


def test_enemy_debuff_projection():
    s = _session()
    s.sim.buffs.add("enemy_res_pen:Quantum", 0.2, "1015", 2, target="elite")
    effects = s.observe()["panels"]["enemies"]["elite"]
    assert effects["buffs"] == []
    assert effects["debuffs"][0]["label"] == "Quantum抗性降低"
