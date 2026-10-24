"""Environment signature of a training run (no PyTorch import)."""


def compact_metadata(metadata):
    # The schema and binary/profile content establish compatibility. Preserve
    # full metadata too; runtime counters and paths need not match on resume.
    result = {key: value for key, value in metadata.items()
              if 'sha256' in key or 'schema' in key or key in
              ('observation_names', 'action_names', 'deadline_hours', 'max_actions')}
    result['profile_sha256'] = [p['sha256'] for p in metadata.get('profiles', [])]
    return result
