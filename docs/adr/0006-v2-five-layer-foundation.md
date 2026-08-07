# 0006 v2 地基重构：五层架构、数据溯源、Effects 领域模型、对账体系（用户决策）

> 状态：已批准，作为 v2 阶段（P0/P1/P2）的架构蓝图与重构边界。实施顺序见"八、迁移路径"。

## 一、背景：精度宣称高于底层保障

v1.5 已能跑通"LLM 提面板 → 精确验证 → 迭代收敛"，且 48 个测试全绿。但对仓库逐项核查后确认一个结构性矛盾：**项目宣称的精度等级（ADR-0004：完整实现全部机制与全量伤害公式），高于底层实际能够保证的精度等级**。

证据（全部经代码核对属实）：

| 证据 | 位置 |
|---|---|
| ADR-0004 宣称"完整实现全部机制…不做简化取舍" | `docs/adr/0004` |
| 但 `Multipliers` 自注"v1 静态值，后续可改为 buff 时间轴驱动" | `hsr_sim/engine/damage.py` |
| 90 级击破系数 3767.5 标注"社区 wiki 值，待实测核对" | `damage.py BREAK_LEVEL_COEF` |
| `break_damage()` 接收 `enemy_toughness_max` 参数却完全未使用 | `damage.py`（docstring 自认"v1 取满韧性系数 1.0"） |
| 光锥统一模板"基础攻击 +582、攻击 +20%"（所有光锥一视同仁） | `build.py LIGHT_CONE_TEMPLATE` |
| ETL 未实现（`scripts/etl/README.md` 为占位），角色/敌人数据手填 | `data/characters/*.json`、`data/enemy_elite90.json` |
| 敌人 HP/防御标注"模板值，可随时更换" | `data/enemy_elite90.json` |
| 测试"每个用例与手算值核对"——证明实现符合自写公式，而非符合游戏 | `tests/test_damage.py` |
| 角色特殊机制（fate_charge / burst_chain / sp_spent_count / concert_rounds / memosprite）全部堆在模拟器 if/else 里 | `hsr_sim/engine/simulate.py`（514 行） |

结论：现在算出的总伤害只能理解为"在我们自定义的简化宇宙中，该方案得到该数字"，**不能**理解为游戏真实值。继续加 LLM 震荡修复、best-so-far、扩大策略搜索，只会把楼盖得更高再返工。

## 二、v2 目标

**`simulate(snapshot)` 成为可信函数**：给定一个面板与战斗配置，模拟器的输出（每次行动的伤害、行动时间、SP、能量、削韧/击破）可与游戏实测逐项对账。不是 100 万伤害，不是 LLM 收敛，不是 864 个策略搜索——而是这一条。

辅助目标：
- 数据逐字段可追溯（一个数错了，能一路找到来源）
- 模拟器输出携带信任度标记（输入含未验证值时，结果自动标注"未验证"）
- 新角色接入 = 写数据 + 写效果，不再改模拟器代码

## 三、v2 五层架构总览

```
            LLM（外层：解释意图 / 约束 / 用户偏好，不承担数值搜索）
                      ↓
                 Optimizer
              ↙            ↘
        Panel Search      Policy Search（保留 v1.5 实现）
              ↘            ↙
                Evaluator（主指标/约束/诊断）
                      ↓
                Simulator（通用事件循环）
                      ↓
         Mechanics Core（Effects 执行器 + AV 队列 + 乘区原语）
                      ↓
             Normalized Data（带溯源的精简数据）
                      ↓
                   ETL（可重跑、固定版本）
                      ↓
          Game Data Source（StarRailRes / TurnBasedGameData / 实测）
```

核心变化：数据（角色机制）不再长在模拟器里，而是长在数据层；模拟器从"红A队特化"退化为"通用机制执行器"；对账体系从"代码自洽"升级为"游戏对账"。

## 四、L1 数据真值层

### 4.1 溯源 schema

`data/normalized/` 下每个数值字段可带溯源包装：

