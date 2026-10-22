"""Combine a complete offer inventory with explicitly scoped evidence supplements."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from .media import sha256, write_json
from .review import PHYSICAL, read_jsonl, wilson
from .full_traps import timing_problem, TIMING_POLICY


def frequency(count, total):
    return {'count': count, 'denominator': total, 'fraction': count / total if total else None}


def unique(rows, key='observation_id'):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('Duplicate visits in an analysis input.')
    return result


def offer_summary(rows):
    pairs = Counter(r['pair_family'] for r in rows)
    styles = Counter(k for r in rows for k in r['offered_kinds'] if k in PHYSICAL)
    gates = {'unlocked': 'spying', 'double_locked': 'lodestone', 'epic': 'devil'}
    anomalies = [r['observation_id'] for r in rows for k in r['offered_kinds']
                 if k in gates and gates[k] not in r['context']['gems']]
    return {'offers': len(rows),
            'pair_frequencies': {k: frequency(n, len(rows)) for k, n in sorted(pairs.items())},
            'physical_slots': sum(styles.values()),
            'physical_style_frequencies': {k: frequency(n, sum(styles.values())) for k, n in sorted(styles.items())},
            'gate_anomalies': anomalies}


def pair_report(rows):
    unique(rows)
    groups = defaultdict(list)
    sources = defaultdict(list)
    for r in rows:
        ctx = r['context']
        key = {k: ctx.get(k) for k in ('version', 'stage', 'run_class', 'theme', 'mode')}
        key.update(source_id=r['source_id'], gems=sorted(ctx['gems']))
        groups[json.dumps(key, sort_keys=True)].append(r)
        sources[r['source_id']].append(r)
    return {'pooled_descriptive': offer_summary(rows),
            'no_active_stones': offer_summary([r for r in rows if r['context']['gems'] == []]),
            'stone_enabled_cohorts': {gem: offer_summary([r for r in rows if gem in r['context']['gems']])
                                     for gem in ('spying', 'lodestone', 'devil')},
            'by_source': {sid: offer_summary(group) for sid, group in sorted(sources.items())},
            'by_full_context': [{'context': json.loads(k), **offer_summary(g)} for k, g in sorted(groups.items())],
            'interpretation': 'Offered pairs are observed frequencies in these selected VOD windows. Full stone sets, source/version, stage and known event-run class remain separate. Stone-enabled cohorts overlap; do not add their denominators. Wall is a physical style, not an entered room.'}


def trap_summary(rows):
    positives = [r for r in rows if any(t is True for t in r['machine_sides'])]
    return {'offers': len(rows),
            'native_two_frame_records': sum(len(r.get('evidence', [])) == 2 and not r.get('quarantine_reasons') for r in rows),
            'raw_two_frame_records': sum(len(r.get('evidence', [])) == 2 for r in rows),
            'quarantined_records': sum(bool(r.get('quarantine_reasons')) for r in rows),
            'machine_positive_pairs': len(positives),
            'machine_positive_sides': sum(t is True for r in rows for t in r['machine_sides']),
            'machine_positive_subtypes': dict(Counter(p['visual'] for r in positives
                for p in r.get('prediction', []) if p['present'] is True)),
            'unresolved_scan_records': sum(r['status'] == 'unresolved' for r in rows),
            'no_machine_positive': len(rows) - len(positives),
            'verified_positive_pairs_in_existing_ledger': sum(any(t is True for t in r['verified_traps']) for r in rows),
            'verified_clear_pairs_in_existing_ledger': sum(all(t is False for t in r['verified_traps']) for r in rows)}


def trap_report(scans, offers, blind_sample=None, reviews=(), door_reviews=(), identity_reviews=None, export_scan=None):
    """Recovery files can replace only unresolved records, never add opportunities."""
    base_ids = set(unique(scans[0])) if scans else set()
    offered_ids = set(unique(offers))
    if offered_ids - base_ids:
        raise ValueError('Primary trap scan does not cover every eligible ordinary offer.')
    combined = {}
    for index, scan in enumerate(scans):
        indexed = unique(scan)
        if index and set(indexed) - base_ids:
            raise ValueError('Recovery scan adds visits absent from the primary inventory.')
        for r in scan:
            ident = r['observation_id']
            if ident in combined:
                previous = combined[ident]
                if (previous['source_id'] != r['source_id'] or previous['context'] != r['context']):
                    raise ValueError('Recovery scan changes the visit context.')
                if previous['status'] != 'unresolved':
                    raise ValueError('Recovery scan would replace an already scanned visit.')
            combined[ident] = r
    if export_scan:
        # Public recount input: preserve original predictions and timing proof,
        # excluding bulky match candidates and machine-specific filesystem paths.
        keys = ('observation_id', 'source_id', 'source_seconds', 'context', 'generation',
                'doors', 'verified_traps', 'source_interval', 'stamps', 'video_sha256',
                'clip_start', 'scan_identity', 'status', 'reason', 'machine_sides')
        compact = []
        for r in combined.values():
            row = {k: r[k] for k in keys if k in r}
            row['evidence'] = [{k: v for k, v in e.items() if k != 'image'} for e in r.get('evidence', [])]
            row['prediction'] = [{k: p[k] for k in ('present', 'visual') if k in p} for p in r.get('prediction', [])]
            compact.append(row)
        export_scan.parent.mkdir(parents=True, exist_ok=True)
        export_scan.write_text(''.join(json.dumps(r, separators=(',', ':'))+'\n' for r in compact), encoding='utf-8')
    door_qc = unique(list(door_reviews))
    if set(door_qc) - base_ids:
        raise ValueError('Door QC refers to visits outside the primary inventory.')
    quarantined = []
    for ident, record in combined.items():
        reasons = []
        issue = timing_problem(record)
        if issue:
            reasons.append(issue)
        if door_qc.get(ident, {}).get('verdict') == 'temporal_mismatch':
            reasons.append('Independent door review identifies a different visit.')
        if reasons:
            quarantined.append({'observation_id': ident, 'reasons': reasons,
                                'previous_machine_positive': any(t is True for t in record['machine_sides'])})
            combined[ident] = {**record, 'status': 'unresolved', 'machine_sides': [None, None],
                              'prediction': [], 'quarantine_reasons': reasons}
    rows = list(combined.values())
    eligible = unique(offers)
    groups = defaultdict(list)
    strata = defaultdict(list)
    families = defaultdict(list)
    for r in rows:
        groups[r['source_id']].append(r)
        if r['observation_id'] in eligible:
            offered = eligible[r['observation_id']]
            ctx = offered['context']
            key = {k: ctx.get(k) for k in ('version', 'stage', 'run_class', 'theme', 'mode')}
            key.update(source_id=r['source_id'], gems=sorted(ctx['gems']))
            strata[json.dumps(key, sort_keys=True)].append(r)
            families[offered['pair_family']].append(r)
    report = {'all_decisions': trap_summary(rows),
              'inventory_coverage': {'primary_decision_ids': len(base_ids),
                                     'eligible_ordinary_ids': len(offered_ids),
                                     'all_ordinary_offers_accounted_for': True},
              'ordinary_offers': trap_summary([r for r in rows if r['observation_id'] in eligible]),
              'by_source': {sid: trap_summary(g) for sid, g in sorted(groups.items())},
              'ordinary_by_pair_family': {k: trap_summary(g) for k, g in sorted(families.items())},
              'ordinary_by_full_context': [{'context': json.loads(k), **trap_summary(g)} for k, g in sorted(strata.items())],
              'timing_policy': TIMING_POLICY,
              'quarantine': quarantined,
              'interpretation': 'Machine positive counts are provisional recognizer output, not verified trap prevalence. No match is unknown, never a clear doorway. Recovery scans replace unresolved records by visit ID without increasing the denominator.'}
    if blind_sample:
        ids = blind_sample['observation_ids']
        if len(ids) != len(set(ids)) or len(ids) != blind_sample['sample_size']:
            raise ValueError('Invalid blind sample identities.')
        reviewed = unique(list(reviews))
        identities = unique(list(identity_reviews)) if identity_reviews is not None else None
        if identities is not None and set(identities) != set(ids):
            raise ValueError('Independent identity audit must cover the complete blind sample.')
        if set(reviewed) - set(ids) or set(ids) - set(eligible):
            raise ValueError('Blind review outside the prespecified ordinary sample.')
        known = []; unknown = []
        for ident in ids:
            r = reviewed.get(ident)
            if not r:
                unknown.append(ident); continue
            sides = r['sides']
            if len(sides) != 2 or any(t is not None and type(t) is not bool for t in sides):
                raise ValueError('Reviewed trap sides must be true, false or unknown.')
            if not r.get('evidence') and any(t is not None for t in sides):
                raise ValueError('A manual review requires image provenance.')
            prediction = combined.get(ident)
            hashes = {e['sha256'] for e in prediction.get('evidence', [])} if prediction else set()
            if any(e['sha256'] not in hashes for e in r['evidence']):
                raise ValueError('Manual evidence does not match the scanned visit.')
            if identities is not None:
                identity = identities[ident]
                native_hashes = {e['sha256'] for e in identity['evidence']
                                 if e.get('audit_role') == 'resampled_native_offer'}
                if any(t is not None for t in sides) and (identity['status'] != 'same_visit_verified'
                        or not hashes <= native_hashes):
                    raise ValueError('Resolved blind labels require independent same-visit evidence.')
            if prediction.get('quarantine_reasons'):
                unknown.append(ident); continue
            if any(t is True for t in sides) or all(t is False for t in sides):
                known.append((ident, any(t is True for t in sides)))
            else:
                unknown.append(ident)
        n = len(known); k = sum(value for _, value in known)
        report['prespecified_blind_review'] = {
            'design': blind_sample['design'], 'sample_size': len(ids), 'reviewed': len(reviewed),
            'original_sampling_population': blind_sample.get('population'),
            'independent_identity_audit': dict(Counter(r['status'] for r in identities.values())) if identities is not None else None,
            'pair_presence_resolved': n, 'trap_pairs': k, 'unresolved': len(unknown),
            'resolved_sample_frequency': wilson(k, n) if n else None,
            'sample_missingness_bounds': [k / len(ids), (k + len(unknown)) / len(ids)] if ids else None,
            'detector_confusion_on_resolved_pairs': dict(Counter(
                ('detected' if any(t is True for t in combined[i]['machine_sides']) else 'unflagged')
                + ('_trap' if v else '_clear') for i, v in known)),
            'caveat': 'Wilson intervals describe sampling uncertainty only, not correlated gameplay or labeling error. Unreadable samples stay unresolved; no outcome-based replacements.'}
        report['prespecified_blind_review']['by_source'] = {}
        for sid in sorted({eligible[i]['source_id'] for i in ids}):
            selected = [i for i in ids if eligible[i]['source_id'] == sid]
            resolved = [(i, v) for i, v in known if i in selected]
            report['prespecified_blind_review']['by_source'][sid] = {
                'sample_size': len(selected), 'resolved_pairs': len(resolved),
                'trap_pairs': sum(v for _, v in resolved),
                'unknown_pairs': len(selected)-len(resolved)}
    return report


def build(args):
    offers = read_jsonl(args.entries)
    report = {'schema': 1, 'offer_pairs': pair_report(offers), 'inputs': {},
              'scope': 'Full inventory reanalysis across supplied source ledgers; manual evidence coverage varies by question. No claim that every interaction or every frame has been verified.'}
    for name in ('entries', 'entry_report', 'container_report', 'arena_report', 'blind_sample', 'trap_reviews', 'door_qc_reviews', 'blind_identity'):
        path = getattr(args, name)
        if path:
            report['inputs'][name] = {'path': path.as_posix(), 'sha256': sha256(path)}
            if name in ('entry_report', 'container_report', 'arena_report'):
                report[name] = json.loads(path.read_text(encoding='utf-8'))
    if args.trap_scan:
        report['traps'] = trap_report([read_jsonl(p) for p in args.trap_scan], offers,
            json.loads(args.blind_sample.read_text(encoding='utf-8')) if args.blind_sample else None,
            read_jsonl(args.trap_reviews) if args.trap_reviews else (),
            read_jsonl(args.door_qc_reviews) if args.door_qc_reviews else (),
            read_jsonl(args.blind_identity) if args.blind_identity else None, args.export_scan)
        report['inputs']['trap_scans'] = [{'path': p.as_posix(), 'sha256': sha256(p)} for p in args.trap_scan]
        if args.export_scan:
            report['inputs']['public_merged_trap_scan'] = {'path': args.export_scan.as_posix(), 'sha256': sha256(args.export_scan)}
    write_json(args.output, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--entries', type=Path, required=True)
    for name in ('entry-report', 'container-report', 'arena-report', 'blind-sample', 'trap-reviews', 'door-qc-reviews', 'blind-identity'):
        p.add_argument('--'+name, type=Path)
    p.add_argument('--trap-scan', type=Path, action='append')
    p.add_argument('--export-scan', type=Path, help='Export compact merged recognizer records for public recounting.')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); report = build(args)
    print(json.dumps({'offers': report['offer_pairs']['pooled_descriptive']['offers'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
