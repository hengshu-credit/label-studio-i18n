import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import torch
from transformers import BertConfig, BertForSequenceClassification, BertForTokenClassification, BertTokenizerFast
from fastapi.testclient import TestClient
from transformers_service import Runtime, create_app

CLASS_XML = '<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="yes"/><Choice value="no"/></Choices></View>'
NER_XML = '<View><Text name="text" value="$content"/><Labels name="entities" toName="text"><Label value="BANK"/></Labels></View>'


class TransformersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        for kind, labels, model_cls in [('classification',['yes','no'],BertForSequenceClassification),
                                        ('ner',['O','B-BANK','I-BANK'],BertForTokenClassification)]:
            path = cls.root/kind; path.mkdir()
            (path/'vocab.txt').write_text('[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\nhello\nworld\nbank\npaid\n')
            tokenizer = BertTokenizerFast(vocab_file=str(path/'vocab.txt'),do_lower_case=True)
            tokenizer.save_pretrained(path)
            cfg = BertConfig(vocab_size=9,hidden_size=16,num_hidden_layers=1,num_attention_heads=2,
                             intermediate_size=24,max_position_embeddings=64,
                             id2label=dict(enumerate(labels)),label2id={s:i for i,s in enumerate(labels)})
            model_cls(cfg).save_pretrained(path)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.output = tempfile.TemporaryDirectory()
        self.addCleanup(self.output.cleanup)
        self.runtime = Runtime(Path(self.output.name), 'cpu')

    def body(self, ner=False):
        return {'project':'test.1','label_config': NER_XML if ner else CLASS_XML,
                'params':{'base_model':str(self.root/('ner' if ner else 'classification')),
                          'model_task':'token-classification' if ner else 'text-classification'},
                'tasks':[{'id':1,'data':{'content':'hello bank paid'}}]}

    def test_real_training_checkpoint_and_restart(self):
        body=self.body(); first=self.runtime.predict(body)['results'][0]
        self.assertTrue(0 <= first['score'] <= 1)
        self.assertEqual(first['result'][0]['type'],'choices')
        body['annotations']=[{**body['tasks'][0],'annotations':[{'result':[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['yes']}}]}]}]
        cfg=self.runtime.config(body); model=self.runtime.load(cfg)
        before=model.classifier.weight.detach().clone()
        trained=self.runtime.train(body)
        self.assertNotEqual(first['model_version'],trained['model_version'])
        cfg=self.runtime.config(body); model=self.runtime.load(cfg)
        self.assertFalse(torch.equal(before,model.classifier.weight.detach()))
        restarted=Runtime(Path(self.output.name),'cpu')
        self.assertEqual(restarted.predict(body),self.runtime.predict(body))
        self.assertEqual(restarted.setup(body)['model_version'],trained['model_version'])
        body['project']='other.2'
        self.assertTrue(restarted.setup(body)['model_version'].endswith('-base'))

    def test_ner_training_offsets_and_confidence(self):
        body=self.body(True)
        body['annotations']=[{**body['tasks'][0],'annotations':[{'result':[{'from_name':'entities','to_name':'text','type':'labels','value':{'start':6,'end':10,'text':'bank','labels':['BANK']}}]}]}]
        rows=self.runtime.training_rows(self.runtime.config(body),body)
        self.assertEqual(rows[0][1],[-100,0,1,0,-100])
        self.runtime.train(body)
        result=self.runtime.predict(body)['results'][0]
        self.assertTrue(0 <= result['score'] <= 1)
        for region in result['result']:
            value=region['value'];self.assertEqual(value['text'],body['tasks'][0]['data']['content'][value['start']:value['end']])

    def test_label_mismatch_and_no_silent_truncation(self):
        body=self.body();body['label_config']=CLASS_XML.replace('yes','different')
        with self.assertRaisesRegex(ValueError,'id2label'):self.runtime.setup(body)
        body=self.body();body['tasks'][0]['data']['content']='hello '*100
        with self.assertRaisesRegex(ValueError,'token limit'):self.runtime.predict(body)

    def test_new_head_needs_training_before_prediction(self):
        body=self.body();body['params']['initialize_head']=True
        body['label_config']=CLASS_XML.replace('yes','new-label')
        with self.assertRaisesRegex(ValueError,'requires human training'):self.runtime.predict(body)
        body['annotations']=[{**body['tasks'][0],'annotations':[{'result':[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['new-label']}}]}]}]
        self.runtime.train(body)
        prediction=self.runtime.predict(body)['results'][0]
        self.assertIn(prediction['result'][0]['value']['choices'][0],['new-label','no'])

    def test_bad_ner_span_preserves_checkpoint(self):
        body=self.body(True); version=self.runtime.setup(body)
        body['annotations']=[{**body['tasks'][0],'annotations':[{'result':[{'from_name':'entities','to_name':'text','type':'labels','value':{'start':7,'end':10,'labels':['BANK']}}]}]}]
        with self.assertRaisesRegex(ValueError,'boundaries split'):self.runtime.train(body)
        self.assertEqual(self.runtime.setup(body),version)

    def test_no_training_on_predictions_or_cancelled_annotations(self):
        body=self.body();body['annotations']=[{**body['tasks'][0],'predictions':[{'result':[]}], 'annotations':[{'was_cancelled':True,'result':[]}]}]
        with self.assertRaisesRegex(ValueError,'No usable'):self.runtime.train(body)

    def test_http_contract_passes_request_configuration(self):
        client=TestClient(create_app(self.output.name,'cpu')); body=self.body()
        setup={'project':body['project'],'schema':body['label_config'],'extra_params':body['params']}
        self.assertEqual(client.post('/setup',json=setup).status_code,200)
        result=client.post('/predict',json=body)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['results'][0]['model_version'],client.post('/setup',json=setup).json()['model_version'])
        body['params']={**body['params'],'login':'','password':'','hostname':'http://local','context':None}
        self.assertEqual(client.post('/predict',json=body).json()['results'][0]['model_version'],client.post('/setup',json=setup).json()['model_version'])

    def test_candidate_requires_explicit_publish_and_cannot_cross_project(self):
        body=self.body();base=self.runtime.setup(body)['model_version']
        body['params'].update(publish=False,validation_split=.2,epochs=1,job_id='job1')
        body['annotations']=[{**body['tasks'][0],'annotations':[{'result':[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['yes']}}]}]}]
        # One sample may hash into validation; use no split for this publication contract.
        body['params']['validation_split']=0
        trained=self.runtime.train(body)
        self.assertEqual(self.runtime.setup(body)['model_version'],base)
        with self.assertRaisesRegex(ValueError,'another project'):
            self.runtime.promote({**body,'project':'other','version':trained['model_version']})
        self.runtime.promote({**body,'version':trained['model_version']})
        self.assertEqual(self.runtime.setup(body)['model_version'],trained['model_version'])
        body['params'].update(epochs=2,job_id='job2')
        self.assertEqual(self.runtime.setup(body)['model_version'],trained['model_version'])

    def test_tokenizer_is_reused_across_requests_and_projects(self):
        from transformers import AutoTokenizer
        body=self.body()
        with patch.object(AutoTokenizer,'from_pretrained',wraps=AutoTokenizer.from_pretrained) as load:
            self.runtime.setup(body);self.runtime.predict(body);self.runtime.setup({**body,'project':'another'})
            self.assertEqual(load.call_count,1)

    def test_snapshot_can_seed_another_project_without_mutating_source(self):
        import copy,hashlib
        body=self.body();body['annotations']=[{**body['tasks'][0],'annotations':[{'result':[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['yes']}}]}]}]
        first=self.runtime.train(body);snapshot=self.runtime.snapshot(body)
        weights=Path(snapshot['base_model'])/'model.safetensors';before=hashlib.sha256(weights.read_bytes()).hexdigest()
        target=copy.deepcopy(body);target['project']='independent';target['params']['base_model']=snapshot['base_model']
        second=self.runtime.train(target)
        self.assertNotEqual(first['model_version'],second['model_version']);self.assertEqual(self.runtime.setup(body)['model_version'],first['model_version'])
        self.assertEqual(hashlib.sha256(weights.read_bytes()).hexdigest(),before)


if __name__ == '__main__':unittest.main()
