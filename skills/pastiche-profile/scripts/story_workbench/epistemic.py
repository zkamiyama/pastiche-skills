"""Explicit perspective records and a deterministic first-order belief ledger.

A claim's evidential status is NOT a character's belief state. This module keeps
world changes, individual perceptions, accepted/unaccepted reports and reader
revelations separate. It validates declared access, not the meaning of prose.
"""
from __future__ import annotations
from copy import deepcopy
from .store import text, dump
from .epistemic_schema import normalize, SCHEMA, CHANNELS

BASES = {'perception', 'communication', 'inference', 'narration', 'unknown'}
RESERVED = {'$world', '$reader'}


def array(v, maximum=1000):
    if not isinstance(v, list) or len(v) > maximum:
        raise ValueError('invalid epistemic array')
    return v


def refs(v, allowed, *, minimum=0):
    result = array(v)
    if len(result) < minimum or any(not isinstance(x, str) or x not in allowed for x in result) or len(result) != len(set(result)):
        raise ValueError('unknown or duplicated epistemic reference')
    return result


def source_contract() -> str:
    return (
        ' Also return REQUIRED perspectives:[] for evidenced mental/world/reader states. '
        'A false belief is valid evidence about a mind, not a contradiction to delete. '
        'Do not infer that a present person saw, heard, understood or believed an event. '
        'Do not give a character narrator knowledge. holder is a supplied character ID, '
        '$world (narrator-described reality), or $reader (explicitly disclosed to reader). '
        'proposition.subject is a supplied character/world entity. Keep value short, '
        'and distinguish affirmed/denied/uncertain attitudes. basis: perception, '
        'communication, inference, narration, unknown. speaker is a character ID or null. '
        'event_id is a supplied event ID or null. Cite actual supplied segment IDs. '
        'At most 30 records. Do not fill missing chronology or mental states from stereotypes. '
        'Example shape: "perspectives":[{"holder":"c1","proposition":'
        '{"subject":"w1","predicate":"location","value":"以前の場所"},'
        '"stance":"affirmed","basis":"perception","speaker":null,"event_id":null,'
        '"evidence_ids":["actual segment ID"]}]. '
    )


def parse_source(raw: dict, entities: set[str], characters: set[str], events: set[str], evidence: set[str], chunk: list[dict]) -> list[dict]:
    if 'perspectives' not in raw:
        raise ValueError('perspectives array is required; absence is not unknown knowledge')
    result = []
    source = ''.join(u['text'] for u in chunk)
    def abstract(value, maximum):
        value = text(value, maximum)
        if len(value) >= 48 and any(value[i:i+48] in source for i in range(len(value)-47)):
            raise ValueError('long source quotation in perspective output')
        return value
    for row in array(raw['perspectives'], 30):
        if not isinstance(row, dict):
            raise ValueError('invalid perspective record')
        holder, prop = row.get('holder'), row.get('proposition')
        if holder not in characters | RESERVED or not isinstance(prop, dict) or prop.get('subject') not in entities:
            raise ValueError('unknown perspective holder or proposition entity')
        if row.get('basis') not in BASES or row.get('stance') not in {'affirmed', 'denied', 'uncertain'}:
            raise ValueError('invalid perspective basis/stance')
        if row.get('speaker') is not None and row['speaker'] not in characters:
            raise ValueError('unknown reported speaker')
        if row.get('event_id') is not None and row['event_id'] not in events:
            raise ValueError('unknown perspective event')
        result.append({'holder': holder, 'proposition': {'subject': prop['subject'],
            'predicate': abstract(prop.get('predicate'), 80), 'value': abstract(prop.get('value'), 280)},
            'stance': row['stance'], 'basis': row['basis'], 'speaker': row.get('speaker'),
            'event_id': row.get('event_id'), 'evidence_ids': refs(row.get('evidence_ids'), evidence, minimum=1)})
    return result


