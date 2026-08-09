"""核心数据模型：面板 / 技能 / 角色 / 敌人 / 循环。

所有模型与输入 JSON（data/）一一对应，是输入层解耦的契约：
米游社导入器（后续阶段）与手填 JSON 都产出这些结构。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class UnitType(str, Enum):
    CHARACTER = "character"
    MEMOSPRITE = "memosprite"
    ENEMY = "enemy"


@dataclass
class Stats:
    """最终面板（基础 + 光锥 + 遗器 + 词条装配后的结果）。"""

    hp: float = 0.0
    atk: float = 0.0
    defense: float = 0.0
    speed: float = 0.0
    crit_rate: float = 0.0        # 0.70 = 70%
    crit_dmg: float = 0.0         # 1.40 = 140%
    break_effect: float = 0.0     # 2.00 = 200%
    energy_regen: float = 1.0     # 充能效率
    dmg_bonus: float = 0.0        # 属性/全伤增伤（加算区）
    heal_bonus: float = 0.0

    def copy(self) -> "Stats":
        return Stats(**{f.name: getattr(self, f.name) for f in Stats.__dataclass_fields__.values() if f.name != "copy"})


@dataclass
class SkillData:
    """一个技能动作的完整数值（v1 字段集，后续可扩展）。

    mult           伤害倍率（对攻击力）
    sp             SP 变化：+1 普攻回点 / -1 战技耗点 / 大招特例见 sp_bonus
    energy         施放后自身回能（普攻 20 / 战技 30 / 大招 5）
    energy_cost    大招能量消耗（仅 ult 有效）
    toughness      削韧值（0 = 不削韧）
    delay          行动延时比例（0 = 无延时）
    advance_pct    拉条比例（对 target，0 = 无）
    advance_target 拉条目标 unit_id（空 = 自身）
    advance_self   官方目标选择器是否允许选择自身（False = 不可自拉，如花火战技）
    extra_action   行动后不结束回合（红A 战技【回路连接】）
    sp_bonus       额外 SP 回复（花火大招 +4）
    note           机制说明/数据来源标注
    """

    mult: float = 0.0
    sp: int = 0
    energy: float = 0.0
    energy_cost: float = 0.0
    toughness: float = 0.0
    delay: float = 0.0
    advance_pct: float = 0.0
    advance_target: str = ""
    advance_self: bool = True
    extra_action: bool = False
    sp_bonus: int = 0
    mult_levels: List[float] = field(default_factory=list)  # 等级表（L1 起；等级类星魂用）
    note: str = ""


@dataclass
class CharacterData:
    """角色静态数据（与 data/characters/*.json 对应）。"""

    id: str
    name: str
    element: str
    path: str
    base_stats: Stats
    skills: Dict[str, SkillData] = field(default_factory=dict)  # basic/skill/ult/talent
    talent_extra: Dict = field(default_factory=dict)            # 天赋特殊效果（结构化，见各角色文件）
    equipment_effects: List[Dict] = field(default_factory=list)  # 装备生效效果（exec DSL，见 build.resolve_equipment）
    max_energy: float = 0.0
    note: str = ""


@dataclass
class Enemy:
    id: str
    name: str
    element: str
    hp: float
    atk: float
    defense: float
    speed: float
    toughness: float
    weaknesses: List[str]
    resistances: Dict[str, float] = field(default_factory=dict)  # element -> 抗性（0.2 = 20%）
    break_immune: bool = False
    # 敌人技能（①敌人 AI：MonsterSkillConfig 结构——ParamList[0]=伤害倍率（分布推断，
    # 待实测验证）；ai_cd = 使用后冷却回合；sp_hit = 我方受击回能基础（官方 SPHitBase））
    skills: List["EnemySkill"] = field(default_factory=list)


@dataclass
class EnemySkill:
    """敌人技能（MonsterSkillConfig 映射；无 skills 的敌人保持 v1 行为——只回韧性不攻击）。"""
    name: str = "普攻"
    mult: float = 1.0            # 伤害倍率（ParamList[0]，标注推断）
    damage_type: str = ""        # 伤害属性（对抗性判定；空 = 我方无抗性 0）
    ai_cd: int = 1               # 使用后冷却回合数（AI_CD）
    sp_hit: float = 10.0         # 受击回能基础（SPHitBase）


@dataclass
class BuildProposal:
    """LLM 每轮输出的面板方案（与 docs/game-knowledge.md 1.2 的 JSON 对应）。

    v1 简化：直接给出最终面板数值目标（速度/双暴/攻击等），
    词条预算校验与光锥/遗器装配推导见 build.py（基础层，后续启用）。
    """

    character_id: str
    speed_target: float = 0.0
    atk_target: float = 0.0
    crit_rate: float = 0.0
    crit_dmg: float = 0.0
    break_effect: float = 0.0
    energy_regen: float = 1.0
    light_cone: str = ""
    relic_set: List[str] = field(default_factory=list)
    main_stats: Dict[str, str] = field(default_factory=dict)
    substats: Dict[str, float] = field(default_factory=dict)  # 词条数 → 由 build.py 换算


@dataclass
class Action:
    """循环中的一条行动指令。"""

    unit_id: str
    action: str          # basic / skill / ult / memosprite_skill（迷迷）/ enemy_action
    target: str = ""     # 主目标 unit_id（技能拉条/单体伤害目标）
    note: str = ""


@dataclass
class CharacterPolicy:
    """单个角色的战斗决策规则（策略字段化：决策成为可验证的优化对象）。

    策略模式：模拟器按规则实时决策，而非消费写死的行动序列。
    """

    ult: str = "on_full"        # on_full（能量满即时释放）/ off（不开大）
    chain_max: int = 0          # 回路连打上限（红A；0=用天赋默认值）
    fallback: str = "basic"     # SP 不足时的降级动作
    skill_budget: int = 999     # 整场战技次数上限（辅助 SP 预算；红A 由 chain_max 控制）
    pull_target: str = ""       # 拉条目标（花火拉谁）


@dataclass
class Rotation:
    """循环：策略（决策规则）或行动序列。

    - policy：策略模式（按规则实时决策，决策点可被程序搜索优化）
    - actions：序列模式（写死行动序列，兼容 v2；无 policy 时使用）
    """

    policy: Dict[str, CharacterPolicy] = field(default_factory=dict)
    actions: Dict[str, List[Action]] = field(default_factory=dict)

    def next_action(self, unit_id: str) -> Optional[Action]:
        seq = self.actions.get(unit_id)
        if not seq:
            return None
        act = seq[0]
        # 不弹出：循环可重复执行（循环执行 N 圈）；由模拟器用 action_index 管理
        return act

    def advance(self, unit_id: str):
        seq = self.actions.get(unit_id)
        if seq:
            seq.append(seq.pop(0))
