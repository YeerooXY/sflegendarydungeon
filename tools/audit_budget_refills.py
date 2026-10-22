"""Replay published budget events with refill telemetry and a paired engine check."""
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil
import torch
import numpy as np
import benchmark_budget_curve as curve
import train_rl
from benchmark_player_ideas import transfer_changes

ROOT=curve.ROOT
SOURCE=ROOT/'out/budget-current-engine-2026-10-14'
OUTPUT=ROOT/'out/budget-refill-audit-2026-10-14'


def run():
    if OUTPUT.exists():raise FileExistsError(OUTPUT)
    OUTPUT.mkdir()
    plan=curve.read(SOURCE/'plan.json')
    torch.set_num_threads(2)
    result=[];all_rows={}
    for engine,budget,events,name in [('0.6.10',1000,1024,'parent'),
                                     ('0.6.11',1000,1024,'parent'),
                                     ('0.6.11',20000,128,'specialist')]:
        target=OUTPUT/f'{engine}-{budget}'
        target.mkdir()
        library=SOURCE/'sfld_rl.dll' if engine=='0.6.11' else ROOT/'out/high-budget-2026-10-14-v2/curve/sfld_rl.dll'
        shutil.copyfile(library,target/'sfld_rl.dll')
        shutil.copyfile(SOURCE/'synthetic.profile',target/'synthetic.profile')
        saved,model=train_rl.load_checkpoint(SOURCE/(name+'.pt'),torch.device('cuda'));model.eval()
        case={**plan,'final':{**plan['final'],'events':events},'checkpoint_sha256':plan['inputs'][name+'.pt']}
        counts=Counter();costs=Counter();prices={'heal_full':Counter(),'heal_step':Counter()}
        old_env=curve.VectorEnv;old_transfer=curve.transfer_changes;changes=[]
        class AuditEnv(old_env):
            def reset(self,*args,**kwargs):
                answer=super().reset(*args,**kwargs);self.previous_observation=answer[0].copy();return answer
            def reset_done(self,*args,**kwargs):
                answer=super().reset_done(*args,**kwargs);self.previous_observation=answer[0].copy();return answer
            def step(self,actions,*args,**kwargs):
                before=self.previous_observation
                old_spent=self._episode_info['recovery_mushrooms'].copy()
                answer=super().step(actions,*args,**kwargs)
                info=answer[-1];valid=info['episode_index']<events
                for action in ('heal_full','heal_step'):
                    used=valid&(actions==self.action_names.index(action))
                    counts[action]+=int(np.sum(used))
                    spent=info['recovery_mushrooms']-old_spent
                    costs[action]+=float(np.sum(spent[used]))
                    prices[action].update(int(round(value)) for value in spent[used])
                    if action=='heal_full':
                        hp=before[:,self.observation_names.index('hp')]
                        counts['full_from_zero']+=int(np.sum(used&(hp<=1e-7)))
                self.previous_observation=answer[0].copy()
                return answer
        def checked_transfer(saved_metadata,current,allowed):
            found=transfer_changes(saved_metadata,current,True);changes.append(found);return found
        curve.VectorEnv=AuditEnv;curve.transfer_changes=checked_transfer
        try:rows=curve.collect(target,case,'final',budget,saved,model)
        finally:curve.VectorEnv=old_env;curve.transfer_changes=old_transfer
        assert sum(costs.values())==sum(row['recovery_mushrooms'] for row in rows)
        matches=None
        if engine=='0.6.11':
            with gzip.open(SOURCE/f'final-supply-{budget}.json.gz','rt') as stream:published=json.load(stream)['rows']
            matches=sum(all(row[k]==published[i][k] for k in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms')) for i,row in enumerate(rows))
            if budget==1000:assert matches==events
        row={'engine':engine,'budget':budget,'events':events,'checkpoint_sha256':case['checkpoint_sha256'],
             'library_sha256':train_rl.sha(library),'counts':dict(counts),'costs':dict(costs),
             'prices':{key:dict(value) for key,value in prices.items()},'matches_published':matches,
             'completions':curve.interval(r['completions'] for r in rows),
             'healing_mushrooms':curve.interval(r['recovery_mushrooms'] for r in rows)}
        result.append(row);all_rows[engine,budget]=rows
        train_rl.write_json(target/'transfer.json',changes)
        print(json.dumps(row),flush=True)
    paired=curve.interval(b['completions']-a['completions'] for a,b in zip(all_rows['0.6.10',1000],all_rows['0.6.11',1000]))
    train_rl.write_json(OUTPUT/'summary.json',{'seed':plan['final'],'cases':result,'paired_engine_change_at_1000':paired})
    shutil.copyfile(__file__,OUTPUT/Path(__file__).name)
    print(json.dumps({'paired_engine_change_at_1000':paired}),flush=True)


if __name__=='__main__':run()
