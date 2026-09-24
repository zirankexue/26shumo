"""Draw the weighted Q2 comparison from saved results; never runs optimization.

The self-contained input only needs ``spec``, ``objective_vectors``, and ``cloud``.
Internal units: coefficient*ms, ms, 1e-9 kWh, and sorties, respectively.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import sys

PROJECT = Path(__file__).resolve().parents[1]
for _runtime in (PROJECT / '.runtime/python', PROJECT / '.runtime/q2'):
    if _runtime.is_dir():
        sys.path.insert(0, str(_runtime))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager, colors
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

METRICS = ('tardiness', 'makespan', 'energy', 'sorties')
KEYS = ('weighted',) + tuple('scenario_' + k for k in METRICS)
UNITS = dict(zip(METRICS, (60000, 3600000, 1e9, 1)))
AXIS_LABELS = ('加权迟到 W\n系数·min', '最后返航 Cmax\nh', '运输能耗 E\nkWh', '架次 N\n次')
COLORS = ('#D69D36', '#438E8C', '#4C6587', '#BC6757', '#786181')
SHORT = ('推荐', '迟到权重↑', '完工权重↑', '能耗权重↑', '架次权重↑')
STEM = '09_四指标加权权衡'


def _number(value, metric):
    if metric == 'sorties':
        return f'{value:.1f}' if abs(value - round(value)) > 1e-6 else str(round(value))
    if abs(value) >= 1000:
        return f'{value:,.0f}'
    if metric == 'energy':
        return f'{value:.1f}'
    if metric == 'makespan':
        return f'{value:.2f}'
    return f'{value:.1f}'


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _checked(payload):
    spec = payload['spec']
    for field in ('lower', 'upper', 'span', 'alpha'):
        if set(spec[field]) != set(METRICS):
            raise ValueError(f'spec.{field} 必须包含且仅包含四个指标: {METRICS}')
        if not all(math.isfinite(spec[field][k]) for k in METRICS):
            raise ValueError(f'spec.{field} 含非有限数值')
    if any(spec['span'][k] <= 0 for k in METRICS):
        raise ValueError('固定归一化跨度必须为正')
    if any(spec['alpha'][k] <= 0 for k in METRICS) or not math.isclose(sum(spec['alpha'].values()), 1, abs_tol=1e-10):
        raise ValueError('归一化权重必须为正且和为1')
    vectors = payload['objective_vectors']
    if any(k not in vectors for k in KEYS):
        raise ValueError('需要加权推荐及四个放大权重情景，不能以词典序方案代填')
    accepted = [r for r in payload['cloud'] if r.get('accepted') is True]
    if not accepted:
        raise ValueError('没有已接受的真实可行解记录，不能构造解云')
    for vector in [vectors[k] for k in KEYS] + [r['vector'] for r in accepted]:
        if any(k not in vector or not math.isfinite(vector[k]) or vector[k] < 0 for k in METRICS):
            raise ValueError('目标向量缺指标或含负值、非有限值')
        if vector['sorties'] != int(vector['sorties']) or vector['sorties'] <= 0:
            raise ValueError('架次必须为正整数')
    return spec, vectors, accepted


def draw(payload, font_path, output):
    """Render one two-panel PNG/PDF plus exact figure data and an audit manifest.

    ``font_path`` must be an existing Chinese font. ``output`` is the destination
    directory. This function reads no original inputs and changes no schedules.
    """
    spec, vectors, accepted = _checked(payload)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    font_path = Path(font_path)
    if not font_path.is_file():
        raise FileNotFoundError('需要有效的中文字体文件：' + str(font_path))
    font_manager.fontManager.addfont(str(font_path))
    family = font_manager.FontProperties(fname=str(font_path)).get_name()
    scenario_multiplier = payload.get('config', {}).get('scenario_multiplier', None)
    weight_values = spec.get('weights', spec['alpha'])
    weights_text = '∶'.join(f'{weight_values[k]:g}' for k in METRICS)
    multiplier_text = f'×{scenario_multiplier:g}' if scenario_multiplier is not None else '放大'
    labels = (f'加权推荐（{weights_text}）',
              f'迟到权重{multiplier_text}', f'完工权重{multiplier_text}',
              f'能耗权重{multiplier_text}', f'架次权重{multiplier_text}')
    raw = np.array([[vectors[key][k] for k in METRICS] for key in KEYS], dtype=float)
    lo = np.array([spec['lower'][k] for k in METRICS], dtype=float)
    span = np.array([spec['span'][k] for k in METRICS], dtype=float)
    normalized = (raw - lo) / span
    physical = raw / np.array([UNITS[k] for k in METRICS])
    cloud = np.array([[r['vector'][k] for k in METRICS] for r in accepted], dtype=float)
    unique_vectors = len({tuple(r['vector'][k] for k in METRICS) for r in accepted})
    source_counts = {}
    for row in accepted:
        name = row.get('source', 'unspecified').split(':', 1)[0]
        source_counts[name] = source_counts.get(name, 0) + 1
    if not np.all(np.isfinite(normalized)):
        raise ValueError('归一化产生非有限值')
    coincident = {}
    for j, key in enumerate(KEYS):
        coincident.setdefault(tuple(vectors[key][k] for k in METRICS), []).append(j)
    same_curve_groups = [members for members in coincident.values() if len(members) > 1]
    same_curve_text = '；'.join('＝'.join(SHORT[j] for j in members) for members in same_curve_groups)
    reassessed = payload.get('scenario_reselected_sources', {})
    compared_counts = sorted({r['complete_candidates_compared'] for r in reassessed.values()})
    scope_text = (f'四情景分别按自身权重，从{compared_counts[0]}个已保存完整方案回评重选；范围仅限该有限集合。'
                  if len(compared_counts) == 1 else '四情景展示各自权重下的已保存可行方案。')

    rc = {'font.family': family, 'font.size': 11, 'axes.unicode_minus': False,
          'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.spines.top': False,
          'axes.spines.right': False, 'savefig.facecolor': 'white'}
    with plt.rc_context(rc):
        fig = plt.figure(figsize=(17, 8.8))
        grid = fig.add_gridspec(1, 2, left=.062, right=.965, top=.835, bottom=.24,
                               width_ratios=(1, 1.32), wspace=.27)
        ax = fig.add_subplot(grid[0, 0])
        right = fig.add_subplot(grid[0, 1])
        ymin = min(0., float(normalized.min()))
        ymax = max(1., float(normalized.max()))
        pad = max(.08 * (ymax - ymin), .08)
        # Limits may extend beyond [0,1]; the original normalization is never refit.
        ax.set_ylim(ymin - pad, ymax + pad)
        ax.set_xlim(-.28, 3.23)
        ticks = MaxNLocator(nbins=4).tick_values(ymin, ymax)
        ticks = ticks[(ticks >= ymin - 1e-10) & (ticks <= ymax + 1e-10)]
        ticks = sorted(set([0., 1., *[float(v) for v in ticks]]))
        if ymin < 0:
            ticks = [ymin, *ticks]
        if ymax > 1:
            ticks = [*ticks, ymax]
        if len(ticks) > 6:
            ticks = [ymin, 0., .5, 1., ymax]
            ticks = sorted({v for v in ticks if ymin <= v <= ymax})
        for t in ticks:
            ax.axhline(t, color='#D6DCE0', lw=.7, ls='--', zorder=0)
        for col, metric in enumerate(METRICS):
            ax.vlines(col, ymin, ymax, color='#A9B1B8', lw=1, zorder=1)
            for t in ticks:
                value = (lo[col] + t * span[col]) / UNITS[metric]
                if value < 0:
                    continue
                ax.plot([col - .025, col + .025], [t, t], color='#88939C', lw=.8)
                # Actual values at shared normalized levels; no independent axis refit.
                ax.annotate(_number(value, metric), (col, t), xytext=(-5, 3),
                            textcoords='offset points', ha='right', va='bottom',
                            fontsize=8.5, color='#53606B',
                            bbox={'fc': 'white', 'ec': 'none', 'alpha': .86, 'pad': .4}, zorder=6)
        for members in reversed(list(coincident.values())):
            if len(members) > 1:
                # Shared centerline, concentric markers: distinguish coincident plans
                # by stroke width only; no horizontal or vertical jitter is applied.
                for layer, index in enumerate(reversed(members)):
                    remaining = len(members) - layer - 1
                    ax.plot(range(4), normalized[index], color=COLORS[index],
                            lw=2.1 + 2.6 * remaining,
                            linestyle='-' if remaining else '--',
                            marker='o', ms=5.2 + 3.4 * remaining, alpha=.94,
                            zorder=4 + layer)
            else:
                index = members[0]
                ax.plot(range(4), normalized[index], color=COLORS[index],
                        lw=2.6 if index == 0 else 1.65,
                        linestyle='-' if index == 0 else ('--', '-.', ':', '-')[index - 1],
                        marker='o', ms=6 if index == 0 else 4.5, alpha=.95,
                        zorder=5 if index == 0 else 3)
        ax.set_xticks(range(4), AXIS_LABELS, fontsize=11)
        ax.tick_params(axis='x', length=0, pad=11)
        ax.set_yticks(ticks, [f'{v:.2f}'.rstrip('0').rstrip('.') for v in ticks], fontsize=9)
        ax.set_ylabel('固定归一化坐标（越低越好）', labelpad=8)
        ax.spines[['top', 'right', 'bottom']].set_visible(False)
        ax.spines['left'].set_color('#BCC4CA')
        ax.set_title('(a) 五方案平行坐标：各轴刻度标实际值', fontsize=12, pad=13)
        if same_curve_text:
            ax.text(.5, .905, '重合：' + same_curve_text, transform=ax.transAxes,
                    ha='center', va='center', fontsize=9, color='#3F4D57',
                    bbox={'fc': 'white', 'ec': '#D8DEE2', 'alpha': .94, 'pad': 3}, zorder=10)

        x = cloud[:, 1] / UNITS['makespan']
        y = cloud[:, 2] / UNITS['energy']
        w = cloud[:, 0] / UNITS['tardiness']
        n = cloud[:, 3]
        # Marker area is directly proportional to sortie count (no minimum-size offset).
        area_scale = 1.0
        vmax = max(1., float(w.max())) if float(w.max()) == float(w.min()) else float(w.max())
        norm = colors.Normalize(vmin=float(w.min()), vmax=vmax)
        points = right.scatter(x, y, c=w, cmap='coolwarm', norm=norm,
                               s=area_scale * n, edgecolors='none', alpha=.35, zorder=2)
        cbar = fig.colorbar(points, ax=right, fraction=.037, pad=.02)
        cbar.set_label('加权迟到 W / 系数·min', fontsize=10)
        cbar.ax.tick_params(labelsize=9)
        right.set(xlabel='全部运输完成／最后返航 Cmax / h', ylabel='总运输能耗 E / kWh')
        right.grid(alpha=.2, lw=.7)
        right.set_title(f'(b) 已接受可行记录 {len(accepted):,} 条；不同四指标 {unique_vectors:,} 组',
                        fontsize=12, pad=13)
        all_x = np.r_[x, physical[:, 1]]
        all_y = np.r_[y, physical[:, 2]]
        dx = max(float(np.ptp(all_x)), .1)
        dy = max(float(np.ptp(all_y)), 1.)
        right.set_xlim(max(0., float(all_x.min()) - dx * .06), float(all_x.max()) + dx * .08)
        right.set_ylim(max(0., float(all_y.min()) - dy * .09), float(all_y.max()) + dy * .09)
        # All stars stay at their true coordinates. Coincident cases share a callout.
        groups = {}
        for j in range(5):
            groups.setdefault((physical[j, 1], physical[j, 2]), []).append(j)
        for group_index, ((px, py), members) in enumerate(sorted(groups.items())):
            for layer, j in enumerate(reversed(members)):
                size = 225 + 210 * (len(members) - layer - 1)
                right.scatter([px], [py], marker='*', s=size,
                              c=[COLORS[j]], edgecolors='#202C35' if layer == 0 else 'white', lw=.8,
                              zorder=6 + layer)
            text = ' / '.join(SHORT[j] for j in members)
            # Text alone is offset; arrows preserve exact metric locations.
            offset = (8, 13 if group_index % 2 == 0 else -22)
            if px > all_x.min() + .68 * dx:
                offset = (-8, offset[1])
            right.annotate(text, (px, py), xytext=offset, textcoords='offset points',
                           ha='left' if offset[0] > 0 else 'right', va='center',
                           fontsize=9, color='#26333D',
                           bbox={'fc': 'white', 'ec': 'none', 'alpha': .9, 'pad': 1.4},
                           arrowprops={'arrowstyle': '-', 'color': '#6C7882', 'lw': .65}, zorder=10)
        samples = sorted({int(n.min()), int(np.median(n)), int(n.max())})
        size_handles = [right.scatter([], [], s=area_scale * s, color='#7A8997', alpha=.55,
                                      edgecolors='none', label=f'{s}架次') for s in samples]
        right.legend(handles=size_handles, title='点面积 ∝ 架次 N', ncol=len(samples),
                     loc='upper left', fontsize=8, title_fontsize=9,
                     framealpha=.93, borderpad=.5, handletextpad=.3, columnspacing=.7)

        handles = [Line2D([0], [0], color=COLORS[j], marker='*', markeredgecolor='#26333D',
                          markersize=11, lw=2.2 if j == 0 else 1.5, label=labels[j]) for j in range(5)]
        fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(.5, .105),
                   ncol=3, fontsize=11, frameon=False, columnspacing=1.6, handlelength=2.3)
        fig.suptitle('四指标加权权衡：推荐方案、权重情景与实际可行解云', fontsize=17, y=.962)
        fig.text(.5, .911, f'主权重 W∶Cmax∶E∶N = {weights_text}；四个情景仅放大一项权重，其余不变',
                 ha='center', fontsize=11.5, color='#43535E')
        fig.text(.5, .075,
                 '归一化使用搜索前冻结的四个词典序方案尺度；坐标超出 [0,1] 时保留原值，不裁剪、不重新缩放。',
                 ha='center', fontsize=9.5, color='#4C5963')
        fig.text(.5, .047, scope_text, ha='center', fontsize=9.5, color='#4C5963')
        fig.text(.5, .019,
                 'W 为优先系数加权的迟到总量；云图保留重复接受记录，星号为五个代表方案。'
                 '全部点仅代表已搜得的可行解，并非完整 Pareto 前沿。',
                 ha='center', fontsize=9.5, color='#4C5963')
        png = output / (STEM + '.png')
        pdf = output / (STEM + '.pdf')
        fig.savefig(png, dpi=300)
        fig.savefig(pdf, metadata={'Title': STEM, 'Subject': 'Fixed normalization and actual accepted feasible solutions'})
        plt.close(fig)

    scheme_rows = []
    for j, key in enumerate(KEYS):
        row = {'scheme': key, 'label': labels[j]}
        row['reassessment_source'] = reassessed.get(key, {}).get('source', key)
        row.update({k + '_internal': vectors[key][k] for k in METRICS})
        row.update(dict(zip(('W_coefficient_min', 'Cmax_h', 'E_kwh', 'N'), physical[j].tolist())))
        row.update({k + '_normalized_fixed': normalized[j, i] for i, k in enumerate(METRICS)})
        row['primary_weighted_score'] = sum(spec['alpha'][k] * normalized[j, i] for i, k in enumerate(METRICS))
        scheme_rows.append(row)
    cloud_rows = []
    for i, row in enumerate(accepted):
        cloud_rows.append({'record': i + 1, 'source': row.get('source', 'unspecified'),
                           **{k + '_internal': row['vector'][k] for k in METRICS},
                           'W_coefficient_min': cloud[i, 0] / UNITS['tardiness'],
                           'Cmax_h': cloud[i, 1] / UNITS['makespan'],
                           'E_kwh': cloud[i, 2] / UNITS['energy'],
                           'N': int(cloud[i, 3]), 'point_area_pt2': area_scale * cloud[i, 3]})
    _write_csv(output / '09_五方案绘图数据.csv', scheme_rows)
    _write_csv(output / '09_已接受可行解云数据.csv', cloud_rows)
    snapshot = {k: payload[k] for k in ('spec', 'objective_vectors', 'cloud')}
    snapshot['scenario_reselected_sources'] = reassessed
    snapshot['config'] = {k: payload.get('config', {}).get(k) for k in ('scenario_multiplier', 'weights')}
    snapshot['format'] = 'q2_weighted_plot_v1'
    (output / '09_重绘数据.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    manifest = {'figure': STEM, 'accepted_record_count': len(accepted),
                'unique_objective_vectors': unique_vectors, 'source_counts': source_counts,
                'five_schemes': list(KEYS), 'fixed_normalization': spec,
                'coincident_four_metric_groups': [[KEYS[j] for j in group] for group in same_curve_groups],
                'scenario_reselected_sources': reassessed,
                'normalized_range': [float(normalized.min()), float(normalized.max())],
                'clip_normalized_values': False, 'point_area_pt2_per_sortie': area_scale,
                'font_sha256': _digest(font_path),
                'png_dpi': 300, 'pdf_vector_art': True,
                'files_sha256': {p.name: _digest(p) for p in (png, pdf, output/'09_五方案绘图数据.csv', output/'09_已接受可行解云数据.csv', output/'09_重绘数据.json')}}
    (output / '09_绘图核验.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / '09_图表说明.md').write_text(
        '# 四指标加权权衡图\n\n'
        f'主权重按 W、Cmax、E、N 顺序为 {weights_text}。情景分别把一项权重{multiplier_text}，其余保持不变。\n\n'
        f'{scope_text}'
        '原独立搜索终点另存于 `tables/scenario_search_endpoints.json`。回评不新增求解，'
        '不把只有目标向量而缺少完整运输时序的云图点恢复为方案，也不声称该有限集合以外的最优性。\n\n'
        '左图使用结果中的 `spec.lower` 和 `spec.span` 进行固定归一化 `(f−lower)/span`；'
        '这与求解尺度一致。若方案超出初始参考范围，坐标允许小于0或大于1，不重新拟合上下限。'
        '每条纵轴旁的数字为该指标的实际值；灰色水平网格和左侧纵坐标为公共归一化位置。'
        '所有指标均越低越好。归一化基准外的刻度是固定线性变换的延伸，不表示存在该物理取值的方案。\n\n'
        f'右图共{len(accepted)}条已接受可行记录，包含{unique_vectors}组不同四指标向量。'
        '保留重复记录以忠实呈现搜索接受历史；不能将点数解释为不同路线方案的数量。'
        '横轴为最后运输机返航时刻，纵轴为运输能耗；色值为 W/60000（系数·min），'
        '点面积（平方点）与架次 N 成正比。星号代表五个方案，坐标相同时使用同心星号并合并标注。'
        '左图完全重合的四指标曲线沿同一中心线以不同粗细叠绘、同心圆点区分；不移动真实坐标。'
        f'{"实际重合：" + same_curve_text + "。" if same_curve_text else ""}'
        '点云不是完整Pareto前沿，不代表连续可行域。\n\n'
        'W 是逐箱优先系数乘正迟到时间后的求和，不是平均交付时刻。'
        '图中不含未接受候选，不插值、补点或移动方案坐标。\n\n'
        '重绘只需本脚本、保存的JSON、NumPy、Matplotlib和中文字体；不读取原始附件，也不运行优化：\n\n'
        '```powershell\npython scripts/plot_q2_weighted.py --data outputs/q2_weighted/figures/09_重绘数据.json --output outputs/q2_weighted_redraw --font ../写作/fonts/SimSun.ttf\n```\n',
        encoding='utf-8')
    return {'png': str(png), 'pdf': str(pdf), 'manifest': manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='outputs/q2_weighted/tables/results.json')
    parser.add_argument('--output', default='outputs/q2_weighted/figures')
    parser.add_argument('--font', default=None)
    args = parser.parse_args()
    def resolve(value):
        path = Path(value)
        return path if path.is_absolute() else PROJECT / path
    source = resolve(args.data)
    font = resolve(args.font) if args.font else source.parent / 'SimSun.ttf'
    if not font.is_file() and args.font is None:
        font = PROJECT.parent / '写作/fonts/SimSun.ttf'
    payload = json.loads(source.read_text(encoding='utf-8'))
    result = draw(payload, font, resolve(args.output))
    print(json.dumps({'png': result['png'], 'pdf': result['pdf'],
                      'accepted_records': result['manifest']['accepted_record_count']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
