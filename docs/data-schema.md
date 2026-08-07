# 数据字段清单与数据源映射（data-schema）

> 配套文档：`game-knowledge.md`。
> 目标：为"AI 计算全队最佳面板"提供**已验证存在**的数据源字段清单，以及 ETL 提取后的精简 JSON schema。
> 数据源：**StarRailRes**（`Mar-7th/StarRailRes`，index_min/cn，raw 直连不限流）+ **TurnBasedGameData**（`DimbreathBot/TurnBasedGameData`，ExcelOutput，raw 直连不限流）。

---

## 一、数据源总览

| 数据源 | 仓库 | 覆盖范围 | 特点 |
|---|---|---|---|
| StarRailRes | Mar-7th/StarRailRes | 角色/光锥/遗器/套装/星魂/行迹/材料/图标 | 社区整理、多语言、字段可读、持续更新（2026-07） |
| TurnBasedGameData | DimbreathBot/TurnBasedGameData | 全部游戏数值（技能/敌人/关卡/场地/难度） | 原始解包数据，字段为内部 ID，文本为 Hash（需 TextMap 翻译） |

---

## 二、player_data/（来自 StarRailRes index_min/cn）

### 2.1 characters.json —— 角色基础信息
- 键：角色 ID（如 `"1204"`）
- 字段：`id, name, rarity, element, path(命途), icon, preview, portrait, release, version`
- 说明：基础属性（HP/攻击/防御/速度）不在此表，在 character_promotions.json

### 2.2 character_promotions.json —— 角色晋阶与等级
- 字段：`id, values`（每级 HP/攻击/防御/速度/暴击率/暴伤/命中/抵抗）、`base_hp, base_atk, base_def, base_spd, max_hp, max_atk, max_def`
- 用途：**80 级满级基础面板**（面板计算起点）

### 2.3 character_skills.json —— 角色技能
- 字段：`id, name, max_level, element, type(技能类型), type_text(普攻/战技/终结技/天赋), effect, effect_text, simple_desc, desc, params, icon`
- `params`：每级数值列表，如 `[[0.5],[0.6],...]`（倍率）
- 用途：技能倍率；**注意：能量回复/SP 精确数值需到 TurnBasedGameData AvatarSkillConfig 补充**

### 2.4 character_ranks.json —— 星魂
- 字段：`id, name, rank, desc, params, icon`
- 用途：1-6 命效果与数值

### 2.5 character_skill_trees.json —— 行迹
- 字段：`id, name, type, max_level, desc, params, icon`
- 用途：额外属性加成与大行迹

### 2.6 light_cones.json + light_cone_ranks.json —— 光锥
- 字段：`id, name, rarity, path, desc, icon` + 叠影 `desc, params`
- 用途：候选光锥效果 + 叠影数值

### 2.7 relic_sets.json —— 遗器套装
- 字段：`id, name, desc(2件套/4件套效果), icon`
- 用途：套装选择

### 2.8 relics.json —— 遗器个体（可选，装备池匹配用）
- 字段：`id, name, set_id, rarity, slot_type`

### 2.9 其他
- `paths.json`（命途）、`elements.json`（元素）、`properties.json`（属性）—— 映射表
- `items.json` —— 材料（无需导入）

---

## 三、battle_data/（来自 TurnBasedGameData ExcelOutput）

### 3.1 AvatarSkillConfig.json —— 角色技能数值（10MB，6804 条）
- 键：`SkillID`；字段：
  - `SkillTag, SkillTypeDesc`（技能分类）、`Level, MaxLevel`
  - **`SPMultipleRatio`**（战技点消耗倍率）、**`BPNeed`**（所需战技点）
  - **`DelayRatio`**（行动延时比例，排轴关键）
  - `InitCoolDown, CoolDown`（冷却）
  - **`StanceDamageDisplay`**（削韧值）、`StanceDamageType`（削韧属性）
  - **`ParamList`**（各等级数值参数）、`ShowDamageList, ShowHealList`
  - `AttackType, SkillEffect`
- 关联：SkillID 与角色 ID 的关系需通过 `AvatarSkillLink.json`（或按角色 ID 前缀匹配，如 1204 → SkillID 1204xxx）

### 3.2 MonsterConfig.json —— 敌人（4MB）
- 字段（首条实测）：`MonsterID, MonsterName, MonsterTemplate, MonsterType, MonsterSubType, ElementType, MonsterProperty(HP/攻击/防御/速度/韧性), Toughness, SkillList, WeaknessList(弱点), Rarity` 等
- 用途：敌人 HP/防御/速度/韧性/弱点 → 防御乘区、削韧判断、行动轴

