import os,sys,unittest,json
from unittest.mock import patch,Mock
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django
django.setup()
from rest_framework.test import APIRequestFactory,force_authenticate
from users.models import User
from projects.models import Project
from tasks.models import Task,Annotation,Prediction
from ml.workflow import ProjectWorkflowAPI,process_workflow_jobs,validate_result,analyze_task,cloud_predict,fingerprint
from ml.workflow_models import AnnotationProvider,LearningRun,LearningItem,ReviewCase
from ml.models import MLBackend

XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="yes"/><Choice value="no"/></Choices></View>'
def result(label):return [{'from_name':'category','to_name':'text','type':'choices','value':{'choices':[label]}}]


class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.user=User.objects.get(email='ci@example.invalid');self.factory=APIRequestFactory()
  self.project=Project.objects.create(title='Workflow synthetic',created_by=self.user,organization=self.user.active_organization,label_config=XML)
  self.task=Task.objects.create(project=self.project,data={'content':'synthetic text'})
  self.addCleanup(self.cleanup)
 def cleanup(self):
  LearningRun.objects.filter(project=self.project).delete();AnnotationProvider.objects.filter(project=self.project).delete()
  self.project.ml_backends.all().delete();self.project.tasks.all().delete();self.project.delete()
 def api(self,body,method='post',user=True):
  r=getattr(self.factory,method)('/test',body,format='json')
  if user:force_authenticate(r,user=self.user)
  return ProjectWorkflowAPI.as_view()(r,pk=self.project.pk)
 def provider(self):
  r=self.api({'action':'provider','name':'Synthetic OpenAI','kind':'openai','model':'synthetic','api_key':'test-private-secret'})
  self.assertEqual(r.status_code,200,r.data);return AnnotationProvider.objects.get(pk=r.data['id'])
 def test_encrypted_keys_and_response_and_key_change(self):
  p=self.provider();self.assertNotIn('test-private-secret',p.encrypted_key)
  response=self.api({},'get');self.assertNotIn('test-private-secret',json.dumps(response.data,default=str));self.assertNotIn('encrypted_key',json.dumps(response.data,default=str))
  self.assertNotEqual(self.api({},'get',user=False).status_code,200)
  self.assertEqual(self.api({'action':'provider','id':p.pk,'name':'Changed','kind':'deepseek','model':'synthetic'}).status_code,200)
  p.refresh_from_db();self.assertFalse(p.encrypted_key)
 def test_cloud_contract_and_invalid_labels(self):
  p=self.provider();response=Mock(status_code=200)
  response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'result':result('yes'),'uncertain':True,'reason':'ambiguous'})}}]}
  with patch('ml.workflow.requests.Session') as session:
   session.return_value.__enter__.return_value.post.return_value=response
   prediction=cloud_predict(p,self.task,XML)
   self.assertIsNone(prediction['score']);self.assertTrue(prediction['uncertain'])
   payload=session.return_value.__enter__.return_value.post.call_args.kwargs['json']
   self.assertEqual(payload['response_format']['type'],'json_schema')
  with self.assertRaises(ValueError):validate_result(result('unknown'),self.task,XML)
 def test_review_requires_annotation_and_excludes_training_until_accepted(self):
  Prediction.objects.create(project=self.project,task=self.task,result=result('no'),score=.2,model_version='test')
  analyze_task(self.task)
  self.assertEqual(self.api({'action':'review','task':self.task.pk,'status':'resolved'}).status_code,400)
  Annotation.objects.create(project=self.project,task=self.task,completed_by=self.user,result=result('yes'))
  backend=MLBackend.objects.create(project=self.project,title='Train',url='http://synthetic.invalid',base_model='synthetic')
  body={'action':'start','kind':'train','backend':backend.pk,'scope':'all','limit':10}
  self.assertEqual(self.api(body).status_code,400)
  self.assertEqual(self.api({'action':'review','task':self.task.pk,'status':'resolved','note':'confirmed'}).status_code,200)
  analyze_task(self.task)
  self.assertEqual(ReviewCase.objects.get(task=self.task).status,'resolved')
  created=self.api(body);self.assertEqual(created.status_code,201,created.data)
  self.assertEqual(LearningRun.objects.get(pk=created.data['id']).items.count(),1)
 def test_prediction_failure_retry_does_not_duplicate_successes(self):
  p=self.provider();t2=Task.objects.create(project=self.project,data={'content':'second'})
  r=self.api({'action':'start','kind':'predict','provider':p.pk,'scope':'all','limit':10});self.assertEqual(r.status_code,201)
  def prediction(provider,task,xml):
   if task.pk==t2.pk:raise ValueError('synthetic error')
   return {'result':result('yes'),'score':None,'model_version':'test','uncertain':False}
  with patch('ml.workflow.cloud_predict',side_effect=prediction):process_workflow_jobs()
  self.assertEqual(Prediction.objects.filter(project=self.project).count(),1)
  run=LearningRun.objects.get(pk=r.data['id']);self.assertEqual(run.status,'failed')
  self.assertEqual(self.api({'action':'retry','run':run.pk}).status_code,200)
  with patch('ml.workflow.cloud_predict',return_value={'result':result('yes'),'score':None,'model_version':'test'}):process_workflow_jobs()
  self.assertEqual(Prediction.objects.filter(project=self.project).count(),2)
  run.refresh_from_db();self.assertEqual(run.status,'completed')
 def test_task_scope_rejects_cross_project_and_job_cancel(self):
  p=self.provider();r=self.api({'action':'start','kind':'predict','provider':p.pk,'task_ids':[999999999]})
  self.assertEqual(r.status_code,400);self.assertEqual(LearningRun.objects.filter(project=self.project).count(),0)
  r=self.api({'action':'start','kind':'predict','provider':p.pk,'scope':'all'})
  self.assertEqual(self.api({'action':'cancel','run':r.data['id']}).status_code,200);process_workflow_jobs()
  self.assertEqual(LearningRun.objects.get(pk=r.data['id']).status,'cancelled')
 def test_holdout_and_changed_review_are_not_trainable(self):
  self.task.data['_workflow_split']='holdout';self.task.save()
  Annotation.objects.create(project=self.project,task=self.task,completed_by=self.user,result=result('yes'))
  backend=MLBackend.objects.create(project=self.project,title='Train',url='http://synthetic.invalid',base_model='synthetic')
  r=self.api({'action':'start','kind':'train','backend':backend.pk,'scope':'all'})
  self.assertEqual(r.status_code,400)

if __name__=='__main__':unittest.main()
