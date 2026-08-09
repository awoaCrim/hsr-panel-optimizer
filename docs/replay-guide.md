# 实战录像对账——数据收集指南（④）

对账框架：`python scripts/compare_replay.py replay.json`
（端点区间诚实对账：暴击判定不可复现，但每段伤害 ∈ {非暴击, 非暴击×(1+暴伤)} 区间内 = 公式一致）

## 三层渐进式（成本递增，可只做前两层）

### 第 1 层：面板（10 分钟）——验证面板装配 + 伤害公式
1. 游戏内 4 个角色逐个打开**角色详情**截图（属性页 + 光锥 + 遗器页）
   或 米游社 App → 战绩 → 角色详情（自动汇总面板）
2. 按下方模板填 `team` 段（光锥/套装/星魂/主词条/副词条缺一不可——影响机制）

### 第 2 层：行动序列（10-15 分钟）——验证行动顺序 + 终态
1. 打一场混沌回忆/模拟宇宙战斗，**录屏**（电脑 OBS / 手机自带录屏）
2. 回放按顺序记：`谁 → 技能(basic/skill) → 目标`，大招另记（ults 字段）
3. 记战斗结束时的：剩余行动值/轮数、敌人剩余 HP、我方死亡

### 第 3 层：伤害数字（可选）——验证公式端点
- 录屏逐帧暂停读伤害数字，每动作填 `damage`（战技/大招的总伤害）
- 每段伤害（红A 战技多段/迷迷 4 段）只记总和即可

## replay.json 模板

```json
{
  "meta": {"stage": "混沌回忆 12 上半", "date": "2026-XX-XX", "player": "你的UID"},
  "team": {
    "1015": {
      "build": {"light_cone": "23001", "relic_sets": [{"id": "102", "pieces": 4}, {"id": "306", "pieces": 2}], "eidolon": 5},
      "main_stats": {"body": "crit_rate", "feet": "speed", "sphere": "atk_pct", "rope": "atk_pct"},
      "substats": {"speed": 2, "crit_rate": 8, "crit_dmg": 16, "atk_pct": 4}
    },
    "1306": { "build": {"light_cone": "23003", "relic_sets": [{"id": "121", "pieces": 4}, {"id": "308", "pieces": 2}], "eidolon": 4}, "main_stats": {...}, "substats": {...} },
    "1309": { "build": {"light_cone": "23026", "relic_sets": [{"id": "102", "pieces": 4}, {"id": "312", "pieces": 2}], "eidolon": 1}, "main_stats": {...}, "substats": {...} },
    "8007": { "build": {"light_cone": "24005", "relic_sets": [{"id": "123", "pieces": 4}, {"id": "318", "pieces": 2}], "eidolon": 1}, "main_stats": {...}, "substats": {...} }
  },
  "enemy": {},      // 留空即可——告诉我打的层数，我从 TBGD 构建同款
  "actions": [
    {"unit": "1306", "skill": "skill", "target": "1015", "damage": 0},
    {"unit": "1015", "skill": "skill", "target": "elite_a", "damage": 52341, "ults": ["1015"]},
    {"unit": "8007", "skill": "skill", "target": "elite_a", "damage": 0}
  ]
}
```

### 字段说明
- `unit`：角色 id（1015 红A / 1306 花火 / 1309 知更鸟 / 8007 记忆主）
- `skill`：basic（普攻）/ skill（战技）
- `target`：敌人 id 或队友 id（拉条目标）
- `damage`：该动作造成**总伤害**（含连锁；记不住可省略 → 只验序列和终态）
- `ults`：本动作后释放大招的角色列表（**必填**——影响 RNG 消费和能量节奏；记不住可省略，但对账精度下降）
- `enemy`：留空 + 告诉我关卡（混沌回忆第几层/模拟宇宙哪个）→ 我从 TBGD 解包构建

## 对账输出解读
- `damage_ok` = 通过数；`damage_mismatch` = 实战值落在模拟端点区间外（公式差异）
- `seq_break` = 行动序列与实战不一致（决策/自动行为差异）
- 偏差项会给出 `sim_range`（模拟端点区间）与 `replay`（实战值）对照


## 米游社战绩自动转换（已有数据）

`python scripts/mihoyo_to_team.py 星穹铁道_角色战绩汇总.json` → `data/team_real.json`
（面板直填 + 光锥/套装/星魂/行迹等级自动装配；忆灵角色用忆灵暴击/暴伤反推——官方继承语义）

已验证：`data/team_real.json` 装配 = 真实配置（红A E2 理想燃烧的地狱 / 花火 E2 回到大地的飞行 /
知更鸟 E2 夜色流光溢彩 / 记忆主 E6 飞向粉色的明天精5，行迹等级含红A 普攻L6、记忆主 战技L5/大招L12/忆灵技L7）。
