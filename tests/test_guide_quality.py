"""Readable vocabulary and separate illustrative prose. Synthetic fixtures only."""
from __future__ import annotations
from collections import Counter
from contextlib import redirect_stderr
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
import vocabulary as v
import lexical
import lexical_review as lr
import guide_examples as ex
import llm_style
import prose_contracts


def word(surface,pos='名詞',sub='普通名詞',detail='一般',base='',cform=''):
    return SimpleNamespace(surface=surface,feature=SimpleNamespace(
        pos1=pos,pos2=sub,pos3=detail,orthBase=base,cForm=cform))

class SequenceTagger:
    def __init__(self,rows):self.rows=rows
    def __call__(self,text):return copy.deepcopy(self.rows)


def pattern(status='model_supported',pid='P0001'):
    return {'pattern_id':pid,'status':status,'dimension':'pacing',
        'trigger':'突然の出来事に直面する','mechanism':'短い文を続けて速さを示す',
        'effect':'判断を急ぐ感覚を示す','support_work_count':3,'anchors':[]}


def assessment(form='ねっとり',kind='lemma',role='sensory',**kwargs):
    row={'form':form,'kind':kind,'primary_pos':'副詞','count':12,'work_count':3,
        'style_signal':2.4,'role':role,'typicality':'ordinary','confidence':'medium',
        'note':'粘性のある質感を表す副詞として用いられる。',
        'count_basis':'orthographic_base','observed_forms':[{'form':form,'count':12}]}
    row.update(kwargs);return row


class VocabularyTests(unittest.TestCase):
    def test_inflection_is_grouped_without_losing_surface_counts(self):
        tag=SequenceTagger([word('思っ','動詞','一般',base='思う',cform='連用形'),word('た','助動詞',base='た')])
        a=v.Vocabulary(tag);a.add('思った');a.add('思った')
        rows=a.rows(2);self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['form'],rows[0]['count'],rows[0]['work_count']),('思う',2,2))
        self.assertEqual(rows[0]['observed_forms'],[{'form':'思っ','count':2}])

    def test_work_counts_are_union_not_sum_of_surface_counts(self):
        tag=SequenceTagger([word('思っ','動詞','一般',base='思う',cform='連用形'),word('思う','動詞','一般',base='思う',cform='終止形')])
        a=v.Vocabulary(tag);a.add('思っ思う')
        self.assertEqual((a.rows(1)[0]['count'],a.rows(1)[0]['work_count']),(2,1))

    def test_compound_uses_exact_contiguous_spelling(self):
        a=v.Vocabulary(SequenceTagger([word('脳'),word('みそ')]))
        a.add('脳みそ');self.assertIn('脳みそ',{r['form'] for r in a.rows(1)})
        self.assertNotIn('脳 みそ',{r['form'] for r in a.rows(1)})

    def test_compound_does_not_bridge_whitespace(self):
        a=v.Vocabulary(SequenceTagger([word('脳'),word('みそ')]))
        a.add('脳 みそ');self.assertFalse(any(r['kind']=='expression' for r in a.rows(1)))

    def test_proper_nouns_and_functional_predicates_are_not_content(self):
        for w in [word('太郎',sub='固有名詞'),word('する','動詞','非自立可能'),word('みたい','形状詞','助動詞語幹'),word('三',detail='数詞')]:
            with self.subTest(surface=w.surface):self.assertFalse(v.eligible(w))

    def test_unknown_inflection_is_not_guessed(self):
        a=v.Vocabulary(SequenceTagger([word('思っ','動詞','一般',cform='連用形')]))
        a.add('思っ');self.assertEqual(a.rows(1),[])

    def test_case_phrase_keeps_complete_predicate(self):
        a=v.Vocabulary(SequenceTagger([word('息'),word('を','助詞','格助詞'),word('呑ん','動詞','一般',base='呑む',cform='連用形'),word('だ','助動詞',base='だ',cform='終止形-一般')]))
        a.add('息を呑んだ')
        self.assertIn('息を呑んだ',{r['form'] for r in a.rows(1) if r['kind']=='expression'})
        self.assertNotIn('息を呑ん',{r['form'] for r in a.rows(1) if r['kind']=='expression'})

    def test_broken_predicate_is_not_promoted_to_phrase(self):
        a=v.Vocabulary(SequenceTagger([word('息'),word('を','助詞','格助詞'),word('呑ん','動詞','一般',base='呑む',cform='連用形')]))
        a.add('息を呑ん');self.assertFalse(any(r['kind']=='expression' for r in a.rows(1)))

    def test_pos_balancing_does_not_reward_symbols_or_small_kana(self):
        rows=[assessment('思っ'),assessment('♡'),assessment('ねっとり'),assessment('しっとり'),assessment('熱い',primary_pos='形容詞'),assessment('よう')]
        found=v.candidates(rows,3)
        self.assertEqual(len(found),3)
        self.assertEqual({r['form'] for r in found},{'ねっとり','しっとり','熱い'})

    def test_shortlist_deduplicates_without_adding_counts(self):
        rows=[assessment(),assessment(kind='expression',primary_pos='連語')]
        found=v.candidates(rows,0);self.assertEqual(len(found),1);self.assertEqual(found[0]['count'],12)

    def test_no_tokenizer_means_no_fake_vocabulary(self):
        self.assertEqual(lexical.interpretation_candidates({'vocabulary_units':[]}),[])

    def test_old_parenthetical_fields_are_not_rendered(self):
        self.assertFalse(hasattr(lexical,'kaomoji'))
        text=lexical.private_guide({'works':2,'kaomoji_candidates':[{'form':'（個人名）','count':5}]})
        self.assertNotIn('個人名',text);self.assertNotIn('Kaomoji',text)


