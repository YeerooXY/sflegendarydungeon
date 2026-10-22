"""Guard the audit against missed counterexamples and duplicate observations."""
import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "audit_stone_reoffers", Path(__file__).parents[1] / "tools/audit_stone_reoffers.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def chain(run="one", offset=0):
    rows, held = [], []
    for index, stones in enumerate((['a', 'b', 'c'], ['d', 'e', 'f'], ['g', 'h', 'i']), 1):
        image = {"path": f"{run}-{index}", "sha256": f"{run}-{index}"}
        rows.append({"selection_index": index, "boss_room": 25 * index,
                     "source_seconds": offset + index, "offered": stones,
                     "chosen": stones[0], "held_before": held[:], "held_after": held + [stones[0]],
                     "offer_evidence": image, "chosen_evidence": image})
        held.append(stones[0])
    return {"source_id": "source", "run_id": run, "cohort": "current_2026", "offers": rows}


class StoneReofferAuditTests(unittest.TestCase):
    def test_rejected_stone_counterexample_is_counted(self):
        c = chain()
        c['offers'][1]['offered'][1] = 'b'
        result = AUDIT.validate([c], check_images=False)
        self.assertEqual(result['cohorts']['current_2026']['chains_with_first_rejected_reappearance'], 1)
        self.assertEqual(c['result']['cross_offer_repeats'], ['b'])

    def test_return_in_next_run_is_not_within_run_repeat(self):
        result = AUDIT.validate([chain(), chain('two', 10)], check_images=False)
        self.assertEqual(result['complete_chains'], 2)
        self.assertEqual(result['cohorts']['current_2026']['chains_with_any_repeat'], 0)

    def test_duplicate_source_frame_fails_even_with_new_run_label(self):
        with self.assertRaisesRegex(ValueError, 'Reused offer frame'):
            AUDIT.validate([chain(), chain('two')], check_images=False)

    def test_broken_hud_continuity_fails(self):
        c = copy.deepcopy(chain())
        c['offers'][1]['held_before'] = ['z']
        with self.assertRaisesRegex(ValueError, 'Broken held-stone continuity'):
            AUDIT.validate([c], check_images=False)


if __name__ == '__main__':
    unittest.main()
