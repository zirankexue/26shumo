"""Run from any working directory: python /path/to/run_q1.py --config configs/q1.toml."""
from pathlib import Path
import argparse
import sys

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))
if (PROJECT / ".runtime" / "python").is_dir():
    sys.path.insert(0, str(PROJECT / ".runtime" / "python"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="问题一：物理运力与精确组批")
    parser.add_argument("--config", default="configs/q1.toml")
    parser.add_argument("--output", help="覆盖配置输出目录；相对工程根目录")
    parser.add_argument("--skip-excel", action="store_true", help="仅生成计算结果与图表")
    parser.add_argument("--test", action="store_true", help="只运行单元测试")
    args = parser.parse_args()
    if args.test:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    from uav_rescue.q1.pipeline import run
    config = Path(args.config)
    if not config.is_absolute():config = PROJECT / config
    run(PROJECT, config, args.output, args.skip_excel)


if __name__ == "__main__":
    main()
