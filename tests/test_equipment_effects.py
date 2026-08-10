"""装备效果（光锥被动/遗器套装 exec DSL）模拟器接入测试。

场景用单角色/双角色可控队伍（直接构造 Simulator，不依赖 team 文件）。
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Action, CharacterData, Enemy, Rotation, Stats


def _mk(char_ids, equip_map, seed=0, target_av=400.0):
    """构造队伍：equip_map = {cid: {"light_cone": id, "relic_sets": [...]}}"""
    from hsr_sim.build import resolve_equipment
    from hsr_sim.data.loader import load_equipment
    eq = load_equipment()
    chars = {}
    stats = {}
    for cid in char_ids:
        ch = load_character(DATA_DIR / "characters" / f"{cid}.json")
        cfg = equip_map.get(cid, {})
        ch.equipment_effects = resolve_equipment(
            {"light_cone": cfg.get("light_cone", ""), "relic_sets": cfg.get("relic_sets", [])},
            eq)["effects"]
        chars[cid] = ch
        stats[cid] = Stats(atk=2000.0, speed=150.0, crit_rate=0.5, crit_dmg=1.0)
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Ice", hp=1e9, atk=1000,
        defense=1100.0, speed=10.0, toughness=300.0, weaknesses=["Quantum"])}
    return Simulator(chars, stats, enemies, Rotation(), target_av=target_av, seed=seed)


def _act(sim, skill="basic"):
    sim.external_action = Action(unit_id=next(iter(sim.queue.next()[0] if False else sim.chars)),
                                 action=skill)
    return sim.run_step()


class TestPanelStats:
    def test_stat_effects_enter_panel(self):
        """stat 类（光锥暴击/充能/套装攻击）进面板。"""
        from hsr_sim.build import assemble, BuildConfig
        from hsr_sim.data.loader import load_equipment
        eq = load_equipment()
        base = Stats(atk=621.0, speed=105.0, crit_rate=0.05, crit_dmg=0.5, energy_regen=1.0)
        cfg = BuildConfig(light_cone="23001", relic_sets=["306"],
                          main_stats={"body": "atk_pct", "feet": "atk_pct",
                                      "sphere": "atk_pct", "rope": "atk_pct"})
        s = assemble(base, "Quantum", cfg, eq)
        # 光锥暴击 18% + 萨尔索图 2 件暴击 8%
        assert s.crit_rate == pytest.approx(0.05 + 0.18 + 0.08)


class TestConditionalDamage:
    def test_speed_over_100_stacks(self):
        """于夜色中：超速每 10 点 → 普攻战技 +6%（150 速 = 5 层 = +30%）。"""
        sim = _mk(["1015"], {"1015": {"light_cone": "23001"}})
        sim.stats["1015"].speed = 150.0
        n0 = len(sim.damage_events)
        sim.run_step()  # 1015 行动（决策点，无 external → policy/序列空 → basic）
        ev = sim.damage_events[n0]
        m = sim._current_multipliers()
        m.dmg_bonus = 0.0
        # 直接验证 _equip_damage 的增伤贡献
        bonus, _ = sim._equip_damage("1015", "normal", "basic", "elite",
                                     sim._effective_stats("1015"))
        assert bonus == pytest.approx(5 * 0.06)

    def test_def_ignore_quantum_weakness(self):
        """量子套：无视防御 10% + 量子弱点额外 10%。"""
        sim = _mk(["1015"], {"1015": {"relic_sets": [{"id": "108", "pieces": 4}]}})
        bonus, di = sim._equip_damage("1015", "normal", "basic", "elite", sim._effective_stats("1015"))
        assert di == pytest.approx(0.20)      # 10% + 弱点额外 10%
        assert bonus == pytest.approx(0.10)   # 量子伤 2 件
        # 伤害公式确实吃 def_ignore（防御乘区提升）
        from hsr_sim.engine.damage import defense_multiplier
        assert defense_multiplier(80, 1100.0, 0.20) > defense_multiplier(80, 1100.0, 0.0)

    def test_crit_ge_ult_dmg(self):
        """萨尔索图 4 件：暴击≥50% 时大招/追击 +15%。"""
        sim = _mk(["1015"], {"1015": {"relic_sets": [{"id": "306", "pieces": 4}]}})
        b_ult, _ = sim._equip_damage("1015", "ult", "ult", "elite", sim._effective_stats("1015"))
        b_norm, _ = sim._equip_damage("1015", "normal", "basic", "elite", sim._effective_stats("1015"))
        assert b_ult == pytest.approx(0.15)
        assert b_norm == pytest.approx(0.0)


class TestStacksAndBuffs:
    def test_stack_energy_regen(self):
        """夜色流光溢彩：我方每次攻击 → 装备者【歌咏】+1 层（≤5），充能效率 +3%/层。"""
        sim = _mk(["1309", "1015"], {"1309": {"light_cone": "23026"}})
        sim.run_step()  # 1309 行动（先行动者，决策点 basic）
        assert sim.equip_stacks.get("1309", {}).get("lc:23026", 0) == 1
        assert sim._energy_regen("1309") == pytest.approx(1.0 + 0.03)
        # 连续攻击叠到上限
        for _ in range(6):
            sim.run_step()
        assert sim.equip_stacks["1309"]["lc:23026"] == 5

    def test_ult_convert(self):
        """大招：移除歌咏 → 华彩（攻击 +48% 1 回合 + 全队增伤 24% 1 回合）。"""
        sim = _mk(["1309"], {"1309": {"light_cone": "23026"}})
        sim.equip_stacks.setdefault("1309", {})["lc:23026"] = 3
        sim.energy["1309"] = sim.chars["1309"].skills["ult"].energy_cost
        sim._execute_ult("1309", sim.chars["1309"].skills["ult"])
        assert "lc:23026" not in sim.equip_stacks["1309"]     # 歌咏清零
        assert sim.buffs.sum_for("atk_pct", "1309") == pytest.approx(0.48)
        assert sim.buffs.sum_for("dmg_bonus") == pytest.approx(0.24)

    def test_ult_sp_refund(self):
        """但战斗还未结束：每 2 次大招回 1 SP（用无 SP 返还的红A 测试，隔离光锥效果）。"""
        sim = _mk(["1015"], {"1015": {"light_cone": "23003"}})
        sim.sp = 1.0
        sim.energy["1015"] = sim.chars["1015"].skills["ult"].energy_cost
        sim._execute_ult("1015", sim.chars["1015"].skills["ult"])
        assert sim.sp == pytest.approx(1.0)     # 第 1 次不触发
        sim.energy["1015"] = sim.chars["1015"].skills["ult"].energy_cost
        sim._execute_ult("1015", sim.chars["1015"].skills["ult"])
        assert sim.sp == pytest.approx(2.0)     # 第 2 次 +1

    def test_skill_next_ally_dmg(self):
        """战技后：下一个行动的队友增伤 30%（buff 存在期间其他角色攻击都吃——近似）。"""
        sim = _mk(["1306", "1015"], {"1306": {"light_cone": "23003"}})
        sim._after_skill_equipment("1306", "skill", "")
        bonus, _ = sim._equip_damage("1015", "normal", "basic", "elite", sim._effective_stats("1015"))
        assert bonus == pytest.approx(0.30)
        # 装备者自己不触发（"其他目标"）
        bonus_self, _ = sim._equip_damage("1306", "normal", "skill", "elite", sim._effective_stats("1306"))
        assert bonus_self == pytest.approx(0.0)

    def test_skill_team_dmg(self):
        """记忆永不落幕：战技后全队增伤 8% 3 回合。"""
        sim = _mk(["8007"], {"8007": {"light_cone": "24005"}})
        sim._after_skill_equipment("8007", "skill", "")
        assert sim.buffs.sum_for("dmg_bonus") == pytest.approx(0.08)

    def test_mem_cd_buff(self):
        """凯歌 4 件：忆灵攻击时装备者暴伤 +30% 2 回合。"""
        sim = _mk(["8007"], {"8007": {"relic_sets": [{"id": "123", "pieces": 4}]}})
        sim._ensure_memosprite_summon()
        sim._memosprite_act()
        assert sim.buffs.sum_for("crit_dmg", "8007") == pytest.approx(0.30)

    def test_stat_conditional_memosprite(self):
        """蕉乐园：迷迷在场时装备者暴伤额外 +32%。"""
        sim = _mk(["8007"], {"8007": {"relic_sets": [{"id": "318", "pieces": 2}]}})
        sim._ensure_memosprite_summon()
        s = sim._effective_stats("8007")
        assert s.crit_dmg == pytest.approx(1.0 + 0.32)

    def test_target_cd_buff(self):
        """司铎 4 件：对单体目标施放战技 → 目标暴伤 +18%（可叠 2 层）。"""
        sim = _mk(["1306", "1015"], {"1306": {"relic_sets": [{"id": "121", "pieces": 4}]}})
        sim._after_skill_equipment("1306", "skill", "1015")
        assert sim.buffs.sum_for("crit_dmg", "1015") == pytest.approx(0.18)
        sim._after_skill_equipment("1306", "skill", "1015")
        assert sim.buffs.sum_for("crit_dmg", "1015") == pytest.approx(0.36)  # 2 层上限


    def test_current_multipliers_and_panel_do_not_double_count_damage_bonus(self):
        """动态增伤进入有效面板后，伤害乘区不得再重复加一次。"""
        from hsr_sim.engine.damage import noncrit_damage
        sim = _mk(["1015"], {})
        sim.buffs.add("dmg_bonus", 0.5, "1015", 2, target="1015")
        sim.stats["1015"].crit_rate = 0.0
        stats = sim._effective_stats("1015")
        multipliers = sim._current_multipliers("1015")
        assert stats.dmg_bonus == pytest.approx(sim.stats["1015"].dmg_bonus + 0.5)
        assert multipliers.dmg_bonus == pytest.approx(0.0)
        expected = noncrit_damage(
            1.0, stats.atk, stats, multipliers,
            sim.enemies["elite"].defense,
            sim.enemies["elite"].resistances.get("Quantum", 0.0),
            sim.attacker_level,
        )
        sim._deal_damage("1015", "elite", 1.0)
        assert sim.damage_events[-1].amount == pytest.approx(expected)

    def test_start_advance_vonwacq(self):
        """翁瓦克 4 件：速度≥120 开局行动提前 40%。"""
        sim = _mk(["1306"], {"1306": {"relic_sets": [{"id": "308", "pieces": 4}]}})
        av0 = sim.queue.snapshot()["1306"]
        sim._apply_start_effects()
        assert sim.queue.snapshot()["1306"] == pytest.approx(av0 * 0.6)


class TestNewLightCones:
    """候选光锥（装备搜索新增映射）：星海巡航 / 如泥酣眠 / 论剑。"""

    def test_data_mapped(self):
        """数据层：3 个候选光锥 effect.exec 已入档。"""
        from hsr_sim.data.loader import load_equipment
        eq = load_equipment()
        for lid, n in (("24001", "星海巡航"), ("23012", "如泥酣眠"), ("21010", "论剑")):
            e = (eq["light_cones"][lid].get("effect") or {})
            v = e.get("value", e) if isinstance(e, dict) else e
            assert v.get("exec"), f"{n} 无 exec"

    def test_hit_stack_dmg(self):
        """论剑：同目标连续命中叠层 8%/层（5 层）；换目标清零。"""
        sim = _mk(["1015"], {"1015": {"light_cone": "21010"}})
        sim.external_action = Action(unit_id="1015", action="basic", target="elite")
        sim.run_step()
        stacks = sim.equip_stacks["1015"].get("lc:21010", 0)
        assert stacks == 0    # 第一击无加成（命中后叠 1 层）
        sim.external_action = Action(unit_id="1015", action="basic", target="elite")
        sim.run_step()
        bonus, _ = sim._equip_damage("1015", "normal", "basic", "elite", sim._effective_stats("1015"))
        assert bonus == pytest.approx(1 * 0.08)   # 第 2 击后 1 层
        sim.external_action = Action(unit_id="1015", action="basic", target="elite")
        sim.run_step()
        assert sim.equip_stacks["1015"].get("lc:21010", 0) == 2   # 第 3 击后 2 层
        # 换目标清零（新增目标）
        sim.enemies["elite2"] = Enemy(id="elite2", name="B", element="Ice", hp=1e9,
                                      atk=1000, defense=1100.0, speed=10.0,
                                      toughness=300.0, weaknesses=["Quantum"])
        sim.toughness["elite2"] = 300.0
        sim.enemy_hp["elite2"] = 1e9
        sim.external_action = Action(unit_id="1015", action="basic", target="elite2")
        sim.run_step()
        assert sim.equip_stacks["1015"].get("lc:21010", 0) == 0

    def test_hp_le_crit(self):
        """星海巡航：目标 HP≤50% → 暴击率 +8%（段级下用多 seed 均值验证）。"""
        def mean_dmg(hp_ratio, seed0):
            tot = 0.0
            for s in range(seed0, seed0 + 30):
                sim = _mk(["1015"], {"1015": {"light_cone": "24001"}}, seed=s)
                sim.enemy_hp["elite"] = hp_ratio * 1e9
                sim.external_action = Action(unit_id="1015", action="basic", target="elite")
                sim.run_step()
                tot += sim.damage_events[-1].amount
            return tot / 30.0
        d_hi = mean_dmg(0.4, 0)     # 40% HP（条件触发：暴击率 0.5+0.08+0.08）
        d_lo = mean_dmg(0.9, 100)   # 90% HP（不触发：暴击率 0.5+0.08）
        assert d_hi > d_lo * 1.005

    def test_on_kill_atk(self):
        """星海巡航：击杀后攻击 +20% 2 回合。"""
        sim = _mk(["1015"], {"1015": {"light_cone": "24001"}})
        sim.enemy_hp["elite"] = 100.0
        sim.external_action = Action(unit_id="1015", action="basic", target="elite")
        sim.run_step()
        assert sim.buffs.sum_for("atk_pct", "1015") == pytest.approx(0.20)

    def test_no_crit_crit(self):
        """如泥酣眠（段级精确）：crit_rate=0 → 首段必不暴击 → 触发暴击 buff + CD 3。"""
        sim = _mk(["1015"], {"1015": {"light_cone": "23012"}})
        sim.stats["1015"] = Stats(atk=2000.0, speed=150.0, crit_rate=0.0, crit_dmg=1.0)
        sim.external_action = Action(unit_id="1015", action="basic", target="elite")
        sim.run_step()
        assert sim.buffs.sum_for("crit_rate", "1015") == pytest.approx(0.36)   # 已触发
        assert sim.equip_stacks["1015"].get("lc:23012", 0) == 2                # CD 3（触发回合已递减 1）
        noncrit = 1.3 * 2000.0 * (1000.0 / 2100.0) * 0.9
        assert sim.damage_events[-1].amount == pytest.approx(noncrit, rel=1e-9)
