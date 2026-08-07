# 0001 自研轻量前向模拟器，不复用 Tactical-Engine

v1 的需求是"验证 LLM 给定的循环"（前向模拟），而非"搜索最优循环"（DFS+剪枝，那是后续阶段）。Tactical-Engine 的 search_engine.py（751 行）是为后者设计的，带事件总线与剪枝复杂度；直接复用意味着学习成本 + 带病入库（其数据为 Mock）。故自研 ~500 行前向模拟器（AV 队列 + 伤害乘区 + SP 追踪），Tactical-Engine 留到"AI 自动搜索循环"阶段再评估集成。

参考：搜索能力后续可通过把前向模拟器作为 `evaluate_path` 注入 Tactical-Engine 的 SearchEngine 复用，两者解耦。
