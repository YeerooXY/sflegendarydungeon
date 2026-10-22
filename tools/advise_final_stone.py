"""Compare three actual final-stone offers using fresh learned-policy rollouts."""
import argparse
import json
from pathlib import Path
import numpy as np
import torch
import train_rl
from sfld_rl.stone_advisor import StoneAdvisor

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, required=True, help='Native .state file at the room-76 stone choice')
    p.add_argument('--budget', type=int, default=5, help='Maximum remaining gross mushroom spending')
    p.add_argument('--hours', type=float, default=168, help='Remaining event horizon')
    p.add_argument('--local-hour', type=float, default=17, help='Local time at this decision, decimal hours')
    p.add_argument('--sleep-until-hour', type=float, default=0, help='No actions from midnight until this hour; 0 disables')
    p.add_argument('--samples', type=int, default=2048, help='Fixed simulations per actual offer')
    p.add_argument('--planning-seed', type=int, help='Independent planning seed; defaults to a fresh random seed')
    p.add_argument('--batch-size', type=int, default=1024)
    p.add_argument('--device', choices=['cpu','cuda'])
    p.add_argument('--library', type=Path, default=ROOT/'build-v0612/Release/sfld_rl.dll')
    p.add_argument('--profile', type=Path, default=ROOT/'profiles/synthetic.profile')
    p.add_argument('--output', type=Path, required=True, help='New result directory; never overwrite a prior comparison')
    a = p.parse_args()
    if a.output.exists(): p.error('Output directory already exists')
    progress = a.state.read_text(encoding='utf-8-sig')
    torch.set_num_threads(1)
    adviser = StoneAdvisor(device=a.device)
    result, data = adviser.from_progress(progress, a.library, a.profile, budget=a.budget, hours=a.hours,
        local_hour=a.local_hour, sleep_until_hour=a.sleep_until_hour, samples=a.samples,
        planning_seed=a.planning_seed, batch_size=a.batch_size)
    a.output.mkdir(parents=True, exist_ok=False)
    (a.output/'input.state').write_text(progress, encoding='utf-8')
    (a.output/'profile.txt').write_bytes(a.profile.read_bytes())
    np.savez_compressed(a.output/'outcomes.npz', data=data)
    result['data_sha256'] = train_rl.sha(a.output/'outcomes.npz')
    result['source_sha256'] = {}
    for name in ('tools/advise_final_stone.py','tools/sfld_rl/stone_advisor.py','tools/publish_final_stones.py'):
        source = ROOT/name; target = a.output/'source'/name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(source.read_bytes())
        result['source_sha256'][name] = train_rl.sha(target)
    plan = dict(schema='sfld-final-stone-advice-plan-v1', seed=result['planning_seed'],
        samples_per_offer=a.samples, remaining_budget=a.budget, remaining_hours=a.hours,
        local_hour=a.local_hour, sleep_until_hour=a.sleep_until_hour,
        input_sha256=train_rl.sha(a.output/'input.state'),
        checkpoint_sha256=result['checkpoint_sha256'], library_sha256=result['library_sha256'],
        profile_sha256=result['profile_sha256'], source_sha256=result['source_sha256'],
        objective=result['objective'], confidence=result['confidence'])
    (a.output/'plan.json').write_text(json.dumps(plan, indent=2)+'\n', encoding='utf-8')
    (a.output/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('estimated_best','supported_best','rankings','trajectories','seconds')}, indent=2))


if __name__ == '__main__': main()
