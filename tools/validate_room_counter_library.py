"""Verify full downloaded-interval coverage of the shared counter pass."""
from collections import defaultdict
import json

from sfld_vod.media import write_json
from sfld_vod.room_sequence import SEQUENCE_DIR
from vod_room_library import ROOT,LIBRARY_ROOTS
from vod_room_sequence import inventory_jobs


def main():
    results=[]
    for folder in LIBRARY_ROOTS:
        root=ROOT/folder;expected=inventory_jobs(root)
        expected_keys={(j['source_id'],j['view_id'],j['start'],j['end']) for j in expected}
        raw=[];missing_refinements=[]
        for path in root.glob('*/'+SEQUENCE_DIR+'/**/scan.json'):
            data=json.loads(path.read_text())
            if data.get('counter_location','bottom')=='bottom':raw.append(data)
            if not path.with_name('refined.json').is_file():missing_refinements.append(str(path))
        actual={(r['source_id'],r.get('view_id',r['source_id']),r['source_start'],r['source_end']) for r in raw}
        if actual!=expected_keys or len(actual)!=len(raw):raise ValueError('Missing, duplicate or unexpected intervals in '+folder)
        if missing_refinements:raise ValueError('Unrefined counter sections remain: '+str(missing_refinements))
        catalog=json.loads((root/SEQUENCE_DIR/'catalog.json').read_text())
        if {s['source_id'] for s in catalog['sources']}!={j['source_id'] for j in expected}:
            raise ValueError('Catalog does not contain every downloaded source: '+folder)
        planned=sum(j['end']-j['start'] for j in expected)
        if abs(sum(s['scanned_seconds'] for s in catalog['sources'])-planned)>.001:
            raise ValueError('Counter intervals were lost or counted twice: '+folder)
        spans=defaultdict(list)
        for span in catalog['spans']:spans[span['source_id']].append(span)
        for view,items in spans.items():
            items.sort(key=lambda s:s['start'])
            if any(a['end']>=b['start'] for a,b in zip(items,items[1:])):
                raise ValueError('Overlapping counter spans for '+view)
        results.append({'root':folder,'videos':len(catalog['sources']),'views':len(spans),'sections':len(expected),
                        'requested_view_seconds':planned,'sampled_view_seconds':sum(s['sampled_seconds'] for s in catalog['sources']),
                        'decode_shortfalls_seconds':planned-sum(s['sampled_seconds'] for s in catalog['sources']),
                        'all_sections_present':True,'all_sections_refined':True,'spans_do_not_overlap':True})
    report={'schema':1,'passed':True,'roots':results,
            'scope':'Checks coverage of acquired intervals, not of entire public VODs or every actual decision. Decode shortfalls are retained, not filled.'}
    write_json(ROOT/'data/vods/room-counter-2026-10-12/pipeline-validation.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
