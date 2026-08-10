"""Correctness regression tests for buff duration across extra-action chains.

These tests intentionally sit outside T2a legacy parity: the frozen v1.5 baseline
contains the old bug where every extra action consumed one buff turn.
"""
import pytest

from hsr_sim.data.loader import load_team_normalized
from hsr_sim.engine.simulate import Simulator
from hsr_sim.model import Action
from hsr_sim.loader import DATA_DIR, load_enemies, load_rotation, load_team


def _first_reda_chain_damage(sim: Simulator) -> list[float]:
    sim.run()
    events = [
        event.amount for event in sim.damage_events
        if event.source == "1015" and event.kind == "normal"
    ]
    return events[:5]


def test_legacy_and_normalized_agree_after_extra_action_duration_fix():
    legacy_chars, legacy_stats, _ = load_team(
        DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    normalized_chars, normalized_stats, _, _ = load_team_normalized(
        DATA_DIR / "team_reda.json")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")

    legacy = Simulator(
        legacy_chars, legacy_stats, enemies,
        load_rotation(DATA_DIR / "rotation.json"), target_av, level)
    normalized = Simulator(
        normalized_chars, normalized_stats, enemies,
        load_rotation(DATA_DIR / "rotation.json"), target_av, level)

    legacy_chain = _first_reda_chain_damage(legacy)
    normalized_chain = _first_reda_chain_damage(normalized)
    assert len(legacy_chain) == len(normalized_chain) == 5
    for actual, expected in zip(normalized_chain, legacy_chain):
        assert actual == pytest.approx(expected, rel=0.001)


def test_extra_action_chain_retains_buffed_damage_until_turn_ends():
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    sim = Simulator(
        chars, stats, enemies,
        load_rotation(DATA_DIR / "rotation.json"), target_av, level)

    chain = _first_reda_chain_damage(sim)
    assert len(chain) == 5
    # All five skills are the same action in one turn. Their damage may change
    # due to skill-triggered stacking effects, but must not drop because a
    # duration buff expired between extra actions.
    assert min(chain[1:]) >= chain[0]


def test_buff_gained_during_extra_action_chain_is_not_consumed_at_chain_end():
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    sim = Simulator(
        chars, stats, enemies,
        load_rotation(DATA_DIR / "rotation.json"), target_av, level)
    sim.sp = 9.0

    sim._character_act("1015")
    assert "1015" in sim.burst_chain
    sim.buffs.add("crit_dmg", 0.4, "1015", 1, target="1015")

    # 结束剩余额外行动链；链尾不能把链内刚获得的 1 回合 buff 清掉。
    for _ in range(4):
        sim._character_act("1015")
    buff = sim.buffs.get("crit_dmg", "1015")
    assert buff is not None
    assert buff.duration == 1

    sim.rotation.actions["1015"] = [Action(unit_id="1015", action="basic")]
    sim._character_act("1015")
    assert sim.buffs.get("crit_dmg", "1015") is None
