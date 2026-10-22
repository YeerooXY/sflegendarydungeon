"""Attach shared counter evidence to existing timelines without changing reviews."""
import hashlib
import json
from pathlib import Path

from sfld_vod.media import write_json
from sfld_vod.room_sequence import SEQUENCE_DIR,attach_shared_sequence
from vod_room_library import ROOT,LIBRARY_ROOTS


def refresh():
    template=(Path(__file__).parent/'sfld_vod/timeline.html').read_text(encoding='utf-8')
    results=[]
    for folder in LIBRARY_ROOTS:
        root=ROOT/folder
        catalog=json.loads((root/SEQUENCE_DIR/'catalog.json').read_text())
        for path in sorted(root.glob('*-timeline.json')):
            data=json.loads(path.read_text(encoding='utf-8'))
            sid=path.name.removesuffix('-timeline.json')
            annotations=json.dumps(data['observations'],sort_keys=True)
            old_revision=data['revision']
            data['counter_sequence']=attach_shared_sequence(data['candidates'],root,sid,catalog)
            if json.dumps(data['observations'],sort_keys=True)!=annotations or data['revision']!=old_revision:
                raise ValueError('Counter enrichment changed reviewed observations: '+str(path))
            write_json(path,data)
            payload=json.dumps(data,ensure_ascii=True).replace('<','\\u003c')
            path.with_name(sid+'-review.html').write_text(template.replace('__PAYLOAD__',payload),encoding='utf-8')
            results.append({'source_id':sid,'timeline':path.relative_to(ROOT).as_posix(),
                            'candidates':len(data['candidates']),
                            'anchored_candidates':sum(r['counter_sequence']['room'] is not None for r in data['candidates']),
                            'reviewed_observations_unchanged':len(data['observations']),
                            'observations_sha256':hashlib.sha256(annotations.encode()).hexdigest(),
                            'revision':old_revision})
    target=ROOT/'data/vods/room-counter-2026-10-12/refreshed-reviews.json'
    write_json(target,{'schema':1,'timelines':results})
    print(json.dumps({'refreshed_timelines':len(results),'candidates':sum(r['candidates'] for r in results),
                      'reviewed_observations_preserved':sum(r['reviewed_observations_unchanged'] for r in results)},indent=2))


if __name__=='__main__':refresh()
