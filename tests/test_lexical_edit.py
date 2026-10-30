"""Offline tests for the standalone lexical edit addition; no corpus or live API."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'skills/pastiche-write/scripts/lexical_edit.py'
spec = importlib.util.spec_from_file_location('lexical_edit', SCRIPT)
x = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = x
spec.loader.exec_module(x)
BEFORE = '粘り気のある蜜がスプーンに付いた。'
AFTER = 'ねっとりした蜜がスプーンに付いた。'
BRIEF = '蜜の粘り気と質感を表す場面。出来事は変えない。'


def row(word='ねっとり', eid='PRIVATE_CARD_001', role='sensory'):
    return {'entry_id': eid, 'form': word, 'preferred_surface': word, 'kind': 'lemma',
            'observed_forms': [{'form': word, 'count': 5}], 'priority': 1.0,
            'role': role, 'note': '粘り気のある蜜の質感を描く。',
            'evidence_scope': 'cross_work', 'count': 5, 'work_count': 3}


def repertoire(*rows):
    return {'schema': 'pastiche.lexical.repertoire.v1', 'entries': list(rows or [row()])}


def job(before=BEFORE, r=None, start=0):
    return x.Opportunity(x.Unit(start, start+len(before), before), (r or row(),), 1.0)


class Fake:
    def __init__(self, choices=('B', 'A'), rewrite=None):
        self.prompts = []
        self.choices = iter(choices)
        self.rewrite = rewrite or (lambda payload: payload['TARGET'].replace('粘り気のある', 'ねっとりした'))
    def ask_text(self, prompt):
        self.prompts.append(prompt)
        data = json.loads(prompt.split('\nDATA=', 1)[1].split('\nFORMAT_REPAIR=', 1)[0])
        if prompt.startswith('TASK=LEXICAL_LOCAL_EDIT'):
            return self.rewrite(data)
        return next(self.choices)


class ContractTests(unittest.TestCase):
    def test_units_preserve_offsets_crlf_and_quotes(self):
        text = '\t「重い蜜だ。」\r\n  粘り気のある蜜がスプーンに付いた。次の文も続く。'
        for u in x.units(text): self.assertEqual(text[u.start:u.end], u.text)
        self.assertEqual(x.units(text)[0].text, '「重い蜜だ。」')
        self.assertEqual(len(x.units(text)), 3)
    def test_units_ignore_markdown_and_code(self):
        text = '# 見出し\n```txt\n秘密の文章。\n```\n普通の文章。'
        self.assertEqual([u.text for u in x.units(text)], ['普通の文章。'])
    def test_oversized_unit_is_not_silently_truncated(self):
        self.assertEqual(x.units('あ'*321+'。'), [])
    def test_duplicate_ids_rejected(self):
        with self.assertRaises(x.EditError): x.catalog(repertoire(row(), row()))
    def test_invalid_repertoire_schema(self):
        with self.assertRaises(x.EditError): x.catalog({'entries': []})
    def test_unsupported_role_and_invalid_priority_rejected(self):
        for change in ({'role': 'invented'}, {'priority': float('nan')}, {'observed_forms': 'bad'}):
            with self.subTest(change=change), self.assertRaises(x.EditError):
                x.catalog(repertoire({**row(), **change}))
    def test_single_work_not_promoted(self):
        self.assertEqual(x.catalog(repertoire({**row(), 'evidence_scope': 'single_work_candidate'})), [])
    def test_dictionary_only_headword_not_used_as_attested_form(self):
        r = row('思う'); r['observed_forms'] = []
        self.assertEqual(x.surfaces(r), [])
        self.assertEqual(x.catalog(repertoire(r)), [])
    def test_exact_unicode_not_normalized(self):
        r = row('トロットロ')
        self.assertEqual(x.surfaces(r), ['トロットロ'])
    def test_no_unrelated_fillers(self):
        r = row('遠い', role='figurative'); r['note'] = '山間と距離。'
        self.assertEqual(x.opportunities('砂糖を溶かした。', '料理', [r]), [])
    def test_already_present_not_stuffed_again(self):
        self.assertEqual(x.opportunities(AFTER, BRIEF, [row()]), [])
    def test_prompt_has_no_ids_or_corpus_counts(self):
        prompt = x.proposal_prompt(BEFORE, BRIEF, job())
        self.assertNotIn('PRIVATE_CARD_001', prompt)
        self.assertNotIn('work_count', prompt)
        self.assertIn('JSON、キー、番号、説明は不要', prompt)
    def test_unchanged_response_is_successful_noop(self):
        self.assertEqual(x.validate_proposal(job(), BEFORE), {'status': 'unchanged'})
    def test_local_replacement_bound_to_python_span(self):
        p = x.validate_proposal(job(start=10), AFTER)
        self.assertEqual(p['start'], 10)
        self.assertEqual(p['targets'][0]['entry_id'], 'PRIVATE_CARD_001')
        self.assertTrue(any(d['kind'] == 'replace' for d in p['changes']))
    def test_short_insertion_is_supported(self):
        r = row('ゆっくり', role='emphasis')
        before = '相手は椅子から立ち上がった。'
        after = '相手はゆっくり椅子から立ち上がった。'
        p = x.validate_proposal(job(before, r), after)
        self.assertTrue(any(d['kind'] == 'insert' for d in p['changes']))
    def test_mixed_short_change_can_preserve_structure(self):
        before = '粘り気のある蜜が重くスプーンに付いた。'
        after = 'ねっとりした蜜が重くスプーンに付いた。'
        self.assertEqual(x.validate_proposal(job(before), after)['status'], 'proposed')
    def test_json_and_explanation_are_not_guessed_as_prose(self):
        for value in ({'text': AFTER}, '{"text":"x"}', '```'+AFTER+'```', '変更後:'+AFTER):
            with self.subTest(type=type(value).__name__), self.assertRaises(x.EditError):
                x.validate_proposal(job(), value)
    def test_number_change_rejected(self):
        with self.assertRaisesRegex(x.EditError, 'protected_marker_changed'):
            x.validate_proposal(job('3杯の蜜が付いた。'), '4杯のねっとりした蜜が付いた。')
    def test_negation_change_rejected(self):
        with self.assertRaisesRegex(x.EditError, 'protected_marker_changed'):
            x.validate_proposal(job('蜜は付いていない。'), 'ねっとりした蜜は付いている。')
    def test_quote_boundary_change_rejected(self):
        with self.assertRaisesRegex(x.EditError, 'protected_marker_changed'):
            x.validate_proposal(job('「'+BEFORE+'」'), AFTER)
    def test_named_protection_rejected(self):
        with self.assertRaisesRegex(x.EditError, 'protected_term_changed'):
            x.validate_proposal(job('太郎は'+BEFORE), '次郎は'+AFTER, ('太郎',))
    def test_no_lexical_change_rejected(self):
        with self.assertRaisesRegex(x.EditError, 'no_suggested_surface_introduced'):
            x.validate_proposal(job(), BEFORE.replace('付いた', 'ついた'))
    def test_repetition_rejected(self):
        before = '蜜の粘り気がスプーンに残り、ゆっくりと垂れていた。'
        with self.assertRaises(x.EditError):
            x.validate_proposal(job(before), 'ねっとりねっとりねっとり'+before)
    def test_whole_scene_rewrite_rejected(self):
        with self.assertRaises(x.EditError):
            x.validate_proposal(job(), 'ねっとりした蜜の工場では新しい人間関係が始まり、'+('長い話。'*20))
    def test_stale_hash_rejected(self):
        with self.assertRaisesRegex(x.EditError, 'stale_draft'):
            x.apply_patches(BEFORE, [], 'bad')
    def test_patch_applies_one_position_not_all_occurrences(self):
        scene = BEFORE+'\n'+BEFORE
        p = x.validate_proposal(job(), AFTER)
        self.assertEqual(x.apply_patches(scene, [p], x.digest(scene)), AFTER+'\n'+BEFORE)
    def test_overlap_rejected(self):
        p = x.validate_proposal(job(), AFTER)
        with self.assertRaisesRegex(x.EditError, 'overlapping_or_stale_patch'):
            x.apply_patches(BEFORE, [p, p], x.digest(BEFORE))
    def test_stale_substring_rejected(self):
        p = x.validate_proposal(job(), AFTER); p['before'] = '違う'
        with self.assertRaises(x.EditError): x.apply_patches(BEFORE, [p], x.digest(BEFORE))
    def test_judge_choices_exact_only(self):
        for bad in ('Bの方が良い', 'ACCEPT', '', True, '{"choice":"B"}'):
            with self.assertRaises(x.EditError): x.parse_choice(bad)
    def test_edit_budget_bounds(self):
        with self.assertRaises(x.EditError): x.opportunities(BEFORE, BRIEF, [row()], 13)
        with self.assertRaises(x.EditError): x.run(BEFORE, BRIEF, repertoire(), Fake(), workers=5)
        with self.assertRaises(x.EditError): x.run(BEFORE, BRIEF, repertoire(), Fake(), retries=3)


class WorkflowTests(unittest.TestCase):
    def test_default_suggestion_does_not_select_rewrite(self):
        m = Fake(); out = x.run(BEFORE, BRIEF, repertoire(), m)
        self.assertEqual(out['state'], 'suggestions_only')
        self.assertEqual(out['selected_text'], BEFORE)
        self.assertEqual(out['candidate_text'], AFTER)
        self.assertEqual(out['model_requests'], 1)
    def test_accept_requires_both_order_swapped_preferences(self):
        out = x.run(BEFORE, BRIEF, repertoire(), Fake(), mode='revise')
        self.assertEqual(out['comparison_verdicts'], ['B', 'A'])
        self.assertEqual(out['selected_text'], AFTER)
        self.assertEqual(out['model_requests'], 3)
    def test_disagreement_tie_uncertainty_keep_original(self):
        for choices in [('B', 'B'), ('TIE', 'A'), ('UNSURE', 'A'), ('A', 'B')]:
            with self.subTest(choices=choices):
                out = x.run(BEFORE, BRIEF, repertoire(), Fake(choices), mode='revise')
                self.assertEqual(out['selected_text'], BEFORE)
                self.assertEqual(out['state'], 'rejected_or_uncertain')
    def test_bad_judge_output_retains_original(self):
        out = x.run(BEFORE, BRIEF, repertoire(), Fake(('invalid', 'A')), mode='revise', retries=0)
        self.assertEqual(out['selected_text'], BEFORE)
    def test_separate_judge_used(self):
        proposal, judge = Fake(), Fake()
        out = x.run(BEFORE, BRIEF, repertoire(), proposal, judge=judge, mode='revise')
        self.assertEqual(len(proposal.prompts), 1)
        self.assertEqual(len(judge.prompts), 2)
        self.assertEqual(out['state'], 'accepted_by_model')
    def test_noop_skips_comparisons(self):
        m = Fake(rewrite=lambda d: d['TARGET'])
        out = x.run(BEFORE, BRIEF, repertoire(), m, mode='revise')
        self.assertEqual(out['model_requests'], 1)
        self.assertEqual(out['state'], 'unchanged')
    def test_no_candidates_make_no_inference(self):
        out = x.run(BEFORE, BRIEF, {'schema': 'pastiche.lexical.repertoire.v1', 'entries': []}, Fake())
        self.assertEqual(out['model_requests'], 0)
        self.assertEqual(out['state'], 'unchanged')
        parallel = x.run(BEFORE, BRIEF, {'schema': 'pastiche.lexical.repertoire.v1', 'entries': []}, Fake(), workers=4)
        self.assertEqual(parallel['model_requests'], 0)
    def test_bad_model_result_retries_bounded(self):
        out = x.run(BEFORE, BRIEF, repertoire(), Fake(rewrite=lambda d: '{bad'), retries=2)
        self.assertEqual(out['model_requests'], 3)
        self.assertEqual(out['selected_text'], BEFORE)
    def test_bad_model_transport_escapes_not_success(self):
        class Broken:
            def ask_text(self, _): raise OSError('network unavailable')
        with self.assertRaises(OSError): x.run(BEFORE, BRIEF, repertoire(), Broken())
    def test_comparison_does_not_send_edit_rationale_or_ids(self):
        prompt = x.compare_prompt(BEFORE, AFTER, BRIEF, [row()])
        data = json.loads(prompt.split('\nDATA=', 1)[1])
        self.assertEqual(set(data), {'A','B','brief','choices'})
        self.assertNotIn('PRIVATE_CARD_001', prompt)
    def test_same_word_does_not_get_inserted_repeatedly(self):
        out = x.run(BEFORE+'\n'+BEFORE, BRIEF, repertoire(), Fake(), max_edits=3)
        self.assertEqual(len(out['patches']), 1)
        self.assertEqual(out['candidate_text'].count('ねっとり'), 1)
    def test_parallel_workers_are_bounded_and_results_deterministic(self):
        state = {'active': 0, 'peak': 0, 'copies': 0}; lock = threading.Lock()
        class Worker(Fake):
            def worker_copy(self):
                state['copies'] += 1
                return Worker()
            def ask_text(self, prompt):
                with lock:
                    state['active'] += 1; state['peak'] = max(state['active'], state['peak'])
                try:
                    time.sleep(.015)
                    return super().ask_text(prompt)
                finally:
                    with lock: state['active'] -= 1
        text = '\n'.join([BEFORE]*6)
        out = x.run(text, BRIEF, repertoire(), Worker(), workers=4)
        self.assertLessEqual(state['peak'], 4)
        self.assertGreater(state['peak'], 1)
        self.assertEqual(state['copies'], 6)
        self.assertEqual(len(out['patches']), 1)


class PersistenceTests(unittest.TestCase):
    def test_file_modification_stops_save(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'draft.txt'; p.write_text(BEFORE)
            inputs = x.Inputs(); inputs.read(p); p.write_text(AFTER)
            with self.assertRaisesRegex(x.EditError, 'input_changed'): inputs.check()
    def test_atomic_result_state_not_source_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); original = root/'draft.txt'; original.write_text(BEFORE)
            snap = x.Inputs(); scene = snap.read(original)
            result = x.run(scene, BRIEF, repertoire(), Fake(), mode='revise')
            folder = root/'run'; folder.mkdir()
            x.persist(folder, result, scene, snap)
            self.assertEqual(original.read_text(), BEFORE)
            self.assertEqual((folder/'selected.txt').read_text(), AFTER)
            self.assertEqual(json.loads((folder/'status.json').read_text())['state'], 'completed')
            self.assertNotIn('candidate_text', json.loads((folder/'result.json').read_text()))
    def test_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'test'; x.write_private(p, 'keep')
            with self.assertRaises(FileExistsError): x.write_private(p, 'overwrite')
            self.assertEqual(p.read_text(), 'keep')
    def test_inputs_cannot_be_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'draft'; p.write_text(BEFORE)
            link = Path(td)/'link'; link.symlink_to(p)
            with self.assertRaises(x.EditError): x.Inputs().read(link)
    def test_crlf_unchanged_outside_patch(self):
        scene = BEFORE+'\r\n次の文章はそのまま残す。\r\n'
        p = x.validate_proposal(job(), AFTER)
        actual = x.apply_patches(scene, [p], x.digest(scene))
        self.assertEqual(actual, AFTER+'\r\n次の文章はそのまま残す。\r\n')



class CommandLineTests(unittest.TestCase):
    def cli_fixture(self, root):
        import shutil
        script = root/'skills/pastiche-write/scripts/lexical_edit.py'
        shared = root/'skills/pastiche-profile/scripts'
        script.parent.mkdir(parents=True); shared.mkdir(parents=True)
        shutil.copy2(SCRIPT, script)
        (shared/'execution.py').write_text('''import json
class Fake:
    def worker_copy(self): return Fake()
    def ask_text(self, prompt):
        data=json.loads(prompt.split("\\nDATA=",1)[1])
        if data['brief']=='SIMULATE_ERROR': raise OSError('fixture failure')
        if data['brief']=='SIMULATE_PENDING':
            from task_transport import TasksPending
            raise TasksPending()
        if prompt.startswith('TASK=LEXICAL_LOCAL_EDIT'):
            return data['TARGET'].replace('粘り気のある','ねっとりした')
        return 'A' if 'ねっとり' in data['A'] else 'B'
def add_model_flags(p):
    p.add_argument('--executor',default='http')
    p.add_argument('--llm-workers',type=int,default=1)
    p.add_argument('--retries',type=int,default=1)
    p.add_argument('--max-output-tokens',type=int,default=1024)
def model_from(args): return Fake()
''', encoding='utf-8')
        (shared/'task_transport.py').write_text('class TasksPending(Exception):\n    pass\n')
        draft = root/'draft.txt'; draft.write_text(BEFORE, encoding='utf-8')
        brief = root/'brief.txt'; brief.write_text(BRIEF, encoding='utf-8')
        rep = root/'rep.json'; rep.write_text(json.dumps(repertoire(), ensure_ascii=False), encoding='utf-8')
        return script, draft, brief, rep
    def invoke(self, root, mode='revise', special=None):
        import subprocess
        script, draft, brief, rep = self.cli_fixture(root)
        if special: brief.write_text(special, encoding='utf-8')
        result = subprocess.run([sys.executable, str(script), '--draft', str(draft), '--brief', str(brief),
                                 '--repertoire', str(rep), '--out', str(root/'output'), '--mode', mode,
                                 '--llm-workers', '4'], capture_output=True, text=True)
        return result, draft, list((root/'output').glob('lexical-edit-*'))
    def test_cli_revise_end_to_end_with_fake_provider(self):
        with tempfile.TemporaryDirectory() as td:
            process, draft, runs = self.invoke(Path(td))
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(draft.read_text(), BEFORE)
            self.assertEqual(len(runs), 1)
            self.assertEqual((runs[0]/'selected.txt').read_text(), AFTER)
            status=json.loads((runs[0]/'status.json').read_text())
            self.assertEqual(status['state'], 'completed')
            if sys.platform!='win32': self.assertEqual((runs[0]/'result.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn(BEFORE, process.stdout+process.stderr)
    def test_cli_suggest_keeps_original(self):
        with tempfile.TemporaryDirectory() as td:
            process, draft, runs = self.invoke(Path(td), 'suggest')
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual((runs[0]/'candidate.txt').read_text(), AFTER)
            self.assertEqual((runs[0]/'selected.txt').read_text(), BEFORE)
    def test_cli_transport_failure_does_not_claim_completion(self):
        with tempfile.TemporaryDirectory() as td:
            process, draft, runs = self.invoke(Path(td), special='SIMULATE_ERROR')
            self.assertEqual(process.returncode, 2)
            self.assertEqual(json.loads((runs[0]/'status.json').read_text())['state'], 'failed')
            self.assertFalse((runs[0]/'selected.txt').exists())
            self.assertEqual(draft.read_text(), BEFORE)
    def test_cli_host_pending_is_not_completion(self):
        with tempfile.TemporaryDirectory() as td:
            process, draft, runs = self.invoke(Path(td), special='SIMULATE_PENDING')
            self.assertEqual(process.returncode, 75)
            self.assertEqual(json.loads((runs[0]/'status.json').read_text())['state'], 'waiting_for_tasks')
            self.assertFalse((runs[0]/'selected.txt').exists())

if __name__ == '__main__': unittest.main()