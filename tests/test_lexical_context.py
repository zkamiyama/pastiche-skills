"""Context disambiguation is transient and never changes measured counts."""
from pathlib import Path
import io,json,sys,tempfile,unittest
from contextlib import redirect_stderr
from unittest.mock import patch
SCRIPTS=Path(__file__).resolve().parents[1]/'skills/pastiche-profile/scripts'
sys.path.insert(0,str(SCRIPTS))
import lexical_context as lc
import lexical_review as lr
import guide_examples as ex
import prose_contracts as pc
import vocabulary as v
from test_guide_quality import word,SequenceTagger,assessment,pattern

class ContextTests(unittest.TestCase):
    def test_names_masked_and_duplicate_contexts_collapsed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for i in range(3):(root/f'{i}.txt').write_text('太郎はねっとりした糊を混ぜた。',encoding='utf-8')
            tag=SequenceTagger([word('太郎',sub='固有名詞'),word('は','助詞'),word('ねっとり','副詞',base='ねっとり')])
            result=lc.collect(root,[{'id':'L0001','form':'ねっとり','kind':'lemma','primary_pos':'副詞'}],tag)
            self.assertEqual(len(result['L0001']),1)
            self.assertTrue(all('太郎' not in s and '［固有名詞］' in s for s in result['L0001']))

    def test_long_quote_is_not_an_abstract_explanation(self):
        text='この文章は説明のために作った十分に長い合成の原文です。'
        self.assertFalse(lc.no_long_quote(text,[text]))
        self.assertTrue(lc.no_long_quote('粘り気のある質感を表す副詞です。',[text]))

    def test_unattested_dictionary_headword_not_shortlisted(self):
        row=assessment('イキる',primary_pos='動詞',headword_attested=False)
        self.assertEqual(v.candidates([row]),[])

    def test_context_binding_and_note_cache_preserve_privacy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);file=root/'lexical_candidates.json'
            row={'id':'L0001',**assessment()}
            file.write_text(json.dumps({'schema':'pastiche.lexical.candidates.v2','candidates':[row]}),encoding='utf-8')
            source='PRIVATE_SOURCE_SENTENCE_CONTEXT'
            class Model:
                backend='fixture';model='fixture';endpoint='fixture'
                def __init__(self):self.prompts=[]
                def ask(self,prompt):
                    self.prompts.append(prompt)
                    return {k:row[k] for k in ('typicality','role','confidence','note')}
            m=Model()
            with patch('lexical.morph_tagger',return_value=object()),patch.object(lc,'collect',return_value={'L0001':[source]}),redirect_stderr(io.StringIO()):
                result=lr.assess(file,root,m,corpus=root,retries=0,progress_interval=0)
                again=lr.assess(file,root,m,corpus=root,retries=0,progress_interval=0)
            self.assertEqual(len(m.prompts),1);self.assertIn(source,m.prompts[0])
            self.assertEqual(result['context_checked_forms'],1);self.assertEqual(again['actual_model_calls_this_run'],0)
            self.assertNotIn(source,'\n'.join(p.read_text() for p in root.rglob('*.json')))

    def test_preferred_heart_is_checked_not_silently_rewritten(self):
        guard=ex.SourceGuard([]);guard.preferred_heart='♡'
        with self.assertRaisesRegex(pc.ProseError,'example_unpreferred_symbol'):
            ex.parse_example('やっと時計が動き始めたよ、嬉しい！♥',guard)
        good='やっと時計が動き始めたよ、嬉しい！♡'
        self.assertEqual(ex.parse_example(good,guard)['text'],good)

    def test_only_ideographic_space_transport_spelling_is_cleaned(self):
        text=r'「時計が動いた！\u3000ほら、見て！」'
        self.assertEqual(ex.parse_example(text,ex.SourceGuard([]))['text'],text.replace(r'\u3000','　'))

    def test_topic_dependency_is_a_visible_omission_not_a_false_generalization(self):
        self.assertTrue(ex.TOPIC_DEPENDENT.search('幼児語と官能用語を混ぜる'))
        self.assertFalse(ex.TOPIC_DEPENDENT.search('短文を交互に配置して速度を変える'))

if __name__=='__main__':unittest.main()
