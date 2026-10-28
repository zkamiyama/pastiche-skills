"""Closed, detached input contract for first-order epistemic replay.

Normalize once before replay. Reject misspelled operations rather than silently
losing a state change. Presentation order is independent of causal chronology.
"""
from __future__ import annotations
from copy import deepcopy
from json_boundary import validate_data
from .store import text

SCHEMA = 'pastiche.epistemic.timeline.v1'
CHANNELS = {'sight', 'hearing', 'reading', 'touch'}


def rows(value, limit=1000):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('invalid epistemic array')
    return value


def record(value, required, optional=()):
    if not isinstance(value, dict) or set(value)-set(required)-set(optional) or set(required)-set(value):
        raise ValueError('invalid or unknown epistemic fields')
    return value


def identifier(value):
    checked = text(value, 100)
    if checked != value or any(c.isspace() for c in value):
        raise ValueError('identifier must not contain whitespace')
    return value


def references(values, allowed, minimum=0):
    values = rows(values)
    if len(values) < minimum or any(not isinstance(v, str) or v not in allowed for v in values) or len(set(values)) != len(values):
        raise ValueError('unknown or duplicated epistemic reference')
    return list(values)


def normalize(timeline, scenes, characters):
    if not isinstance(timeline, dict) or timeline.get('schema') != SCHEMA:
        raise ValueError('explicit epistemic timeline v1 required; regenerate legacy plans')
    validate_data(timeline)
    record(timeline, ('schema','facts','initial_beliefs','events'))
    agents = [identifier(c.get('id')) if isinstance(c, dict) else identifier(None) for c in rows(characters, 50)]
    scene_ids = [identifier(s.get('id')) if isinstance(s, dict) else identifier(None) for s in rows(scenes, 200)]
    if not agents or not scene_ids or len(set(agents)) != len(agents) or len(set(scene_ids)) != len(scene_ids):
        raise ValueError('duplicate or empty character/scene IDs')
    agents = set(agents)
    by_scene = {s['id']:s for s in scenes}
    for scene in scenes:
        references(scene.get('character_ids'), agents)
        deps = references(scene.get('depends_on', []), set(scene_ids))
        if scene['id'] in deps:
            raise ValueError('scene cannot be its own prerequisite')
    result = deepcopy(timeline)
    facts = {}
    for f in rows(result['facts'], 300):
        record(f, ('id','description','values','initial_world'))
        fid = identifier(f['id'])
        text(f['description'],400)
        values=rows(f['values'],60)
        if fid in facts or not values or any(not isinstance(v,str) or not v.strip() or len(v)>280 for v in values) or len(set(values)) != len(values):
            raise ValueError('fact values must be finite unique strings')
        if f['initial_world'] is not None and f['initial_world'] not in values:
            raise ValueError('unknown initial world value')
        facts[fid] = values

    def fact(row, with_value=True):
        fid=row.get('fact')
        if not isinstance(fid,str) or fid not in facts:
            raise ValueError('unknown epistemic fact')
        if with_value and row['value'] is not None and row['value'] not in facts[fid]:
            raise ValueError('invalid epistemic value')

    def actor(value):
        if not isinstance(value,str) or value not in agents:
            raise ValueError('unknown belief holder')

    for b in rows(result['initial_beliefs'],2000):
        record(b, ('agent','fact','value','basis'));actor(b['agent']);fact(b)
        if not isinstance(b['basis'],str) or b['basis'] not in {'given','perception','communication','inference'}:
            raise ValueError('invalid initial belief basis')
    definitions = {
        'requires': (('agent','fact','value','mode'), (), 200),
        'updates': (('fact','value'), (), 300),
        'observations': (('agent','fact','channel'), ('value',), 500),
        'messages': (('speaker','recipients','accepted_by','fact','value','channel'), (), 300),
        'inferences': (('agent','fact','value','premises'), (), 200),
        'forget': (('agent','fact'), (), 200),
    }
    orders, event_ids, covered, bounds = set(), set(), set(), {}
    for e in rows(result['events'],1200):
        record(e, ('id','scene_id','order','participants','access'), (*definitions,'reader_reveals'))
        eid=identifier(e['id']);sid=e['scene_id'];order=e['order']
        if not isinstance(sid,str) or sid not in by_scene or eid in event_ids or type(order) is not int or order<0 or order in orders:
            raise ValueError('duplicate/unknown event, chronological order or scene')
        event_ids.add(eid);orders.add(order);covered.add(sid)
        low, high = bounds.get(sid,(order,order));bounds[sid]=(min(low,order),max(high,order))
        participants=set(references(e['participants'],set(by_scene[sid]['character_ids'])))
        if not isinstance(e['access'],dict) or set(e['access'])-participants:
            raise ValueError('access refers to a nonparticipant')
        for channels in e['access'].values():references(channels, CHANNELS)
        for name,(required,optional,limit) in definitions.items():
            for row in rows(e.get(name,[]),limit):
                record(row,required,optional)
                fact(row, name not in {'observations','forget'} or 'value' in row)
                if 'agent' in row:actor(row['agent'])
                if name=='messages':
                    actor(row['speaker'])
                    recipients=references(row['recipients'],participants)
                    references(row['accepted_by'],set(recipients))
                if 'channel' in row and (not isinstance(row['channel'],str) or row['channel'] not in CHANNELS):
                    raise ValueError('invalid perception channel')
                if name=='requires' and (not isinstance(row['mode'],str) or row['mode'] not in {'believes','knows','unknown'}):
                    raise ValueError('invalid mental requirement')
                if name=='inferences':
                    for premise in rows(row['premises'],100):
                        record(premise,('fact','value'));fact(premise)
        references(e.get('reader_reveals',[]),set(facts))
    if covered != set(scene_ids):
        raise ValueError('epistemic timeline must cover every scene')
    for scene in scenes:
        for dependency in scene.get('depends_on',[]):
            if bounds[dependency][1] >= bounds[scene['id']][0]:
                raise ValueError('causal prerequisite is not completed earlier in story chronology')
    return result
