"""An offline, filterable ranking of every possible third stone for every pair."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
from publish_final_stones import FULL, SHORT, read

ROOT = Path(__file__).resolve().parents[1]


def build_data(study):
    verification = read(study/'verification.json')
    for name, field in (('plan.json','plan_sha256'),('rankings.json','rankings_sha256')):
        if hashlib.sha256((study/name).read_bytes()).hexdigest() != verification[field]:
            raise ValueError('Study verification mismatch: '+name)
    if not verification['passed']: raise ValueError('Study is not verified')
    plan = read(study/'plan.json'); rankings = read(study/'rankings.json')
    rows = rankings['rows']
    by_key = {(r['run'],frozenset(r['held']),r['keys']):r for r in rows}
    for row in rows: row['beats'] = []
    # A subset needs ALL pairwise comparisons, not just gaps to the globally
    # fastest candidate. Otherwise a good-but-unavailable stone hides the answer.
    contrasts = 0
    with gzip.open(study/'paired-contrasts.csv.gz','rt',encoding='utf-8',newline='') as stream:
        for contrast in csv.DictReader(stream):
            row = by_key[int(contrast['run']),frozenset((contrast['held_1'],contrast['held_2'])),int(contrast['keys'])]
            a,b = contrast['stone_a'],contrast['stone_b']
            if float(contrast['ci_95_high']) < 0: row['beats'].append([a,b])
            elif float(contrast['ci_95_low']) > 0: row['beats'].append([b,a])
            contrasts += 1
    if contrasts != verification['simultaneous_contrasts']:
        raise ValueError('Pairwise comparison coverage mismatch')
    pools = {str(run): sorted({g for row in rows if row['run']==run for g in row['held']},key=lambda g:FULL[g]) for run in (1,2)}
    return dict(full=FULL,short=SHORT,pools=pools,keys=plan['keys'],
        trajectories=verification['trajectories'],contrasts=contrasts,
        source_rankings_sha256=verification['rankings_sha256'],
        rows=[dict(run=r['run'],held=r['held'],keys=r['keys'],beats=r['beats'],
                   samples=r['samples_per_candidate'],
                   ranks=[dict(stone=x['stone'],hours=x['mean_hours'],mushrooms=x['mean_mushrooms'],
                               completion=x['completion_probability']) for x in r['rankings']]) for r in rows])


def render(study, output):
    data = build_data(study)
    template = (ROOT/'tools/templates/final_stone_guide.html').read_text(encoding='utf-8')
    payload = json.dumps(data,separators=(',',':')).replace('<','\\u003c')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(template.replace('__STUDY_JSON__',payload),encoding='utf-8')
    print(json.dumps(dict(page=str(output),pair_key_states=len(data['rows']),
                         candidate_rankings=sum(len(r['ranks']) for r in data['rows']))))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();render(a.study.resolve(),a.output.resolve())
