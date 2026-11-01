"""Synthetic CI fixture: never submits an annotation or uses real user data."""
import os,sys
sys.path.insert(0,'/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django
django.setup()
from users.models import User
from projects.models import Project
from tasks.models import Task,Prediction
user=User.objects.get(email='ci@example.invalid')
config='<View><Text name="sms" value="$content"/><Choices name="category" toName="sms"><Choice value="Customer category"/></Choices><Labels name="entity" toName="sms"><Label value="Customer bank"/></Labels></View>'
Project.validate_label_config(config)
project=Project.objects.create(title='Localization fixture',created_by=user,organization=user.active_organization,
                              label_config=config,show_collab_predictions=True,model_version='synthetic-ci')
task=Task.objects.create(project=project,data={'content':'Acme Bank transferred 2500 THB.'})
Prediction.objects.create(task=task,project=project,model_version='synthetic-ci',result=[
 {'from_name':'category','to_name':'sms','type':'choices','value':{'choices':['Customer category']}},
 {'id':'bank','from_name':'entity','to_name':'sms','type':'labels','value':{'start':0,'end':9,'text':'Acme Bank','labels':['Customer bank']}}])
assert project.id==1 and task.id==1
from ml.models import MLBackend,create_ml_webhook
from django.db.models.signals import post_save
disconnected=post_save.disconnect(create_ml_webhook,sender=MLBackend)
try:
 for title,version in [('CI Classification','ci-first'),('CI Entities','ci-second')]:
  MLBackend.objects.create(project=project,title=title,url='http://localhost:9',model_version=version,state='CO')
finally:
 if disconnected:post_save.connect(create_ml_webhook,sender=MLBackend)
print('Created synthetic localization fixture')
