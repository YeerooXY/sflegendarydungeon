"""Publish the minimal review with auditable, exact-image corrections."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from sfld_vod.media import sha256, write_json

ROOT = Path(__file__).resolve().parents[1]


def apply_corrections(bundle, evidence):
    result = deepcopy(bundle)
    history = result.setdefault('history', {})
    for correction in evidence['corrections']:
        ident = correction['id']
        if ident not in result['predictions']: continue
        prediction = result['predictions'][ident]
        if prediction['image']['sha256'] != correction['image_sha256']:
            raise ValueError('Correction does not match the displayed image: ' + ident)
        before = deepcopy(prediction)
        side = correction['side']
        if side not in (0, 1): raise ValueError('Invalid correction side.')
        prediction['doors'][side].update({k:correction[k] for k in ('kind','trap','trap_visual')})
        prediction['doors'][side]['door_status'] = 'reviewed_image_correction'
        prediction['doors'][side]['trap_status'] = 'reviewed_image_correction'
        prediction['image_correction'] = {'reviewer':evidence['reviewer'], 'evidence':correction,
                                        'scope':evidence['scope']}
        if prediction != before and before not in history.setdefault(ident, []):
            history[ident].append(before)
    return result


def display_catalog(catalog, source_file):
    """Keep the full evidence catalog on disk; send only UI fields to the page."""
    rows = []
    for row in catalog['candidates']:
        item = {k:row[k] for k in ('id','source_id','uploader','source_url','source_link',
                                  'upload_date','start','end','best_time','recovery') if k in row}
        for field in ('frame_room_sequence','room_sequence'):
            counter = row.get(field)
            item[field] = {k:counter.get(k) for k in ('room','sequence_id','status','span_id')} if counter else None
        rows.append(item)
    return {'schema':catalog['schema'], 'version':catalog['version'], 'sources':catalog['sources'],
            'candidates':rows, 'coverage':catalog.get('coverage'),
            'evidence_catalog':{'file':source_file.name,'sha256':sha256(source_file)},
            'scope':'Display index only. Full counter proofs and native evidence remain in the evidence catalog.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'out/vod-older-2026-10-12')
    args = parser.parse_args(); root = args.directory.resolve()
    bundle = json.loads((root/'quick-predictions.json').read_text())
    corrections = json.loads((ROOT/'data/vods/quick-review-image-corrections-2026-10-12.json').read_text())
    result = apply_corrections(bundle, corrections)
    catalog_path = root/'room-choice-candidates.json'
    if not catalog_path.exists(): catalog_path = root/'monster-mystery-candidates.json'
    catalog = json.loads(catalog_path.read_text())
    checked = set()
    for row in catalog['candidates']:
        p = result['predictions'].get(row['id'])
        if p is None: raise ValueError('Missing prediction: ' + row['id'])
        for proof in [p['image'], *p.get('evidence', [])]:
            identity = (proof['image'], proof['sha256'])
            if identity in checked: continue
            if sha256(root/proof['image']) != proof['sha256']:
                raise ValueError('Changed review evidence: ' + row['id'])
            checked.add(identity)
    write_json(root/'quick-predictions.json', result)
    # Publish a catalog only after every image and prediction is available.
    # A still-running recovery batch must not break the active voting page.
    write_json(root/'quick-review-catalog.json', display_catalog(catalog, catalog_path))
    (root/'monster-mystery.html').write_text((ROOT/'web/vod_quick_review.html').read_text(encoding='utf-8'), encoding='utf-8')
    print(json.dumps({'published_images':len(catalog['candidates']), 'corrections':len(corrections['corrections']),
                      'historical_predictions_preserved':sum(map(len,result['history'].values()))}))


if __name__ == '__main__': main()