### 3.3 MonsterSkillConfig.json —— 敌人技能（2.4MB）
- 字段：`MonsterSkillID, SkillName, SkillTrigger, AttackType, StanceDamage, ParamList, DelayRatio, MaxLevel` 等
- 用途：敌人行动延时/伤害（防御需求评估，可选）

### 3.4 StageConfig.json —— 关卡（24MB，最大表）
- 字段：`StageID, StageName, Monsters(波次敌人列表), MonsterLevel, StageType, BuffList, MapEntranceID` 等
- 用途：MoC 每层敌人构成与等级

### 3.5 ChallengeMazeConfig.json —— 混沌回忆（MoC）专项（519KB）
- 字段：`ChallengeMazeID, MazeID, Level, ChallengeType, RoundLimit(轮次限制), BuffList(场地buff), MonsterList` 等
- 用途：**轮次约束 + 场地 buff 关联**（面板优化的硬约束来源）

### 3.6 BattleEventConfig.json —— 战斗事件/场地事件（304KB，459 条）
- 字段（实测）：`BattleEventID, Team, EventSubType, BattleEventName, HeadIcon, AbilityList, OverrideProperty, Speed, HardLevel, ParamList`
- 用途：**场地事件（含 Speed 修正、属性覆盖）** —— 场地 buff 的数值载体

### 3.7 MazeBuff.json —— 场地 buff 定义（1.4MB）
- 字段：`MazeBuffID, BuffName, Modifier(效果), ParamList, Duration, MaxLayer` 等
- 用途：buff 效果数值（与 StageConfig/ChallengeMazeConfig 的 BuffList 关联）

### 3.8 HardLevelGroup.json —— 难度换算（375KB）
- 字段：`HardLevelGroupID, Level, MonsterProperty(按等级的属性系数), PlayerProperty`
- 用途：**敌人等级 → 实际 HP/防御/速度**（伤害公式中防御乘区必需）

### 3.9 其他可能用到的表
- `DamageType.json`（伤害类型）、`EnergyBarConfig.json`（能量条）
- `Config/TextMap`（Hash → 名称文本，多语言）

---

## 四、ETL 输出 schema（scripts/etl/ 后续阶段生成）

> ETL 脚本（Python，后续实现）：从两个数据源提取面板计算所需字段，输出精简 JSON + 中文描述。

```
data/
├─ player_data/
│  ├─ characters.json     # id, name, path, element, base(mp/atk/def/spd), ult_cost
│  ├─ skills.json         # char_id → {basic:{mult,sp}, skill:{mult,sp,delay}, ult:{mult,cost}, talent:{...}}
│  ├─ eidolons.json       # char_id → 1-6 命效果数值
│  ├─ traces.json         # char_id → 行迹属性
│  ├─ light_cones.json    # id, path, effect, superimposition params
│  └─ relic_sets.json     # id, 2pc/4pc effect params
├─ battle_data/
│  ├─ monsters.json       # id, name, hp/atk/def/spd, toughness, weaknesses, skills
│  ├─ stages.json         # stage_id, waves[{monster_ids}], level, round_limit
│  ├─ events.json         # battle_event_id, speed_mod, ability_list, param_list
│  ├─ maze_buffs.json     # buff_id, effect, params
│  └─ level_curves.json   # level → monster/player property multipliers
└─ textmap/               # hash → 中文名（仅名称字段需要）
```

### 字段提取优先级（按面板计算依赖）

1. **必选**：角色满级面板（promotions）、技能倍率+SP+延时（AvatarSkillConfig）、ult_cost、敌人属性（MonsterConfig）、难度系数（HardLevelGroup）、轮次限制（ChallengeMazeConfig）
2. **强烈建议**：光锥/遗器套装效果（伤害乘区）、场地事件 Speed 修正（BattleEventConfig）、敌人弱点
3. **可选**：敌人技能、星魂/行迹细节（非默认配置）

---

## 五、注意事项

1. **TurnBasedGameData 字段为内部 ID**：文本（SkillName/Desc）是 Hash 值，需 TextMap 翻译；角色名等以 StarRailRes 为准
2. **AvatarSkillConfig 与角色的关联**：SkillID 前缀 = 角色 ID（如 1204 → 12040xxx），需验证 `AvatarSkillLink.json` 确认
3. **能量回复数值**：StarRailRes 无精确回能字段，需从 AvatarSkillConfig `ParamList` 的特定参数位提取（每个角色参数位约定不同，需人工核对 1-2 个角色验证）
4. **仓库体量**：StageConfig.json 24MB / AvatarSkillConfig.json 10MB —— 只提取需要的字段，不要整表入库
5. **更新节奏**：两个数据源都随版本持续更新；ETL 应支持全量重跑
