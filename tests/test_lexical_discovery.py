"""Synthetic lexical discovery, privacy, cache and writer-retrieval regressions."""
from __future__ import annotations
from collections import Counter
from contextlib import redirect_stderr
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/pastiche-profile/scripts'))
import lexical_discovery as d
import lexical_repertoire as rep
import lexical_review as review
import lexical_context as context
import lexical
import vocabulary as v
from test_guide_quality import word,SequenceTagger
spec=importlib.util.spec_from_file_location('palette',ROOT/'skills/pastiche-write/scripts/lexical_palette.py')
palette=importlib.util.module_from_spec(spec);spec.loader.exec_module(palette)


def row(form='ねっとり',count=8,works=3,**kw):
    result={'form':form,'kind':'lemma','primary_pos':'副詞','count':count,'work_count':works,
        'headword_attested':True,'observed_forms':[{'form':form,'count':count}],
        'count_basis':'orthographic_base','nonstandard_signals':[],'context_diversity':3,
        'dispersion_dp':.1,'max_work_share':.5,'selection_version':d.VERSION}
    result.update(kw);return result


def judged(**kw):
    options=dict(typicality='marked',role='sensory',confidence='medium',
        note='粘る手触りを描く場面に使う。軽さを示す場面では使わない。',
        context_count=3,style_signal=2)
    options.update(kw)
    return row(**options)


class SelectionTests(unittest.TestCase):
    def test_audit_accounts_for_every_unit(self):
        rows=[row(),row('別の語'),row('一度だけ',1,1),row('もう'),row(kind='expression',primary_pos='連語')]
        selected,audit,decisions=d.select(rows)
        self.assertEqual(len(decisions),len(rows))
        self.assertEqual(audit['all_units_examined'],sum(audit['screened_out'].values())+audit['selected']+audit['deferred_budget'])
        self.assertEqual(len({r['form'] for r in selected}),len(selected))

    def test_rare_and_nonstandard_survive_common_word_flood(self):
        rows=[row('一般語'+str(i),10000,40,primary_pos='名詞') for i in range(100)]
        rows += [row('こもれび色',3,2,primary_pos='未知語',kind='surface',nonstandard_signals=['dictionary_unknown']),
                 row('雨ほどけ',2,2,primary_pos='名詞')]
        selected,audit,_=d.select(rows,8)
        self.assertIn('こもれび色',{x['form'] for x in selected})
        self.assertIn('雨ほどけ',{x['form'] for x in selected})
        self.assertGreater(audit['selected_by_lane'].get('low_frequency',0),0)

    def test_shortlist_position_does_not_change_review_identity(self):
        a=row();a.update(id='L0001',selection_score=2,context_digest='same')
        b=dict(a,id='L0999',selection_score=99,discovery_lane='low_frequency')
        self.assertEqual(review.lexical_binding(a),review.lexical_binding(b))
        self.assertNotEqual(review.lexical_binding(a),review.lexical_binding(dict(b,context_digest='different')))

    def test_auto_budget_is_not_fixed_96(self):
        rows=[row('語彙'+str(i),30,10,primary_pos='名詞') for i in range(600)]
        selected,audit,_=d.select(rows)
        self.assertGreater(len(selected),96)
        self.assertLessEqual(len(selected),d.AUTO_MAX)
        self.assertEqual(audit['deferred_budget'],600-len(selected))

    def test_explicit_budget_and_all_mode(self):
        rows=[row('語彙'+str(i)) for i in range(150)]
        self.assertEqual(len(d.select(rows,7)[0]),7)
        self.assertEqual(len(d.select(rows,0)[0]),150)
        with self.assertRaises(ValueError):d.select(rows,-2)

    def test_single_work_candidate_is_marked_not_generalized(self):
        selected,_,_=d.select([row('とろりん色',4,1,nonstandard_signals=['orthographic_variant'])])
        self.assertEqual(selected[0]['evidence_scope'],'single_work_candidate')
        self.assertEqual(d.select([row('普通語',4,1)])[0],[])

    def test_singletons_and_clipped_stems_still_filtered(self):
        rows=[row('珍しいだけ',1,1),row('思っ',20,10,primary_pos='動詞'),row('そう'),row('脳 みそ')]
        self.assertEqual(d.select(rows)[0],[])

    def test_unattested_base_never_fabricates_a_spelling(self):
        selected,_,_=d.select([row('辞書の仮説',headword_attested=False)])
        self.assertEqual(selected,[])

    def test_spelling_is_not_normalized(self):
        rows=[row('とろっとろ'),row('トロットロ')]
        self.assertEqual({r['form'] for r in d.select(rows,0)[0]},{'とろっとろ','トロットロ'})

    def test_no_reference_never_claims_contrast(self):
        chosen,audit,_=d.select([row()])
        self.assertFalse(audit['reference_available'])
        self.assertNotIn('contrast',chosen[0]['discovery_reasons'])

    def test_reference_uses_same_kind_pos_and_size(self):
        a=row('ねっとり',20,5);b=row('ねっとり',100,10)
        out=d.contrast([a],[b],1000,100000)[0]
        self.assertGreater(out['reference_log2_ratio'],4)
        self.assertAlmostEqual(out['reference_log2_ratio'],math.log2((20.5/1001)/(100.5/100001)),places=5)
        selected,audit,_=d.select([out]);self.assertTrue(audit['reference_available'])
        self.assertEqual(selected[0]['discovery_lane'],'contrast')

    def test_reference_counts_are_not_substring_matches(self):
        out=d.contrast([row('脳みそ',kind='expression',primary_pos='連語')],
                       [row('脳みそ',kind='lemma',primary_pos='名詞')],1000,1000)[0]
        self.assertEqual(out['reference_count'],0)

    def test_negative_contrast_is_not_selected_as_distinctive(self):
        out=d.contrast([row(count=3)],[row(count=900)],10000,10000)
        self.assertNotIn('contrast',d.select(out)[0][0]['discovery_reasons'])
        with self.assertRaises(ValueError):d.contrast(out,[],0,10)

    def test_nesting_only_follows_component_boundaries(self):
        a=row('静かな雨',10,3,kind='expression',primary_pos='連語',token_count=2,components=['静かな','雨'])
        b=row('深い静かな雨',8,3,kind='expression',primary_pos='連語',token_count=3,components=['深い','静かな','雨'])
        scores=d.phrase_scores([a,b]);self.assertEqual(scores[d.identity(a)],2)
        self.assertGreater(scores[d.identity(b)],scores[d.identity(a)])
        c=row('かな雨',7,3,kind='expression',primary_pos='連語',token_count=2,components=['かな','雨'])
        self.assertEqual(d.phrase_scores([b,c])[d.identity(c)],7)

    def test_spread_changes_recurrent_rank(self):
        broad=row('均等な語',20,5,dispersion_dp=0)
        clump=row('偏る語',20,5,dispersion_dp=.85)
        chosen,_,_=d.select([clump,broad],2)
        self.assertGreater(next(x for x in chosen if x['form']=='均等な語')['selection_score'],
                           next(x for x in chosen if x['form']=='偏る語')['selection_score'])

    def test_new_selection_is_on_normal_interpretation_path(self):
        profile={'vocabulary_units':[row('こもれび色',3,2,kind='surface',primary_pos='未知語',nonstandard_signals=['dictionary_unknown'])]}
        self.assertEqual(lexical.interpretation_candidates(profile)[0]['selection_version'],d.VERSION)


