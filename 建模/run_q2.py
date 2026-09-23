"""Question two entry point; paths are relative to this project, not cwd."""
from pathlib import Path
import argparse
import sys

PROJECT = Path(__file__).resolve().parent
# Anaconda's optional binary extensions may target NumPy 1.x. Keep the prepared
# NumPy 2 / OR-Tools runtime isolated instead of modifying the user's Anaconda.
_bundled_python = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
if (__name__ == "__main__" and (PROJECT / ".runtime/q2/ortools").is_dir()
        and _bundled_python.is_file() and Path(sys.executable).resolve() != _bundled_python.resolve()):
    import subprocess
    raise SystemExit(subprocess.call([str(_bundled_python), str(Path(__file__).resolve()), *sys.argv[1:]]))
for folder in (PROJECT / "src", PROJECT / ".runtime/python", PROJECT / ".runtime/q2"):
    if folder.is_dir():
        sys.path.insert(0, str(folder))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="问题二：多点运输与无人机、电池联合调度")
    parser.add_argument("--config", default="configs/q2.toml")
    parser.add_argument("--output")
    parser.add_argument("--skip-excel", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--budget-seconds", type=float, help="按比例缩放各阶段预算，用于快速核验")
    parser.add_argument("--report-only", action="store_true", help="从已保存计算结果重新导出报告和表格")
    parser.add_argument("--resume", action="store_true", help="从本输出目录的已验证检查点继续搜索")
    parser.add_argument("--verify-only", action="store_true", help="从原始输入独立重算已保存方案，无需求解器搜索")
    args = parser.parse_args()
    if args.test:
        import unittest
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(PROJECT / "tests")))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    from uav_rescue.q2.pipeline import run
    config = Path(args.config)
    run(PROJECT, config if config.is_absolute() else PROJECT / config, args)


if __name__ == "__main__":
    main()