def plan_contract() -> str:
    return (
        ' REQUIRED epistemic timeline: distinguish objective world values, actor beliefs and reader disclosures. '
        'Use discrete facts {id,description,values:[...],initial_world:value|null}; null means unknown, '
        'not false. initial_beliefs contain {agent,fact,value,basis:given|perception|communication|inference}. '
        'For canon characters do not turn all available setting claims into their knowledge. '
        'Each scene needs an event: {id,scene_id,order,participants,access,updates,observations,messages,'
        'requires,reader_reveals}. order is an explicitly authored chronological integer, independent '
        'of scene presentation order. access maps a participant to available channels: sight/hearing/reading/touch. '
        'An absent/asleep/inattentive participant has no relevant access. '
        'updates:[{fact,value}] change ONLY reality. observations:[{agent,fact,channel}] derive the '
        'current world value only for eligible observers. Observers retain earlier beliefs until new evidence; '
        'unobserved changes never update minds. messages:[{speaker,recipients,accepted_by,fact,value,channel}] '
        'require hearing/reading access; heard is not believed, lying is allowed, accepting false testimony '
        'creates a false belief. requires:[{agent,fact,value,mode:believes|knows|unknown}] describe EVERY '
        'mental premise of that event BEFORE updates. inferences:[{agent,fact,value,premises:[{fact,value}]}] '
        'use only held premises and remain inferred. forget:[{agent,fact}] withdraws a belief. '
        'reader_reveals:[fact IDs] disclose the post-event world value in scene presentation order. '
        'Do not use removed facts/known_by_initial/reveals/requires_knowledge fields. '
        'epistemic:{schema:"pastiche.epistemic.timeline.v1",facts:[],initial_beliefs:[],events:[]} '
        'is the shape (include one event per scene even when there are no facts). '
    )


