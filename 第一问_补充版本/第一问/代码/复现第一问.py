"""Keep existing solver paths valid while exposing the requested question folder."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='第一问求解与论文材料复现入口；默认仅整理已有结果。')
    parser.add_argument('--step', choices=['materials', 'baseline', 'pareto', 'reserve', 'solve-all'], default='materials')
    args = parser.parse_args()
    solve = {'baseline': 'code/solve_q1_batching.py', 'pareto': 'code/solve_q1_multiobjective.py', 'reserve': 'code/solve_q1_reserve_sensitivity.py'}
    material_scripts = ['prepare_q1_paper_data.py', 'plot_q1_geography.py', 'make_q1_paper_figures.py', 'build_paper_package.py']
    if args.step == 'materials':
        scripts = [ROOT / '第一问' / '代码' / name for name in material_scripts]
    elif args.step == 'solve-all':
        scripts = [ROOT / name for name in solve.values()]
    else:
        scripts = [ROOT / solve[args.step]]
    for script in scripts:
        subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True,
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        if script.name == 'solve_q1_batching.py':
            subprocess.run([sys.executable, str(ROOT/'code/verify_q1_local_plane.py'), '--geometry-only'],
                           cwd=ROOT, check=True, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})


if __name__ == '__main__':
    main()
