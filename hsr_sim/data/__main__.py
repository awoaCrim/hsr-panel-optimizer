"""python -m hsr_sim.data ... 入口。"""
import sys

from .audit import main as audit_main


def main(argv) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        return audit_main(argv[1:])
    if cmd == "paths":
        from .loader import load
        data = load()
        for p, trust, val in data.unverified_paths():
            print(f"~ {p}  [{trust}/{val}]")
        print(f"共 {len(data.unverified_paths())} 处未验证")
        return 0
    print(f"未知子命令 {cmd!r}（可选：audit / paths）")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
