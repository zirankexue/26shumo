"""Redraw the complete Q2 atlas without input workbooks, DEM, or optimization."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import sys

PROJECT=Path(__file__).resolve().parents[1]
for folder in (PROJECT/'.runtime/python',PROJECT/'.runtime/q2'):
    if folder.is_dir():sys.path.insert(0,str(folder))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--from-bundle',default='outputs/q2/plotting/data/plot_data.json')
    parser.add_argument('--output',default='outputs/q2_redraw')
    parser.add_argument('--font',default=None)
    parser.add_argument('--z-exaggeration',type=float,default=5)
    args=parser.parse_args()
    def resolve(p):
        p=Path(p);return p if p.is_absolute() else PROJECT/p
    source=resolve(args.from_bundle);output=resolve(args.output)
    if args.z_exaggeration<=0:parser.error('高程夸张系数必须为正')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    manifest=source.with_name('manifest.json')
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))['plot_data_sha256']!=digest:
        raise ValueError('绘图快照哈希不一致')
    module=Path(__file__).with_name('q2_figures.py')
    if not module.exists():module=PROJECT/'src/uav_rescue/q2/figures.py'
    spec=importlib.util.spec_from_file_location('q2_figure_renderer',module)
    plot=importlib.util.module_from_spec(spec);spec.loader.exec_module(plot)
    font=resolve(args.font) if args.font else (source.parent/'SimSun.ttf')
    if not font.exists():font=PROJECT.parent/'写作/fonts/SimSun.ttf'
    plot.draw_all(json.loads(source.read_text(encoding='utf-8')),font,output,args.z_exaggeration)
    (output/'redraw_manifest.json').write_text(json.dumps({'source_sha256':digest,'font_sha256':hashlib.sha256(font.read_bytes()).hexdigest(),'z_exaggeration':args.z_exaggeration},indent=2),encoding='utf-8')

if __name__=='__main__':main()
