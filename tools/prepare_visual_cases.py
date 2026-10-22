"""Find small, reproducible real-engine scenes for the browser presentation tests."""
import json
import os
from pathlib import Path
from visual_lab import Session, REPO


def main():
    cases = []
    exe = Path(os.environ.get('SFLD_EXE', REPO / 'build/Release/sfld.exe'))
    profile = REPO / 'profiles/synthetic.profile'
    def find(name, extra, action, required=()):
        state = 'schema=1\nroom=60\nrun_number=2\nphase=encounter\nhp_percent=70\nkeys=8\ngems=rabbit,moonstone\n' + extra
        if action['kind'] in ('heal_step', 'heal_full'):
            state = state.replace('phase=encounter\n', 'phase=recovery\nresume_phase=encounter\n')
        for seed in range(1, 201):
            settings = {'initial_state': state, 'seed': seed, 'budget': 100, 'run_number': 2}
            session = Session(exe, profile, settings)
            try:
                before = session.observation
                result = session.step(action)
                kinds = {o['kind'] for o in result['journal'][-1]['outcomes']}
                if set(required) <= kinds:
                    cases.append({'name': name, 'settings': settings, 'action': action,
                                  'before': before, 'after': result['observation'],
                                  'journal': result['journal'][-1]})
                    break
            finally:
                session.close()
        else:
            raise RuntimeError('No deterministic fixture for ' + name)
    interact = {'kind': 'interact'}
    find('fight-and-heal', 'encounter=monster\nblessings=recovery:weak:3\ncurses=poison:weak:3\n', {'kind': 'fight'}, ['damage', 'healing'])
    find('fight-keys', 'encounter=monster\n', {'kind': 'fight'}, ['keys'])
    find('refill', 'encounter=monster\n', {'kind': 'heal_step'}, ['healing'])
    for kind in ('nothing', 'gold', 'item', 'blessing'):
        find('crate-' + kind, 'encounter=crate\n', interact, [kind])
    find('epic', 'encounter=epic\n', interact, ['epics'])
    find('legendary', 'encounter=prize\ntrial_seen=true\npending_trial_reward=5\n', interact, ['legendaries'])
    find('soldier', 'encounter=skeleton\n', interact, ['awakened'])
    find('barrel-curse', 'encounter=barrel\n', interact, ['curse'])
    # Replace fields instead of putting duplicates in the state format.
    for name, state, action in [
        ('trap-death', 'schema=1\nroom=30\nrun_number=2\nphase=doors\nhp_percent=5\nkeys=1\ngems=rabbit\ndoors=monster:trap,mystery\n', {'kind': 'choose_door', 'index': 0}),
        ('boss-death', 'schema=1\nroom=100\nrun_number=2\nphase=encounter\nencounter=boss\nhp_percent=5\nkeys=0\ngems=rabbit,moonstone,greed\n', {'kind': 'fight'}),
    ]:
        settings = {'initial_state': state, 'seed': 1, 'budget': 100, 'run_number': 2}
        session = Session(exe, profile, settings)
        try:
            before = session.observation
            result = session.step(action)
            assert any(o['kind'] == 'death' for o in result['journal'][-1]['outcomes'])
            cases.append({'name': name, 'settings': settings, 'action': action, 'before': before,
                          'after': result['observation'], 'journal': result['journal'][-1]})
        finally:
            session.close()
    target = REPO / 'out/visual-cases-2026-10-11.json'
    target.write_text(json.dumps(cases, indent=2), encoding='utf-8')
    print(json.dumps([{'name': c['name'], 'seed': c['settings']['seed']} for c in cases]))


if __name__ == '__main__':
    main()
