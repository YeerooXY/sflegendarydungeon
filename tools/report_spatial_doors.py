"""Save measured recognition results separately from dungeon probability data."""
from collections import Counter
import json
from pathlib import Path

from sfld_vod.media import sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'out/vod-spatial-recognition-2026-10-12'
REVIEW = ROOT/'out/vod-older-2026-10-12'
OUTPUT = ROOT/'data/vods/spatial-recognition-2026-10-13'


def portable(value):
    if isinstance(value, dict): return {k:portable(v) for k,v in value.items()}
    if isinstance(value, list): return [portable(v) for v in value]
    if isinstance(value, str) and value.startswith(str(ROOT)):
        return Path(value).relative_to(ROOT).as_posix()
    return value


def main():
    evaluation = json.loads((WORK/'evaluation-final.json').read_text())
    old_stress = json.loads((WORK/'old-stress-evaluation.json').read_text())
    bundle = json.loads((REVIEW/'quick-predictions.json').read_text())
    catalog = json.loads((REVIEW/'room-choice-candidates.json').read_text())
    counts, classes, changes, temporal = Counter(), Counter(), [], Counter()
    for row in catalog['candidates']:
        current = bundle['predictions'][row['id']]
        if current['inputs']['version'] != '1.2.0':
            raise ValueError('Wait for the complete 1.2.0 pass before reporting.')
        previous = next((p for p in reversed(bundle['history'].get(row['id'], []))
                         if p['inputs']['version'] == '1.1.0'), None)
        if previous is None: raise ValueError('Missing preserved prior prediction.')
        if current['image']['sha256'] != previous['image']['sha256']:
            raise ValueError('Comparison requires the same displayed image.')
        for side,(old,new) in enumerate(zip(previous['raw_doors'],current['door_temporal'])):
            before,after = old['kind'],new['kind']
            classes[after or '<unknown>'] += 1
            temporal[new['temporal_status']] += 1
            if before is None and after is not None: counts['unknown_to_suggestion'] += 1
            if before is not None and after is None: counts['suggestion_to_unknown'] += 1
            if before is not None and after is not None and before != after:
                counts['changed_type'] += 1
                changes.append({'id':row['id'],'side':side,'image':current['image'],
                                'previous':before,'new':after})
    negatives = {}
    for method in ('whole_sprite','spatial_regions'):
        rows=[r for r in old_stress['records'] if not r['decision']]
        negatives[method]={'non_choice_images':len(rows),
            'images_with_false_door_suggestion':sum(any(d['kind'] is not None for d in r['predictions'][method]['frames'][0]) for r in rows)}
    report={'schema':1,'recognizer':'spatial_regions_v1','quick_prediction_version':'1.2.0',
            'benchmark_code':evaluation['code'],'summary':evaluation['summary'],
            'older_stress':{'door_positions':{m:v['frames'] for m,v in old_stress['summary']['test']['methods'].items()},
                            'non_choice_frames':negatives},
            'queue':{'images':len(catalog['candidates']),'sources':len(catalog['sources']),
                     'class_suggestions':dict(classes),'changes':dict(counts),'temporal_checks':dict(temporal)},
            'changed_types_for_review':changes,
            'inputs':{name:sha256(WORK/name) for name in ('evaluation-final.json','old-stress-evaluation.json','frozen-plan.json')},
            'scope':'Recognition evaluation and unreviewed queue suggestions only. No room odds, trap rates, stones or simulator parameters changed.',
            'limitations':['Accuracy is conditional on selected reviewed choice images, not whole-video recall.',
                           'The separate older stress selection oversamples previous abstentions; it is not a prevalence sample.',
                           'One close-up transition is still mistaken for a partial mystery choice; scene selection remains imperfect.',
                           'Region checks and adjacent frames are correlated; scores are not calibrated probabilities.',
                           'Only identical displayed images are compared; saved votes stay bound to their original labels.']}
    write_json(OUTPUT/'report.json',portable(report))
    for name in ('frozen-plan.json','supplemental-holdout.json','old-stress-reviewed.json'):
        write_json(OUTPUT/name,portable(json.loads((WORK/name).read_text())))
    # Reviewed manifest plus concise predictions permit audit without shipping artwork.
    proof=[]
    for row in evaluation['records']:
        proof.append({k:row[k] for k in ('observation_id','source_id','partition','context','images','doors')})
        proof[-1]['predictions']={method:{'frames':[[d['kind'] for d in pair] for pair in p['frames']],
                                         'stable':[d['kind'] for d in p['stable']]}
                                 for method,p in row['predictions'].items()}
    write_json(OUTPUT/'benchmark-records.json',portable({'schema':1,'records':proof}))
    print(json.dumps({'queue':report['queue'],'report':str(OUTPUT/'report.json')}))


if __name__ == '__main__': main()
