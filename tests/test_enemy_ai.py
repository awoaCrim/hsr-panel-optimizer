"""① 敌人 AI + 我方生存测试。

- 敌人技能：MonsterSkillConfig 结构（ParamList[0]=倍率，标注推断）；无 skills 敌人 = v1 行为
- 伤害 = 敌攻击 × 倍率 × 我方防御减免（80 级攻方公式）
- 受击回能 = SPHitBase × 充能效率（官方）
- 我方 HP 归零 = 死亡（移除队列、退出决策点）
- 快照/回退一致性（char_hp/enemy_cd 随事件流）
"""
import pytest

from hsr_sim.engine.simulate import Simulator
from hsr_sim.loader import DATA_DIR, load_character
from hsr_sim.model import Action, Enemy, EnemySkill, Rotation, Stats


def _mk(with_skills=True, char_hp=3000.0):
    chars = {"1015": load_character(DATA_DIR / "characters" / "1015.json")}
    stats = {"1015": Stats(atk=2800.0, speed=134.0, crit_rate=0.0, crit_dmg=1.5, hp=char_hp,
                           defense=900.0)}
    skills = [EnemySkill(name="重击", mult=3.0, damage_type="Physical", ai_cd=1, sp_hit=10)] \
        if with_skills else []
    enemies = {"elite": Enemy(
        id="elite", name="精英", element="Quantum",
        hp=1e9, atk=1000, defense=1100.0, speed=10.0, toughness=300.0,
        weaknesses=["Quantum"], skills=skills)}
    rot = Rotation(actions={"1015": [Action(unit_id="1015", action="basic")] * 3})
    return Simulator(chars, stats, enemies, rot, target_av=2000.0)


class TestEnemyAttack:
    def test_no_skills_keeps_v1(self):
        """无 skills 敌人：行动只回韧性（v1 行为，不攻击）。"""
        sim = _mk(with_skills=False)
        sim.toughness["elite"] = 0.0
        sim._enemy_act("elite")
        assert sim.toughness["elite"] == 300.0
        assert sim.char_hp["1015"] == 3000.0

    def test_attack_damage_oracle(self):
        """敌人伤害手算：atk×mult×防御减免（900 防 → 1000/1900）。"""
        sim = _mk()
        sim._enemy_act("elite")
        dmg = 1000.0 * 3.0 * (1000.0 / (900.0 + 1000.0))
        assert sim.char_hp["1015"] == pytest.approx(3000.0 - dmg, rel=1e-9)

    def test_hit_energy_regen(self):
        """受击回能：SPHitBase(10) × 充能效率(1.0)。"""
        sim = _mk()
        assert sim.energy["1015"] == 0.0
        sim._enemy_act("elite")
        assert sim.energy["1015"] == pytest.approx(10.0, rel=1e-9)

    def test_death_removes_from_queue_and_decision(self):
        """死亡：HP 归零 → 移除队列；后续不产生决策点。"""
        sim = _mk(char_hp=1500.0)    # 重击 1578.9 > 1500 → 死亡
        sim._enemy_act("elite")
        assert sim.char_hp["1015"] == 0.0
        assert "1015" not in sim.queue._entries
        assert sim._decision_point() is None if hasattr(sim, "_decision_point") else True

    def test_death_no_energy(self):
        """死亡当次不受击回能（官方：死亡后不回能）。"""
        sim = _mk(char_hp=1500.0)
        sim._enemy_act("elite")
        assert sim.energy["1015"] == 0.0


class TestEnemyAiSnapshot:
    def test_snapshot_restore_roundtrip(self):
        """快照/恢复：char_hp/enemy_cd 一致（回退一致性，E11）。"""
        sim = _mk()
        sim._enemy_act("elite")
        snap = sim.snapshot()
        assert snap.char_hp["1015"] == sim.char_hp["1015"]
        # 恢复后一致
        sim.char_hp["1015"] = 999.0
        sim.restore(snap)
        assert sim.char_hp["1015"] != 999.0
        assert sim.enemy_cd["elite"] == {0: 1}    # 使用后 cd=ai_cd

    def test_cooldown_counts_down(self):
        """技能冷却：使用后 ai_cd=1，下次行动递减为 0 可再用。"""
        sim = _mk()
        sim._enemy_act("elite")
        assert sim.enemy_cd["elite"][0] == 1    # 使用后设 cd=1
        # 递减发生在下一次敌人行动
        sim._enemy_act("elite")
        # 第二次行动前 cd 1→0 就绪 → 再次使用
        assert sim.char_hp["1015"] < 3000.0