```json
{
  "id": "1306",
  "name": "花火",
  "element": { "value": "Quantum", "source": "starrailres", "version": "index_min@2026-07", "field": "characters.element" },
  "base_stats": {
    "hp":  { "value": 1397.1, "source": "starrailres", "version": "index_min@2026-07", "field": "character_promotions.base_hp" }
  },
  "skills": {
    "skill": {
      "advance_pct": { "value": 0.5, "source": "datamine", "version": "tbgd@<commit>", "field": "AvatarSkillConfig.ParamList[3]" },
      "mult":        { "value": 0.0, "source": "wiki", "version": "biligame-2026-08", "field": "", "override": true, "note": "解包参数位待核对" }
    }
  }
}
```

字段语义：
- `value`：数值本身
- `source`：`datamine`（解包，最高信任）/ `starrailres`（社区整理数据）/ `wiki`（人工核对）/ `handfill`（手填/模板/假设）
- `version`：上游数据源固定版本（GitHub commit / 日期），保证可重跑
- `field`：上游原始字段路径（如 `AvatarSkillConfig.ParamList[3]`）
- `override`：true 表示该值经人工修正/覆盖了上游值（记录 note 说明理由）

允许的简写：纯数字 = 继承所在块的 `_default_source`；`_default_source` 缺失的块在数据审计中报错。**任何无溯源的字段无法通过 L1 审计门禁**。

### 4.2 信任等级

| 等级 | 含义 | 举例 | 可否进入"可信"结果 |
|---|---|---|---|
| Lv A | 解包真值 | TurnBasedGameData（固定 commit） | 可 |
| Lv B | 社区整理数据 | StarRailRes（固定 commit） | 可 |
| Lv C | 社区 wiki 人工核对 | biligame 核对（ADR-0003 修正项） | 可（文档化） |
| Lv D | 手填/模板/假设 | 光锥模板、敌人模板值、迷迷倍率 | 否——结果必须标注"未验证" |

### 4.3 ETL 管线

`scripts/etl/` 三阶段，全部可全量重跑：

1. **fetch**：按固定版本（上游 repo commit）拉取 StarRailRes（index_min/cn）与 TurnBasedGameData 所需表，落 `data/raw/<source>@<version>/`；版本记录写 `data/raw/VERSIONS.json`
2. **extract**：从 raw 提取面板计算所需字段（按 `docs/data-schema.md` 第四节的优先级清单），生成带溯源包装的 `data/normalized/*.json`
3. **audit**：`python -m hsr_sim.data audit` 校验：所有字段有溯源、版本一致、引用完整（如技能 ID 前缀=角色 ID）、无 Lv D 值混入"可信路径"而未标记

首批 ETL 范围（P0）：4 角色（1015/1306/1309/8007）+ 敌人 + HardLevelGroup 难度系数 + 轮次规则。红A（1015）联动数据上游缺失（ADR-0003），维持 wiki 手填 + `override` 标记，属已知例外。

### 4.4 遗留数据迁移

`data/characters/` 手填 JSON 迁移到 `data/legacy/` 冻结，作为 ETL 输出正确性的对照样本；`data/enemy_elite90.json` 的"模板值"标注 `handfill` 溯源，进入 Lv D，直到 ETL 产出真实敌人。

## 五、L2 领域模型层：Effects 模型

### 5.1 问题

现在 `SkillData` = mult/sp/energy/energy_cost/toughness/delay/advance_pct/extra_action/sp_bonus + `talent_extra` 杂物袋；红A/花火/知更鸟/记忆主一复杂，fate_charge、burst_chain、sp_spent_count、concert_rounds、memosprite 全部逃逸进 simulate.py 的 if/else。换角色（扩散/弹射/追加/延迟结算/层数/光环/结界/自拉条/召唤物/额外回合/行动外触发）时 simulate.py 会迅速膨胀。

### 5.2 设计

**技能 = Effects[]**，每个 Effect 是类型化数据（字段含溯源），角色机制属于数据层：

