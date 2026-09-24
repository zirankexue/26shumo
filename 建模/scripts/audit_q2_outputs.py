"""Audit exported artifacts and render every PDF for human inspection."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[1]
for folder in (PROJECT/'src',PROJECT/'.runtime/python',PROJECT/'.runtime/q2'):
    if folder.is_dir():sys.path.insert(0,str(folder))
from PIL import Image,ImageDraw,ImageFont,ImageStat
from pypdf import PdfReader
from uav_rescue.q2.pipeline import verify_csv_tables

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def contacts(files,destination,prefix,font):
    for start in range(0,len(files),4):
        batch=files[start:start+4];canvas=Image.new('RGB',(1800,1240),'#ECEFF1');draw=ImageDraw.Draw(canvas)
        for j,path in enumerate(batch):
            x=(j%2)*900;y=(j//2)*620
            draw.text((x+12,y+8),path.stem,fill='black',font=font)
            with Image.open(path) as im:
                im=im.convert('RGB');im.thumbnail((880,570))
                canvas.paste(im,(x+(900-im.width)//2,y+40+(570-im.height)//2))
        canvas.save(destination/f'{prefix}_{start//4+1:02}.png')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/q2');args=parser.parse_args()
    output=Path(args.output);output=output if output.is_absolute() else PROJECT/output
    payload=json.loads((output/'tables/results.json').read_text(encoding='utf-8'))
    checks=verify_csv_tables(payload,output)
    for name,value in payload['metadata']['input_sha256'].items():
        if digest(Path(name))!=value:raise AssertionError('原始输入哈希改变')
    protected=json.loads((output/'logs/protected_files.json').read_text(encoding='utf-8'))
    if any(digest(Path(name))!=value for name,value in protected.items()):raise AssertionError('问题一或论文工程改变')
    checks['protected_files_unchanged']=len(protected)
    cases=payload['schemes'];assert len(cases)==4
    assert all(r['verification']['passed'] and r['verification']['unique_boxes']==80 for r in cases.values())
    main=payload['main'];assert len({r['box'] for r in main['deliveries'] if r['hard_s'] is not None})==31
    bundle=output/'plotting/data/plot_data.json'
    manifest=json.loads(bundle.with_name('manifest.json').read_text(encoding='utf-8'))
    assert digest(bundle)==manifest['plot_data_sha256']
    snapshot=json.loads(bundle.read_text(encoding='utf-8'))
    assert snapshot['main']==payload['main'] and snapshot['schemes']==cases
    vertices=next(s['rows'] for s in payload['sheets'] if s['name']=='航迹坐标')
    assert len(vertices)==4*len(main['legs'])
    for i,leg in enumerate(main['legs']):
        vs=vertices[i*4:i*4+4]
        assert all(v[0]==leg['sortie'] and v[1]==leg['segment'] for v in vs)
        assert vs[0][5:7]==vs[1][5:7] and vs[2][5:7]==vs[3][5:7]
        assert vs[1][7]==vs[2][7]==leg['cruise_altitude_m']
        assert abs(vs[1][7]-vs[0][7]-leg['climb_m'])<1e-8
        assert abs(vs[2][7]-vs[3][7]-leg['descent_m'])<1e-8
    checks['trajectory_vertices']=len(vertices)
    for name in cases:
        order=payload['orders'][name]
        def objective(result):
            s=result['summary'];values={'tardiness':round(s['weighted_tardiness_s']*1000),'makespan':round(s['makespan_s']*1000),
                                        'energy':sum(round(r['energy_kwh']*1e9) for r in result['sorties']),'sorties':s['sorties']}
            return tuple(values[k] for k in order)
        assert all(objective(cases[name])<=objective(other) for other in cases.values())
    checks['best_seen_consistent_across_four_orders']=True
    pngs=sorted((output/'figures').glob('*.png'));pdfs=sorted((output/'figures').glob('*.pdf'))
    assert len(pngs)==len(pdfs)==8
    qa=output/'logs/visual_qa';qa.mkdir(parents=True,exist_ok=True)
    poppler=shutil.which('pdftoppm') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
    records=[]
    for png,pdf in zip(pngs,pdfs):
        assert png.stem==pdf.stem
        with Image.open(png) as im:
            assert min(im.size)>1000 and min(im.info['dpi'])>299
            assert max(ImageStat.Stat(im.convert('RGB')).stddev)>8
            size=list(im.size)
        reader=PdfReader(pdf);assert len(reader.pages)==1
        assert len(reader.pages[0].extract_text())>30
        subprocess.run([poppler,'-f','1','-singlefile','-scale-to','1700','-png',str(pdf),str(qa/pdf.stem)],check=True,capture_output=True)
        with Image.open(qa/(pdf.stem+'.png')) as im:assert max(ImageStat.Stat(im.convert('RGB')).stddev)>8
        records.append({'name':png.stem,'png_size':size,'png_sha256':digest(png),'pdf_sha256':digest(pdf),'pdf_pages':1,'pdf_render_nonblank':True})
    font=ImageFont.truetype(str(PROJECT.parent/'写作/fonts/SimSun.ttf'),23)
    contacts(sorted(qa.glob('0*.png')),qa,'pdf_contact',font)
    previews=sorted((output/'logs/previews').glob('*.png'))
    if previews:contacts(previews,qa,'excel_contact',font)
    checks['figures']=records;checks['excel_preview_count']=len(previews)
    checks['visual_review']='Rendered every PDF and worksheet preview; human inspection recorded separately.'
    (output/'logs/export_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in checks.items() if k!='figures'},ensure_ascii=False))

if __name__=='__main__':main()
