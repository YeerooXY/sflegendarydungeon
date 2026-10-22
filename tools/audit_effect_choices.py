"""Record exact greedy choices/probabilities on the fixed diagnostic panel."""
import argparse
import json
from pathlib import Path
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from probe_effect_choices import decisions
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values


def run(study,cases_path):
    torch.set_num_threads(2);plan=json.loads((study/'plan.json').read_text())
    candidates=json.loads((study/'training-selection.json').read_text())
    cases=json.loads(cases_path.read_text());records=[]
    for candidate in candidates:
        path=study/candidate['checkpoint']
        if train_rl.sha(path)!=candidate['sha256']:raise RuntimeError('Checkpoint changed')
        _,model=train_rl.load_checkpoint(path,torch.device('cpu'));model.eval()
        profile=profile_values(study/'synthetic.profile')
        for budget in plan['budgets']:
            with VectorEnv(study/'sfld_rl.dll',[study/'synthetic.profile'],len(cases),budgets=[budget],auto_reset=False) as env:
                env.reset();lab=EffectLab(env)
                for i,case in enumerate(cases):
                    with lab.fixture(case['progress'],budget) as snapshot:lab.restore(i,snapshot,0)
                obs,masks=lab.observe();rule=Practical(env.observation_names,env.action_names,'learned',profile)
                with torch.inference_mode():
                    distribution,_=model.distribution(torch.from_numpy(obs),torch.from_numpy(masks))
                    actions,targets=decisions(model,rule,obs,masks,profile,'cpu')
                for i,case in enumerate(cases):
                    records.append({'candidate':candidate['name'],'budget':budget,'case':case['name'],
                        'chosen':env.action_names[int(actions[i])],
                        'action_probabilities':{env.action_names[j]:float(distribution.probs[i,j]) for j in np.flatnonzero(masks[i])}})
    write_once(study/'policy-choices.json',{'cases_sha256':train_rl.sha(cases_path),'candidates':candidates,'rows':records,
        'scope':'Exact greedy decisions in engineered positions, not natural encounter frequencies or an optimal-action oracle.'})
    print(json.dumps({'audited_positions':len(records)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True);p.add_argument('--cases',type=Path,required=True)
    a=p.parse_args();run(a.study.resolve(),a.cases.resolve())
