"""Exhaustive finite checks against an independently written small-state oracle.

Not an unbounded proof of Python or a test of LLM literary understanding.
All propositions, participants and histories below are synthetic.
"""
from __future__ import annotations
import argparse
import copy
import itertools
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'))
from story_workbench.epistemic import simulate, scene_view, SCHEMA

VALUES = (None, 'v0', 'v1')
AGENTS = ('A','B')
# value and evidential basis are separate; an absent modeled belief is unknown.
ACTIONS = [('update',v) for v in VALUES] + [('observe',a) for a in AGENTS]
ACTIONS += [('message',a,v,accept) for a in AGENTS for v in VALUES[1:] for accept in (False,True)]
ACTIONS += [('forget',a) for a in AGENTS] + [('infer',a,v) for a in AGENTS for v in VALUES[1:]]
ACTIONS += [('knows',a) for a in AGENTS] + [('disclose',)]


def oracle(state, action, fid):
    """Small algebraic spec; does not call implementation or its validators."""
    world, beliefs = copy.deepcopy(state)
    tag=action[0]
    if tag=='update':
        world[fid]=action[1]
    elif tag=='observe':
        if world[fid] is None:return None
        beliefs[action[1]][fid]=(world[fid],'perception')
    elif tag=='message':
        speaker,value,accepted=action[1:]
        if accepted:beliefs['B' if speaker=='A' else 'A'][fid]=(value,'communication')
    elif tag=='forget':
        beliefs[action[1]][fid]=(None,'forgotten')
    elif tag=='infer':
        if beliefs[action[1]][fid][0] is None:return None
        beliefs[action[1]][fid]=(action[2],'inference')
    elif tag=='knows':
        value,basis=beliefs[action[1]][fid]
        if value is None or value!=world[fid] or basis in {'inference','forgotten',None}:return None
    elif tag!='disclose':
        raise AssertionError('unknown specification operation')
    return world, beliefs


def event_for(action, fid, state, index):
    # Only operation syntax is shared with the system under test. Values and
    # expected state transitions are produced by the separate oracle above.
    e={'id':f'E{index}','scene_id':f'S{index}','order':index,'participants':list(AGENTS),
       'access':{a:['sight','hearing'] for a in AGENTS}}
    tag=action[0]
    if tag=='update':e['updates']=[{'fact':fid,'value':action[1]}]
    elif tag=='observe':e['observations']=[{'agent':action[1],'fact':fid,'channel':'sight'}]
    elif tag=='message':
        a,v,accept=action[1:];b='B' if a=='A' else 'A'
        e['messages']=[{'speaker':a,'recipients':[b],'accepted_by':[b] if accept else [],
                        'fact':fid,'value':v,'channel':'hearing'}]
    elif tag=='forget':e['forget']=[{'agent':action[1],'fact':fid}]
    elif tag=='infer':e['inferences']=[{'agent':action[1],'fact':fid,'value':action[2],
                   'premises':[{'fact':fid,'value':state[1][action[1]][fid][0]}]}]
    elif tag=='knows':e['requires']=[{'agent':action[1],'fact':fid,
                         'value':state[1][action[1]][fid][0],'mode':'knows'}]
    elif tag=='disclose':e['reader_reveals']=[fid]
    return e


def plan_for(initial, operations):
    world,beliefs=copy.deepcopy(initial)
    facts=list(world)
    timeline={'schema':SCHEMA,'facts':[{'id':f,'description':'neutral modeled property',
              'values':list(VALUES[1:]),'initial_world':world[f]} for f in facts],
              'initial_beliefs':[{'agent':a,'fact':f,'value':value,'basis':basis}
                  for a in AGENTS for f,(value,basis) in beliefs[a].items() if basis is not None], 'events':[]}
    expected=(world,beliefs)
    for i,(action,fid) in enumerate(operations):
        timeline['events'].append(event_for(action,fid,expected,i))
        expected=oracle(expected,action,fid)
        if expected is None:break
    scenes=[{'id':e['scene_id'],'character_ids':list(AGENTS),'depends_on':[]} for e in timeline['events']]
    return timeline,scenes,[{'id':a} for a in AGENTS],expected


