"""Numerical-only continuation with unchanged physical model and primary weights."""
from pathlib import Path
from dataclasses import asdict
import argparse
import json
import time
import run_q2


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=360)
    parser.add_argument('--seeds',default='0,1,2')
    parser.add_argument('--input',default='outputs/q2_weighted/tables/results.json')
    parser.add_argument('--output',default='outputs/q2_improved/dynamic_experiment.json')
    parser.add_argument('--additional',default='outputs/q2_improved/pool_experiment.json')
    args=parser.parse_args()
    from uav_rescue.q2.data import load_scheduling_inputs
    from uav_rescue.q2.routes import RouteFactory
    from uav_rescue.q2.dynamic_search import improve
    from uav_rescue.common.data import file_hash
    project=run_q2.PROJECT
    source=project/args.input;payload=json.loads(source.read_text(encoding='utf-8'))
    old=json.loads(Path(payload['reference_path']).read_text(encoding='utf-8'))
    paths=old['metadata']['config']['paths'];physics=old['metadata']['config']['physics']
    data=load_scheduling_inputs((project/paths['data_root']).resolve(),(project/paths['template']).resolve())
    factory=RouteFactory(data,physics)
    initial=[];seen=set()
    for name,schedule in payload['schedules'].items():
        if str(schedule) in seen:continue
        seen.add(str(schedule));pool={}
        for row in payload['schemes'][name]['sorties']:
            p=factory.make(row['drone'],row['boxes'],row['visits'])
            if p is None:raise AssertionError('输入路线物理不可行')
            pool[p.id]=p
        initial.append((name,schedule,pool))
    additional=project/args.additional
    additional_hash=None
    if additional.exists():
        extra=json.loads(additional.read_text(encoding='utf-8'))
        if extra['spec']!=payload['spec']:raise AssertionError('禁止跨归一化尺度混用')
        records={'pool_best':{'schedule':extra['schedule'],'result':extra['result']},**extra['snapshots']}
        for name,item in records.items():
            pool={}
            for row in item['result']['sorties']:
                p=factory.make(row['drone'],row['boxes'],row['visits'])
                if p is None:raise AssertionError('新增来源路线物理不可行')
                pool[p.id]=p
            initial.append((name,item['schedule'],pool))
        additional_hash=file_hash(additional)
    output=project/args.output;output.parent.mkdir(parents=True,exist_ok=True)
    def notify(row,schedule,pool):
        print(json.dumps({k:row[k] for k in ['iteration','elapsed_s','score','summary']},ensure_ascii=False),flush=True)
    result=improve(factory,payload['spec'],initial,args.seconds,tuple(map(int,args.seeds.split(','))),notify)
    pool=result.pop('pool')
    result['generated_candidates']=len(pool)
    result['candidates']=[asdict(pool[a['candidate']]) for a in result['schedule']]
    result.update(spec=payload['spec'],source_file=str(source),source_sha256=file_hash(source),
                  input_sha256=old['metadata']['input_sha256'],parameters=vars(args),
                  additional_source_sha256=additional_hash,
                  code_sha256={str(p):file_hash(p) for p in [project/'src/uav_rescue/q2/dynamic_search.py',project/'src/uav_rescue/q2/fast_schedule.py',Path(__file__)]})
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'score':result['score'],'summary':result['main']['summary'],
                      'iterations':result['iterations'],'evaluations':result['evaluations']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
