"""Verify cited local frame/manifest hashes without relabeling any observation."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data/vods/trap-order-2026-10-12'


def validate():
    targets = {}
    citations = 0

    def resolve(value, base):
        p = Path(value)
        if p.is_absolute():
            return p
        if p.parts[0] in {'out','data','docs'}:
            return ROOT/p
        return base/p

    def add(value, expected, base):
        nonlocal citations
        p = resolve(value,base)
        if p in targets and targets[p] != expected:
            raise ValueError('Conflicting evidence hashes: '+str(p))
        targets[p] = expected
        citations += 1

    def visit(obj, base):
        if isinstance(obj,list):
            for x in obj:
                visit(x,base)
        elif isinstance(obj,dict):
            base = ROOT/obj.get('evidence_root',obj.get('work_root',str(base)))
            if 'sha256' in obj:
                value = obj.get('image',obj.get('path'))
                if value and not value.startswith(('https:','http:')):
                    add(value,obj['sha256'],base)
            if isinstance(obj.get('sequence_manifest'),str):
                expected = obj.get('sequence_manifest_sha256',obj.get('manifest_sha256'))
                if expected:
                    add(obj['sequence_manifest'],expected,base)
            if isinstance(obj.get('manifest'),str) and obj.get('manifest_sha256'):
                add(obj['manifest'],obj['manifest_sha256'],base)
            for value in obj.values():
                visit(value,base)

    files = sorted(DATA.glob('*reviewed.json'))
    files.append(ROOT/'data/vods/latest-event-review-2026-10-12/trap-validation--dense-reviewed.json')
    if (DATA/'duplicates.json').exists():
        files.append(DATA/'duplicates.json')
    annotation_hashes = {}
    for f in files:
        raw = f.read_bytes()
        annotation_hashes[f.relative_to(ROOT).as_posix()] = hashlib.sha256(raw).hexdigest()
        visit(json.loads(raw.decode('utf-8-sig')),ROOT/'out/vod-hunt-2026-10-12')
    failures=[]
    for p,expected in targets.items():
        if not p.is_file():
            failures.append({'path':str(p),'error':'missing'})
        elif hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            failures.append({'path':str(p),'error':'sha256_mismatch'})
    result={'schema':1,'annotation_files':len(files),'citations':citations,
            'unique_local_evidence_files':len(targets),'failures':failures,
            'annotation_sha256':annotation_hashes,
            'scope':'Integrity of cited files; does not independently validate visual labels or unsampled instants.'}
    (DATA/'evidence-validation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    if failures:
        raise SystemExit(1)
    return result


if __name__=='__main__':
    validate()