def simulate(timeline: dict, scenes: list[dict], characters: list[dict]) -> dict:
    """Replay an authored timeline, keeping false beliefs and unknowns intact.

    `knows` is checked against the modeled world and an evidential basis. It is
    not a philosophical proof or a guarantee about LLM extraction from prose.
    """
    if not isinstance(timeline, dict) or timeline.get('schema') != SCHEMA:
        raise ValueError('explicit epistemic timeline v1 required; regenerate legacy plans')
    timeline = normalize(timeline, scenes, characters)
    if len(dump(timeline)) > 2_000_000:
        raise ValueError('epistemic input exceeds the bounded replay budget')
    agents = {c['id'] for c in characters}
    by_scene = {s['id']: s for s in scenes}
    facts, world, minds = {}, {}, {a: {} for a in agents}
    history, heard, snapshots, normalized = [], {a: [] for a in agents}, [], []
    for f in array(timeline.get('facts'), 300):
        if not isinstance(f, dict): raise ValueError('invalid epistemic fact')
        fid = text(f.get('id'), 80)
        values = array(f.get('values'), 60)
        if fid in facts or not values or any(not isinstance(v, str) or not v or len(v) > 280 for v in values) or len(set(values)) != len(values):
            raise ValueError('fact values must be finite unique strings')
        initial = f.get('initial_world')
        if initial is not None and initial not in values:
            raise ValueError('unknown initial world value')
        facts[fid] = {'id': fid, 'description': text(f.get('description'), 400), 'values': values, 'initial_world': initial}
        world[fid] = initial

    def pair(row):
        if not isinstance(row, dict) or row.get('fact') not in facts:
            raise ValueError('unknown epistemic fact')
        value = row.get('value')
        if value is not None and value not in facts[row['fact']]['values']:
            raise ValueError('invalid epistemic value')
        return row['fact'], value

    def set_belief(agent, fid, value, basis, event, supports=None):
        if agent not in agents: raise ValueError('unknown belief holder')
        previous = deepcopy(minds[agent].get(fid))
        minds[agent][fid] = {'value': value, 'basis': basis, 'event_id': event, 'supports': supports or []}
        history.append({'agent': agent, 'fact': fid, 'previous': previous, 'current': deepcopy(minds[agent][fid])})

    initial = []
    seen_initial = set()
    for b in array(timeline.get('initial_beliefs'), 2000):
        fid, value = pair(b)
        agent, basis = b.get('agent'), b.get('basis')
        if agent not in agents or basis not in {'given', 'perception', 'communication', 'inference'} or (agent, fid) in seen_initial:
            raise ValueError('invalid or duplicate initial belief')
        seen_initial.add((agent, fid))
        set_belief(agent, fid, value, basis, None)
        initial.append({'agent': agent, 'fact': fid, 'value': value, 'basis': basis})
    events = array(timeline.get('events'), 1200)
    if len(events) * max(1, len(agents)) * max(1, len(facts)) > 250_000:
        raise ValueError('perspective snapshots exceed replay budget; split the plan')
    snapshot_bytes = 0
    seen_ids, seen_orders, covered = set(), set(), set()
    for e in events:
        if not isinstance(e, dict): raise ValueError('invalid epistemic event')
        eid, order, sid = text(e.get('id'), 80), e.get('order'), e.get('scene_id')
        if eid in seen_ids or type(order) is not int or order < 0 or order in seen_orders or sid not in by_scene:
            raise ValueError('duplicate/unknown epistemic event, chronological order or scene')
        seen_ids.add(eid); seen_orders.add(order); covered.add(sid)
    if covered != set(by_scene):
        raise ValueError('epistemic timeline must cover every scene')
    for e in sorted(events, key=lambda r: r['order']):
        eid, sid = e['id'], e['scene_id']
        participants = refs(e.get('participants'), set(by_scene[sid]['character_ids']))
        access = e.get('access')
        if not isinstance(access, dict) or set(access) - set(participants):
            raise ValueError('access refers to a nonparticipant')
        access = {a: refs(v, CHANNELS) for a, v in access.items()}
        def eligible(agent, channel):
            if agent not in participants or channel not in access.get(agent, []):
                raise ValueError('observation/transmission lacks declared perceptual access')
        before = {'world': deepcopy(world), 'beliefs': deepcopy(minds), 'reports': deepcopy(heard)}
        reqs = []
        for r in array(e.get('requires', []), 200):
            fid, val = pair(r); a, mode = r.get('agent'), r.get('mode')
            if a not in participants or mode not in {'believes', 'knows', 'unknown'}:
                raise ValueError('invalid mental requirement')
            belief = minds[a].get(fid, {})
            if mode == 'unknown':
                if val is not None or belief.get('value') is not None:
                    raise ValueError('character is not in the declared unknown state')
            elif val is None or belief.get('value') != val:
                raise ValueError('mental premise is unavailable or contradicts the character belief')
            elif mode == 'knows' and (world[fid] != val or belief.get('basis') in {'inference', 'unknown', 'forgotten'}):
                raise ValueError('belief cannot be used as verified knowledge of the modeled world')
            reqs.append({'agent': a, 'fact': fid, 'value': val, 'mode': mode})
        updates, updated = [], set()
        for row in array(e.get('updates', []), 300):
            fid, value = pair(row)
            if fid in updated: raise ValueError('duplicate world update in one event')
            updated.add(fid);world[fid] = value;updates.append({'fact': fid, 'value': value})
        observations = []
        for row in array(e.get('observations', []), 500):
            a, fid, channel = row.get('agent'), row.get('fact'), row.get('channel')
            eligible(a, channel)
            if fid not in facts or world[fid] is None:
                raise ValueError('cannot perceive an unspecified world value')
            # No model-supplied observation value can smuggle the wrong reality.
            if 'value' in row and row['value'] != world[fid]:
                raise ValueError('observation disagrees with modeled reality; use an explicitly inferred belief instead')
            set_belief(a, fid, world[fid], 'perception', eid)
            observations.append({'agent': a, 'fact': fid, 'channel': channel})
        messages = []
        for row in array(e.get('messages', []), 300):
            fid, value = pair(row)
            speaker, channel = row.get('speaker'), row.get('channel')
            recipients = refs(row.get('recipients'), set(participants))
            accepted = refs(row.get('accepted_by'), set(recipients))
            if speaker not in participants or channel not in {'hearing', 'reading'} or value is None:
                raise ValueError('invalid communication')
            for a in recipients:
                eligible(a, channel)
                heard[a].append({'speaker': speaker, 'fact': fid, 'value': value, 'event_id': eid, 'accepted': a in accepted})
                if a in accepted: set_belief(a, fid, value, 'communication', eid, [speaker])
            messages.append({'speaker': speaker, 'recipients': recipients, 'accepted_by': accepted,
                             'channel': channel, 'fact': fid, 'value': value})
        inferences = []
        for row in array(e.get('inferences', []), 200):
            fid, value = pair(row);a=row.get('agent')
            if a not in participants or value is None: raise ValueError('invalid inference agent/value')
            premises=[]
            for prem in array(row.get('premises'), 100):
                pf,pv=pair(prem)
                if pv is None or minds[a].get(pf, {}).get('value') != pv:
                    raise ValueError('inference uses information unavailable to the character')
                premises.append({'fact': pf, 'value': pv})
            if not premises: raise ValueError('inference must declare available premises')
            set_belief(a, fid, value, 'inference', eid, premises)
            inferences.append({'agent': a, 'fact': fid, 'value': value, 'premises': premises})
        forget=[]
        for row in array(e.get('forget', []), 200):
            a,fid=row.get('agent'),row.get('fact')
            if a not in participants or fid not in facts: raise ValueError('invalid belief withdrawal')
            set_belief(a, fid, None, 'forgotten', eid);forget.append({'agent': a, 'fact': fid})
        reveal = refs(e.get('reader_reveals', []), set(facts))
        normalized.append({'id': eid,'scene_id':sid,'order':e['order'],'participants':participants,'access':access,
            'requires':reqs,'updates':updates,'observations':observations,'messages':messages,
            'inferences':inferences,'forget':forget,'reader_reveals':reveal})
        snapshot = {'event_id':eid,'scene_id':sid,'order':e['order'],'before':before,
            'after':{'world':deepcopy(world),'beliefs':deepcopy(minds),'reports':deepcopy(heard)}}
        snapshot_bytes += len(dump(snapshot).encode('utf-8'))
        if snapshot_bytes > 16_000_000:
            raise ValueError('perspective snapshots exceed byte budget; split the plan')
        snapshots.append(snapshot)
    # Reader state follows presentation, not the modeled chronological sequence.
    reader, reader_views = {}, []
    by_event = {s['event_id']:s for s in snapshots}
    for scene in scenes:
        start=deepcopy(reader)
        for e in normalized:
            if e['scene_id'] == scene['id']:
                for fid in e['reader_reveals']:
                    reader[fid]={'value':by_event[e['id']]['after']['world'][fid],'event_id':e['id']}
        reader_views.append({'scene_id':scene['id'],'before':start,'after':deepcopy(reader)})
    return {'schema':'pastiche.epistemic.audit.v1',
        'timeline':{'schema':SCHEMA,'facts':list(facts.values()),'initial_beliefs':initial,'events':normalized},
        'events':snapshots,'reader':reader_views,'history':history,
        'final_world':world,'final_beliefs':minds,
        'warnings':([] if facts else ['No mental propositions declared; this is not evidence of full perspective coverage.']),
        'limits':['Checks declared first-order access/state transitions, not the semantics of raw prose.',
                  'Initial beliefs and access declarations are author/model assertions, not independent verification.',
                  'Higher-order beliefs, uncertain alternatives and logical entailment are not solved by this ledger.']}


