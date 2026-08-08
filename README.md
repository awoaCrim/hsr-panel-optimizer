# 星穹铁道 AI 战斗推演与面板优化（HSR Panel Optimizer）

> **v2 定位（ADR-0007）**：**战斗推演模拟引擎**——高度模拟星穹铁道官方战斗逻辑，LLM 作为指挥者全程掌控推演（逐步决策、可回退任意行动、分支探索，用户不操作战斗流程）；面板优化为上层应用之一。
> **v1.5 工作模式（面板优化应用）**：LLM 提出面板方案 → 程序用精确公式验证 → 多轮迭代收敛。
> 目标：为指定队伍计算"全队最佳面板属性"（v1 队伍：红A + 花火 + 知更鸟 + 记忆主）。

## 快速开始

```bash
python -m hsr_sim.cli verify                # 验证默认方案（红A队 / 90级双精英 / 2T）
python -m hsr_sim.cli verify --json         # 结构化输出（LLM 迭代的输入格式）
python -m hsr_sim.cli verify --llm          # LLM 自动迭代（最多 5 轮，见下）
python -m pytest tests/                     # 对账测试（手算用例）
```

## LLM 自动迭代（--llm）

```bash
set HSR_LLM_BASE_URL=https://api.deepseek.com/v1   # OpenAI 兼容接口
set HSR_LLM_API_KEY=sk-xxx
set HSR_LLM_MODEL=deepseek-chat
python -m hsr_sim.cli verify --llm --rounds 5
```

每轮：LLM 输出面板方案（主词条 + 副词条分配）→ 程序按标准词条价值装配面板并验证（伤害/SP/能量/速度断点/词条预算）→ 反馈诊断 → LLM 调整，直到击杀且约束全达标。

## 目录结构

```
hsr_sim/
├── model.py          # 领域模型（Stats/SkillData/Character/Enemy/Rotation）
├── build.py          # 面板装配器（主词条+副词条 → 面板；词条预算审计 30）
├── loader.py         # 输入加载层（JSON→模型，输入层解耦点：米游社适配器后补）
├── engine/
│   ├── av_queue.py   # 行动队列（AV 模型：拉条/推条/速度变化/额外行动）
│   ├── damage.py     # 伤害公式（全乘区：暴击/防御/抗性/真伤/附加/击破）
│   ├── buffs.py      # 简化 buff（乘区加成，target/cap/计时）
│   └── simulate.py   # 前向模拟器（执行循环→伤害/SP/能量/行动记录）
├── report.py         # 验证报告（主指标+约束清单+诊断）
└── cli.py            # CLI（verify；--llm 自动迭代为下一阶段）
data/
├── characters/       # 角色技能数值（v1.5 手填，已冻结为 legacy；真实数据见 normalized/）
├── team_reda.json    # 队伍面板方案（LLM 迭代对象）
├── enemy_elite90.json# 靶场（90 级双精英 + 2T，模板值）
├── rotation.json     # 循环（基线）
├── normalized/       # ETL 产物：带双维溯源的精简数据（ADR-0006 L1，ETL 生成）
└── raw/              # 上游原始数据缓存（固定 sha，.gitignore 不跟踪；VERSIONS.json 保留）
scripts/
├── etl/              # ETL 管线：fetch（固定版本下载）→ extract（溯源化精简）
└── ...

## 数据层（P0-1 已实现）

```bash
python scripts/etl/fetch.py      # 下载固定版本上游数据 → data/raw/
python scripts/etl/extract.py    # → data/normalized/（带 source_trust × validation 双维溯源）
python -m hsr_sim.data audit     # 审计门禁：溯源合法 / 版本一致 / 引用完整
python -m hsr_sim.data paths     # 列出全部未验证字段（信任度信封）
```
docs/                 # 游戏规则大纲 + 数据字段清单 + ADR
CONTEXT.md            # 领域术语表
```

## 关键文档

- `docs/game-knowledge.md` — 给 AI 的游戏信息大纲（规则书 + prompt 模板）
- `docs/mechanics-spec.md` — 机制规格：伤害公式定值（fribbels/实测依据）+ 事件语义十二项（P0-2 交付物）
- `docs/data-schema.md` — 数据源字段清单（StarRailRes / TurnBasedGameData）
- `docs/adr/` — 架构决策（自研引擎 / 输入层解耦 / 红A数据来源 / 机制范围）；**0006 = v2 地基重构蓝图（五层架构 + 数据溯源 + Effects 模型 + 对账体系）**；**0007 = 战斗推演模拟引擎（可回退任意行动 + LLM 交互式指挥）**
- `CONTEXT.md` — 领域术语表

## 已知限制（v1）

- 面板方案以词条分配形态（main_stats + substats）输入，程序按标准词条价值（攻击4.32%/速2.4/暴击3.24%/暴伤6.48%/充能3.24%）装配面板并审计 30 词条预算——LLM 无法再输出超出可实现范围的面板
- 光锥按统一模板（攻击+582、攻击+20%）简化，未按具体光锥区分（ETL 阶段精确化）
- 击破系数 90 级 3767.5、迷迷倍率/真伤 10% 等为社区值，标注待验证
- 受击回能、敌人攻击结算、buff 覆盖率模型未实现（确定性模型）
- 靶场 110 万 HP 双精英对 30 词条面板偏难：可能 5 轮内不收敛（输出最近方案+差距清单），属真实约束
- `--llm` 自动迭代：已实现（需配 API Key，见上）
