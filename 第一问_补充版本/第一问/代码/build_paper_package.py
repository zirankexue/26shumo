"""Assemble the Q1 writing bundle; preserve verified solvers and result files."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
Q = ROOT/'第一问'
sys.path.insert(0,str(ROOT/'.runtime_py'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image
import pypdf
from pypdf import PdfReader, PdfWriter


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_files(folder):
    """Explicitly skip symlinks/junctions, caches and dependency directories."""
    for current, dirs, files in os.walk(folder, followlinks=False):
        dirs[:]=[d for d in dirs if d not in {'__pycache__','node_modules'} and not (Path(current)/d).is_symlink() and not os.path.isjunction(Path(current)/d)]
        for name in files:
            p=Path(current)/name
            if p.suffix!='.pyc' and not p.is_symlink(): yield p


def get_figures():
    quantitative=json.loads((Q/'图片/figure_manifest.json').read_text(encoding='utf-8'))['figures']
    geo=json.loads((Q/'结果/geography_figures.json').read_text(encoding='utf-8'))['figures']
    figures=[]
    for x in geo:
        name=Path(x['outputs'][0]['file']).stem
        figures.append(dict(id=x['id'],name=name,title={'F01':'地形、节点及直达航线','F02':'关键航线的地形剖面'}[x['id']],caption=x['caption'],source_csv=x['source_data'],recommended_main=x['id']=='F01',width_mm=x['width_mm'],height_mm=x['height_mm']))
    figures+=quantitative
    assert [x['id'] for x in figures]==[f'F{i:02d}' for i in range(1,17)]
    return figures


def write_guide(figures):
    lines=['# 第一问图表说明与选用建议','','16幅图依据当前局部椭球平面正式结果生成。每幅均有600dpi PNG、可编辑文字SVG和嵌入字体PDF。PNG适合Word插图，PDF适合LaTeX，SVG适合继续排版编辑；`_preview.png`为轻量浏览版，不作为正式投稿图。常规宽度180 mm，字号按信息密度约7–10 pt。','','优先正文图：**F01、F04、F08、F09、F11、F14**，对应空间与地形、组批方案、安全载荷、目标权衡、余量总体变化及精确末段阈值。篇幅允许可补F02与F16；其余作为附图或按章节选用，避免相同结论重复占篇幅。','','全部数值来自确定性模型，无重复试验或误差条。小数显示位数不代表原始经纬度、DEM或机型参数的实测精度。安全余量为20表示20%，区间宽度另以百分点表示。','','[打开17页图册（首页索引＋16幅图）](../图片/第一问_论文图册.pdf)','','|编号|内容|建议位置|','|---|---|---|']
    for f in figures:lines.append(f"|{f['id']}|{f['title']}|{'正文' if f['recommended_main'] else '备选/附图'}|")
    for f in figures:
        srcs=f['source_csv'] if isinstance(f['source_csv'],list) else [f['source_csv']]
        lines.extend(['',f"## {f['id']} {f['title']}",'',f['caption'],'',
                      '格式：'+' · '.join(f'[{ext.upper()}](../图片/{f["name"]}.{ext})' for ext in ['png','pdf','svg']),
                      '', '源数据：'+'、'.join(f'[{Path(p).name}](../{p})' for p in srcs)])
    lines+=['','## 排版与数值使用','','1. 图片应按原纵横比插入；不要将高幅载荷曲线压成扁图。','2. F09只有两个离散点，不应补成平滑或连续可选前沿。F11和F14的实心上端包含等号。','3. F12按方案类别等距排列，不表示余量区间等宽。F13为1个百分点采样曲线，不替代精确事件扫描。','4. F15保留S008/C型负满载公式阈值，负值表示0%余量下也无法额定满载，不是建议取负余量。','5. 论文中的精确区间以正文附表与 reserve_intervals.csv 为准；不要用图中舍入的刻度重新求解。','6. 正文可改用最终论文统一图号，建议保留F01–F16作为源文件标识以便回查。','']
    (Q/'文字/图表说明与选用建议.md').write_text('\n'.join(lines),encoding='utf-8')


def assemble_atlas(figures):
    font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'pdf.fonttype':42,'svg.fonttype':'none'})
    cover=Q/'结果/图册首页.pdf'
    fig=plt.figure(figsize=(180/25.4,220/25.4),facecolor='white')
    fig.text(.08,.94,'第一问 · 论文图册',fontsize=20,weight='bold',color='#264A61')
    fig.text(.08,.885,'局部椭球坐标 · 整箱组批 · 多目标权衡 · 精确余量阈值',fontsize=9,color='#546571')
    fig.text(.08,.84,'16幅图 / 独立矢量PDF与SVG / 600dpi PNG / 配套源数据',fontsize=9)
    for i,f in enumerate(figures):
        y=.785-i*.037
        fig.text(.09,y,f['id'],fontsize=10,color='#347D9D')
        fig.text(.19,y,f['title'],fontsize=10)
        if f['recommended_main']:fig.text(.84,y,'正文优先',fontsize=8,color='#996F34')
    totals=json.loads((ROOT/'results/question1_batching/solution.json').read_text(encoding='utf-8'))['totals']
    fig.text(.08,.12,f"基准：{totals['flight_count']}架次 / {totals['energy_kwh']:.6f} kWh / 累计{totals['work_time_s']/60:.6f} min",fontsize=9)
    fig.text(.08,.078,'完整图注与解释见“文字/图表说明与选用建议.md”。\n图中数值为给定模型下的计算结果；不代表实测精度。',fontsize=8,color='#62717A',linespacing=1.5)
    fig.savefig(cover);fig.savefig(Q/'图片/图册首页_preview.png',dpi=140);plt.close(fig)
    writer=PdfWriter();writer.append(cover)
    for i,f in enumerate(figures):
        writer.append(Q/'图片'/f"{f['name']}.pdf")
        writer.add_outline_item(f"{f['id']} {f['title']}",i+1)
    writer.add_metadata({'/Title':'第一问论文图册','/Subject':'当前局部椭球平面模型的16幅论文图','/Author':'数学建模项目'})
    writer.write(Q/'图片/第一问_论文图册.pdf')
    for start in range(0,16,4):
        fig,axes=plt.subplots(2,2,figsize=(11,10),layout='constrained')
        for ax,f in zip(axes.flat,figures[start:start+4]):
            with Image.open(Q/'图片'/f"{f['name']}_preview.png") as im: ax.imshow(im.copy())
            ax.axis('off');ax.set_title(f"{f['id']}  {f['title']}",fontsize=10,color='#294C61')
        fig.savefig(Q/'图片'/f'图组索引_{start//4+1:02d}.png',dpi=150,facecolor='white');plt.close(fig)


def copies():
    solution=json.loads((ROOT/'results/question1_batching/solution.json').read_text(encoding='utf-8'))
    dest=Q/'数据/原始附件';dest.mkdir(exist_ok=True)
    copied=[]
    original_sources=[]
    for kind,entry in solution['sources'].items():
        src=ROOT/entry['path'];target=dest/src.name
        assert digest(src)==entry['sha256'],f'原始来源发生变化: {src}'
        shutil.copy2(src,target)
        assert digest(src)==digest(target)
        original_sources.append(src)
        copied.append(dict(kind=kind,source=src.relative_to(ROOT).as_posix(),copy=target.relative_to(ROOT).as_posix(),sha256=digest(src)))
    for directory,name in [('question1_batching','第一问_货箱组批方案.xlsx'),('question1_multiobjective','第一问_多目标优化结果.xlsx'),('question1_reserve_sensitivity','第一问_安全余量敏感性.xlsx')]:
        src=ROOT/'outputs'/directory/name;target=Q/'结果'/name
        shutil.copy2(src,target);assert digest(src)==digest(target)
        copied.append(dict(kind='verified_workbook',source=src.relative_to(ROOT).as_posix(),copy=target.relative_to(ROOT).as_posix(),sha256=digest(src)))
    text=['# 第一问原始附件副本','','本目录复制第一问使用的3份工作簿、1份DEM和题目规则文件，逐文件SHA256与正式结果绑定来源一致。数值求解仍从工作区根“数据”读取，这些是便于阅读与分享的交付副本，不是第二套独立维护的数据。','','|文件|原始来源|SHA256|','|---|---|---|']
    text.extend(f"|{Path(x['copy']).name}|{x['source']}|`{x['sha256']}`|" for x in copied if x['kind']!='verified_workbook')
    (dest/'README.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return copied,original_sources


def verify(figures):
    checks=[]
    review_path=Q/'结果/图形人工审阅.json'
    review=json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {'figures':{}}
    visual_current=[]
    for f in figures:
        files={ext:Q/'图片'/f"{f['name']}.{ext}" for ext in ['png','pdf','svg']}
        with Image.open(files['png']) as im:
            dpi=im.info.get('dpi');pixels=im.size
        pdf=PdfReader(files['pdf']);text=pdf.pages[0].extract_text()
        fonts={str(v.get_object().get('/Subtype')) for v in pdf.pages[0]['/Resources']['/Font'].get_object().values()}
        svg=files['svg'].read_text(encoding='utf-8')
        assert len(pdf.pages)==1 and len(text)>20 and '<text' in svg
        assert all(abs(d-600)<.1 for d in dpi) and '/Type3' not in fonts
        visual_current.append(review['figures'].get(f['id'],{}).get('png_sha256')==digest(files['png']))
        checks.append(dict(id=f['id'],pixels=pixels,dpi=dpi,pdf_pages=1,pdf_text_chars=len(text),font_subtypes=sorted(fonts),svg_text=True,files={k:dict(path=p.relative_to(ROOT).as_posix(),sha256=digest(p),size=p.stat().st_size) for k,p in files.items()}))
    missing=[]
    md_files=[ROOT/'工作区目录说明.md']+list(Q.glob('*.md'))+list((Q/'文字').glob('*.md'))+list((Q/'代码').glob('*.md'))
    for p in md_files:
        for link in re.findall(r'!?\[[^\]]*\]\(([^\)]+)\)',p.read_text(encoding='utf-8')):
            if link.startswith(('http:','https:','#')): continue
            if not (p.parent/link.split('#')[0]).exists():missing.append(dict(file=str(p),link=link))
    assert not missing,missing
    assert len(PdfReader(Q/'图片/第一问_论文图册.pdf').pages)==17
    geo_path=Q/'结果/geography_figures.json'
    geo=json.loads(geo_path.read_text(encoding='utf-8'))
    for item in geo['figures']:
        idx=int(item['id'][1:])-1
        item['qa']['visual_review']='passed_by_matching_reviewed_png' if visual_current[idx] else 'pending'
        item['qa']['visual_review_record']=review_path.relative_to(Q).as_posix()
        item['qa']['pdf_text_extractable']=checks[idx]['pdf_text_chars']>20
        item['qa']['pdf_font_subtypes']=checks[idx]['font_subtypes']
    geo_path.write_text(json.dumps(geo,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(figures=checks,all_markdown_links_valid=True,atlas_pages=17,
                visual_review_current=all(visual_current),
                visual_review_record=review_path.relative_to(Q).as_posix(),
                visual_review_note='Images changed since the recorded visual review require a fresh manual inspection.' if not all(visual_current) else 'PNG hashes match the completed visual review.',
                deterministic_model=True)


def package(copied,original_sources,qa):
    result=Q/'结果/材料清单与核验.json'
    result.write_text(json.dumps(dict(created_utc=datetime.now(timezone.utc).isoformat(),versions=dict(python=sys.version,matplotlib=matplotlib.__version__,pypdf=pypdf.__version__),copies=copied,qa=qa),ensure_ascii=False,indent=2),encoding='utf-8')
    files=set(actual_files(Q))
    # Include the real dependencies by their existing project-relative names.
    files.update(original_sources)
    for name in ['question1_batching','question1_multiobjective','question1_reserve_sensitivity','question1_local_plane','question1_peer_alignment']:
        files.update(actual_files(ROOT/'results'/name))
    for p in (ROOT/'code').iterdir():
        if p.is_file() and ('q1' in p.name.lower()):files.add(p)
    files.add(ROOT/'CODE_CHANGE_LOG.md')
    # Avoid a misleading link to absent Q2/Q3 material in the Q1-only archive.
    archive=ROOT/'archives/第一问_论文材料包_20260924.zip'
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        entries=[]
        for p in sorted(files):
            rel=p.relative_to(ROOT).as_posix();z.write(p,rel);entries.append(dict(path=rel,sha256=digest(p),size_bytes=p.stat().st_size))
        z.writestr('材料包说明.md','# 第一问论文材料包\n\n从“第一问/README.md”开始阅读。code、results及根数据路径是求解依赖，请保留相对结构。压缩包包含真实文件，不含Windows联接。第一问/结果中的三个实时联接在本包不复制；请直接打开根results内的对应目录。正式Excel已放在第一问/结果。\n\n只包含第一问材料，没有复制其他问结果、运行环境、私有文献或旧UTM归档。数学公式为Markdown/LaTeX可编辑原文；论文图另附PNG、SVG、PDF。\n')
        z.writestr('package_manifest.json',json.dumps(dict(files=entries,file_count=len(entries)),ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        meta=json.loads(z.read('package_manifest.json'))
        for entry in meta['files']:
            assert hashlib.sha256(z.read(entry['path'])).hexdigest()==entry['sha256']
    print(json.dumps(dict(archive=str(archive),files=len(files),bytes=archive.stat().st_size,figures=16,atlas_pages=17),ensure_ascii=False))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    figures=get_figures();write_guide(figures);assemble_atlas(figures)
    copied,inputs=copies();qa=verify(figures);package(copied,inputs,qa)


if __name__=='__main__':main()
