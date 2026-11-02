"""Model reuse/removal and project isolation, using the disposable CI database."""
import os,sys,unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django;django.setup()
from rest_framework.test import APIRequestFactory,force_authenticate
from projects.models import Project
from users.models import User
from tasks.models import Task,Prediction
from ml.models import MLBackend
from ml.workflow_models import AnnotationProvider,LearningRun,LearningItem
from ml.workflow import ProjectWorkflowAPI
from ml.api_connector import MLApi

XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="yes"/><Choice value="no"/></Choices></View>'

class ModelManagementTests(unittest.TestCase):
 def test_generated_signing_key_survives_a_fresh_process_environment(self):
  import tempfile
  from core.utils.secret_key import generate_secret_key_if_missing
  with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ):
   os.environ.pop('SECRET_KEY',None)
   with patch('core.utils.secret_key.get_random_secret_key',return_value='#synthetic-signing-key'):
    first=generate_secret_key_if_missing(directory)
   os.environ.pop('SECRET_KEY',None)
   self.assertEqual(generate_secret_key_if_missing(directory),first)
 def setUp(self):
  self.user=User.objects.get(email='ci@example.invalid');self.factory=APIRequestFactory()
  self.projects=[Project.objects.create(title=f'Synthetic model project {i}',created_by=self.user,organization=self.user.active_organization,label_config=XML) for i in range(2)]
  self.addCleanup(self.cleanup)
 def cleanup(self):
  for p in self.projects:
   LearningRun.objects.filter(project=p).delete();AnnotationProvider.objects.filter(project=p).delete();p.ml_backends.all().delete();p.tasks.all().delete();p.delete()
 def api(self,p,body,method='post'):
  request=getattr(self.factory,method)('/test',body,format='json');force_authenticate(request,user=self.user)
  return ProjectWorkflowAPI.as_view()(request,pk=p.pk)
 def cloud(self):
  p=self.projects[0]
  r=self.api(p,{'action':'provider','name':'Reusable DeepSeek','kind':'deepseek','model':'deepseek-flash','api_key':'synthetic-secret','options':{'prompt':'Source instructions'}})
  self.assertEqual(r.status_code,200,r.data);return AnnotationProvider.objects.get(pk=r.data['id'])
 def test_reuse_copies_secret_without_exposing_it_and_removal_is_independent(self):
  source=self.cloud();target=self.projects[1]
  catalog=self.api(target,{'catalog':1},'get');self.assertEqual(catalog.status_code,200)
  self.assertIn(source.pk,[x['id'] for x in catalog.data['models']]);self.assertNotIn('synthetic-secret',str(catalog.data))
  copied=self.api(target,{'action':'reuse_model','source_type':'provider','source':source.pk,'name':'Copied model','copy_instructions':False})
  self.assertEqual(copied.status_code,200,copied.data);self.assertTrue(copied.data['key_configured']);self.assertEqual(copied.data['options']['prompt'],'')
  clone=AnnotationProvider.objects.get(pk=copied.data['id']);self.assertEqual(clone.encrypted_key,source.encrypted_key)
  task=Task.objects.create(project=target,data={'content':'synthetic'})
  prediction=Prediction.objects.create(project=target,task=task,result=[],model_version='old')
  run=LearningRun.objects.create(project=target,provider=clone,kind='predict',status='completed',created_by=self.user)
  LearningItem.objects.create(run=run,task=task,prediction=prediction,state='succeeded')
  removed=self.api(target,{'action':'remove_model','provider':clone.pk});self.assertEqual(removed.status_code,200)
  clone.refresh_from_db();source.refresh_from_db();self.assertFalse(clone.encrypted_key);self.assertTrue(source.encrypted_key)
  self.assertTrue(Prediction.objects.filter(pk=prediction.pk).exists());self.assertTrue(LearningRun.objects.filter(pk=run.pk).exists())
  self.assertNotIn(clone.pk,[p['id'] for p in self.api(target,{},'get').data['providers']])
 def test_removal_blocks_running_jobs_and_cross_project_provider_ids(self):
  source=self.cloud();p=self.projects[0]
  LearningRun.objects.create(project=p,provider=source,kind='predict',created_by=self.user)
  self.assertEqual(self.api(p,{'action':'remove_model','provider':source.pk}).status_code,400)
  self.assertEqual(self.api(self.projects[1],{'action':'remove_model','provider':source.pk}).status_code,404)
  source.refresh_from_db();self.assertTrue(source.encrypted_key)
 def test_local_snapshot_reuse_creates_new_backend_and_retains_source(self):
  source=MLBackend.objects.create(project=self.projects[0],title='Source encoder',url='http://synthetic.invalid',base_model='foundation',model_version='hf-source-published',extra_params={'initialize_head':True})
  with patch('ml.serializers.validate_upload_url'),patch('ml.serializers.MLBackendSerializer._validate_healthcheck'),patch('ml.serializers.MLBackendSerializer._validate_setup'),patch.object(MLBackend,'update_state'),patch.object(MLApi,'_request',return_value=SimpleNamespace(is_error=False,response={'base_model':'/immutable/source-checkpoint'})):
   response=self.api(self.projects[1],{'action':'reuse_model','source_type':'connection','source':source.pk,'name':'Independent copy','copy_weights':True})
  self.assertEqual(response.status_code,200,response.data)
  target=MLBackend.objects.get(pk=response.data['backend']);self.assertNotEqual(target.pk,source.pk);self.assertEqual(target.project_id,self.projects[1].pk)
  self.assertEqual(target.base_model,'/immutable/source-checkpoint');self.assertFalse(target.extra_params['initialize_head']);self.assertFalse(target.active_learning)
  source.refresh_from_db();self.assertEqual(source.base_model,'foundation');self.assertEqual(source.model_version,'hf-source-published')
  self.assertEqual(self.api(self.projects[1],{'action':'remove_model','provider':response.data['id']}).status_code,200)
  self.assertFalse(self.projects[1].get_ml_backends().filter(pk=target.pk).exists());self.assertTrue(self.projects[0].get_ml_backends().filter(pk=source.pk).exists())

if __name__=='__main__':unittest.main()
