"""Check that controlled reintroductions of audited defects produce counterexamples.

Only trusted repository source and the fixed edits below are compiled. No model
response, user corpus or external Python is executed by this test utility.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import types
from model_check import run_checks
import story_workbench.epistemic as implementation

MUTATIONS = {
 'lost_world_update': ('world[fid] = value;updates.append', 'updates.append'),
 'perception_records_unknown': ("set_belief(a, fid, world[fid], 'perception', eid)", "set_belief(a, fid, None, 'perception', eid)"),
 'all_heard_reports_are_believed': ('if a in accepted: set_belief', 'if True: set_belief'),
 'testimony_changes_reality': ("            messages.append({'speaker': speaker", "            world[fid] = value\n            messages.append({'speaker': speaker"),
 'forgetting_is_ignored': ("set_belief(a, fid, None, 'forgotten', eid);forget.append", 'forget.append'),
 'perceptual_access_guard_removed': ('if agent not in participants or channel not in access.get(agent, []):', 'if False:'),
 'schema_and_chronology_guard_removed': ('timeline = normalize(timeline, scenes, characters)', 'timeline = deepcopy(timeline)'),
 'future_belief_leak': ("beliefs = selected['before']['beliefs'][agent]", "beliefs = selected['after']['beliefs'][agent]"),
}


def run_mutations():
    source=Path(implementation.__file__).read_text(encoding='utf-8')
    rows=[]
    for name,(old,new) in MUTATIONS.items():
        if source.count(old)!=1:
            raise AssertionError('mutation no longer matches source: '+name)
        module=types.ModuleType('story_workbench._verification_mutant')
        module.__package__='story_workbench'
        # A syntax/import failure is an invalid mutant, never counted as killed.
        exec(compile(source.replace(old,new),'<controlled verification mutant>','exec'),module.__dict__)
        try:run_checks(module.simulate,module.scene_view,two_facts=False)
        except AssertionError as exc:
            rows.append({'mutation':name,'detected':True,'counterexample':str(exc)[:700]})
        else:
            rows.append({'mutation':name,'detected':False})
    if not all(r['detected'] for r in rows):
        raise AssertionError('a controlled defect escaped: '+repr(rows))
    return {'schema':'pastiche.mutation-verification.v1','status':'passed',
            'mutants':len(rows),'detected':sum(r['detected'] for r in rows),'results':rows,
            'limits':'These eight controlled defects are not an exhaustive mutation analysis.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path);args=parser.parse_args()
    result=run_mutations();content=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.out:args.out.write_text(content,encoding='utf-8')
    print(content)
