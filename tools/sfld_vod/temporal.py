"""Evidence-linked temporal review; extraction and observation counts stay distinct."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from .batch import select_clips, subtract_intervals
from .media import sha256, write_json
from .review import DOORS, read_jsonl, wilson

CONTENTS = ('empty', 'monster', 'wooden_box', 'chest', 'corpse', 'barrel',
            'mimic', 'fountain', 'lantern_zombie', 'storyteller', 'spider',
            'water', 'arena', 'armory', 'curse_shop', 'other')


def selection_basis(row):
    """Describe counted evidence without treating every verified side as a click."""
    if all(d.get('kind') == 'mystery' for d in row.get('doors', [])) and len(row.get('doors', [])) == 2:
        return 'both_sides_mystery'
    if ('chosen_side' not in row.get('verified_fields', [])
            or type(row.get('chosen_side')) is not int or row['chosen_side'] not in (0, 1)):
        return 'unverified'
    if row.get('chosen_side_status') == 'verified_by_resource_constraint':
        return 'verified_resource_constraint'
    return 'verified_other_method'


def normalize_generation(row):
    """Keep compulsory encounters and unresolved opening progress out of random-room odds.

    Reviewers may use 'ordinary' as a working label. A verified shop/boss/trial
    pair identifies a special phase directly. An unreadable stage-one counter
    does not prove that the five-room opening has ended.
    """
    ctx=row.get('context', {})
    phase=ctx.get('phase', 'ordinary')
    generation=row.get('generation')
    kinds={d.get('kind') for d in (row.get('doors') or [])}
    reason=None
    if 'doors' in row.get('verified_fields', []):
        special=('boss' if 'boss' in kinds else 'arena' if kinds & {'trial','exit_trial'}
                 else 'shop' if 'shop' in kinds else None)
        if special and (phase != special or generation != special):
            phase=generation=special;reason='Verified special encounter door pair.'
    if phase=='ordinary' and generation=='ordinary':
        room=ctx.get('room')
        if type(room) is int and 1<=room<=5:
            generation='opening';reason='Displayed counter belongs to the five-room opening.'
        elif ctx.get('stage')==1 and room is None:
            generation='unknown';reason='Stage-one counter is unreadable; opening versus ordinary is unverified.'
    if reason is None:
        return row
    return {**row,'generation':generation,'context':{**ctx,'phase':phase},
            'generation_note':reason}


def observation_counts(rows):
    """Count reviewed opportunities, never detector frames or unknown stone sets.

    A displayed counter is not a complete identity: an arena exit and the next
    ordinary choice may share it. Give those different phase labels. Unknown
    counters require a reviewed visit label, not a guessed counter sequence.
    """
    coverage = Counter(records=len(rows))
    identities = set()
    ids = set()
    groups = defaultdict(list)
    mysteries = defaultdict(list)
    for row in rows:
        row=normalize_generation(row)
        ident = row.get('id')
        if not ident or ident in ids:
            raise ValueError('Every observation needs a unique ID.')
        ids.add(ident)
        status = row.get('status', 'uncertain')
        if status not in ('decision', 'duplicate', 'not_decision', 'uncertain'):
            raise ValueError('Unknown observation status.')
        coverage[status] += 1
        if status != 'decision':
            continue
        fields = set(row.get('verified_fields', []))
        choice_known = ('chosen_side' in fields and type(row.get('chosen_side')) is int
                        and row['chosen_side'] in (0, 1))
        coverage['decision_choice_verified' if choice_known else 'decision_choice_unverified'] += 1
        if choice_known and row.get('chosen_side_status') == 'verified_by_resource_constraint':
            coverage['decision_choice_verified_resource_constraint'] += 1
        if not choice_known and row.get('chosen_side_status') == 'unresolved_direct_transition':
            coverage['decision_choice_reviewed_unresolved'] += 1
        pair = row.get('doors') or []
        trap_values = [d.get('trap') for d in pair]
        trap_known = sum(type(value) is bool for value in trap_values) if 'traps' in fields else 0
        coverage['decision_traps_complete' if trap_known == 2 else
                 'decision_traps_partial' if trap_known else 'decision_traps_unverified'] += 1
        ctx = row.get('context', {})
        run = ctx.get('run_id')
        phase = ctx.get('phase', 'ordinary')
        attempt = ctx.get('offer_attempt', 1)
        if type(attempt) is not int or attempt < 1:
            raise ValueError('Offer attempt must be a positive integer.')
        room = ctx.get('room')
        visit = ctx.get('visit_id')
        if room is not None and (type(room) is not int or not 0 <= room <= 100):
            raise ValueError('Displayed counter must be 0-100 or unknown.')
        # Reviewed event identities can include named arena rounds / boss retry.
        if not run or (room is None and not visit) or 'identity' not in fields:
            coverage['decision_identity_unverified'] += 1
            continue
        key = (row.get('source_id'), run, phase, room if room is not None else visit, attempt)
        if key in identities:
            raise ValueError('Repeated source/run/phase/room identity: mark duplicates explicitly.')
        identities.add(key)
        if row.get('generation') != 'ordinary' or phase != 'ordinary':
            coverage['nonordinary_or_unclassified'] += 1
            continue
        doors = row.get('doors')
        if ('doors' not in fields or not isinstance(doors, list) or len(doors) != 2
                or any(d.get('kind') not in DOORS for d in doors)):
            coverage['doors_unverified'] += 1
            continue
        for door in doors:
            if door.get('trap') is not None and type(door['trap']) is not bool:
                raise ValueError('Trap presence must be true, false, or unknown.')
        gems = ctx.get('gems')
        if ('stones' not in fields or ctx.get('stone_status') != 'verified'
                or not isinstance(gems, list)):
            coverage['stone_context_unknown'] += 1
            continue
        if any(not isinstance(g, str) or not g.strip() for g in gems):
            raise ValueError('Verified stones need explicit nonempty identifiers.')
        stage = ctx.get('stage')
        if stage is not None and (type(stage) is not int or not 1 <= stage <= 4):
            raise ValueError('Stage must be 1-4 or unknown.')
        run_class = ctx.get('run_class')
        if run_class not in (None, 'first', 'later'):
            raise ValueError('First/later event-run status must be explicit or unknown.')
        group = {'source_id': row['source_id'], 'version': ctx.get('version'),
                 'stage': stage, 'run_class': run_class, 'gems': sorted(set(gems)),
                 'theme': ctx.get('theme'), 'mode': ctx.get('mode')}
        group_key = json.dumps(group, sort_keys=True)
        groups[group_key].append(row)
        coverage['eligible_decisions'] += 1
        side = row.get('chosen_side')
        outcome = row.get('outcome', {})
        if not any(d['kind'] == 'mystery' for d in doors):
            continue
        coverage['mystery_offered'] += 1
        both_mystery = all(d['kind'] == 'mystery' for d in doors)
        if both_mystery and 'initial_content' in fields and 'entered_room' in fields:
            # Either selected side has the same conditional other-choice type.
            side = 0
        elif 'chosen_side' not in fields or type(side) is not int or side not in (0, 1):
            coverage['mystery_selection_unknown'] += 1
            continue
        if doors[side]['kind'] != 'mystery':
            coverage['mystery_not_selected'] += 1
            continue
        if ('initial_content' not in fields or outcome.get('initial_content') not in CONTENTS
                or not outcome.get('evidence')):
            coverage['selected_mystery_outcome_unknown'] += 1
            continue
        # A corpse awakening is still a corpse on entry, not an initial monster.
        conditional = {**group, 'other_choice': doors[1-side]['kind']}
        mysteries[json.dumps(conditional, sort_keys=True)].append(row)
        coverage['eligible_mystery_outcomes'] += 1
    decisions = []
    for key, members in sorted(groups.items()):
        pair_counts = Counter(tuple(sorted(d['kind'] for d in r['doors'])) for r in members)
        known_traps = [r for r in members if 'traps' in r.get('verified_fields', [])
                       and (any(d.get('trap') is True for d in r['doors'])
                            or all(d.get('trap') is False for d in r['doors']))]
        decisions.append({'context': json.loads(key), 'decisions': len(members),
                          'runs': len({r['context']['run_id'] for r in members}),
                          'pairs': [{'choices': list(pair), **wilson(n, len(members))}
                                    for pair, n in sorted(pair_counts.items())],
                          'pair_has_trap': wilson(sum(any(d.get('trap') is True for d in r['doors'])
                                                      for r in known_traps), len(known_traps))
                          if known_traps else None,
                          'trap_estimate_scope': 'reviewed subset; includes targeted positive reviews; not population prevalence',
                          'trap_status_unknown': len(members)-len(known_traps)})
    outcomes = []
    for key, members in sorted(mysteries.items()):
        counts = Counter(r['outcome']['initial_content'] for r in members)
        outcomes.append({'context': json.loads(key), 'opportunities': len(members),
                         'runs': len({r['context']['run_id'] for r in members}),
                         'selection_bases': dict(sorted(Counter(selection_basis(r) for r in members).items())),
                         'contents': {name: wilson(n, len(members)) for name, n in sorted(counts.items())}})
    return {'schema': 2, 'coverage': dict(coverage), 'door_groups': decisions,
            'mystery_groups': outcomes, 'calibrated': False,
            'interpretation': ['Unknown or incomplete stone sets are excluded, never assigned to the empty set.',
                               'Sources, versions, stages, first/later run and complete stone sets remain separate.',
                               'Mystery outcomes describe selected and visible rooms, conditional on the other choice.',
                               'Corpse awakening is separate from initial content. Hover is not selection evidence.',
                               'Resource-constrained selections are labeled separately from other verified methods; unresolved timing hypotheses are excluded.',
                               'Wilson intervals cover binomial sampling only, not missed rooms, label errors or clustering.',
                               'Review coverage and selection bias must be evaluated before simulator calibration.']}


def _local(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Evidence must be a file within the review root: {path}')
    return path.relative_to(root).as_posix()


def build_timeline(root, source_id, scene_files=(), candidate_files=(), observations=None, hud_suggestions=None):
    """Build an evidence browser from explicitly selected scan versions.

    Do not glob all historical scans: running a second detector is not a second
    observation. Inputs can overlap in time; the browser labels them candidates.
    """
    root = Path(root).resolve()
    source_path = root/source_id/'source.json'
    source = json.loads(source_path.read_text(encoding='utf-8'))
    url = urlparse(source['url'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
        raise ValueError('Expected a public source URL without credentials.')
    clips = []
    for quality in ('720p', '360p'):
        for marker, clip in select_clips(root/source_id/quality):
            clips.append({'start': clip['source_start'], 'end': clip['source_end'],
                          'quality': quality, 'path': _local(root, marker.parent/clip['file']),
                          'timing': clip.get('timing')})
    rows = []
    coverage = []
    for marker in map(Path, scene_files):
        data = json.loads(marker.read_text(encoding='utf-8'))
        inputs = data.get('inputs', {})
        for epoch in data.get('epochs', []):
            frames = []
            for field, stamp in (('image', epoch.get('best_time')), ('last_image', epoch['end'])):
                if epoch.get(field):
                    path = marker.parent/epoch[field]
                    frames.append({'image': _local(root, path), 'source_seconds': stamp,
                                   'role': field, 'sha256': sha256(path)})
            for item in epoch.get('evidence', []):
                path = marker.parent/item['image']
                frames.append({**item, 'image': _local(root, path), 'role': 'following', 'sha256': sha256(path)})
            rows.append({'id': epoch.get('id') or f"{source_id}:{epoch['start']:.3f}:scene",
                         'source_id': source_id, 'start': epoch['start'], 'end': epoch['end'],
                         'best_time':epoch.get('best_time',epoch['start']),
                         'counter_view_id':epoch.get('source_id') or source_id,
                         'stage_suggestion': epoch.get('stage_suggestion'), 'evidence': frames,
                         'kind': 'background_candidate', 'clip_boundary_review': epoch.get('clip_boundary_review', False)})
        if data.get('decoded_frames') and inputs.get('fps'):
            start = data.get('source_start', inputs.get('source_start', 0))
            coverage.append([start, start+data['decoded_frames']/inputs['fps']])
        elif data.get('reported_scan_interval') and data.get('coverage_provenance'):
            coverage.append(data['reported_scan_interval'])
    for path in map(Path, candidate_files):
        for candidate in read_jsonl(path):
            image = path.parent/candidate['image']
            digest = sha256(image)
            if digest != candidate['image_sha256']:
                raise ValueError('Candidate image changed.')
            rows.append({'id': candidate['id'], 'source_id': source_id,
                         'start': candidate['source_seconds'], 'end': candidate.get('episode_end', candidate['source_seconds']),
                         'kind': 'door_template_candidate', 'suggested_doors': candidate.get('suggested_doors'),
                         'evidence': [{'image': _local(root, image), 'sha256': digest,
                                       'source_seconds': candidate['source_seconds'], 'role': 'candidate'}]})
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Choose one version of each scan; candidate IDs overlap.')
    if hud_suggestions:
        marker=Path(hud_suggestions)
        suggestions=json.loads(marker.read_text(encoding='utf-8'))
        by_id={r['candidate_id']:r for r in suggestions['rows']}
        for row in rows:
            hud=by_id.get(row['id'])
            if hud is None:
                continue
            row['hud_suggestions']=hud
            native=[]
            for sample in hud['samples']:
                path=marker.parent/sample['image']
                digest=sha256(path)
                if digest!=sample['image_sha256']:
                    raise ValueError('Native HUD evidence changed.')
                native.append({'image':_local(root,path),'sha256':digest,
                               'source_seconds':sample['source_seconds'],'role':'native_720_'+sample['role']})
            row['evidence']=native+row['evidence']
    annotations=[normalize_generation(row) for row in read_jsonl(observations)] if observations else []
    candidate_ids={r['id'] for r in rows}
    by_candidate={r['id']:r for r in rows}
    for annotation in annotations:
        evidence=[]
        for owner in (annotation, annotation.get('outcome', {})):
            normalized=[]
            for item in owner.get('evidence', []):
                path=Path(item['image'])
                if not path.is_absolute():
                    path=root/path
                name=_local(root,path);digest=sha256(path)
                if item.get('sha256') and item['sha256']!=digest:
                    raise ValueError('Reviewed evidence changed.')
                normalized.append({**item,'image':name,'sha256':digest,
                                   'role':item.get('role','reviewed_evidence')})
            if normalized:
                owner['evidence']=normalized
                evidence.extend(normalized)
        candidate=by_candidate.get(annotation.get('candidate_id')) or by_candidate.get(annotation['id'])
        if candidate is not None:
            known={item['image'] for item in candidate['evidence']}
            for item in evidence:
                if item['image'] not in known:
                    candidate['evidence'].append(item)
                    known.add(item['image'])
            continue
        if evidence:
            ident=annotation['id']+':manual_candidate'
            stamp=annotation['source_seconds']
            annotation['candidate_id']=ident
            rows.append({'id':ident,'source_id':source_id,'start':stamp,'end':stamp,
                         'kind':'manually_recovered','evidence':evidence})
            candidate_ids.add(ident)
            by_candidate[ident]=rows[-1]
    rows.sort(key=lambda r: (r['start'], r['id']))
    from .room_sequence import attach_shared_sequence
    counter_sequence=attach_shared_sequence(rows,root,source_id)
    downloaded = [[c['start'], c['end']] for c in clips]
    result = {'schema': 2, 'source': source, 'clips': clips, 'candidates': rows,
              'counter_sequence':counter_sequence,
              'observations': annotations,
              'revision': hashlib.sha256(json.dumps(annotations, sort_keys=True).encode()).hexdigest()[:20],
              'coverage': {'candidates': len(rows), 'ungated_scan_intervals': coverage,
                           'downloaded_without_ungated_scan': subtract_intervals(downloaded, coverage)},
              'interpretation': 'Candidates may merge or split decisions. Use video, context frames and reviewed visit identities.'}
    write_json(root/(source_id+'-timeline.json'), result)
    template = Path(__file__).with_name('timeline.html').read_text(encoding='utf-8')
    payload = json.dumps(result, ensure_ascii=True).replace('<', '\\u003c')
    page = root/(source_id+'-review.html')
    page.write_text(template.replace('__PAYLOAD__', payload), encoding='utf-8')
    return result
