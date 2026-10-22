"""Validate quick votes and export parser labels without changing gameplay odds."""
import argparse
from collections import Counter
import json
from pathlib import Path

from sfld_vod.media import sha256,write_json

KINDS={'monster','mystery','locked','unlocked','double_locked','epic','wall','cursed',
       'sacrifice','hungry','blessing','destiny','golden','shop','boss','trial','exit_trial',None}


def fingerprint(prediction):
    return json.dumps([prediction['image']['sha256'],[[d['kind'],d['trap']] for d in prediction['doors']]],separators=(',',':'))


def validate_review(review,prediction):
    if review.get('id')!=prediction['id'] or review.get('prediction_fingerprint')!=fingerprint(prediction):
        raise ValueError('Vote refers to an unknown or changed prediction.')
    if review.get('image')!=prediction['image']:
        raise ValueError('Vote evidence differs from the image shown.')
    if review.get('verdict') not in {'correct','incorrect','duplicate'} or type(review.get('corrected')) is not bool:
        raise ValueError('Invalid review verdict.')
    labels=review.get('labels')
    if not isinstance(labels,list) or len(labels)!=2 or any(
            d.get('kind') not in KINDS or (d.get('trap') is not None and type(d['trap']) is not bool) for d in labels):
        raise ValueError('Expected two valid door/trap labels.')
    if review['corrected'] and review['verdict']!='incorrect':
        raise ValueError('Only an incorrect first-pass vote can carry a correction.')
    if review['verdict']=='correct' and any([d['kind'],d['trap']]!=[p['kind'],p['trap']]
                                          for d,p in zip(labels,prediction['doors'])):
        raise ValueError('A correct vote must accept the displayed labels exactly.')
    if review['corrected'] and type(review.get('decision')) is not bool:
        raise ValueError('A correction must identify whether this is a choice screen.')
    return review['verdict']=='correct' or review['corrected']


def prepare(payload,predictions,history=None):
    if payload.get('schema')!=1 or payload.get('kind')!='sfld-quick-label-review' or not isinstance(payload.get('reviews'),list):
        raise ValueError('Expected a quick-review JSON export.')
    seen=set();accepted=[];counts=Counter();confusions=Counter()
    for review in payload['reviews']:
        ident=review['id']
        if ident in seen:raise ValueError('Duplicate review ID in export: '+ident)
        seen.add(ident)
        if ident not in predictions:raise ValueError('Unknown prediction: '+ident)
        options=[predictions[ident]]+(history or {}).get(ident,[])
        prediction=next((p for p in options if fingerprint(p)==review.get('prediction_fingerprint')),None)
        if prediction is None:raise ValueError('Vote refers to an unknown or changed prediction.')
        confirmed=validate_review(review,prediction)
        counts[review['verdict']]+=1
        if review['corrected']:counts['corrected']+=1
        if not confirmed:continue
        if review.get('decision') is False:
            counts['not_choice_screen']+=1
        else:
            for side,(before,after) in enumerate(zip(prediction['doors'],review['labels'])):
                confusions[(before['kind'],after['kind'])]+=1
        accepted.append({'id':ident,'source_id':prediction['source_id'],'source_seconds':prediction['image']['source_seconds'],
                         'image':prediction['image'],'doors':review['labels'],'decision':review.get('decision'),
                         'label_verification':[{'kind':d['kind'] is not None,'trap':type(d['trap']) is bool} for d in review['labels']],
                         'reviewer':review.get('reviewer'),'reviewed_at':review.get('reviewed_at'),
                         'scope':'Image classification labels only; visit identity, stones, outcome and sampled-population eligibility remain unverified.'})
    return {'schema':1,'first_pass_counts':dict(counts),'confirmed_image_records':len(accepted),
            'door_confusions':[{'suggested':a,'reviewed':b,'count':n} for (a,b),n in confusions.items()],
            'scope':'Parser feedback only. Targeted views and counter-section recovery images are not a verified room-frequency census; duplicates and unresolved incorrect votes do not supply confirmed labels.'},accepted


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reviews',type=Path);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=args.directory.resolve()
    payload=json.loads(args.reviews.read_text(encoding='utf-8'))
    bundle=json.loads((root/'quick-predictions.json').read_text());predictions=bundle['predictions']
    summary,accepted=prepare(payload,predictions,bundle.get('history',{}))
    for review in payload['reviews']:
        proof=review['image'];image=(root/proof['image']).resolve()
        if not image.is_relative_to(root) or sha256(image)!=proof['sha256']:
            raise ValueError('Missing or changed native evidence.')
    args.output.mkdir(parents=True,exist_ok=True)
    existing=args.output/'votes.json'
    if existing.exists() and json.loads(existing.read_text())!=payload:
        raise ValueError('Output already contains different votes; choose a new directory to preserve them.')
    write_json(existing,payload);write_json(args.output/'summary.json',summary)
    (args.output/'image-labels.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in accepted),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
