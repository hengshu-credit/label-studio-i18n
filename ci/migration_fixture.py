"""Small full-schema SQLite fixture for migration rehearsal. Never run on live data."""
import os,sys,hashlib,json
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django;django.setup()
from django.db import connection,models
from users.models import User
from organizations.models import Organization
from projects.models import Project
from tasks.models import Task,Annotation,Prediction,AnnotationDraft
from projects.labeling_models import LabelingAssignment,LabelingWorkspaceDraft
from projects.labeling import input_hash
from labels_manager.models import Label
from data_manager.models import View

assert connection.vendor=='sqlite' and str(connection.settings_dict['NAME']).endswith('migration-source.sqlite3')
assert not User.objects.exists() and not Task.objects.exists()
user=User.objects.create_superuser(email='migration@example.invalid',password='synthetic-migration-only')
org=Organization.create_organization(created_by=user,title='Synthetic migration');org.add_user(user);user.active_organization=org;user.save(update_fields=['active_organization'])
xml='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="positive"/></Choices></View>'
project=Project.objects.create(title='Migration 中文 😀',created_by=user,organization=org,label_config=xml)
first=Task.objects.create(project=project,data={'content':'ภาษาไทย 😀\n\t\u0000','sample_id':'Café','value':1.0,'flag':True,'nested':{'b':[None,1],'a':'中文'}},meta=None)
second=Task.objects.create(project=project,data={'content':'Second sample','value':-0.0,'flag':False})
Task.objects.filter(pk=second.pk).update(meta=models.Value(None,output_field=models.JSONField()))
result=[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['positive']}}]
prediction=Prediction.objects.create(project=project,task=first,model_version='模型,version',result=result,score=.75)
annotation=Annotation.objects.create(project=project,task=first,completed_by=user,result=result,parent_prediction=prediction)
AnnotationDraft.objects.create(task=second,user=user,result=result)
LabelingAssignment.objects.create(task=second,user=user)
LabelingWorkspaceDraft.objects.create(task=second,user=user,result=result,input_hash=input_hash(second))
Label.objects.create(title='Long label '+('泰😀'*500),value={'label':'中文'},created_by=user,organization=org)
View.objects.create(project=project,user=user,data={'title':'Default','hiddenColumns':{'explore':[],'labeling':[]},'filters':{'conjunction':'and','items':[]}})
high=Task.objects.create(pk=900,project=project,data={'content':'Deleted high-water mark'});high.delete()
connection.close()
with open(connection.settings_dict['NAME'],'rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
print('FIXTURE:'+json.dumps({'sha256':digest,'tasks':2,'next_task_minimum':901}))
