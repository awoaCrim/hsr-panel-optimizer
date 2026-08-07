# 0003 红A（1015 联动角色）技能数值：社区 wiki 手填 + 标注待验证

实测 TurnBasedGameData `AvatarSkillConfig.json` 中 1014/1015（Fate 联动角色）技能数据完全缺失（同队花火 1306/知更鸟 1309/记忆主 8007 均齐全）；StarRailRes 仅有倍率（params）无能量/SP/行动延时。决策：倍率用 StarRailRes，能量/SP/行动延时从社区 wiki 人工核对手填，数据文件标注来源与"待验证"标记；等 TurnBasedGameData 更新后回填真值。

## 2026-08 更新：biligame wiki 核对结果

- **能量上限 220 确认**（wiki"速度 105 能量上限 220"与 StarRailRes max_sp 一致）
- **倍率等级修正**：此前误用数据表最高级（L15，含命座/特殊加成），游戏内满级为 **L10**。已全角色修正为 L10（红A 战技 360%/大招 1000%，知更鸟战技 50%，花火天赋 6%/层 等），StarRailRes params[9] 与 wiki 交叉验证一致
- **削韧修正**：红A 战技 20、大招 30（此前 30/60）
- **天赋补充**：红A 追击回能 5（此前遗漏）
- 待回填：TurnBasedGameData 更新联动数据后以解包值为准
