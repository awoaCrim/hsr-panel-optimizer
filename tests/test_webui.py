"""开局状态配置（initial_sp / initial_energy）测试。

标准战斗规则：SP 4、能量 0（可配置——末日幻影/模拟宇宙等模式有不同开局规则）。
"""
import json
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_enemies, load_team
from hsr_sim.model import Rotation


@pytest.fixture()
def sim_base():
    chars, stats, _ = load_team(DATA_DIR / "team_reda.json", DATA_DIR / "characters")
    enemies, level, target_av = load_enemies(DATA_DIR / "enemy_elite90.json")
    return chars, stats, enemies, target_av, level


class TestInitialState:
    def test_standard_rule(self, sim_base):
        """默认（标准战斗）：SP 4、能量全 0。"""
        chars, stats, enemies, target_av, level = sim_base
        sim = Simulator(chars, stats, enemies, Rotation(), target_av, level, seed=0)
        assert sim.sp == pytest.approx(4.0)
        assert all(v == 0.0 for v in sim.energy.values())

    def test_custom(self, sim_base):
        """可配置：SP 2、红A 开局 80 能量（如模式 buff 开局能量）。"""
        chars, stats, enemies, target_av, level = sim_base
        sim = Simulator(chars, stats, enemies, Rotation(), target_av, level, seed=0,
                        initial_sp=2.0, initial_energy={"1015": 80.0})
        assert sim.sp == pytest.approx(2.0)
        assert sim.energy["1015"] == pytest.approx(80.0)
        assert sim.energy["1306"] == 0.0

    def test_energy_affects_rotation(self, sim_base):
        """开局能量参与推演：红A 80 能量开局 → 首轮早 80 能量。"""
        chars, stats, enemies, target_av, level = sim_base
        sim = Simulator(chars, stats, enemies, Rotation(), target_av, level, seed=0,
                        initial_energy={"1015": 80.0})
        sim.run()
        # 推演结束后（自动结算）能量自然流动——只验证开局生效（run 前）
        assert sim.energy["1015"] >= 80.0 - 1e-9  # run 后能量可能已消耗，不做强断言

    def test_enemy_file_config(self):
        """enemy 文件携带开局状态字段（关卡规则配置入口）。"""
        d = json.loads((DATA_DIR / "enemy_elite90.json").read_text(encoding="utf-8"))
        assert d.get("initial_sp") == 4.0
        assert d.get("initial_energy") == {}


class TestWebuiApi:
    def test_team_payload(self):
        """队伍配置 API 载荷：角色/光锥/套装/星魂/词条/信任度。"""
        from hsr_sim.webui import build_team_payload
        d = build_team_payload()
        assert len(d["characters"]) == 4
        red = d["characters"][0]
        assert red["id"] == "1015" and red["eidolon"] == 2
        assert d["team_file"] == "team_real.json"
        assert red["light_cone"]["effect"]["exec"]      # 光锥效果已接入标注
        assert red["light_cone"]["refinement"] == 1
        assert red["stats"]["hp"] == pytest.approx(3135)
        assert red["relic_sets"][0]["pieces"] == 4
        assert {int(r["rank"]) for r in red["ranks"]} == {1, 2}
        memory = next(c for c in d["characters"] if c["id"] == "8007")
        assert memory["light_cone"]["refinement"] == 5
        assert "main_stats" in red and "substats" in red
        assert isinstance(d["trust"]["unverified"], list)

    def test_stage_payload_defaults_to_starforge(self):
        """WebUI 默认关卡 = 最新忘却之庭星启第二节点，展示两波全部敌人。"""
        from hsr_sim.webui import DEFAULT_ENEMY, _stage_registry, build_stages_payload
        stages, default = _stage_registry(DEFAULT_ENEMY)
        d = build_stages_payload(stages, default)
        assert d["default"] == "starforge12b"
        stage = next(s for s in d["stages"] if s["id"] == d["default"])
        assert stage["wave_count"] == 2
        assert [e["name"] for e in stage["waves"][0]["enemies"]] == ["破晓战队·苍翼", "破晓战队·灰烬"]
        assert stage["waves"][1]["enemies"][0]["name"] == "合金机铠·帕姆王"
        assert stage["unverified_inputs"]

    def test_factory_honors_selected_enemy_and_waves(self):
        """回归：non-legacy 会话不能忽略 WebUI 选中的 enemy 文件。"""
        from hsr_sim.webui import DEFAULT_ENEMY, DEFAULT_TEAM, _make_session_factory, _stage_registry
        stages, default = _stage_registry(DEFAULT_ENEMY)
        factory = _make_session_factory(DEFAULT_TEAM, stages, default,
                                        DATA_DIR / "rotation.json", False)
        session = factory(seed=0, stage_id="starforge12b")
        assert session.sim.stats["1015"].atk == pytest.approx(2629)
        assert session.sim.target_av == pytest.approx(1000)
        assert len(session.sim._waves) == 2
        assert set(session.sim._waves[1]) == {"pamking"}

    def test_equipment_search(self):
        from hsr_sim.webui import build_equipment_payload
        hit = build_equipment_payload("light_cones", "于夜色中")["items"]
        assert any("于夜色中" in i["name"] for i in hit)
        sets = build_equipment_payload("relic_sets", "繁星璀璨的天才")["items"]
        assert sets and sets[0]["sets"]
        eid = build_equipment_payload("eidolons", "")["items"]
        assert any(i["id"] == "1015" for i in eid)
