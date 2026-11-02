"""Real database checks for explicit human confirmation and reserved allocation."""
import os,sys,unittest,datetime,json
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django;django.setup()
from django.db import close_old_connections
from django.utils import timezone
from rest_framework.test import APIRequestFactory,force_authenticate
from projects.labeling import LabelingWorkspaceAPI,input_hash
from projects.models import Project
from projects.labeling_models import LabelingAssignment,LabelingWorkspaceDraft,LabelingQueueSettings
from organizations.models import OrganizationMember
from tasks.models import Task,Prediction,Annotation,TaskLock
from users.models import User

XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text" required="true"><Choice value="yes"/><Choice value="no"/></Choices></View>'
def result(value):return [{'from_name':'category','to_name':'text','type':'choices','value':{'choices':[value]}}]

class LabelingTests(unittest.TestCase):
 def setUp(self):
  self.user=User.objects.get(email='ci@example.invalid');self.other=User.objects.create(email='queue-worker@example.invalid',username='queue-worker',active_organization=self.user.active_organization)
  OrganizationMember.objects.create(user=self.other,organization=self.user.active_organization)
  self.project=Project.objects.create(title='Queue synthetic',created_by=self.user,organization=self.user.active_organization,label_config=XML)
  self.tasks=[Task.objects.create(project=self.project,data={'content':f'Synthetic {i}'}) for i in range(3)]
  self.prediction=Prediction.objects.create(project=self.project,task=self.tasks[0],model_version='synthetic-model',result=result('yes'),score=.7)
  self.project.model_version='synthetic-model';self.project.save(update_fields=['model_version'])
  self.addCleanup(self.cleanup)
 def cleanup(self):
  from ml.workflow_models import LearningRun,AnnotationProvider
  LearningRun.objects.filter(project=self.project).delete();AnnotationProvider.objects.filter(project=self.project).delete()
  self.project.tasks.all().delete();self.project.delete();self.other.delete()
 def sample_provider(self):
  from ml.workflow_models import AnnotationProvider
  return AnnotationProvider.objects.create(project=self.project,name='Sample model',kind='openai',model='synthetic',endpoint='https://api.openai.com/v1/chat/completions',encrypted_key='synthetic-encrypted-secret')
 def test_sample_columns_default_order_and_visibility(self):
  from data_manager.functions import get_all_columns
  summary=self.project.summary;summary.all_data_columns={'content':3,'sample_id':3,'internal_metadata':3};summary.save(update_fields=['all_data_columns'])
  columns=get_all_columns(self.project)['columns'];index={c['id']:c for c in columns};visible=[]
  for c in columns:
   if c.get('parent'):continue
   for child in ([index[key] for key in c['children']] if 'children' in c else [c]):
    if child.get('visibility_defaults',{}).get('explore'):visible.append(child['id'])
  self.assertEqual(visible,['id','sample_id','content','predictions_model_versions','predictions_results','predictions_score','total_annotations','cancelled_annotations','total_predictions','annotators','completed_at'])
  self.assertFalse(index['internal_metadata']['visibility_defaults']['explore'])
  self.assertTrue(index['content']['visibility_defaults']['labeling'])
 def test_default_column_migration_preserves_filters_and_custom_views(self):
  from importlib import import_module
  update=import_module('data_manager.migrations.0022_sample_column_defaults').update_default
  original={'title':'Default','hiddenColumns':{'explore':['tasks:predictions_score'],'labeling':['tasks:id']},'columnOrder':{},'filters':{'conjunction':'and','items':[]},'ordering':['-tasks:id']}
  updated=update(original,{'content':'Text'},{'content':3,'sample_id':3,'metadata':3})
  self.assertEqual(updated['filters'],original['filters']);self.assertEqual(updated['ordering'],original['ordering']);self.assertEqual(updated['hiddenColumns']['labeling'],['tasks:id'])
  self.assertNotIn('tasks:predictions_score',updated['hiddenColumns']['explore']);self.assertIn('tasks:data.metadata',updated['hiddenColumns']['explore'])
  self.assertEqual(update(updated,{},{}),updated)
  custom={**original,'title':'My difficult samples'};self.assertEqual(update(custom,{},{}),custom)
 def test_single_sample_prediction_is_deduplicated_and_requires_human_acceptance(self):
  from ml.workflow import process_workflow_jobs
  from ml.workflow_models import LearningRun
  provider=self.sample_provider();task=self.tasks[0]
  options=self.call({'prediction_options':1,'task':task.pk},method='get');self.assertEqual(options.status_code,200);self.assertNotIn('synthetic-encrypted-secret',json.dumps(options.data))
  body={'action':'preannotate','task':task.pk,'model':f'provider:{provider.pk}','input_hash':input_hash(task)}
  created=self.call(body);self.assertEqual(created.status_code,201,created.data);duplicate=self.call(body);self.assertEqual(duplicate.status_code,200);self.assertEqual(created.data['id'],duplicate.data['id'])
  self.assertEqual(LearningRun.objects.filter(project=self.project).count(),1)
  with patch('ml.workflow.cloud_predict',return_value={'result':result('no'),'score':None,'model_version':'synthetic-sample','uncertain':False}) as predict:process_workflow_jobs();self.assertEqual(predict.call_count,1)
  ready=self.call({'prediction_run':created.data['id'],'task':task.pk},method='get');self.assertEqual(ready.data['status'],'completed');self.assertEqual(ready.data['prediction']['result'],result('no'))
  self.assertFalse(Annotation.objects.filter(project=self.project).exists());self.call({'action':'reserve','task':task.pk})
  accepted=self.call({'action':'accept','task':task.pk,'prediction':ready.data['prediction']['id'],'input_hash':input_hash(task)});self.assertEqual(accepted.status_code,200,accepted.data)
  self.assertEqual(Annotation.objects.get(pk=accepted.data['annotation']['id']).parent_prediction_id,ready.data['prediction']['id'])
 def test_sample_prediction_rejects_wrong_project_stale_input_and_unassigned_annotator(self):
  provider=self.sample_provider();task=self.tasks[0];body={'action':'preannotate','task':task.pk,'model':f'provider:{provider.pk}'}
  self.assertEqual(self.call({**body,'expected_data':{'content':'wrong'}}).status_code,409)
  self.assertEqual(self.call({**body,'label_config':'<View/>'}).status_code,409)
  self.assertEqual(self.call({**body,'model':'provider:9999999'}).status_code,404)
  LabelingQueueSettings.objects.create(project=self.project,mode='manual')
  with patch.object(self.other,'has_perm',side_effect=lambda p:p!='projects.change'):
   self.assertEqual(self.call(body,user=self.other).status_code,403)
 def test_stale_sample_prediction_is_not_delivered_after_text_changes(self):
  from ml.workflow import process_workflow_jobs
  provider=self.sample_provider();task=self.tasks[0]
  run=self.call({'action':'preannotate','task':task.pk,'model':f'provider:{provider.pk}'})
  with patch('ml.workflow.cloud_predict',return_value={'result':result('yes'),'score':None,'model_version':'synthetic-sample','uncertain':False}):process_workflow_jobs()
  Task.objects.filter(pk=task.pk).update(data={'content':'Edited while model was running'})
  response=self.call({'prediction_run':run.data['id'],'task':task.pk},method='get');self.assertEqual(response.data['status'],'failed');self.assertIsNone(response.data['prediction'])
 def call(self,body=None,user=None,method='post',project=None):
  r=getattr(APIRequestFactory(),method)('/synthetic',body or {},format='json');force_authenticate(r,user=user or self.user)
  return LabelingWorkspaceAPI.as_view()(r,pk=(project or self.project).pk)
 def claim(self,user=None):
  r=self.call({'action':'next'},user=user);self.assertEqual(r.status_code,200,r.data);return r.data['task']
 def test_prediction_requires_acceptance_and_double_submit_is_idempotent(self):
  task=self.claim();self.assertFalse(Annotation.objects.filter(project=self.project).exists());self.assertEqual(task['predictions'][0]['id'],self.prediction.pk)
  body={'action':'accept','task':task['id'],'prediction':self.prediction.pk,'input_hash':task['input_hash']}
  first=self.call(body);self.assertEqual(first.status_code,200,first.data)
  annotation=Annotation.objects.get(pk=first.data['annotation']['id']);self.assertEqual(annotation.completed_by_id,self.user.pk);self.assertEqual(annotation.parent_prediction_id,self.prediction.pk);self.assertFalse(annotation.ground_truth)
  second=self.call(body);self.assertEqual(second.status_code,200,second.data);self.assertTrue(second.data['reused']);self.assertEqual(Annotation.objects.filter(task=self.tasks[0]).count(),1)
  self.assertTrue(Prediction.objects.filter(pk=self.prediction.pk).exists());self.assertFalse(self.tasks[0].locks.exists());self.assertNotEqual(self.claim()['id'],task['id'])
 def test_draft_is_not_an_annotation_and_resumes_after_refresh(self):
  task=self.claim();body={'action':'draft','task':task['id'],'input_hash':task['input_hash'],'result':result('no'),'prediction':self.prediction.pk}
  first=self.call(body);self.assertEqual(first.status_code,200,first.data);self.assertEqual(Annotation.objects.filter(project=self.project).count(),0)
  resumed=self.claim();self.assertEqual(resumed['id'],task['id']);self.assertEqual(resumed['draft']['result'],result('no'))
  self.assertEqual(self.call(body).status_code,409)
  submitted=self.call({**body,'action':'submit'});self.assertEqual(submitted.status_code,200,submitted.data);self.assertFalse(LabelingWorkspaceDraft.objects.filter(task_id=task['id']).exists())
  self.assertEqual(Annotation.objects.get(pk=submitted.data['annotation']['id']).parent_prediction_id,self.prediction.pk)
 def test_expired_reservation_and_cross_task_prediction_are_rejected(self):
  task=self.claim();TaskLock.objects.filter(task_id=task['id']).update(expire_at=timezone.now()-datetime.timedelta(seconds=1))
  body={'action':'submit','task':task['id'],'input_hash':task['input_hash'],'result':result('yes')};self.assertEqual(self.call(body).status_code,409)
  task=self.claim();other_pred=Prediction.objects.create(project=self.project,task=self.tasks[1],result=result('no'))
  self.assertEqual(self.call({**body,'action':'accept','prediction':other_pred.pk}).status_code,404)
 def test_expired_lease_preserves_private_draft_without_submitting(self):
  task=self.claim();TaskLock.objects.filter(task_id=task['id']).delete()
  draft=self.call({'action':'draft','task':task['id'],'input_hash':task['input_hash'],'result':result('no')})
  self.assertEqual(draft.status_code,200,draft.data);self.assertTrue(LabelingWorkspaceDraft.objects.filter(task_id=task['id']).exists());self.assertFalse(Annotation.objects.filter(project=self.project).exists())
 def test_no_overwrite_of_existing_human_annotation_and_optimistic_update(self):
  task=self.claim();r=self.call({'action':'submit','task':task['id'],'input_hash':task['input_hash'],'result':result('no')});self.assertEqual(r.status_code,200,r.data);a=r.data['annotation']
  self.assertEqual(self.call({'action':'accept','task':task['id'],'prediction':self.prediction.pk,'input_hash':task['input_hash']}).status_code,409)
  self.call({'action':'reserve','task':task['id']})
  body={'action':'submit','task':task['id'],'input_hash':task['input_hash'],'result':result('yes'),'annotation':a['id'],'annotation_updated_at':a['updated_at']}
  updated=self.call(body);self.assertEqual(updated.status_code,200,updated.data);self.call({'action':'reserve','task':task['id']});self.assertEqual(self.call(body).status_code,409);self.assertEqual(Annotation.objects.filter(task_id=task['id']).count(),1)
 def test_changed_input_and_invalid_label_do_not_create_annotation(self):
  task=self.claim();body={'action':'submit','task':task['id'],'input_hash':task['input_hash'],'result':result('invalid-label')}
  self.assertEqual(self.call(body).status_code,400)
  Task.objects.filter(pk=task['id']).update(data={'content':'Changed input'})
  self.assertEqual(self.call({**body,'result':result('yes')}).status_code,409);self.assertFalse(Annotation.objects.filter(project=self.project).exists())
 def test_required_fields_and_malformed_ids_are_validation_errors(self):
  task=self.claim()
  for value in [[],[1]]:
   self.assertEqual(self.call({'action':'submit','task':task['id'],'input_hash':task['input_hash'],'result':value}).status_code,400)
  self.assertEqual(self.call({'action':'reserve','task':'invalid'}).status_code,400)
  self.assertEqual(self.call({'task':'invalid'},method='get').status_code,400)
 def test_manual_assignments_control_queue_and_can_be_removed(self):
  self.assertEqual(self.call({'action':'configure','mode':'manual','sampling':Project.SEQUENCE,'overlap':1,'reservation_minutes':5}).status_code,200)
  self.assertIsNone(self.claim())
  assigned=self.call({'action':'assign','tasks':[self.tasks[1].pk],'users':[self.other.pk]});self.assertEqual(assigned.status_code,200,assigned.data)
  self.assertIsNone(self.claim());self.assertEqual(self.claim(self.other)['id'],self.tasks[1].pk)
  self.assertEqual(self.call({'action':'reserve','task':self.tasks[1].pk}).status_code,403)
  self.assertEqual(self.call({'action':'unassign','tasks':[self.tasks[1].pk],'users':[self.other.pk]}).status_code,409)
  self.call({'action':'release','task':self.tasks[1].pk},user=self.other)
  self.assertEqual(self.call({'action':'unassign','tasks':[self.tasks[1].pk],'users':[self.other.pk]}).status_code,200)
 def test_uncertainty_respects_configured_model_and_null_falls_back(self):
  Prediction.objects.create(project=self.project,task=self.tasks[1],model_version='synthetic-model',result=result('no'),score=.1)
  Prediction.objects.create(project=self.project,task=self.tasks[2],model_version='other-model',result=result('yes'),score=.01)
  self.project.sampling=Project.UNCERTAINTY;self.project.save(update_fields=['sampling'])
  self.assertEqual(self.claim()['id'],self.tasks[1].pk)
 def test_manual_completion_requires_all_assigned_people(self):
  LabelingQueueSettings.objects.create(project=self.project,mode='manual')
  assigned=self.call({'action':'assign','tasks':[self.tasks[0].pk],'users':[self.user.pk,self.other.pk]});self.assertEqual(assigned.status_code,200,assigned.data)
  first=self.claim();second=self.claim(self.other);self.assertEqual(first['id'],second['id'])
  for user,task,complete in [(self.user,first,False),(self.other,second,True)]:
   response=self.call({'action':'submit','task':task['id'],'input_hash':task['input_hash'],'result':result('yes')},user=user);self.assertEqual(response.status_code,200,response.data)
   self.tasks[0].refresh_from_db();self.assertEqual(self.tasks[0].is_labeled,complete)
 def test_config_roundtrip_skip_and_independent_claims(self):
  r=self.call({'action':'configure','mode':'automatic','sampling':Project.SEQUENCE,'overlap':1,'reservation_minutes':7,'skip_queue':Project.SkipQueue.REQUEUE_FOR_OTHERS});self.assertEqual(r.status_code,200,r.data)
  config=self.call(method='get');self.assertEqual(config.data['reservation_minutes'],7)
  task=self.claim();self.assertNotEqual(self.claim(self.other)['id'],task['id'])
  self.assertEqual(self.call({'action':'configure','mode':'manual'}).status_code,409)
  skipped=self.call({'action':'skip','task':task['id'],'input_hash':task['input_hash']});self.assertEqual(skipped.status_code,200,skipped.data);self.assertNotEqual(self.claim()['id'],task['id'])
 def test_two_simultaneous_claims_do_not_reserve_same_task(self):
  barrier=Barrier(2)
  def claim(user):
   close_old_connections()
   try:barrier.wait();return self.call({'action':'next'},user=user)
   finally:close_old_connections()
  with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(claim,[self.user,self.other]))
  for r in responses:self.assertEqual(r.status_code,200,r.data)
  self.assertNotEqual(responses[0].data['task']['id'],responses[1].data['task']['id'])
 def test_anonymous_background_response_cannot_overwrite_login_cookie(self):
  from django.conf import settings
  from django.contrib.auth.models import AnonymousUser
  from django.contrib.sessions.middleware import SessionMiddleware
  from django.http import HttpResponse
  from core.middleware import SetSessionUIDMiddleware
  from organizations.middleware import DummyGetSessionMiddleware
  from importlib import import_module
  for path in ['/__lsa/','/heidi-tips','/health']:
   for with_uid in [False,True]:
    session=import_module(settings.SESSION_ENGINE).SessionStore();session['organization_pk']=self.user.active_organization_id
    if with_uid:session['uid']='old-anonymous-session'
    session.save();request=APIRequestFactory().get(path);request.COOKIES[settings.SESSION_COOKIE_NAME]=session.session_key
    middleware=SessionMiddleware(lambda r:HttpResponse());middleware.process_request(request);request.user=AnonymousUser()
    SetSessionUIDMiddleware(lambda r:HttpResponse()).process_request(request)
    response=DummyGetSessionMiddleware(lambda r:HttpResponse())(request)
    response=middleware.process_response(request,response)
    self.assertFalse(settings.SESSION_COOKIE_NAME in response.cookies, "Anonymous responses must not send a session cookie")

if __name__=='__main__':unittest.main()
