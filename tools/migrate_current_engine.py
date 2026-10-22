"""Explicit environment transfer with unchanged weights, Adam state and inputs."""
import copy
from pathlib import Path
import torch
import train_rl
from benchmark_player_ideas import transfer_changes


def migrate(parent, output, args):
    parent, output = Path(parent), Path(output)
    if output.exists(): raise FileExistsError(output)
    original, original_model = train_rl.load_checkpoint(parent, torch.device('cpu'))
    if original['metadata'].get('stone_priority', 'learned') != 'learned' or original['metadata'].get('shop_policy'):
        raise ValueError('This transfer starts from the unconstrained policy')
    with train_rl.make_env(args, n_envs=1) as env:
        changes = transfer_changes(original['metadata']['environment_signature'], env.metadata, True)
        metadata = train_rl.source_metadata(args, env)
    result = copy.deepcopy(original)
    metadata['seed'] = original['metadata']['seed']
    if 'finetune' in original['metadata']: metadata['finetune'] = copy.deepcopy(original['metadata']['finetune'])
    metadata['engine_transfer'] = {
        'schema': 'sfld-current-engine-transfer-v1', 'parent_sha256': train_rl.sha(parent),
        'original_metadata': original['metadata'], 'environment_changes': changes,
        'script_sha256': train_rl.sha(__file__), 'new_training_decisions': 0,
        'weights_and_adam': 'Unchanged. This is explicit transfer to corrected mechanics, not old-experiment resume.'}
    result['metadata'] = metadata
    result['state']['migration_only'] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(result, output)
    transferred, model = train_rl.load_checkpoint(output, torch.device('cpu'))
    for name, tensor in original_model.state_dict().items():
        torch.testing.assert_close(tensor, model.state_dict()[name], rtol=0, atol=0)
    for index, state in original['optimizer']['state'].items():
        for field, value in state.items():
            actual = transferred['optimizer']['state'][index][field]
            if isinstance(value, torch.Tensor): torch.testing.assert_close(value, actual, rtol=0, atol=0)
            elif value != actual: raise RuntimeError('Optimizer transfer changed')
    train_rl.write_json(output.with_suffix('.transfer.json'), metadata['engine_transfer'])
    return train_rl.sha(output)
