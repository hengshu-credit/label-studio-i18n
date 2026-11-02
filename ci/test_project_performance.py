"""Exact counts and lightweight project loading on the disposable CI database."""
import os,sys,unittest,re
from unittest.mock import patch
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django;django.setup()
from rest_framework.test import APIRequestFactory,force_authenticate
from rest_framework.request import Request
from django.db import connection
from django.test.utils import CaptureQueriesContext
from projects.models import Project
from projects.serializers import ProjectSerializer
from projects.api import ProjectAPI
from data_manager.api import ProjectStateAPI,TaskListAPI
from tasks.models import Task,Annotation
from users.models import User

XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="yes"/></Choices></View>'
RESULT=[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['yes']}}]

class ProjectPerformanceTests(unittest.TestCase):
 def setUp(self):
  self.user=User.objects.get(email='ci@example.invalid')
  self.project=Project.objects.create(title='Performance synthetic',organization=self.user.active_organization,created_by=self.user,label_config=XML)
  self.tasks=[Task.objects.create(project=self.project,data={'content':f'Sample {i}'}) for i in range(5)]
  for i,cancelled in [(0,False),(0,False),(2,True),(3,False)]:Annotation.objects.create(project=self.project,task=self.tasks[i],completed_by=self.user,was_cancelled=cancelled,result=RESULT)
  Task.objects.filter(project=self.project).update(is_labeled=False)
  Task.objects.filter(pk__in=[t.pk for t in self.tasks[1:4]]).update(is_labeled=True)
  self.addCleanup(self.cleanup)
 def cleanup(self):
  self.project.tasks.all().delete();self.project.delete()
 def request(self,view,params=None,detail=False):
  request=APIRequestFactory().get('/api/projects/',params or {})
  force_authenticate(request,self.user)
  response=view.as_view()(request,**({'pk':self.project.pk} if detail else {}));response.render();return response
 def test_project_totals_do_not_group_over_full_task_rows(self):
  queryset=Project.objects.with_counts().filter(pk=self.project.pk)
  self.assertNotIn('LEFT OUTER JOIN "task"',str(queryset.query))
  self.assertNotIn('GROUP BY',str(queryset.query))
  p=queryset.get();self.assertEqual((p.task_number,p.finished_task_number,p.total_annotations_number,p.skipped_annotations_number),(5,3,3,1))
 def test_queue_totals_preserve_overlap_skips_and_unfinished_tasks(self):
  request=Request(APIRequestFactory().get('/'));request.user=self.user
  for project in [self.project,Project.objects.with_counts().get(pk=self.project.pk)]:
   serializer=ProjectSerializer(project,context={'request':request})
   self.assertEqual(serializer.get_queue_total(project),4)
   self.assertEqual(serializer.get_queue_done(project),3)
   project.skip_queue=Project.SkipQueue.REQUEUE_FOR_ME
   self.assertEqual(serializer.get_queue_done(project),2)
 def test_lightweight_explorer_does_not_compute_labeling_queues(self):
  with patch.object(ProjectSerializer,'get_queue_total',side_effect=AssertionError('Queue scan')),patch.object(ProjectSerializer,'get_queue_done',side_effect=AssertionError('Queue scan')):
   response=self.request(ProjectStateAPI,{'project':self.project.pk,'lightweight':'true'})
  self.assertEqual(response.status_code,200,response.data)
  self.assertEqual(response.data['task_count'],5);self.assertEqual(response.data['annotation_count'],4)
  self.assertNotIn('queue_total',response.data);self.assertNotIn('queue_done',response.data)
  full=self.request(ProjectStateAPI,{'project':self.project.pk})
  self.assertEqual(full.data['queue_total'],4);self.assertEqual(full.data['queue_done'],3)
 def test_project_bootstrap_can_omit_queues_without_losing_configuration(self):
  with patch.object(ProjectSerializer,'get_queue_total',side_effect=AssertionError('Queue scan')),patch.object(ProjectSerializer,'get_queue_done',side_effect=AssertionError('Queue scan')):
   response=self.request(ProjectAPI,{'omit':'queue_total,queue_done'},detail=True)
  self.assertEqual(response.status_code,200,response.data);self.assertEqual(response.data['id'],self.project.pk)
  self.assertEqual(response.data['label_config'],XML);self.assertEqual(response.data['task_number'],5)
 def test_initial_metadata_never_counts_the_task_table(self):
  for view,params,detail in [(ProjectAPI,{'metadata_only':'true'},True),(ProjectStateAPI,{'project':self.project.pk,'metadata_only':'true'},False)]:
   with CaptureQueriesContext(connection) as queries:
    response=self.request(view,params,detail)
   self.assertEqual(response.status_code,200,response.data);self.assertTrue(response.data['has_tasks'])
   self.assertNotIn('task_number',response.data);self.assertNotIn('task_count',response.data)
   self.assertFalse(any('COUNT(' in q['sql'].upper() and re.search(r'FROM\s+[`\"]?task(?:[`\"]|\s)',q['sql'],re.I) for q in queries))
 def test_sample_pagination_keeps_exact_filtered_totals(self):
  import json
  response=self.request(TaskListAPI,{'project':self.project.pk,'page_size':2,'query':json.dumps({'filters':{'conjunction':'and','items':[{'filter':'filter:tasks:id','operator':'greater','type':'Number','value':self.tasks[1].pk}]}})})
  self.assertEqual(response.status_code,200,response.data);self.assertEqual(response.data['total'],3);self.assertEqual(len(response.data['tasks']),2)
  self.assertTrue(all(t['id']>self.tasks[1].pk for t in response.data['tasks']))

if __name__=='__main__':unittest.main()
