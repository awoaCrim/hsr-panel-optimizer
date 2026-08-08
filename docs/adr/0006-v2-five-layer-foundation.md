# 0006 v2 地基重构：五层架构、数据溯源、Effects 领域模型、对账体系（用户决策）

> 状态：已批准，作为 v2 阶段（P0/P1/P2）的架构蓝图与重构边界。实施顺序见"八、迁移路径"。
> 修订 v1.1（2026-08-09）：吸收对账评审 7 项修正——①公式审计范围升级（80/90 等级混淆、负抗减半、Max Toughness Multiplier、Broken 乘区）②双维信任模型（来源信任 × 验证状态）③新增事件语义章节（5.3）④T2 拆分为 T2a 重构对等 / T2b 黄金对账 ⑤L5 层正式闭合 ⑥P0 路径重排（P0-2 先锁公式与语义）⑦"新角色接入"表述修正。

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

**`simulate(snapshot)` 成为可信函数**：给定一个面板与战斗配置，模拟器的输出（每次行动的伤害、行动时间、SP、能量、削韧/击破）可与游戏实测逐项对账。不是 100 万伤害，不是 LLM 收敛，不是 864 个策略搜索——而是这一条。**产品形态为战斗推演模拟引擎（见 ADR-0007）：可信之上支持任意行动回退（事件溯源）与 LLM 交互式指挥（环境-智能体接口）。**

辅助目标：
- 数据逐字段可追溯（一个数错了，能一路找到来源）
- 模拟器输出携带信任度标记（输入含未验证值时，结果自动标注"未验证"）
- 新角色接入：使用既有机制原语的角色 = 写数据 + 写效果，不改模拟器；出现全新机制时只扩展 Mechanics Core 的通用 Effect/Trigger primitive，**禁止角色 ID 特判**（`if cid == "1015"`）

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

**L5 Optimization & Application（优化与落地层，P1/P2 实现，本蓝图先定型）**：Evaluator（Score / Constraints / Diagnostics）→ Optimizer（Panel Search / Policy Search）→ LLM Adapter（用户意图 / 约束翻译 / 结果解释）。依赖方向不变：一切优化都建立在 L1-L4 可信之上。

## 四、L1 数据真值层

### 4.1 溯源 schema

`data/normalized/` 下每个数值字段可带溯源包装：

```json
{
  "id": "1306",
  "name": "花火",
  "element": { "value": "Quantum", "source": "starrailres", "source_trust": "B", "version": "index_min@2026-07", "field": "characters.element", "validation": "mapped" },
  "base_stats": {
    "hp":  { "value": 1397.1, "source": "starrailres", "source_trust": "B", "version": "index_min@2026-07", "field": "character_promotions.base_hp", "validation": "mapped" }
  },
  "skills": {
    "skill": {
      "advance_pct": { "value": 0.5, "source_trust": "A", "version": "tbgd@<commit>", "field": "AvatarSkillConfig.ParamList[3]", "validation": "raw", "note": "参数位语义待核对" },
      "mult":        { "value": 0.0, "source": "biligame", "source_trust": "C", "version": "biligame-2026-08", "field": "", "override": true, "validation": "cross_checked", "note": "解包参数位待核对" }
    }
  }
}
```

字段语义：
- `value`：数值本身
- `source`：具体来源名（datamine / starrailres / biligame / handfill）
- `source_trust`：来源信任等级 A/B/C/D（见 4.2）
- `validation`：验证状态 raw / mapped / cross_checked / game_verified（见 4.2）
- `version`：上游数据源固定版本（GitHub commit / 日期），保证可重跑
- `field`：上游原始字段路径（如 `AvatarSkillConfig.ParamList[3]`）
- `override`：true 表示该值经人工修正/覆盖了上游值（记录 note 说明理由）

允许的简写：纯数字 = 继承所在块的 `_default_source`；`_default_source` 缺失的块在数据审计中报错。**任何无溯源或无验证状态的字段无法通过 L1 审计门禁**。

### 4.2 信任模型：来源信任 × 验证状态

原始数据可信 ≠ 你对原始数据的解释可信：`AvatarSkillConfig.ParamList[3] = 0.5` 确实来自解包，但 "[3] 就是 advance_pct" 这一步映射完全可能错。因此拆成两个正交维度：

**来源信任（Source Trust）**——值从哪里来：

| 等级 | 含义 | 举例 |
|---|---|---|
| A | datamine 解包 | TurnBasedGameData（固定 commit） |
| B | 社区整理数据 | StarRailRes（固定 commit） |
| C | 社区 wiki 人工核对 | biligame 核对（ADR-0003 修正项） |
| D | 手填/模板/假设 | 光锥模板、敌人模板值、迷迷倍率 |

