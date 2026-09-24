"""Fixed normalization and explicit preference weights, not lexicographic priorities."""
from fractions import Fraction
from functools import reduce
import copy
import math

METRICS = ('tardiness', 'makespan', 'energy', 'sorties')
UNITS = {'tardiness':1000, 'makespan':1000, 'energy':10**9, 'sorties':1}


def specification(vectors, weights, precision=1000000):
    if len(weights)!=4 or any(not math.isfinite(x) or x<=0 for x in weights):
        raise ValueError('四指标权重必须均为有限正数')
    fs=[Fraction(str(x)) for x in weights]
    multiple=math.lcm(*(x.denominator for x in fs))
    iw=[int(x*multiple) for x in fs]
    divisor=reduce(math.gcd,iw);iw=[x//divisor for x in iw]
    lower={k:min(v[k] for v in vectors) for k in METRICS}
    upper={k:max(v[k] for v in vectors) for k in METRICS}
    # Equal reference values must not make the metric disappear. Use one native unit.
    span={k:max(upper[k]-lower[k],UNITS[k] if upper[k]==lower[k] else 1) for k in METRICS}
    return {'weights':dict(zip(METRICS,weights)), 'integer_weights':dict(zip(METRICS,iw)),
            'alpha':dict(zip(METRICS,[x/sum(iw) for x in iw])),
            'lower':lower,'upper':upper,'span':span,'precision':precision,
            'normalization_source':'Four archived lexicographic solutions, fixed before weighted search',
            'score_quantization_absolute_error_bound':1/precision}


def score(vector, spec):
    return math.fsum(spec['alpha'][k]*(vector[k]-spec['lower'][k])/spec['span'][k] for k in METRICS)


def integer_score(vector, spec):
    p=spec['precision']
    return sum(spec['integer_weights'][k]*((p*vector[k]+spec['span'][k]-1)//spec['span'][k]) for k in METRICS)


def reselect_scenarios(payload):
    """Compare saved complete schedules under each preference; retain search endpoints.

    Cloud entries contain objective vectors only and are deliberately ineligible.
    This deterministic postprocessing is also safe to repeat during report export.
    """
    fields = ('schemes', 'schedules', 'objective_vectors')
    if 'scenario_search_endpoints' not in payload:
        payload['scenario_search_endpoints'] = {
            'scenario_' + metric: {field: copy.deepcopy(payload[field]['scenario_' + metric])
                                  for field in fields}
            for metric in METRICS
        }
    saved = {name: {field: payload[field][name] for field in fields}
             for name in payload['schemes'] if not name.startswith('scenario_')}
    saved.update(payload['scenario_search_endpoints'])
    selections = {}
    for metric in METRICS:
        name = 'scenario_' + metric
        spec = payload['scenario_specs'][metric]
        source = min(saved, key=lambda key: score(saved[key]['objective_vectors'], spec))
        for field in fields:
            payload[field][name] = copy.deepcopy(saved[source][field])
        selections[name] = {
            'source': source,
            'search_endpoint_score': score(saved[name]['objective_vectors'], spec),
            'selected_score': score(saved[source]['objective_vectors'], spec),
            'complete_candidates_compared': len(saved),
            'comparison_scope': 'saved complete schedules only; no additional optimization',
        }
    payload['scenario_reselected_sources'] = selections
    return selections


def add_objective(model, metrics, bounds, spec):
    terms=[];precision=spec['precision']
    for k in METRICS:
        divisor=spec['span'][k]
        maximum=bounds[k]
        if maximum*precision+divisor>=2**62:
            raise ValueError('归一化整数表达式可能溢出；请降低score_precision')
        norm=model.new_int_var(0,(maximum*precision+divisor-1)//divisor,'normalized_'+k)
        model.add_division_equality(norm,precision*metrics[k]+divisor-1,divisor)
        terms.append(spec['integer_weights'][k]*norm)
    return sum(terms)
