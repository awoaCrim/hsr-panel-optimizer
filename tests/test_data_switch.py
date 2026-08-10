"""数据层切换测试（P0-3 Step B）—— normalized 直驱模拟器 + 信任度信封。

- 结构等价：normalized 构建的 CharacterData 与 legacy 手填等价（ETL 正确性）
- 运行等价：normalized 路径 2T 结果与 legacy 当前实现一致（历史 golden 仅锁行动与时序）
- 信任度信封：D/raw 输入 → trust_level=unverified；纯 legacy 无信封 → trusted
"""
import json
from pathlib import Path

import pytest

from hsr_sim.data.loader import (
    NORMALIZED_DIR,
    load_characters_normalized,
    load_enemies_normalized,
    load_team_normalized,
)
from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character, load_enemies, load_rotation, load_team

GOLDEN = Path(__file__).parent / "golden" / "reda_v1.5_2t.json"
TEAM = DATA_DIR / "team_reda.json"
ROTATION = DATA_DIR / "rotation.json"


class TestStructureEquivalence:
    def test_characters_match_legacy(self):
        """normalized 构建的角色与 legacy 手填：技能数值相等、机制钩子相等、面板近等。"""
        norm_chars, _ = load_characters_normalized()
        legacy_chars = {cid: load_character(DATA_DIR / "characters" / f"{cid}.json")
                        for cid in ("1015", "1306", "1309", "8007")}
        for cid, lc in legacy_chars.items():
            nc = norm_chars[cid]
            # 技能数值（mult/sp/energy/toughness）相等
            for slot, ls in lc.skills.items():
                ns = nc.skills[slot]
                for f in ("mult", "sp", "energy", "energy_cost", "toughness"):
                    if cid == "8007" and slot == "ult" and f == "toughness":
                        # 已知差异（P0-1 交叉核对报告）：手填 0 遗漏，解包 StanceDamageDisplay=20，
                        # normalized 采用解包值（A/mapped，更可信）
                        assert getattr(ns, f) == 20.0
                        continue
                    assert getattr(ns, f) == getattr(ls, f), f"{cid}.{slot}.{f}"
            # 天赋钩子机制（mechanic → skill_effects）相等
            for slot, lmech in lc.talent_extra.get("skill_effects", {}).items():
                assert nc.talent_extra["skill_effects"][slot] == lmech, f"{cid}.{slot}.mechanic"
            # 角色级机制相等
            for k, v in lc.talent_extra.items():
                if k != "skill_effects":
                    assert nc.talent_extra.get(k) == v, f"{cid}.talent_extra.{k}"
            # 基础面板近等（legacy 1 位小数 vs 上游 3 位小数）
            for stat in ("hp", "atk", "defense", "speed"):
                a, b = getattr(lc.base_stats, stat), getattr(nc.base_stats, stat)
                if a:
                    assert abs(a - b) / a < 0.001, f"{cid}.base_stats.{stat}: {a} vs {b}"


class TestRunParity:
    def test_normalized_run_matches_legacy_current_rules(self):
        """normalized 与 legacy 在当前已修正规则下的动作、时序和总伤害一致。"""
        legacy_chars, legacy_stats, _ = load_team(TEAM, DATA_DIR / "characters")
        normalized_chars, normalized_stats, _, _ = load_team_normalized(TEAM)
        enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
        rot_legacy = load_rotation(ROTATION)
        rot_normalized = load_rotation(ROTATION)
        legacy = Simulator(legacy_chars, legacy_stats, enemies, rot_legacy, target_av, level).run()
        normalized = Simulator(
            normalized_chars, normalized_stats, enemies, rot_normalized, target_av, level).run()

        assert [(a.unit_id, a.action) for a in normalized.actions] == [
            (a.unit_id, a.action) for a in legacy.actions]
        assert normalized.total_damage == pytest.approx(legacy.total_damage, rel=0.001)
        assert normalized.t_end == pytest.approx(legacy.t_end, rel=0.001)


class TestTrustEnvelope:
    def test_normalized_path_is_unverified(self):
        """normalized 输入含 D/raw（敌人模板/默认值/忆灵手填）→ 结果标注未验证。"""
        chars, stats, _, unv = load_team_normalized(TEAM)
        enemies, level, target_av, unv2 = load_enemies_normalized()
        rot = load_rotation(ROTATION)
        sim = Simulator(chars, stats, enemies, rot, target_av, level,
                        unverified_inputs=unv + unv2)
        r = sim.run()
        assert r.trust_level == "unverified"
        # 8 角色默认值 + 34 敌人字段（22 模板 + 12 敌人技能 D 级手填）
        assert len(r.unverified_inputs) == len(unv) + len(unv2) == 42
        assert any(p.startswith("enemies.") for p in r.unverified_inputs)
        assert any("break_effect" in p for p in r.unverified_inputs)

    def test_legacy_path_no_envelope_is_trusted(self):
        """legacy 路径（不传 unverified）→ 默认 trusted（信封未启用）。"""
        chars, stats, _ = load_team(TEAM, DATA_DIR / "characters")
        enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
        rot = load_rotation(ROTATION)
        sim = Simulator(chars, stats, enemies, rot, target_av, level)
        r = sim.run()
        assert r.trust_level == "trusted"
        assert r.unverified_inputs == []

    def test_audit_and_loader_agree_on_count(self):
        """audit 未验证数与 loader 信封一致（门禁与信封同源）。"""
        from hsr_sim.data.audit import audit
        _, unverified = audit()
        assert len(unverified) == 42