class MeasurementTests(unittest.TestCase):
    def test_reference_signal_reaches_cli_shortlist(self):
        class Tagger:
            def __call__(self,text):
                return [word(text[i:i+2],base=text[i:i+2]) for i in range(0,len(text),2)]
        with tempfile.TemporaryDirectory() as td,redirect_stderr(io.StringIO()):
            root=Path(td);target=root/'target';reference=root/'ref';out=root/'out'
            target.mkdir();reference.mkdir()
            for i in range(2):
                (target/f'{i}.txt').write_text('薄霧'*20+'青海'*2)
                (reference/f'{i}.txt').write_text('薄霧'*2+'青海'*20)
            with patch.object(lexical,'morph_tagger',return_value=Tagger()):
                code=lexical.main([str(target),'--out',str(out),'--reference',str(reference),
                                   '--candidate-limit','0','--min-chars','1'])
            self.assertEqual(code,0)
            candidates=json.loads((out/'lexical_candidates.json').read_text())
            self.assertTrue(candidates['discovery_audit']['reference_available'])
            thin=next(r for r in candidates['candidates'] if r['form']=='薄霧')
            self.assertGreater(thin['reference_log2_ratio'],3)
            self.assertIn('contrast',thin['discovery_reasons'])
            self.assertTrue((out/'lexical_selection.jsonl').is_file())

    def test_dp_uses_document_size_not_only_file_count(self):
        tag=SequenceTagger([word('風',base='風')]);a=v.Vocabulary(tag)
        a.add('風');a.add('風風風')
        out=a.rows(2)[0]
        self.assertAlmostEqual(out['dispersion_dp'],.25)
        self.assertEqual(out['work_count'],2)
        self.assertNotIn('filename',out)

    def test_unknown_surface_recovery_preserves_exact_text(self):
        token=word('ふわぷる',base='ふわぷる');token.is_unk=True
        a=v.Vocabulary(SequenceTagger([token]));a.add('ふわぷる');a.add('ふわぷる')
        surfaces=[r for r in a.rows(2) if r['kind']=='surface']
        self.assertEqual(surfaces[0]['form'],'ふわぷる')
        self.assertIn('dictionary_unknown',surfaces[0]['nonstandard_signals'])

    def test_reduplicated_surface_has_signal_not_rarity_claim(self):
        token=word('ふわふわ','副詞',base='ふわふわ')
        a=v.Vocabulary(SequenceTagger([token]));a.add('ふわふわ')
        self.assertIn('reduplication',a.rows(1)[0]['nonstandard_signals'])
        self.assertNotIn('author_unique',a.rows(1)[0])

    def test_duplicate_contexts_not_independent_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for i in range(3):(root/f'{i}.txt').write_text('ねっとりした糊。')
            tag=SequenceTagger([word('ねっとり','副詞',base='ねっとり')])
            result=context.collect(root,[{'id':'L0001',**row()}],tag)
            self.assertEqual(len(result['L0001']),1)

    def test_context_reservoir_reaches_three_different_works(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for i in range(8):(root/f'{i}.txt').write_text('ねっとりした糊'+str(i)+'。')
            tag=SequenceTagger([word('ねっとり','副詞',base='ねっとり')])
            rows=[{'id':'L0001',**row()}]
            first=context.collect(root,rows,tag)
            self.assertEqual(len(first['L0001']),3)
            self.assertEqual(first,context.collect(root,rows,tag))

    def test_missing_context_skips_model_not_invents_gloss(self):
        with tempfile.TemporaryDirectory() as td,redirect_stderr(io.StringIO()):
            root=Path(td);f=root/'lexical_candidates.json'
            f.write_text(json.dumps({'schema':'pastiche.lexical.candidates.v2','candidates':[{'id':'L0001',**row()}]}))
            class Model:
                backend='fixture';model='fixture';endpoint='fixture'
                def ask(self,prompt):raise AssertionError('no context must not cause inference')
            result=review.assess(f,root,Model(),retries=0)
            self.assertEqual(result['actual_model_calls_this_run'],0)
            self.assertEqual(result['assessments'][0]['typicality'],'uncertain')
            self.assertEqual(json.loads((root/'lexical_repertoire.json').read_text())['entries'],[])


class WriterTests(unittest.TestCase):
    def test_contextual_cards_preserve_exact_spelling(self):
        data=rep.build({'assessments':[judged(form='トロットロ')],'reviewed_count':1})
        self.assertEqual(data['entries'][0]['preferred_surface'],'トロットロ')
        self.assertFalse(data['reference_available'])

    def test_topic_low_confidence_and_uncertain_are_not_repertoire(self):
        a=judged();rows=[dict(a,role='topic_bound'),dict(a,confidence='low'),dict(a,typicality='uncertain'),dict(a,context_count=0)]
        self.assertEqual(rep.build({'assessments':rows})['entries'],[])

    def test_ordinary_but_stylistic_words_remain(self):
        self.assertEqual(rep.build({'assessments':[dict(judged(),typicality='ordinary')]})['eligible_entries'],1)

    def test_palette_prefers_relevant_scene(self):
        a=judged(form='ねっとり');b=judged(form='どくん',role='emotion',note='心臓の緊張を伝える。')
        data=rep.build({'assessments':[a,b]})
        chosen=palette.select(data,'糊の粘る手触りと質感を描く。',1)
        self.assertEqual(chosen[0]['form'],'ねっとり')

    def test_literal_audit_longest_first_without_quotas(self):
        r={'entry_id':'V1','form':'さらさら','kind':'surface','observed_forms':[{'form':'さら','count':2},{'form':'さらさら','count':3}]}
        out=palette.exact_usage([r],'さらさらと落ちる。')[0]
        self.assertEqual(out['hits'],1);self.assertEqual(out['matched_forms'],{'さらさら':1})
        self.assertEqual(palette.exact_usage([r],'雨が止んだ。')[0]['status'],'not_observed')

    def test_invalid_palette_schema_and_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.json';p.write_text('{"schema":"wrong"}')
            with self.assertRaises(ValueError):palette.load(p)
            data=rep.build({'assessments':[judged()]});data['entries']*=2;p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):palette.load(p)

    def test_cli_never_stores_brief_or_draft_text(self):
        with tempfile.TemporaryDirectory() as td,redirect_stderr(io.StringIO()):
            root=Path(td);p=root/'r.json';p.write_text(json.dumps(rep.build({'assessments':[judged()]})))
            brief=root/'brief.txt';brief.write_text('PRIVATE_BRIEF_SENTINEL 糊の手触り')
            draft=root/'draft.txt';draft.write_text('PRIVATE_DRAFT_SENTINEL ねっとり')
            out=root/'palette.json'
            self.assertEqual(palette.main(['--repertoire',str(p),'--brief',str(brief),'--draft',str(draft),'--out',str(out)]),0)
            text=out.read_text();self.assertNotIn('PRIVATE_BRIEF_SENTINEL',text);self.assertNotIn('PRIVATE_DRAFT_SENTINEL',text)
            self.assertEqual(json.loads(text)['usage'][0]['hits'],1)


if __name__=='__main__':unittest.main()
