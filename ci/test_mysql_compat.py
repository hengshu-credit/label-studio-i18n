"""MySQL-specific import, JSON, uniqueness, and index regressions. Synthetic data only."""
import os,sys,unittest,json,importlib.util,datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django;django.setup()
from django.db import connection,transaction,IntegrityError,close_old_connections
from rest_framework.test import APIClient
from projects.models import Project
from tasks.models import Task,Annotation,Prediction
from users.models import User
from labels_manager.models import Label
from core.utils.db import bulk_create_with_ids

XML='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="positive"/></Choices></View>'
RESULT=[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['positive']}}]

@unittest.skipUnless(connection.vendor=='mysql','MySQL-specific contract')
class MySQLCompatibilityTests(unittest.TestCase):
 def setUp(self):
  self.assertTrue(connection.settings_dict['NAME'].endswith(('_validation','_ci')),'Use the synthetic validation database')
  self.user=User.objects.get(email='ci@example.invalid');self.client=APIClient();self.client.force_authenticate(self.user)
  self.project=Project.objects.create(title='MySQL compatibility fixture',created_by=self.user,organization=self.user.active_organization,label_config=XML)
  self.labels=[];self.addCleanup(self.cleanup)
 def cleanup(self):
  self.project.tasks.all().delete();self.project.delete();Label.objects.filter(pk__in=self.labels).delete()
 def test_bulk_import_returns_real_ids_and_links_children(self):
  body=[{'data':{'content':f'ภาษาไทย 😀 {i}'},'annotations':[{'result':RESULT}],'predictions':[{'model_version':'mysql-test','result':RESULT}]} for i in range(7)]
  response=self.client.post(f'/api/projects/{self.project.pk}/import?return_task_ids=true',body,format='json')
  self.assertEqual(response.status_code,201,response.data);ids=response.data['task_ids'];self.assertEqual(len(set(ids)),7)
  self.assertTrue(all(isinstance(pk,int) for pk in ids))
  self.assertEqual(list(Task.objects.filter(pk__in=ids).order_by('id').values_list('data__content',flat=True)),[r['data']['content'] for r in body])
  self.assertEqual(Annotation.objects.filter(task_id__in=ids).count(),7);self.assertEqual(Prediction.objects.filter(task_id__in=ids).count(),7)
 def test_concurrent_bulk_ids_do_not_overlap(self):
  barrier=Barrier(2)
  def insert(batch):
   close_old_connections()
   try:
    barrier.wait();objects=bulk_create_with_ids(Task,[Task(project_id=self.project.pk,data={'batch':batch,'content':str(i)}) for i in range(5)],batch_size=2)
    return [obj.pk for obj in objects]
   finally:close_old_connections()
  with ThreadPoolExecutor(max_workers=2) as pool:groups=list(pool.map(insert,['a','b']))
  self.assertEqual(len(set(groups[0]+groups[1])),10)
  for group,batch in zip(groups,['a','b']):self.assertEqual(set(Task.objects.filter(pk__in=group).values_list('data__batch',flat=True)),{batch})
 def test_long_unicode_label_is_unique_without_truncation(self):
  title='MySQL唯一😀'+('泰😀'*500)
  label=Label.objects.create(title=title,value={},created_by=self.user,organization=self.user.active_organization);self.labels.append(label.pk)
  self.assertEqual(Label.objects.get(pk=label.pk).title,title)
  with self.assertRaises(IntegrityError),transaction.atomic():Label.objects.create(title=title,value={},created_by=self.user,organization=self.user.active_organization)
  other=Label.objects.create(title=title.lower(),value={},created_by=self.user,organization=self.user.active_organization);self.labels.append(other.pk)
  self.assertNotEqual(label.pk,other.pk)
 def test_json_aggregates_and_text_filters(self):
  task=Task.objects.create(project=self.project,data={'content':'ธนาคาร Acme 😀','amount':12})
  for _ in range(2):Annotation.objects.create(project=self.project,task=task,completed_by=self.user,result=RESULT)
  for version in ['本地,模型v1','v2']:Prediction.objects.create(project=self.project,task=task,model_version=version,result=RESULT,score=.8)
  response=self.client.get('/api/tasks/',{'project':self.project.pk,'fields':'all','page_size':10})
  self.assertEqual(response.status_code,200,response.data);row=response.data['tasks'][0]
  self.assertIn('本地,模型v1',row['predictions_model_versions']);self.assertEqual(len(row['annotators']),1)
  self.assertIn('positive',row['predictions_results'])
  for field,kind,value in [('data.content','String','ธนาคาร'),('annotations_results','String','positive')]:
   response=self.client.get('/api/tasks/',{'project':self.project.pk,'query':json.dumps({'filters':{'conjunction':'and','items':[{'filter':'filter:tasks:'+field,'operator':'contains','type':kind,'value':value}]}})})
   self.assertEqual(response.status_code,200,response.data);self.assertEqual(response.data['total'],1)
 def test_query_indexes_exist(self):
  with connection.cursor() as cursor:
   for table,name in [('task','task_project_totals_idx'),('task','task_proj_updated_id_idx'),('task_completion','anno_user_task_cancel_idx'),('prediction','pred_proj_model_task_idx'),('fsm_taskstate','task_project_latest_idx')]:
    self.assertIn(name,connection.introspection.get_constraints(cursor,table))
 def test_migration_comparison_preserves_json_types_and_timezone(self):
  candidates=[Path('/tmp/migrate-sqlite-to-mysql.py'),Path('/migration-tools/migrate-sqlite-to-mysql.py')]
  file=next(path for path in candidates if path.exists());spec=importlib.util.spec_from_file_location('mysql_transfer',file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  self.assertNotEqual(module.comparable(None,'json'),module.comparable('null','json'))
  self.assertNotEqual(module.comparable('{"value":true}','json'),module.comparable('{"value":1}','json'))
  self.assertNotEqual(module.comparable('{"value":1.0}','json'),module.comparable('{"value":1}','json'))
  self.assertEqual(module.comparable('{"b":"😀","a":1}','json'),module.comparable('{"a":1,"b":"😀"}','json'))
  self.assertEqual(module.converted('2026-11-02T08:30:00.123456+08:00','datetime(6)'),datetime.datetime(2026,11,2,0,30,0,123456))

if __name__=='__main__':unittest.main()
