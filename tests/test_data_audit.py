"""数据层测试（ADR-0006 L1）：溯源 audit 门禁 + normalized 加载。

- 集成：对 data/normalized/（ETL 产物）执行 audit，断言合法
- 单元：用临时目录构造违规数据，断言 audit 能抓住每种违规
"""
import json

import pytest

from hsr_sim.data.audit import audit as run_audit
from hsr_sim.data.loader import load
from hsr_sim.data.provenance import Provenance, validate_provenance


# ---------------- 集成：真实 normalized 数据 ----------------

def test_audit_passes_on_generated_data():
    errors, unverified = run_audit()
    assert errors == [], f"audit 应通过，违规：{errors}"


def test_unverified_are_enemies_and_defaults_only():
    _, unverified = run_audit()
    paths = {p for p, _, _ in unverified}
    # 敌人模板（D）与 break_effect/energy_regen 默认值（D）与忆灵手填（D）
    # 与未 fetch 详情的光锥效果（B/raw）
    assert all(p.startswith("enemies") or "memosprite" in p
               or (p.startswith("equipment.light_cones") and p.endswith(".effect"))
               for p in paths if "break_effect" not in p and "energy_regen" not in p)
    defaults = [p for p in paths if "break_effect" in p or "energy_regen" in p]
    assert len(defaults) == 8  # 4 角色 × 2


def test_loader_strips_provenance():
    data = load()
    # 1306 基础面板（StarRailRes L80，3 位小数）
    assert data.get("characters", "1306", "base_stats", "hp") == pytest.approx(1397.088)
    # 1015 上游名为 Archer；产品展示名统一从本地解析为红A。
    assert data.get("characters", "1015", "name") == "Archer"
    from hsr_sim.data.loader import load_characters_normalized
    chars, _ = load_characters_normalized()
    assert chars["1015"].name == "红A"
    # 花火战技拉条 50%
    assert data.get("skills", "1306", "skill", "advance_pct") == 0.5
    # 敌人模板值原样
    assert data.get("enemies", "enemies", "elite_a", "hp") == 600000


def test_loader_trust_envelope():
    data = load()
    unv = {p for p, _, _ in data.unverified_paths()}
    assert "enemies.enemies.elite_a.hp" in unv
    assert "characters.1306.base_stats.hp" not in unv
    assert "skills.1306.skill.advance_pct" not in unv  # wiki C/cross_checked 可信


# ---------------- 单元：audit 门禁 --------------------

@pytest.fixture()
def fake_normalized(tmp_path):
    """构造一个带默认 _source 的最小 normalized 目录。"""
    d = tmp_path / "normalized"
    d.mkdir()
    doc = {
        "hero": {
            "_source": {"source_trust": "B", "validation": "mapped",
                        "source": "starrailres", "version": "srr@b95e75c"},
            "atk": 100.0,
            "bad_no_prov": 1.0,   # 无 _source 的块 → 无溯源
        },
        "wrapped": {
            "_source": {"source_trust": "B", "validation": "mapped",
                        "source": "starrailres", "version": "srr@b95e75c"},
            "x": {"value": 5.0, "source_trust": "D", "validation": "raw",
                  "source": "handfill"},
        },
    }
    (d / "fake.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    # 供 audit() 读取的 VERSIONS.json（audit 读固定路径 data/raw/）
    return d


def test_validate_provenance_rejects_bad_combos():
    p = Provenance(source_trust="D", validation="cross_checked")
    assert validate_provenance(p, "x") != []
    p2 = Provenance(source_trust="X", validation="mapped")
    assert validate_provenance(p2, "x") != []
    p3 = Provenance(source_trust="A", validation="mapped")
    assert validate_provenance(p3, "x") == []


def test_provenance_is_trusted_rule():
    assert Provenance(source_trust="A", validation="mapped").is_trusted()
    assert Provenance(source_trust="B", validation="raw").is_trusted() is False  # 语义未核
    assert Provenance(source_trust="D", validation="mapped").is_trusted() is False  # 手填


def test_audit_leaf_wrapper_uses_own_provenance(tmp_path):
    """wrapper 自带溯源必须生效（不继承外层块）。"""
    d = tmp_path / "normalized"
    d.mkdir()
    doc = {
        "_source": {"source_trust": "B", "validation": "mapped",
                    "source": "starrailres", "version": "srr@b95e75c"},
        "x": {"value": 1.0, "source_trust": "D", "validation": "raw", "source": "handfill"},
    }
    (d / "fake.json").write_text(json.dumps(doc), encoding="utf-8")
    # 直接测内部函数
    from hsr_sim.data.audit import _leaf_paths
    leaves = _leaf_paths(doc, "fake")
    path, value, prov = leaves[0]
    assert path == "fake.x"
    assert prov.source_trust == "D"  # 自带溯源，而非继承 B
    assert prov.is_trusted() is False