**验证状态（Validation Status）**——值是否被正确理解：

| 状态 | 含义 |
|---|---|
| raw | 刚从上游提取，字段语义未核对（如 ParamList[3]=0.5 未确认就是 advance_pct） |
| mapped | 已映射到领域字段，映射经人工确认 |
| cross_checked | 与第二来源（wiki / 另一实现）交叉验证一致 |
| game_verified | 游戏内实测一致 |

可进入"可信结果"的组合：**source_trust ∈ {A,B,C} 且 validation ∈ {mapped, cross_checked, game_verified}**；source_trust=D 或 validation=raw 的值 → 计算结果自动标注"未验证"。ETL 最大的风险不是数据源造假，而是字段语义映射错——mapping 必须人工确认后才可提升为 mapped。

### 4.3 ETL 管线

`scripts/etl/` 三阶段，全部可全量重跑：

1. **fetch**：按固定版本（上游 repo commit）拉取 StarRailRes（index_min/cn）与 TurnBasedGameData 所需表，落 `data/raw/<source>@<version>/`；版本记录写 `data/raw/VERSIONS.json`
2. **extract**：从 raw 提取面板计算所需字段（按 `docs/data-schema.md` 第四节的优先级清单），生成带溯源包装的 `data/normalized/*.json`
3. **audit**：`python -m hsr_sim.data audit` 校验：所有字段有溯源与验证状态、版本一致、引用完整（如技能 ID 前缀=角色 ID）、无 source_trust=D 或 validation=raw 的值混入"可信路径"而未标记

首批 ETL 范围（P0）：4 角色（1015/1306/1309/8007）+ 敌人 + HardLevelGroup 难度系数 + 轮次规则。红A（1015）联动数据上游缺失（ADR-0003），维持 wiki 手填 + `override` 标记，属已知例外。

### 4.4 遗留数据迁移

`data/characters/` 手填 JSON 迁移到 `data/legacy/` 冻结，作为 ETL 输出正确性的对照样本；`data/enemy_elite90.json` 的"模板值"标注 `source_trust=D`（handfill），直到 ETL 产出真实敌人。

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

### 5.3 事件语义（Event Semantics，P0-2 先钉死）

Effect 类型清单只是骨架；决定模拟器长期存活的是事件语义。不定义清楚，Effects 执行器只是把现在 simulate.py 的 if/else 原样搬进去。以下十项在写执行器之前以 Mechanics Spec 文档逐条落档，附 T3/T4 对账依据；无法确定的条目标记 UNKNOWN 进入实测清单，不允许拍脑袋定：

- **Action lifecycle**：一次行动从开始到结束的阶段划分（伤害前/后、buff 施加、SP/能量结算、削韧、击破、拉条的固定次序）
- **Event phases**：触发时机锚点（on_ally_attack 是伤害前还是伤害后；击破发生在伤害前还是后）
- **Trigger timing**：追加攻击/触发效果的插入位置（当前行动结算后？队列原位？）
- **Priority & tie breaking**：同时触发的多个效果谁先执行
- **Duration clock**：buff 计时时钟（施加者行动次数？全局轮次？）
- **Stacking**：同类效果叠加/刷新规则
- **Target selector**：目标解析语法（主目标/全体/自身/随机/最近）
- **Cancellation**：死亡/离场后当前 action 剩余 effects 是否继续执行
- **Re-entrancy**：触发链中再触发的深度与环路保护
- **Extra action vs 100% advance**：不占行动条 vs 清零剩余距离的语义区分

### 5.4 兼容垫片与回归对等

重构分两步走，避免大爆炸：

- **Step A（P0-3 内）**：定义 Effect 类型 + 翻译器 `legacy_skill_to_effects(SkillData) -> Effects[]`，把现有 v1.5 数据翻译成 Effects 喂给新循环。验收：**红A场景逐行动输出与 v1.5 完全一致**（行动时间/伤害/SP/能量逐项相等），差异即为 bug。
- **Step B（P0-3 内）**：翻译器下线，`data/characters/*.json` 直接写 effects。角色 JSON 与 L1 溯源 schema 合并（effects 内数值同样带 source_trust + validation 双维溯源）。

## 六、L3 模拟器与机制核心

保留**架构形态**，不保留公式正确性假设：`av_queue.py`（AV 模型通用、无角色耦合）、`damage.py` 纯函数架构（公式正确性见 6.1，整体 UNTRUSTED UNTIL VERIFIED）、`buffs.py`（stat/cap/target/duration 语义已是效果化）。