```
Damage(mult, target, kind, element?)            # 伤害（normal/followup/additional/break）
EnergyGain(amount) / EnergyCost(amount)
SPChange(delta)                                  # +1 普攻 / -1 战技 / +4 花火大
ToughnessDamage(amount, element?)                # 仅弱点属性生效
AdvanceAction(target, pct)                       # 拉条（花火 E 50% / 知更鸟大 100% 全队）
Postpone(target, pct)                            # 推条/击破延后 25%
ApplyBuff(stat, value, target, duration, cap)    # 复用 BuffManager 语义
Aura(stat, value)                                # 常驻（迷迷真伤光环）
MemospriteCharge(amount)                         # 迷迷充能
ExtraAction(max_count)                           # 红A 回路连接链
AdditionalDamage(mult, fixed_crit_rate, fixed_crit_dmg, source_stat)  # 知更鸟协奏
BreakDamage(trigger)                             # 韧性归零时触发
Trigger(condition, effects[])                    # 条件触发：on_ally_attack（红A追击）/ on_ult / on_spent
```

**事件体系**（效果执行的轨迹，也是对账的观测点）：

```
Skill.Effects[] 执行 → 产生事件（DamageEvent/EnergyEvent/SPEvent/ToughnessEvent/
BuffApplyEvent/AdvanceEvent/SummonEvent/TriggerEvent）→ 入队 → 状态变化 → 触发新效果
```

**Simulator = 通用事件循环**：不感知任何具体角色。角色 JSON 的 `effects` 字段是唯一机制入口；删掉 `_apply_skill_effects` 的 if/else 链与全部"天赋运行时"字段。

### 5.3 兼容垫片与回归对等

重构分两步走，避免大爆炸：

- **Step A（P0-2a）**：定义 Effect 类型 + 翻译器 `legacy_skill_to_effects(SkillData) -> Effects[]`，把现有 v1.5 数据翻译成 Effects 喂给新循环。验收：**红A场景逐行动输出与 v1.5 完全一致**（行动时间/伤害/SP/能量逐项相等），差异即为 bug。
- **Step B（P0-2b）**：翻译器下线，`data/characters/*.json` 直接写 effects。角色 JSON 与 L1 溯源 schema 合并（effects 内数值同样带溯源）。

## 六、L3 模拟器与机制核心

保留不动：`av_queue.py`（AV 模型通用、无角色耦合）、`damage.py` 纯函数原语（暴击/防御/抗性/真伤/附加）、`buffs.py`（stat/cap/target/duration 语义已是效果化）。

v2 修复项：

1. **击破韧性修正**：`break_damage()` 已接收 `enemy_toughness_max` 却取满韧性系数 1.0。v2 按真实公式实现韧性系数（当前韧性/最大韧性参与），具体公式以对账测试锁定，不靠猜。
2. **乘区时间轴化**：`Multipliers` 静态值改为由 buff 时间轴驱动（BuffManager 已有 duration 语义，补事件驱动递减）。
3. **信任度信封**：`SimResult` 增加 `trust_level` 与 `unverified_inputs: [字段路径]`。任何 Lv D 输入参与的计算，报告顶部强制标注"未验证（原因：…）"，LLM 迭代反馈中同步携带该标记——验证器不再"精确地验证不精确的世界"而不自知。

## 七、L4 对账体系（测试分层）

| 层 | 内容 | 现有基础 |
|---|---|---|
| T1 单元测试 | 公式自洽、队列行为、面板装配 | 现有 48 个全部保留（它们是合格的单元测试） |
| T2 黄金测试 | 固定场景逐行动追踪：每次行动时间/伤害/SP/能量/buff 层数/削韧/击破/敌人剩余 HP，全部断言 | 重构后首次全绿即锁定为 golden baseline |
| T3 交叉对账 | 与成熟模拟器逐项对比（fribbels/hsr-optimizer 对伤害，Honkai-Star-Rail-Simulator 对 AV），**不比总量，比首次出现差异的行动** | 待选型 |
| T4 游戏实测 | 固定场景实测协议：等级/面板/技能等级/敌人等级抗性防御/buff 全固定 → 游戏内录数（击破 3767.5、迷迷倍率、真伤 10% 等全部待核值优先）→ 模拟器误差 ≤ 0.1% | 待建 |

P0 通过标准：**红A固定队 + 固定靶场，T2 全绿且所有 P0 输入 ≥ Lv C；Lv D 值（如有）全部在 T4 实测清单上并已标记**。此时"simulate(snapshot) 可信"成立。

