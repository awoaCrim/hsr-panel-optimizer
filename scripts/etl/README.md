# scripts/etl — ETL 管线（ADR-0006 L1 数据真值层，P0-1 已实现）

从两个上游数据源提取面板计算所需字段，产出**带双维溯源**（source_trust × validation）的精简数据。

## 用法

```bash
python scripts/etl/fetch.py             # 下载固定版本原始数据 → data/raw/<Source>@<sha>/（幂等）
python scripts/etl/fetch.py --force     # 全部重下
python scripts/etl/extract.py           # raw → data/normalized/（带溯源包装 + 与 v1.5 手填交叉核对）
python -m hsr_sim.data audit            # 审计门禁：溯源合法 + 版本一致 + 引用完整（P0-1 验收）
python -m hsr_sim.data paths            # 列出全部未验证字段（信任度信封预览）
```

## 数据源与固定版本（sources.json）

| 源 | 仓库 | 分支 | 用途 |
|---|---|---|---|
| StarRailRes | Mar-7th/StarRailRes | master | 角色/技能倍率/光锥/遗器（社区整理） |
| TurnBasedGameData | DimbreathBot/TurnBasedGameData | main | 解包数值：技能/敌人/难度系数 |

版本以 **commit sha 钉死**（sources.json），fetch 按 sha 下载，extract 从 VERSIONS.json 定位 —— 全量可重跑、可复现。

## 溯源策略（extract.py）

- **基础面板**：StarRailRes promotions，L80 = base + step×79（攻击者等级上限 80，非 90）
- **削韧**：AvatarSkillConfig.StanceDamageDisplay（显式字段）→ A/mapped
- **技能倍率**：basic 槽位 params[9][0]（通用约定）→ B/mapped；**其余槽位参数位语义因角色而异，不猜测**——保留 wiki 值（C），上游参数整体记入 `_upstream_params` 供 P0-2 Mechanics Spec 锁定
- **SP/能量/延时/拉条**：解包字段语义未锁定（SPMultipleRatio/BPNeed/DelayRatio/回能参数位），维持 ADR-0003 wiki 核对值（C/cross_checked）
- **红A（1015）**：AvatarSkillConfig 无联动数据（0 条），全部 wiki + override 标记（ADR-0003）
- **敌人**：v1 模板值 → D（P1 由 StageConfig/MonsterConfig 替换）

## 已知差异（extract 交叉核对报告）

- 基础面板：仅四舍五入差异（手填 1 位小数 vs 上游 3 位）
- 8007 大招削韧：手填 0 ≠ 解包 20（手填遗漏，P0-2 确认后以解包为准）
- 花火战技 130602 上游参数 [0.24, 0.45, 1, 0.5]：`[3]=0.5` 即拉条 50%（与 wiki 一致），其余参数位待 P0-2 落档
