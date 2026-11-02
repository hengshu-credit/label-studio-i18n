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
from ml.workflow import ProjectWorkflowAPI,process_workflow_jobs,validate_result,analyze_task,cloud_predict,fingerprint,classification_metrics,cloud_version,ProviderUnavailable
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

 def test_deepseek_preserves_non_thinking_configuration(self):
  saved=self.api({'action':'provider','name':'DeepSeek','kind':'deepseek','model':'deepseek-flash','api_key':'synthetic-key','options':{'thinking':'disabled','temperature':0,'max_tokens':800}})
  self.assertEqual(saved.status_code,200,saved.data);p=AnnotationProvider.objects.get(pk=saved.data['id'])
  self.assertEqual(p.options['thinking'],'disabled');self.assertEqual(p.options['temperature'],0)
  response=Mock(status_code=200);response.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'result':result('yes'),'uncertain':False,'reason':''})}}]}
  with patch('ml.workflow.requests.Session') as session:
   session.return_value.__enter__.return_value.post.return_value=response
   cloud_predict(p,self.task,XML);payload=session.return_value.__enter__.return_value.post.call_args.kwargs['json']
   self.assertEqual(payload['thinking'],{'type':'disabled'});self.assertEqual(payload['temperature'],0);self.assertEqual(payload['max_tokens'],800);self.assertEqual(payload['response_format'],{'type':'json_object'})
   p.options['thinking']='enabled';p.save();cloud_predict(p,self.task,XML)
   self.assertNotIn('temperature',session.return_value.__enter__.return_value.post.call_args.kwargs['json'])
  invalid=self.api({'action':'provider','id':p.pk,'name':p.name,'kind':p.kind,'model':p.model,'options':{'temperature':3}})
  self.assertEqual(invalid.status_code,400)
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
  legacy=ReviewCase.objects.get(task=self.task);legacy.evidence.pop('prediction_hash');legacy.save(update_fields=['evidence'])
  Prediction.objects.create(project=self.project,task=self.task,result=result('no'),score=.2,model_version='test')
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
  self.assertEqual(run.items.get(task=t2).attempts,2)
  self.assertEqual(self.api({'action':'retry','run':run.pk}).status_code,400)
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

 def test_provider_edit_preserves_concurrent_usage_and_revocation(self):
  p=self.provider();original=AnnotationProvider.save
  def racing_save(instance,*args,**kwargs):
   if instance.pk==p.pk:AnnotationProvider.objects.filter(pk=p.pk).update(requests_used=9)
   return original(instance,*args,**kwargs)
  with patch.object(AnnotationProvider,'save',racing_save):
   response=self.api({'action':'provider','id':p.pk,'name':'Changed','kind':'openai','model':'synthetic','clear_key':True})
  self.assertEqual(response.status_code,200);self.assertEqual(response.data['requests_used'],9)
  with patch('ml.workflow.requests.Session') as session:
   with self.assertRaisesRegex(ValueError,'configuration changed'):cloud_predict(p,self.task,XML)
   session.assert_not_called()

 def test_local_model_connection_survives_blocked_optional_webhook(self):
  from core.utils.exceptions import SsrfBlockedUrlError
  with patch('ml.models.WebhookSerializer.is_valid',side_effect=SsrfBlockedUrlError('blocked')):
   backend=MLBackend.objects.create(project=self.project,title='Local inference',url='http://127.0.0.1:9091')
  self.assertTrue(MLBackend.objects.filter(pk=backend.pk).exists())

 def test_local_chat_endpoint_is_explicitly_scoped(self):
  from django.test import override_settings
  body={'action':'provider','name':'Local chat','kind':'local_chat','model':'local-model','endpoint':'http://host.docker.internal:11434/v1/chat/completions'}
  with override_settings(ML_BLOCK_LOCAL_IP=False):
   saved=self.api(body);self.assertEqual(saved.status_code,200,saved.data)
   self.assertFalse(saved.data['key_configured'])
   self.assertEqual(self.api({**body,'endpoint':'http://10.0.0.1/admin'}).status_code,400)
  with override_settings(ML_BLOCK_LOCAL_IP=True):self.assertEqual(self.api(body).status_code,400)

 def test_holdout_metrics_and_full_prediction_export(self):
  self.task.data['_workflow_split']='holdout';self.task.save()
  Annotation.objects.create(project=self.project,task=self.task,completed_by=self.user,result=result('yes'))
  run=LearningRun.objects.create(project=self.project,created_by=self.user,kind='predict',status='completed',config={'label_config':XML})
  prediction=Prediction.objects.create(task=self.task,project=self.project,result=result('yes'),model_version='version')
  LearningItem.objects.create(run=run,task=self.task,state='succeeded',prediction=prediction,snapshot={'data_hash':fingerprint(self.task.data)})
  metric=classification_metrics(run)[0];self.assertEqual(metric['split'],'holdout');self.assertEqual(metric['samples'],1);self.assertEqual(metric['accuracy'],1)
  exported=self.api({'run_results':run.pk},'get');self.assertEqual(exported.status_code,200)
  self.assertEqual(json.loads(exported.content)['items'][0]['prediction']['result'],result('yes'))
  selected=self.api({'action':'use_predictions','run':run.pk});self.assertEqual(selected.status_code,200)
  self.project.refresh_from_db();self.assertEqual(self.project.model_version,'version');self.assertTrue(self.project.show_collab_predictions)
  self.task.data['content']='Changed input';self.task.save()
  self.assertEqual(classification_metrics(run),[])

 def test_prediction_and_analysis_commit_together(self):
  p=self.provider();r=self.api({'action':'start','kind':'predict','provider':p.pk,'scope':'all'})
  with patch('ml.workflow.cloud_predict',return_value={'result':result('yes'),'score':.9,'model_version':'test'}),patch('ml.workflow.analyze_task',side_effect=ValueError('synthetic analysis failure')):
   process_workflow_jobs()
  self.assertFalse(Prediction.objects.filter(project=self.project).exists())
  item=LearningItem.objects.get(run_id=r.data['id']);self.assertEqual(item.state,'failed');self.assertIsNone(item.prediction_id)

 def test_budget_update_resumes_a_stopped_job_without_repeating_success(self):
  p=self.provider();Task.objects.create(project=self.project,data={'content':'second'})
  r=self.api({'action':'start','kind':'predict','provider':p.pk,'scope':'all'})
  with patch('ml.workflow.cloud_predict',side_effect=ProviderUnavailable('quota reached')) as call:
   process_workflow_jobs();self.assertEqual(call.call_count,1)
  run=LearningRun.objects.get(pk=r.data['id']);self.assertEqual(run.items.filter(state='pending').count(),1)
  changed=self.api({'action':'provider','id':p.pk,'name':p.name,'kind':p.kind,'model':p.model,'options':{**p.options,'request_limit':2000}});self.assertEqual(changed.status_code,200)
  self.assertEqual(self.api({'action':'retry','run':run.pk}).status_code,200)
  with patch('ml.workflow.cloud_predict',return_value={'result':result('yes'),'score':None,'model_version':'test'}):process_workflow_jobs()
  run.refresh_from_db();self.assertEqual(run.status,'completed');self.assertEqual(run.items.filter(state='succeeded').count(),2)

 def test_missing_prediction_scope_and_secret_validation(self):
  p=self.provider();second=Task.objects.create(project=self.project,data={'content':'second'})
  Prediction.objects.create(project=self.project,task=self.task,result=result('yes'),model_version=cloud_version(p,XML))
  r=self.api({'action':'start','kind':'predict','provider':p.pk,'scope':'missing'});self.assertEqual(r.status_code,201)
  self.assertEqual(list(LearningRun.objects.get(pk=r.data['id']).items.values_list('task_id',flat=True)),[second.pk])
  bad=self.api({'action':'provider','name':'bad','kind':'openai','model':'synthetic','api_key':'private\nsecret'})
  self.assertEqual(bad.status_code,400);self.assertNotIn('private',json.dumps(bad.data,default=str))
  self.assertEqual(self.api({'action':'provider','name':'bad','kind':'compatible','model':'synthetic','endpoint':'https://example.com/chat/completions?api_key=secret'}).status_code,400)

 def test_extra_model_parameters_accept_form_json_but_reject_arrays(self):
  from ml.serializers import MLBackendSerializer
  from rest_framework.exceptions import ValidationError
  serializer=MLBackendSerializer()
  self.assertEqual(serializer.validate_extra_params('{"epochs":2}'),{'epochs':2})
  self.assertEqual(serializer.validate_extra_params(''),{})
  with self.assertRaises(ValidationError):serializer.validate_extra_params(['invalid'])

if __name__=='__main__':unittest.main()