class LexicalGuideTests(unittest.TestCase):
    def test_ordinary_useful_word_is_not_removed_as_insufficiently_rare(self):
        guide=lr.make_guide({'assessments':[assessment()]})
        self.assertIn('ねっとり',guide);self.assertIn('粘性のある質感',guide)
        self.assertNotIn('model impression=marked',guide)

    def test_symbols_stems_and_fake_spaced_collocations_are_absent(self):
        rows=[assessment(),assessment('♡','symbol'),assessment('♡','variant'),
              assessment('思っ','token'),assessment('脳 みそ','collocation')]
        self.assertEqual([r['form'] for r in lr.guide_rows({'assessments':rows})],['ねっとり'])

    def test_duplicate_representations_are_not_summed(self):
        rows=[assessment(),assessment(kind='expression',count=99)]
        picked=lr.guide_rows({'assessments':rows});self.assertEqual(len(picked),1)
        self.assertEqual(picked[0]['count'],99)

    def test_topic_and_uncertain_categories_are_not_style_evidence(self):
        rows=[assessment(role=r) for r in ('topic_bound','unclear','general_word')]
        self.assertEqual(lr.guide_rows({'assessments':rows}),[])

    def test_note_survives_validated_batch_cache(self):
        row={'id':'L0001',**assessment()}
        raw={'assessments':[row]}
        clean=lr.validate_judgments(raw,[row]);self.assertEqual(clean[0]['note'],row['note'])
        legacy={k:row[k] for k in ('id','typicality','role','confidence')}
        self.assertEqual(lr.validate_judgments({'assessments':[legacy]},[row]),[legacy])


class IllustrationTests(unittest.TestCase):
    def test_prompt_has_no_source_or_pattern_identifier(self):
        p=pattern();p['evidence_ids']=['W1234:S1234'];p['source_passage']='PRIVATE_SOURCE'
        prompt=ex.example_prompt(p,'時計を直す')
        for term in ['P0001','W1234','PRIVATE_SOURCE']:self.assertNotIn(term,prompt)
        self.assertIn('新しい題材',prompt);self.assertIn('非性的',prompt)

    def test_unsupported_pattern_cannot_get_example(self):
        with self.assertRaises(ValueError):ex.example_prompt(pattern('hypothesis'),'時計')

    def test_example_is_explicitly_separate_from_evidence(self):
        p=pattern();s=[{'dimension':'pacing','summary':'短文を連続させる技法です','writing_use':'新しい場面で短文の間隔を選ぶ','pattern_ids':['P0001']}]
        examples={'origin':'synthetic_illustration_not_source_evidence','patterns':[{'pattern_id':'P0001','examples':[{'status':'available','text':'時計が止まった。指を伸ばす。針が、動いた。'}]}]}
        guide=llm_style.qualitative_guide([p],total_works=4,sampled_works=4,chunks=8,synthesis=s,illustrations=examples)
        self.assertIn('説明例1（新規作成）',guide);self.assertIn('少なくとも3作品',guide)
        self.assertIn('根拠そのものでもありません',guide);self.assertNotIn('根拠: P0001',guide)
        self.assertNotIn('P0001',guide)

    def test_source_overlap_is_rejected_before_caching(self):
        source='静かな部屋で古い時計を眺めていると針が突然止まってしまった。'
        with self.assertRaisesRegex(prose_contracts.ProseError,'example_source_overlap'):
            ex.parse_example(source,ex.SourceGuard([source]))

    def test_unavailable_is_not_a_fabricated_example(self):
        parsed=ex.parse_example('例示保留',ex.SourceGuard([]))
        self.assertEqual(parsed,{'status':'unavailable','reason':'context_dependent'})
        self.assertNotIn('text',parsed)

    def test_json_and_ids_are_not_guessed_into_plain_text(self):
        for value in ['{"text":"短い説明の文章を返します"}','P0001の文章をここに書くことにします。']:
            with self.assertRaises(prose_contracts.ProseError):ex.parse_example(value,ex.SourceGuard([]))

    def test_enrichment_and_replay_leave_support_artifacts_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);corpus=root/'corpus';corpus.mkdir()
            (corpus/'story.txt').write_text('青い鳥が古い駅で羽を休めた。'*10,encoding='utf-8')
            p=pattern();summary={'sections':[{'dimension':'pacing','summary':'短文を連続させる技法です','writing_use':'新しい場面で文長を選びます','pattern_ids':['P0001']}]}
            artifacts={'qualitative_patterns.json':{'patterns':[p]},'qualitative_summary.json':summary,
                       'style_llm_audit.json':{'total_works':1,'sampled_works':1,'selected_chunks':1}}
            for name,obj in artifacts.items():(root/name).write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
            before={n:(root/n).read_bytes() for n in artifacts}
            class Model:
                backend='fixture';model='fixture';endpoint='fixture';max_output_tokens=512
                def __init__(self):self.calls=0
                def ask_text(self,prompt):
                    self.calls+=1
                    return '窓が鳴った。手が止まる。相手は私のほうを向いた。'
            model=Model()
            with redirect_stderr(io.StringIO()):report=ex.enrich(root,corpus,model,per_pattern=1,workers=1,retries=0)
            self.assertEqual(report['available_examples'],1);self.assertEqual(model.calls,1)
            self.assertEqual(before,{n:(root/n).read_bytes() for n in artifacts})
            with redirect_stderr(io.StringIO()):again=ex.enrich(root,corpus,model,per_pattern=1,workers=1,retries=0)
            self.assertEqual(again['model_calls'],0);self.assertEqual(model.calls,1)
            self.assertFalse(json.loads((root/'guide_examples.json').read_text())['source_passages_sent_to_model'])

if __name__=='__main__':unittest.main()
