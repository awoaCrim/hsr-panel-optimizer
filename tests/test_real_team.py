"""真实配置（米游社战绩）装配测试——验证面板直填 + 行迹等级 + 装备机制。"""
import json
from pathlib import Path

import pytest

from hsr_sim.data.loader import load_team_normalized
from hsr_sim.loader import DATA_DIR

REAL = Path("data/team_real.json")


@pytest.fixture()
def real():
    chars, stats, _, unv = load_team_normalized(REAL)
    return chars, stats, unv


class TestRealPanel:
    def test_panel_matches_mihoyo(self, real):
        """面板直填 = 米游社战绩总计（ATK/SPD/HP/DEF）。"""
        _, stats, _ = real
        assert stats["1015"].atk == pytest.approx(2629)
        assert stats["1015"].speed == pytest.approx(107)
        assert stats["1015"].hp == pytest.approx(3135)
        assert stats["1306"].speed == pytest.approx(160)
        assert stats["1309"].atk == pytest.approx(4549)
        assert stats["8007"].speed == pytest.approx(162)

    def test_trace_levels_applied(self, real):
        """行迹技能等级：红A 普攻 L6、记忆主 战技 L5/大招 L12（E5+2）。"""
        chars, _, _ = real
        assert chars["1015"].skills["basic"].mult == pytest.approx(1.0)   # L6
        assert chars["1015"].skills["skill"].mult == pytest.approx(3.6)   # L10
        assert chars["8007"].skills["skill"].mult == pytest.approx(0.42)  # L5
        assert chars["8007"].skills["ult"].mult == pytest.approx(2.64)    # L12

    def test_memosprite_level(self, real):
        """记忆主 E5 忆灵技+1 → 迷迷 L7（行迹忆灵技 7 级一致）。"""
        chars, _, _ = real
        m = chars["8007"].talent_extra["memosprite"]
        assert m["basic_mult"] == pytest.approx(0.396)
        assert m["support_true_dmg"] == pytest.approx(0.30)

    def test_gear_effects(self, real):
        """真实装备机制：红A 23046 战技叠攻击 / 花火 23034 圣咏 / 记忆主 22006 全队增伤。"""
        chars, _, _ = real
        types = {e["type"] for e in chars["1015"].equipment_effects}
        assert {"sp_cap_ge_atk", "skill_stack_atk"} <= types
        types2 = {e["type"] for e in chars["1306"].equipment_effects}
        assert {"single_skill_energy", "target_dmg_stack", "every_n_skill_sp"} <= types2
        types3 = {e["type"] for e in chars["8007"].equipment_effects}
        assert "mem_team_dmg" in types3

    def test_ranks_real(self, real):
        """真实星魂：红A E2（终结技量子抗穿）、花火 E2（天赋无视防御）、知更鸟 E2（回能+1）。"""
        chars, _, _ = real
        t1 = {e["type"] for e in chars["1015"].equipment_effects}
        assert "ult_quantum_pen" in t1
        t2 = {e["type"] for e in chars["1306"].equipment_effects}
        assert "talent_def_ignore" in t2
        t3 = {e["type"] for e in chars["1309"].equipment_effects}
        assert "talent_energy_bonus" in t3

    def test_robin_sets_no_4piece(self, real):
        """知更鸟：勇烈2+快枪手2+翁瓦克2（无 4 件套）——机制类只有翁瓦克 start_advance。
        stat 类（快枪手攻击 12%）进 stat_bonus 由 assemble 处理（stats 直填模式面板已含）。"""
        chars, _, _ = real
        t = {e["type"] for e in chars["1309"].equipment_effects}
        assert "start_advance" in t       # 翁瓦克 2 件（速度 127 ≥ 120 → 开局拉条）
        assert "basic_dmg" not in t       # 无快枪手 4 件（3 件不够）
        assert "ult_dmg" not in t         # 无勇烈 4 件（2 件不够）


class TestMihoyoConverter:
    def test_converter_matches_hand_check(self):
        """转换脚本输出与战绩 JSON 关键值一致（可复现审计）。"""
        from scripts.mihoyo_to_team import CHAR_IDS, main as conv_main
        src = Path("G:/Users/admin/Download/星穹铁道_角色战绩汇总.json")
        if not src.exists():
            pytest.skip("无战绩文件")
        out = Path("G:/tmp/team_real_conv.json")
        import sys as _sys
        _sys.argv = ["mihoyo_to_team.py", str(src), "--out", str(out)]
        conv_main()
        conv = json.loads(out.read_text(encoding="utf-8"))["builds"]
        team = json.loads(REAL.read_text(encoding="utf-8"))["builds"]
        for cid in team:
            for k in ("crit_rate", "crit_dmg", "energy_regen"):
                assert conv[cid]["stats"][k] == pytest.approx(team[cid]["stats"][k], abs=0.001)
            assert conv[cid]["eidolon"] == team[cid]["eidolon"]
            assert conv[cid]["light_cone"] == team[cid]["light_cone"]
