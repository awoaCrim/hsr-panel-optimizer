"""等级类星魂（E3/E5：技能等级+N）接入测试。

等级表：战技/大招/天赋 L1-L15、普攻 L1-L10（StarRailRes params）；基础等级 = 10
（满级）。红A E5 配置：E3 战技+2（≤15）、普攻+1（≤10）；E5 大招+2、天赋+2。
校验保护：L10 与当前 mult 不一致（参数位非倍率）静默跳过，不猜测。
"""
import pytest

from hsr_sim.loader import DATA_DIR, load_character


def _load(cid, eidolon):
    from hsr_sim.build import resolve_equipment
    from hsr_sim.data.loader import load_equipment
    from hsr_sim.loader import _apply_rank_levels
    ch = load_character(DATA_DIR / "characters" / f"{cid}.json")
    eq = load_equipment()
    ch.equipment_effects = resolve_equipment(
        {"light_cone": "", "relic_sets": [], "eidolon": eidolon, "cid": cid}, eq)["effects"]
    _apply_rank_levels(ch)
    return ch


class TestArcherE5:
    def test_skill_ult_talent_levels(self):
        """E5：战技/大招/天赋 10 → 12 级（等级表 L12 值）。"""
        ch = _load("1015", 5)
        assert ch.skills["skill"].mult == pytest.approx(3.96)    # L12（L10=3.6）
        assert ch.skills["ult"].mult == pytest.approx(10.8)      # L12（L10=10.0）
        assert ch.skills["talent"].mult == pytest.approx(2.2)    # L12（L10=2.0）

    def test_basic_capped(self):
        """普攻 +1 ≤10：红A 数据表最高 L9（1.3）→ 保持（无 L10 表项）。"""
        ch = _load("1015", 5)
        assert ch.skills["basic"].mult == pytest.approx(1.3)

    def test_e2_keeps_l10(self):
        """对照：2 命（无 E3/E5）→ 技能保持 L10。"""
        ch = _load("1015", 2)
        assert ch.skills["skill"].mult == pytest.approx(3.6)
        assert ch.skills["ult"].mult == pytest.approx(10.0)

    def test_rank_levels_in_effects(self):
        ch = _load("1015", 5)
        types = [e["type"] for e in ch.equipment_effects]
        assert types.count("skill_level") == 4     # E3 战技+普攻、E5 大招+天赋
        assert "ult_dmg" in types                  # E4


class TestGuard:
    def test_mismatched_params_not_guessed(self):
        """校验保护：参数位不是倍率的技能（L10 ≠ 当前 mult）不应用等级。"""
        ch = _load("1306", 5)     # 花火 E5（等级映射存在：ult+2、basic+1）
        # 花火战技 L10 params[0]=0.24（暴伤）≠ mult=0 → 静默跳过
        assert ch.skills["skill"].mult == pytest.approx(0.0)
        # 花火大招 params[0]=2（SP 相关）≠ mult=0 → 跳过
        assert ch.skills["ult"].mult == pytest.approx(0.0)


class TestMemoLevel:
    def test_mem_skill_level(self):
        """8007 E5：忆灵技 +1 → 迷迷普攻 L7（0.396/0.99）、真伤 L7（0.30）。"""
        ch = _load("8007", 5)
        m = ch.talent_extra["memosprite"]
        assert m["basic_mult"] == pytest.approx(0.396)
        assert m["basic_aoe_mult"] == pytest.approx(0.99)
        assert m["support_true_dmg"] == pytest.approx(0.30)
        # 对照：1 命 → L6 基准不变
        ch1 = _load("8007", 1)
        m1 = ch1.talent_extra["memosprite"]
        assert m1["basic_mult"] == pytest.approx(0.36)


class TestUltKind:
    def test_ult_damage_kind(self):
        """ult 伤害 kind='ult'（专属乘区 E4/于夜色大招暴伤可识别）。"""
        from hsr_sim.engine.simulate import Simulator
        from hsr_sim.model import Enemy, Rotation, Stats
        ch = _load("1015", 5)
        stats = {"1015": Stats(atk=2000.0, speed=150.0, crit_rate=0.5, crit_dmg=1.0)}
        enemies = {"e": Enemy(id="e", name="E", element="Ice", hp=1e9, atk=1000,
                              defense=1100.0, speed=10.0, toughness=300.0,
                              weaknesses=["Quantum"])}
        sim = Simulator({"1015": ch}, stats, enemies, Rotation(), 400.0, seed=0)
        sim.energy["1015"] = sim.chars["1015"].skills["ult"].energy_cost
        sim._execute_ult("1015", sim.chars["1015"].skills["ult"])
        ev = sim.damage_events[-1]
        assert ev.kind == "ult"
        # E4 终结技伤害 +150% 生效（真实链路）
        m = sim._current_multipliers()
        m.dmg_bonus = 0.0
        bonus, _ = sim._equip_damage("1015", "ult", "ult", "e", sim._effective_stats("1015"))
        assert bonus == pytest.approx(1.50)
