import copy,json,hashlib,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from engine import Store,encode
from skill_runner import SkillStore,resources,sample,preview,validate_output,model_info,RESOURCE,input_material
from demo_model import fixture_output,MODEL

class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.skills=SkillStore(self.store)
        self.config={'llm_url':'http://127.0.0.1:9999/v1/chat/completions','llm_model':MODEL,'llm_key':'SECRET-NOT-IN-RECORD'}
    def test_pins_and_prompt(self):
        p=preview(sample(),self.config)
        for content in resources()['documents'].values():self.assertIn(content,p['messages'][0]['content'])
        self.assertNotIn(self.config['llm_key'],encode(p))
        root=Path(self.tmp.name)/'resources';shutil.copytree(RESOURCE,root)
        (root/'SKILL.md').write_text('corrupted', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'SKILL_UNAVAILABLE'):resources(root)
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'));manifest['files']['SKILL.md']=hashlib.sha256(b'corrupted').hexdigest()
        (root/'manifest.json').write_text(encode(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'SKILL_UNAVAILABLE'):resources(root)
    def test_inputs_and_unknowns(self):
        for change in [{'text':''},{'text':'x'*65537},{'data_kind':'wrong'},{'as_of':'2026-01-01'},{'text':'bad\ufffd'}]:
            p=sample();p.update(change)
            with self.assertRaises(ValueError):preview(p,{})
        p=sample();p.update(data_kind='real',as_of='2026-01-01',source='',cutoff='')
        self.assertEqual(len(preview(p,{})['warnings']),3)
        self.assertFalse(preview(sample(),{})['model']['configured'])
    def test_input_materials_and_complete_example(self):
        data=sample('complete');p=preview(data,{})
        self.assertEqual(data['data_kind'],'synthetic');self.assertFalse(p['warnings'])
        output=validate_output(encode(fixture_output('complete')),p['lines'])
        self.assertEqual(len(output['layers']),5)
        self.assertTrue(all(x['references'] for x in output['layers']))
        self.assertIn('非AI推理',output['data_completeness'])
        blank=sample('template');self.assertEqual(blank['data_kind'],'unknown')
        self.assertTrue(any('占位符' in w for w in preview(blank,{})['warnings']))
        blank['text']='问题：板块持续性？\n事实：未知';self.assertFalse(any('占位符' in w for w in preview(blank,{})['warnings']))
        self.assertIn('在哪里找',input_material('guide'))
        for bad in ['../config.json','guide','/etc/passwd']:
            with self.assertRaises(ValueError):sample(bad)
        with self.assertRaises(ValueError):input_material('../config.json')

    def test_model_address(self):
        for url in ['http://remote.example/api','https://user:pass@host/api','https://host/api?key=secret','invalid']:
            with self.assertRaises(ValueError):model_info({'llm_url':url,'llm_model':'test'})
    def test_output_contract(self):
        lines=preview(sample(),{})['lines'];good=fixture_output()
        self.assertEqual(len(validate_output(encode(good),lines)['layers']),5)
        for change in [lambda x:x['layers'].pop(),lambda x:x['layers'][0].update(status='买入'),lambda x:x['layers'][1]['references'][0].update(id='S999'),lambda x:x['layers'][1]['references'][0].update(quote='编造行情'),lambda x:x['layers'][0].update(support=['未引用事实']),lambda x:x.update(disclaimer='')]:
            bad=copy.deepcopy(good);change(bad)
            with self.assertRaises(ValueError):validate_output(encode(bad),lines)
    def test_consent_config_idempotence_and_redaction(self):
        draft=self.skills.draft(sample(),self.config)
        with self.assertRaises(ValueError):self.skills.start(draft['id'],self.config,False)
        with self.assertRaises(ValueError):self.skills.start(draft['id'],dict(self.config,llm_model='changed'),True)
        item,started=self.skills.start(draft['id'],self.config,True);self.assertTrue(started)
        with patch('skill_runner.post_json',return_value={'choices':[{'message':{'content':encode(fixture_output())}}]}) as call:
            result=self.skills.execute(item,self.config)
            self.assertEqual(result['state'],'COMPLETED');self.assertEqual(call.call_count,1)
        self.assertFalse(self.skills.start(draft['id'],self.config,True)[1])
        self.assertNotIn(self.config['llm_key'],encode(self.skills.get(draft['id'])))
        self.assertNotEqual(self.skills.draft(sample(),self.config)['id'],draft['id'])
    def test_failures_raw_output_recovery_and_backup(self):
        for response,status in [({'choices':[{'message':{'content':'bad JSON'}}]},'OUTPUT_INVALID'),({},'FAILED')]:
            draft=self.skills.draft(sample(),self.config);item,_=self.skills.start(draft['id'],self.config,True)
            with patch('skill_runner.post_json',return_value=response):result=self.skills.execute(item,self.config)
            self.assertEqual(result['state'],status)
            if status=='OUTPUT_INVALID':self.assertEqual(result['raw_output'],'bad JSON')
        draft=self.skills.draft(sample(),self.config);item,_=self.skills.start(draft['id'],self.config,True)
        with patch('skill_runner.post_json',side_effect=TimeoutError('SECRET')):result=self.skills.execute(item,self.config)
        self.assertNotIn('SECRET',encode(result));self.assertEqual(result['state'],'FAILED')
        draft=self.skills.draft(sample(),self.config);self.skills.start(draft['id'],self.config,True)
        self.skills.recover();self.assertEqual(self.skills.get(draft['id'])['state'],'INTERRUPTED')
        restored=Path(self.tmp.name)/'restored';restored.mkdir();shutil.copy2(self.store.backup(),restored/'workflow.sqlite3')
        self.assertEqual(SkillStore(Store(restored)).get(draft['id'])['state'],'INTERRUPTED')
    def test_csv_not_assumed_real(self):
        p={'plan':{'name':'x','version':'1','definition':'x','horizons':[1],'benchmark':'000300.SH','source':'csv'},'as_of':'2026-01-05','candidates':[]}
        self.store.create_batch(p);self.assertEqual(self.store.batches()[0]['data_kind'],'unknown')
        p.update(data_kind='synthetic');self.store.create_batch(p);self.assertEqual(self.store.batches()[0]['data_kind'],'synthetic')

if __name__=='__main__':unittest.main()
