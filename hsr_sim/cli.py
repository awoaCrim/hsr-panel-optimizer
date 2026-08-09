"""CLI 入口：验证 LLM 面板方案。

用法：
  python -m hsr_sim.cli verify                       # 用默认数据（红A队/90级双精英/基线循环）
  python -m hsr_sim.cli verify --team x --rotation y # 指定输入文件
  python -m hsr_sim.cli verify --json                # 完整结构化输出
  python -m hsr_sim.cli verify --llm                 # LLM 自动迭代（下一阶段，见 ADR 规划）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from .engine.simulate import Simulator
from .loader import DATA_DIR, load_enemies, load_rotation, load_substat_counts, load_team
from .model import Stats
from .report import ValidationReport, build_report, format_report


def _report_dict(report: ValidationReport) -> Dict[str, Any]:
    return {
        "score": report.score,
        "enemy_total_hp": report.enemy_total_hp,
        "constraints": [
            {"name": c.name, "met": c.met, "detail": c.detail} for c in report.constraints
        ],
        "diagnostics": report.diagnostics,
        "all_met": report.all_met,
        "sim": {
            "t_end": report.result.t_end,
            "total_damage": report.result.total_damage,
            "damage_by_source": report.result.damage_by_source,
            "damage_by_kind": report.result.damage_by_kind,
            "sp_min": report.result.sp_min,
            "sp_timeline": report.result.sp_timeline,
            "ult_count": report.result.ult_count,
            "action_count": report.result.action_count,
            "breaks": report.result.breaks,
            "enemy_hp_left": report.result.enemy_hp_left,
            "energy_shortfalls": report.result.energy_shortfalls,
        },
    }


def cmd_verify(args: argparse.Namespace) -> int:
    team_path = Path(args.team)
    enemy_path = Path(args.enemy)
    rotation_path = Path(args.rotation)

    if args.legacy:
        # v1.5 手填数据路径（data/characters/ + enemy_elite90.json，冻结）
        char_dir = team_path.parent / "characters"
        characters, stats, speed_targets = load_team(team_path, char_dir)
        enemies, level, target_av = load_enemies(enemy_path)
        unverified = []
    else:
        # 默认：normalized 数据层（P0-3 切换；带溯源与信任度信封）
        from .data.loader import load_enemies_normalized, load_team_normalized
        characters, stats, speed_targets, unverified = load_team_normalized(team_path)
        enemies, level, target_av, unv2 = load_enemies_normalized()
        unverified = unverified + unv2

    rotation = load_rotation(rotation_path)

    # 开局状态（标准规则 SP 4/能量 0；关卡配置可覆盖）
    _ed = json.loads(enemy_path.read_text(encoding="utf-8"))
    initial_sp = _ed.get("initial_sp", 4.0)
    initial_energy = _ed.get("initial_energy", {})

    mem_speed = 130.0
    for c in characters.values():
        mem = c.talent_extra.get("memosprite")
        if mem:
            mem_speed = mem.get("speed", mem_speed)

    sim = Simulator(characters, stats, enemies, rotation, target_av, level, mem_speed,
                    unverified_inputs=unverified, initial_sp=initial_sp,
                    initial_energy=initial_energy)
    result = sim.run()
    report = build_report(
        result,
        sum(e.hp for e in enemies.values()),
        speed_targets,
        {cid: s.speed for cid, s in stats.items()},
        substat_counts=load_substat_counts(team_path),
        substat_budget=json.loads(team_path.read_text(encoding="utf-8")).get("substat_budget", 30.0),
    )

    if args.json:
        print(json.dumps(_report_dict(report), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))

    if getattr(args, "search", False):
        from .engine.policy_search import search_policy, search_summary
        search = search_policy(characters, stats, enemies, level, target_av, mem_speed)
        print(search_summary(search))
        print("最优策略：" + json.dumps(
            {cid: {"ult": p.ult, "chain_max": p.chain_max, "skill_budget": p.skill_budget,
                   "pull_target": p.pull_target}
             for cid, p in search.best_policy.items()},
            ensure_ascii=False,
        ))
    return 0 if report.all_met else 1


def main(argv=None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(prog="hsr-sim", description="星穹铁道 AI 面板验证引擎")
    sub = parser.add_subparsers(dest="cmd")
    v = sub.add_parser("verify", help="验证面板方案")
    v.add_argument("--team", default=str(DATA_DIR / "team_reda.json"))
    v.add_argument("--enemy", default=str(DATA_DIR / "enemy_elite90.json"))
    v.add_argument("--rotation", default=str(DATA_DIR / "rotation.json"))
    v.add_argument("--json", action="store_true", help="输出完整 JSON")
    v.add_argument("--legacy", action="store_true",
                   help="使用 v1.5 手填数据（data/characters/，冻结）而非 normalized 数据层")
    v.add_argument("--llm", action="store_true", help="LLM 自动迭代（OpenAI 兼容接口，环境变量 HSR_LLM_BASE_URL/API_KEY/MODEL）")
    v.add_argument("--search", action="store_true", help="验证后枚举策略参数空间，输出当前面板下最优战斗策略")
    v.add_argument("--llm-config", default=None, help="LLM 配置 JSON 文件（可选，覆盖环境变量）")
    v.add_argument("--rounds", type=int, default=5, help="迭代轮数上限（默认 5）")
    args = parser.parse_args(argv)

    if args.cmd == "verify":
        if args.llm:
            return cmd_llm(args)
        return cmd_verify(args)
    parser.print_help()
    return 2


def cmd_llm(args: argparse.Namespace) -> int:
    """LLM 自动迭代：提方案 → 验证 → 反馈 → 收敛。"""
    import json as _json

    from .llm.client import LLMClient, default_config
    from .llm.loop import run_iteration

    cfg = default_config()
    if args.llm_config:
        try:
            with open(args.llm_config, encoding="utf-8") as f:
                cfg.update(_json.load(f))
        except FileNotFoundError:
            print(f"LLM 配置文件不存在：{args.llm_config}", file=sys.stderr)
            return 2
    try:
        client = LLMClient(cfg["base_url"], cfg["api_key"], cfg["model"],
                           disable_thinking=bool(cfg.get("no_thinking")))
        if not cfg["api_key"]:
            raise RuntimeError("未配置 API Key")
    except RuntimeError as e:
        print(f"❌ LLM 配置错误：{e}\n"
              "请设置环境变量 HSR_LLM_BASE_URL / HSR_LLM_API_KEY / HSR_LLM_MODEL\n"
              "（支持 OpenAI 兼容接口：DeepSeek / 智谱 / 通义 / OpenAI），或 --llm-config 指定 JSON",
              file=sys.stderr)
        return 2
    team_path = Path(args.team)
    result = run_iteration(
        client, team_path, Path(args.enemy), Path(args.rotation),
        max_rounds=args.rounds, verbose=True,
    )
    if args.json:
        summary = {
            "converged": result.converged,
            "rounds": result.rounds,
            "scores": [r.score for r in result.reports],
            "all_met_rounds": [r.all_met for r in result.reports],
            "final_proposal": result.final_team,
        }
        print(_json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.converged else 1


if __name__ == "__main__":
    sys.exit(main())
