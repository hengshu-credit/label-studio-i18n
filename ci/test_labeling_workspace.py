"""Real database checks for explicit human confirmation and reserved allocation."""
import os,sys,unittest,datetime
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
  self.project.tasks.all().delete();self.project.delete();self.other.delete()
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

if __name__=='__main__':unittest.main()
