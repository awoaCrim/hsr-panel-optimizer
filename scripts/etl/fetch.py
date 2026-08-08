"""fetch —— 下载固定版本的上游原始数据到 data/raw/<Source>@<sha>/。

用法：
    python scripts/etl/fetch.py            # 拉取缺失文件（可重跑，幂等）
    python scripts/etl/fetch.py --force    # 全部重下
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES = json.loads((Path(__file__).parent / "sources.json").read_text(encoding="utf-8"))
RAW_DIR = ROOT / "data" / "raw"
VERSIONS_FILE = RAW_DIR / "VERSIONS.json"


def _opener() -> urllib.request.OpenerDirector:
    """代理探测（本地 clash 常见端口），失败回退直连。"""
    import urllib.request as u
    for port in (7897, 7890, 10809):
        try:
            proxy = u.ProxyHandler({"http": f"http://127.0.0.1:{port}",
                                    "https": f"http://127.0.0.1:{port}"})
            op = u.build_opener(proxy)
            with op.open("https://static.nanoka.cc/hsr/4.4.54/lightcone.json", timeout=5) as r:
                r.read(64)
            return op
        except Exception:
            continue
    return u.build_opener()   # 直连兜底


_OPENER = None


def fetch_file(url: str, dest: Path, force: bool) -> bool:
    global _OPENER
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _OPENER is None:
        _OPENER = _opener()
    req = urllib.request.Request(url, headers={"User-Agent": "hsr-panel-optimizer-etl"})
    with _OPENER.open(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return True


def main(argv) -> int:
    force = "--force" in argv
    versions = {}
    if VERSIONS_FILE.exists():
        versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))

    for name, cfg in SOURCES.items():
        if name.startswith("_"):
            continue
        tag = f"{name}@{cfg['sha']}"
        fetched = 0
        for rel in cfg["files"]:
            if "base_url" in cfg:
                # 直连数据接口（如 static.nanoka.cc 静态 JSON）
                url = f"{cfg['base_url'].rstrip('/')}/{rel}"
            else:
                url = f"https://raw.githubusercontent.com/{cfg['repo']}/{cfg['sha']}/{rel}"
            dest = RAW_DIR / tag / rel
            if fetch_file(url, dest, force):
                fetched += 1
                print(f"↓ {name} {rel} ({dest.stat().st_size}B)")
        versions[name] = {
            "repo": cfg.get("repo", ""),
            "branch": cfg.get("branch", ""),
            "sha": cfg["sha"],
            "base_url": cfg.get("base_url", ""),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        print(f"{name}: {cfg['files'].__len__()} 个文件，新下载 {fetched}")

    VERSIONS_FILE.write_text(
        json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"VERSIONS.json 已更新（{VERSIONS_FILE}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
