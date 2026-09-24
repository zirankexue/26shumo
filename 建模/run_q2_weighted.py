"""Weighted Q2 extension; preserves the archived lexicographic experiment."""
from pathlib import Path
import argparse
import sys
import run_q2

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='configs/q2_weighted.toml')
    parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--budget-seconds',type=float)
    parser.add_argument('--iterations',type=int)
    args=parser.parse_args()
    from uav_rescue.q2.weighted_run import run
    run(run_q2.PROJECT,args)

if __name__=='__main__':
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if runtime.exists() and Path(sys.executable).resolve()!=runtime.resolve():
        import subprocess
        raise SystemExit(subprocess.call([str(runtime),str(Path(__file__).resolve()),*sys.argv[1:]]))
    main()