## 八、迁移路径与里程碑

### P0：地基（目标：对账通过）

| 步骤 | 内容 | 验收 |
|---|---|---|
| P0-0 | 确认基线：v1.5 已全部提交（commit `4513792`，工作区无未提交代码）；重构分支从该 commit 切出；清理杂散文件（如根目录 `nul`，Windows 重定向误产物） | 工作区干净；重构回退点 = `4513792` |
| P0-1 | ETL 首批（4 角色 + 敌人 + 难度系数）+ 溯源 schema + audit 门禁 | `hsr_sim.data audit` 全绿；红A队数据全部 Lv A/B，例外仅红A（C+override） |
| P0-2a | Effect 类型 + 翻译器 + 通用事件循环 | 与 v1.5 逐行动输出完全一致 |
| P0-2b | 角色 JSON 直接写 effects；翻译器下线 | 数据层无 SkillData 旧字段；`talent_extra` 删除 |
| P0-3 | T2 黄金测试锁定 + 击破韧性修正 + 乘区时间轴化 + 信任度信封 | T2 全绿；报告正确标注信任度 |
| P0-4 | T3 选型对账 + T4 实测协议（首批：击破系数/迷迷倍率/真伤） | 与外部模拟器首次差异定位机制；待核值逐项入实测清单 |

### P1：真实环境 + 数值优化器

- 真实关卡 ETL（StageConfig / ChallengeMazeConfig / MazeBuff / BattleEvent）：靶场从手填模板换真实 MoC 层
- 光锥/遗器套装 ETL：光锥模板退役，按具体光锥+叠影装配（build.py 适配）
- Panel Search：词条空间（atk/spd/cr/cd/break/energy，sum ≤ 30，主词条离散集合）是明确的数学空间，**程序确定性搜索**（贪心/束搜索），不依赖 LLM
- Policy Search：保留 v1.5 实现，挂在 Optimizer 下

### P2：LLM 外层化 + 输入适配器

- LLM 职责收缩：解释用户意图/约束、输出方案约束与策略偏好、解读诊断；**不承担核心数值搜索**（修复震荡问题的最优解是让它别干这个活）
- 米游社适配器（ADR-0002 后补项）接入，schema 不变

## 九、重构边界（保留 / 重写 / 废弃）

**保留**（视为稳定件）：`av_queue.py`、`damage.py` 纯函数、`buffs.py`、`policy_search.py`、`build.py` 的词条预算审计逻辑、全部 48 个单测。

**重写**：`model.py` 的 SkillData/CharacterData（→ effects 模型 + 溯源 schema）、`simulate.py`（→ 通用事件循环）、`build.py` 光锥装配（等 P1 真实光锥数据）。

**废弃/冻结**：`data/characters/` 手填格式（→ `data/legacy/` 对照样本）、`Multipliers` 静态值用法（→ 时间轴驱动）、`enemy_elite90.json` 模板值（→ Lv D 标记，P1 替换）。

**v1.5 代码定位**：第一版实验代码与测试素材，不是稳定基础设施——不删，不信任。

## 十、决策与备选

- **决策**：先定架构与边界，再动代码（本文档即交付物）；重构以"逐行动输出对等"为唯一安全网，禁止大爆炸式重写
- **被否**：继续修 LLM 震荡/best-so-far/扩大策略搜索（在不可信地基上叠优化）；LLM 作为核心搜索方法（词条是明确数学空间，程序搜索更优，LLM 退居外层）
- **被否**：一次性迁移全部机制（分两步走，翻译器垫底，回归对等后再换数据格式）

## 十一、风险

- 红A 联动数据上游长期缺失 → 维持 wiki 手填 + override，T4 实测协议兜底
- 击破韧性系数等公式细节无文档 → 以 T3 交叉对账 + T4 实测锁定，不在文档里猜
- 重构期间 v1.5 功能不可用 → P0-2 期间保留 v1.5 分支可回退（基线 = commit `4513792`）
- 对账口径争议（期望值 vs 实测值） → T2/T4 一律记录单次确定性伤害（非期望），期望值只用于优化层
