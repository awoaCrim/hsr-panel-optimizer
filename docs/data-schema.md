# 数据字段清单与数据源映射（data-schema）

> 配套文档：`game-knowledge.md`。
> 目标：为"AI 计算全队最佳面板"提供**已验证存在**的数据源字段清单，以及 ETL 提取后的精简 JSON schema。
> 数据源：**StarRailRes**（`Mar-7th/StarRailRes`，index_min/cn，raw 直连不限流）+ **TurnBasedGameData**（`DimbreathBot/TurnBasedGameData`，ExcelOutput，raw 直连不限流）。
>
> **2026-08 实测修正（P0-1 ETL 落地后）**：
> - `character_promotions.json` 无 `base_hp/base_atk/base_def/base_spd` 字段；实际结构为 `values[i].{hp,atk,def,spd,crit_rate,crit_dmg}.{base,step}`，L80 = base + step×79（详见 `scripts/etl/extract.py`）
> - `AvatarSkillConfig.json` 是 **6804 条数组**（SkillID × Level），不是按 SkillID 为键的对象；每级一条
> - 能量回复/SP 精确数值**不在** AvatarSkillConfig 的显式字段中（有 SPMultipleRatio/BPNeed/DelayRatio/StanceDamageDisplay，但语义待 P0-2 锁），当前维持 wiki 值 + C 溯源
> - 本仓库实现以 `docs/adr/0006`（五层架构）为准；ETL 输出为 `data/normalized/`（带双维溯源），非本文第四节旧目录名

---

## 一、数据源总览

| 数据源 | 仓库 | 覆盖范围 | 特点 |
|---|---|---|---|
| StarRailRes | Mar-7th/StarRailRes | 角色/光锥/遗器/套装/星魂/行迹/材料/图标 | 社区整理、多语言、字段可读、持续更新（2026-07） |
| TurnBasedGameData | DimbreathBot/TurnBasedGameData | 全部游戏数值（技能/敌人/关卡/场地/难度） | 原始解包数据，字段为内部 ID，文本为 Hash（需 TextMap 翻译） |
| Nanoka（hsr.nanoka.cc） | 静态 JSON 接口 `static.nanoka.cc/hsr/<版本>/` | 光锥白值/精炼效果、套装 2/4 件、星魂（中文描述+数值合一） | 用户提供；与 TBGD 解包 ParamList 交叉一致；版本 4.4.54 固定 |

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

## 六、equipment/（光锥/遗器套装/星魂管理系统）

> 数据源：Nanoka wiki 接口（`static.nanoka.cc/hsr/4.4.54/`，中文描述+数值合一，与 TBGD 解包交叉一致）。
> 输出：`data/normalized/equipment.json`（双维溯源，audit 门禁覆盖）。

### 6.1 光锥（light_cones）
- 键：光锥 id（20000-24000 段）；字段：`id, name(中文), path(命途), rarity, base_stats(80 级白值)`
- `effect`（仅已 fetch 详情的）：`name`（精炼名）、`desc`（中文效果，`#n[i]` 为参数占位符）、`level_1_params`（精炼 1 数值）
- 80 级白值 = promotion 6 段 `base + add×79`（验证：于夜色中 = 582.12 = 列表 atk）

### 6.2 遗器套装（relic_sets）
- 键：套装 id（101-132 外圈 / 301-328 内圈）；字段：`name, two_piece{desc,params}, four_piece{desc,params}`

### 6.3 星魂（eidolons）
- 键：角色 id；字段：`ranks.{1-6}.{name, desc, param_list}`

### 6.4 队伍配置（team builds 扩展）
```json
"builds": {"1015": {"main_stats": {...}, "substats": {...},
                    "light_cone": "23001", "relic_sets": ["108", "306"], "eidolon": 0}}
```
- `light_cone`：光锥 id（装配面板白值）；`relic_sets`：1 个 = 4 件套、2 个 = 2+2
- `eidolon`：星魂等级 0-6
- **效果边界（已接入）**：光锥被动/套装效果已接入模拟器——面板类（暴击/攻击%/速度%/充能/
  暴伤）进 assemble；机制类经 exec DSL 执行：条件增伤（超速/暴击达标/普攻/属性伤）、
  无视防御（含弱点加成）、叠层充能（【歌咏】）、大招 SP 返还、战技后增伤、开局行动提前、
  目标暴伤 buff、忆灵暴伤 buff、召唤暴伤条件。星魂效果已接入：红A E1（单回合 3 战技回 SP）、红A E2（终结技量子抗性-20%+动态弱点）、
  花火 E4（SP 上限+1、大招额外回 SP）、知更鸟 E1（协奏全属性抗穿）、记忆主 E1（声援暴击）；
  等级类 E3/E5 已接入：技能等级表（战技/大招/天赋 L1-L15、普攻 L1-L10）随技能入档，
  星魂 +N 级静态应用（L10 与当前倍率一致才生效，防参数位错位；红A E5 → 战技/大招/天赋 L12）；
  忆灵技 +1 同样支持（迷迷 L6→L7）；ult 伤害 kind='ult'（专属乘区可识别）；近似项（23003 战技后"下一个行动队友"= buff 存在期间其他角色攻击都吃）在代码注释标注
- **搜索候选光锥已接入**（装备搜索新增）：星海巡航 24001（目标 HP≤50% 暴击 +8%、击杀后攻击+20% 2回合）、
  如泥酣眠 23012（暴伤+30%；未暴击→暴击 +36%——期望值模型近似：无条件折算 +36%×(1-暴击率)，近似项）、
  论剑 21010（同目标命中叠层 8%/层×5——按 act 计层，战技多段未逐段模拟，近似项）
- **装备组合搜索**：`python scripts/search_builds.py`（红A 光锥×套装×主词条×副词条穷举，
  demo pilot 公平基线，评估 = 击杀数→总伤害→用时；480 组合约 3 秒）
