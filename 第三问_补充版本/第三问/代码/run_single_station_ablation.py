"""Controlled single-station ablation; intentionally leaves recommendations untouched."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'code'))
from improve_q3_from_agents import inherit
from solve_q3_senior import Profiles, dump, solve
from q3_senior_adapter import load_senior, SeniorGeometry
from validate_q3_senior import validate


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = Path(__file__).resolve().parent
    seed_path = out / 'solution_deduplicated_seed.json'
    station_path = out / 'station_pool.json'
    seed = read(seed_path)
    stations = read(station_path)
    assert len(stations) == 65
    assert len({tuple(s[k] for k in ('x', 'y', 'z')) for s in stations}) == 65
    data, factory, _, _ = load_senior()
    pool = {}
    for row in seed['transport']:
        p = factory.make(row['model'], row['boxes'], row['order'])
        assert p is not None and p.id == row['candidate_id']
        pool[p.id] = p
    assert len(pool) == len(seed['transport']) == 21
    profiles = Profiles(data, factory, SeniorGeometry(), stations)
    summary = dict(
        experiment='Single-station policy on the same 21 transport structures and 65 unique positions',
        source_seed=str(seed_path), source_seed_sha256=digest(seed_path),
        station_pool_sha256=digest(station_path), weighted_spec=seed['weighted_spec'],
        seconds=65, workers=4, random_seed=61, zero_lateness=True,
        transport_candidates=21, station_positions=65, max_relay_stations_per_sortie=1,
        hint=None, objective_cutoff=None,
        hint_explanation='No hint is supplied: the shared solver imposes a cutoff whenever a feasible hint is used, and the multi-station score is not a justified single-station cutoff.',
        multistation_seed_metrics=seed['metrics'], multistation_seed_search=seed['search'],
        limits=[
            'Same finite transport structures and station positions; no continuous-position or global routing optimality claim.',
            'Transport departure times, box ranks, resource assignment and selected relay positions are reoptimized.',
            'No multi-station cutoff is imposed on the single-station model.',
            'The 65-second run is a bounded search; FEASIBLE does not establish an optimal single-station objective.',
            'The comparator may benefit from an incumbent hint and earlier search; differences between feasible results alone do not isolate a proven causal benefit of handover.',
        ],
    )
    dump(out / 'ablation_summary.json', summary)
    found = inherit(solve(pool, data, profiles, seed['weighted_spec'], 65, 61,
        hint=None, workers=4, label='single_station_ablation', zero_lateness=True),
        seed, 'single_station_ablation')
    solution_path = out / 'solution_single_station_ablation.json'
    dump(solution_path, found)
    summary['single_station_search'] = found['search']
    summary['single_station_feasible'] = bool(found.get('feasible'))
    if found.get('feasible'):
        validation = validate(solution_path)
        dump(out / 'validation_single_station_ablation.json', validation)
        summary['validation_passed'] = validation['passed']
        summary['validation_check_count'] = validation['check_count']
        summary['validation_error_count'] = validation['error_count']
        summary['single_station_metrics'] = found['metrics']
        summary['minimum_hard_deadline_slack_s'] = validation['minimum_hard_deadline_slack_s']
        summary['integer_optimality_gap'] = found['search']['integer_objective'] - found['search']['integer_bound']
        summary['single_minus_multistation_seed'] = {
            k: found['metrics'][k] - seed['metrics'][k]
            for k in ['score', 'joint_finish_s', 'total_energy_kwh', 'relay_energy_kwh', 'relay_sorties', 'late_boxes']
        }
        assert validation['passed'], validation['errors'][:10]
    else:
        summary['validation_passed'] = None
        summary['no_incumbent_interpretation'] = (
            'No feasible single-station incumbent was found in the time budget; UNKNOWN is not proof of infeasibility.'
            if found['search']['status'] == 'UNKNOWN' else
            'Interpret status only within this finite candidate model; no multi-station score cutoff was applied.'
        )
    summary['solution_sha256'] = digest(solution_path)
    dump(out / 'ablation_summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