### 6.1 公式审计清单（P0-2 先锁公式；本 ADR 不写终值，全部以 T3/T4 对账为准）

现有 damage.py 不只是"击破系数待核"——对账评审新增以下项目，均经代码核实：

1. **攻击者等级 80/90 混淆**：Simulator 与 `expected_damage()` 默认 `attacker_level=90`，但星铁角色等级上限为 80（击破基础伤害 3767.5 是 80 级角色乘区而非 90 级）。当前实现把"敌人等级 90"与"攻击者等级 80"混用，防御乘区与击破系数同时受影响。
2. **负抗性减半是原神规则**：`resistance_multiplier()` 对负抗性取 `1 - res/2`（穿透溢出收益减半），星铁公式为 `1 - (目标抗性 - 抗性穿透)`，无减半段。注意 `tests/test_damage.py::test_penetration_overflow_half` 正在固化该错误规则，T1 需同步修正。
3. **击破韧性公式方向修正**：v2 采用 Max Toughness Multiplier（基于**最大**韧性），参考公式 `0.5 + 最大韧性 / 120`（Fribbels 实现同款）；此前草案误写"当前韧性/最大韧性参与"，已更正。`break_damage()` 未使用的 `enemy_toughness_max` 参数正是为此准备。
4. **Broken Multiplier 缺失**：星铁普通伤害在敌人韧性未破时存在 0.9 乘区（击破后 1.0）；`expected_damage()` 未实现——`docs/game-knowledge.md` 2.6 已记录"韧性未破时受到伤害降低"，公式层遗漏。
5. **击破等级系数**：`BREAK_LEVEL_COEF` 按正确等级语义重核（见第 1 条），确认系数对应等级。
6. **乘区时间轴化**：`Multipliers` 静态值改为由 buff 时间轴驱动（BuffManager 已有 duration 语义，补事件驱动递减）。

### 6.2 信任度信封

**信任度信封**：`SimResult` 增加 `trust_level` 与 `unverified_inputs: [字段路径]`。任何 source_trust=D 或 validation=raw 的输入参与的计算，报告顶部强制标注"未验证（原因：…）"，LLM 迭代反馈中同步携带该标记——验证器不再"精确地验证不精确的世界"而不自知。

## 七、L4 对账体系（测试分层）

| 层 | 内容 | 现有基础 |
|---|---|---|
| T1 单元测试 | 公式自洽、队列行为、面板装配 | 现有 48 个全部保留（它们是合格的单元测试） |
| T2a 重构对等（Legacy Parity） | v2 输出 == v1.5 输出（逐行动），目的：证明重构未改变行为——**不证明行为正确**（v1.5 本身含 6.1 的错误） | 重构后首次全绿即锁定为 parity baseline |
| T2b 黄金对账（Golden Oracle） | v2 输出 == 已验证预期值（6.1 审计后的公式 + 人工核算），目的：证明游戏正确性 | 公式审计后生成，**不自动锁定** |
| T3 交叉对账 | 与成熟模拟器逐项对比（fribbels/hsr-optimizer 对伤害，Honkai-Star-Rail-Simulator 对 AV），**不比总量，比首次出现差异的行动** | 待选型 |
| T4 游戏实测 | 固定场景实测协议：等级/面板/技能等级/敌人等级抗性防御/buff 全固定 → 游戏内录数（击破 3767.5、迷迷倍率、真伤 10% 等全部待核值优先）→ 模拟器误差 ≤ 0.1% | 待建 |

P0 通过标准：**红A固定队 + 固定靶场，T2b 全绿（T2a 仅作重构安全网）；所有 P0 输入 source_trust ∈ {A,B,C} 且 validation ≥ mapped；validation=raw 或 source_trust=D 的值全部在 T4 实测清单上并已标记**。此时"simulate(snapshot) 可信"成立。

## 八、迁移路径与里程碑

### P0：地基（目标：对账通过）