def scene_view(plan: dict, scene_id: str, agent: str | None = None, *, event_id: str | None = None) -> dict:
    report = simulate(plan['epistemic'], plan['scenes'], plan['characters'])
    snapshots = [s for s in report['events'] if s['scene_id'] == scene_id]
    if not snapshots: raise ValueError('unknown scene')
    if event_id is not None:
        snapshots = [s for s in snapshots if s['event_id'] == event_id]
        if not snapshots:
            raise ValueError('event does not belong to the selected scene')
    if agent is None:
        return {'audience':'director','scene_id':scene_id,'events':snapshots,
                'reader':[r for r in report['reader'] if r['scene_id']==scene_id]}
    if agent not in {c['id'] for c in plan['characters']}:
        raise ValueError('unknown viewpoint character')
    # Do NOT export reality, other minds, future event summaries, or correctness
    # flags that would reveal that the viewpoint character has a false belief.
    # The writer starts BEFORE the first event in a scene (or an explicitly
    # selected event). Exporting all after-states leaks discoveries from later
    # in the same scene into earlier dialogue, even though they are "own" beliefs.
    selected = snapshots[0]
    beliefs = selected['before']['beliefs'][agent]
    reports = selected['before']['reports'][agent]
    visible = set(beliefs) | {r['fact'] for r in reports}
    facts = [{'id':f['id'],'description':f['description']} for f in report['timeline']['facts'] if f['id'] in visible]
    return {'schema':'pastiche.scene.perspective.v2','audience':'character','scene_id':scene_id,
            'character_id':agent, 'at':'before_event',
            'facts':facts,'events':[{'event_id':selected['event_id'],
                'beliefs_before':beliefs,'heard_reports':reports}],
            'rule':'Only this viewpoint at event entry is available. No future discoveries, objective truth or other minds.'}