def run_checks(simulator=simulate, viewer=scene_view, *, two_facts=True):
    counts={'checked':0,'valid':0,'rejected':0,'private_view_pairs':0}
    def check(initial,operations):
        timeline,scenes,chars,expected=plan_for(initial,operations)
        counts['checked']+=1
        try:result=simulator(timeline,scenes,chars)
        except ValueError:
            if expected is not None:raise AssertionError('valid specification trace was rejected: '+repr(operations))
            counts['rejected']+=1;return
        if expected is None:raise AssertionError('invalid specification trace accepted: '+repr(operations))
        actual=(result['final_world'],{a:{f:(result['final_beliefs'][a].get(f,{}).get('value'),
                    result['final_beliefs'][a].get(f,{}).get('basis')) for f in initial[0]} for a in AGENTS})
        if actual!=expected:raise AssertionError('reference transition mismatch: '+repr((operations,expected,actual)))
        # Normalization and serialization are semantics-preserving and do not
        # cause caller aliasing or a different result on a second replay.
        normalized=simulator(json.loads(json.dumps(result['timeline'])),scenes,chars)
        if normalized!=result:raise AssertionError('replay normalization is not idempotent')
        counts['valid']+=1
    for w,a,b in itertools.product(VALUES,repeat=3):
        initial=({'F':w},{'A':{'F':(a,'given' if a is not None else None)},
                         'B':{'F':(b,'given' if b is not None else None)}})
        for ops in itertools.product(ACTIONS,repeat=2):
            check(initial,[(op,'F') for op in ops])
    if two_facts:
        # Exhaust every initial value combination for two independent facts
        # and all one-step updates; this tests cross-fact and cross-agent frame conditions.
        for values in itertools.product(VALUES,repeat=6):
            w0,w1,a0,a1,b0,b1=values
            initial=({'F':w0,'G':w1},{'A':{'F':(a0,'given' if a0 is not None else None),'G':(a1,'given' if a1 is not None else None)},
                                     'B':{'F':(b0,'given' if b0 is not None else None),'G':(b1,'given' if b1 is not None else None)}})
            for fid in ('F','G'):
                for op in ACTIONS:check(initial,[(op,fid)])
    # Relational check: different secret worlds and other minds must yield the
    # same character entry view if that character has the same private state.
    for own in VALUES:
        views=[]
        for world,other in itertools.product(VALUES,repeat=2):
            initial=({'F':world},{'A':{'F':(own,'given' if own is not None else None)},
                                 'B':{'F':(other,'given' if other is not None else None)}})
            t,s,c,_=plan_for(initial,[(('update','v1'),'F'),(('observe','A'),'F')])
            views.append(viewer({'epistemic':t,'scenes':s,'characters':c},'S1','A'))
        if any(v!=views[0] for v in views[1:]):raise AssertionError('private view leaks invisible world/other-mind state')
        counts['private_view_pairs']+=len(views)-1
    # Same past, different future discovery: the entry projection must not
    # reveal which observation the scene will eventually contain.
    for own in VALUES:
        pair=[]
        for future in VALUES[1:]:
            initial=({'F':own},{a:{'F':(own,'given' if own is not None else None)} for a in AGENTS})
            t,scenes,chars,_=plan_for(initial,[(('observe','A'),'F')])
            t['events'][0]['updates']=[{'fact':'F','value':future}]
            pair.append(viewer({'epistemic':t,'scenes':scenes,'characters':chars},'S0','A'))
        if pair[0]!=pair[1]:raise AssertionError('entry projection reveals a future discovery')
        counts['private_view_pairs']+=1
    # Access and chronology are rejection contracts as well as normal-case
    # state transitions; mutations that omit guards must be caught.
    initial=({'F':'v0'},{a:{'F':('v0','given')} for a in AGENTS})
    for guard in ('absent','blind','future_prerequisite'):
        t,scenes,chars,_=plan_for(initial,[(('observe','A'),'F'),(('update','v1'),'F')])
        if guard=='absent':
            t['events'][0]['participants']=['B'];t['events'][0]['access'].pop('A')
        elif guard=='blind':t['events'][0]['access']['A']=[]
        else:
            scenes[0]['depends_on']=['S1']
        try:simulator(t,scenes,chars)
        except ValueError:pass
        else:raise AssertionError('missing access/chronology guard: '+guard)
        counts['checked']+=1;counts['rejected']+=1
    return {'schema':'pastiche.bounded-verification.v1','status':'passed',
            'method':'exhaustive finite transition comparison and relational private-view checks',
            'bounds':{'agents':2,'value_domain_size':3,'one_fact_trace_length':2,'two_fact_trace_length':1 if two_facts else 0,
                      'operation_variants':len(ACTIONS)},**counts,
            'limits':['Not a proof of all Python code or unbounded traces.','Free-text semantics and LLM extraction are outside the model.',
                      'Access and initial states are explicit assumptions, not independently inferred evidence.']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path);p.add_argument('--one-fact-only',action='store_true');a=p.parse_args()
    result=run_checks(two_facts=not a.one_fact_only)
    content=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.out:a.out.write_text(content,encoding='utf-8')
    print(content)