| 步骤 | 内容 | 验收 |
|---|---|---|
| P0-0 | 冻结 v1.5 baseline（commit `4513792` 已确认，工作区干净）；清理杂散文件（如根目录 `nul`）；重构分支从该 commit 切出 | 回退点 = `4513792` |
| P0-1 | Provenance + ETL 首批（4 角色 + 敌人 + 难度系数）：source_trust × validation 双维溯源 + audit 门禁 | `hsr_sim.data audit` 全绿；红A队数据全部 A/B 且 ≥ mapped，例外仅红A（C+override） |
| P0-2 | Mechanics Spec：锁公式（6.1 清单逐项定值，UNKNOWN 入实测清单）+ 事件语义（5.3 十项逐条落档） | 每项公式有来源与对账依据；事件语义无未决项 |
| P0-3 | Effects + Event Engine：翻译器垫底 → 通用事件循环 → 数据直写 effects（翻译器下线） | T2a Legacy Parity 全绿（与 v1.5 逐行动一致）；随后数据层无 SkillData 旧字段、`talent_extra` 删除 |
| P0-4 | Formula/Mechanics Oracle Tests（T2b）：DEF / RES / Broken / Break / AV / SP / Energy 逐项 | T2b 全绿；报告信任度标注正确 |
| P0-5 | Cross-validation（T3）：Fribbels / 其他实现逐行动对比 | 首次差异定位到具体机制与字段 |
| P0-6 | In-game verification（T4）：实测协议执行（击破系数 / 迷迷倍率 / 真伤 / Broken 乘区等） | 全部 D/raw 值出清 → **simulate(snapshot) = TRUSTED** |

### P1：真实环境 + 数值优化器

- 真实关卡 ETL（StageConfig / ChallengeMazeConfig / MazeBuff / BattleEvent）：靶场从手填模板换真实 MoC 层
- 光锥/遗器套装 ETL：光锥模板退役，按具体光锥+叠影装配（build.py 适配）
- Panel Search：词条空间（atk/spd/cr/cd/break/energy，sum ≤ 30，主词条离散集合）是明确的数学空间，**程序确定性搜索**（贪心/束搜索），不依赖 LLM
- Policy Search：保留 v1.5 实现，挂在 Optimizer 下

### P2：LLM 外层化 + 输入适配器

- LLM 职责收缩：解释用户意图/约束、输出方案约束与策略偏好、解读诊断；**不承担核心数值搜索**（修复震荡问题的最优解是让它别干这个活）
- 米游社适配器（ADR-0002 后补项）接入，schema 不变

## 九、重构边界（保留 / 重写 / 废弃）

**保留**（架构形态）：`av_queue.py`、`damage.py` 纯函数架构（**不保留公式正确性假设**，见 6.1）、`buffs.py`、`policy_search.py`、`build.py` 的词条预算审计逻辑、全部 48 个单测（T1，其中固化错误规则的断言按 6.1 修正）。

**重写**：`model.py` 的 SkillData/CharacterData（→ effects 模型 + 溯源 schema）、`simulate.py`（→ 通用事件循环）、`build.py` 光锥装配（等 P1 真实光锥数据）。

**废弃/冻结**：`data/characters/` 手填格式（→ `data/legacy/` 对照样本）、`Multipliers` 静态值用法（→ 时间轴驱动）、`enemy_elite90.json` 模板值（→ source_trust=D 标记，P1 替换）。

**v1.5 代码定位**：第一版实验代码与测试素材，不是稳定基础设施——不删，不信任。

## 十、决策与备选

- **决策**：先定架构与边界，再动代码（本文档即交付物）；重构以"逐行动输出对等"为唯一安全网，禁止大爆炸式重写
- **决策（评审修订）**：damage.py 整体 UNTRUSTED UNTIL VERIFIED——公式正确性不随架构沿用；datamine 来源 ≠ 语义已验证（双维信任模型，mapping 必须人工确认）
- **被否**：继续修 LLM 震荡/best-so-far/扩大策略搜索（在不可信地基上叠优化）；LLM 作为核心搜索方法（词条是明确数学空间，程序搜索更优，LLM 退居外层）
- **被否**：一次性迁移全部机制（分两步走，翻译器垫底，回归对等后再换数据格式）
- **被否**："Simulator 永远不改"（禁止的是 `if cid == "1015"` 特判，而非禁止新增机制代码——全新机制应扩展通用 primitive）

## 十一、风险

- 红A 联动数据上游长期缺失 → 维持 wiki 手填 + override，T4 实测协议兜底
- 击破韧性系数等公式细节无文档 → 以 T3 交叉对账 + T4 实测锁定，不在文档里猜
- 重构期间 v1.5 功能不可用 → P0-3 期间保留 v1.5 分支可回退（基线 = commit `4513792`）
- 对账口径争议（期望值 vs 实测值） → T2/T4 一律记录单次确定性伤害（非期望），期望值只用于优化层
- T1 测试固化错误公式（负抗减半） → 与 6.1 审计同步修正断言，避免"测试证明错误公式"
- attacker_level 默认值修正会改变全部既有伤害数值 → 属预期行为变更：T2a（与 v1.5 对等）先过，再进 T2b（新基线）
