"""Run in the disposable CI database. Network is mocked; database writes and queues are real."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, '/label-studio/label_studio')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.label_studio')
import django
django.setup()
from ml.active_learning import run_round, process_backends
from ml.models import MLBackend
from ml.api_connector import MLApi
from projects.models import Project
from tasks.models import Task, Annotation, Prediction
from users.models import User


class ActiveLearningTests(unittest.TestCase):
    def setUp(self):
        self.user=User.objects.get(email='ci@example.invalid')
        self.project=Project.objects.create(title='Synthetic active learning',created_by=self.user,
            organization=self.user.active_organization, label_config='<View><Text name="text" value="$content"/><Choices name="category" toName="text"><Choice value="yes"/><Choice value="no"/></Choices></View>')
        self.addCleanup(self.cleanup_project)
        self.backend=MLBackend.objects.create(project=self.project,title='Synthetic HF',url='http://synthetic.invalid',
            base_model='/synthetic/model',active_learning=True,extra_params={'min_training_samples':1,'train_every':1,'candidate_pool_size':2})
        self.project.model_version=self.backend.title;self.project.save(update_fields=['model_version'])
        self.tasks=[Task.objects.create(project=self.project,data={'content':f'synthetic {i}'}) for i in range(4)]
        self.version='base';self.calls=[]
        self.mock=patch.object(MLApi,'_request',autospec=True,side_effect=self.respond);self.mock.start();self.addCleanup(self.mock.stop)

    def cleanup_project(self):
        # Let upstream on_commit task-state callbacks run while the parent exists.
        self.project.ml_backends.all().delete()
        self.project.tasks.all().delete()
        self.project.delete()

    def respond(self, api, endpoint, request=None, **kwargs):
        self.calls.append((endpoint,request))
        if endpoint=='health':data={'status':'UP'}
        elif endpoint=='setup':data={'model_version':self.version}
        elif endpoint=='train':
            self.version='trained-'+str(len(self.calls));data={'model_version':self.version}
        elif endpoint=='predict':
            self.assertEqual(request['params']['base_model'],'/synthetic/model')
            data={'results':[{'model_version':self.version,'score':0.2+i/10,'result':[]} for i,_ in enumerate(request['tasks'])]}
        else:raise AssertionError(endpoint)
        return SimpleNamespace(is_error=False,response=data,error_message=None)

    def annotate(self):
        annotation=Annotation.objects.create(project=self.project,task=self.tasks[0],completed_by=self.user,
            result=[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['yes']}}])
        from ml.workflow import fingerprint
        from ml.workflow_models import ReviewCase
        case=ReviewCase.objects.filter(task=self.tasks[0]).first()
        if case:
            case.status='resolved';case.evidence.update(accepted_hash=fingerprint(annotation.result),accepted_data=fingerprint(self.tasks[0].data));case.save()

    def test_persistent_training_refresh_and_no_duplicate_round(self):
        run_round(self.backend)
        self.assertEqual(Prediction.objects.filter(project=self.project).count(),2)
        self.annotate();self.backend.refresh_from_db();run_round(self.backend)
        requests=[body for endpoint,body in self.calls if endpoint=='train']
        self.assertEqual(len(requests),1)
        self.assertEqual(len(requests[0]['annotations']),1)
        self.assertNotIn('predictions',requests[0]['annotations'][0])
        self.backend.refresh_from_db();run_round(self.backend)
        self.assertEqual(len([c for c in self.calls if c[0]=='train']),1)
        self.assertEqual(self.backend.learning_state['trained']['count'],1)

    def test_sampling_resolves_live_backend_title_to_current_version(self):
        from projects.functions.next_task import _try_uncertainty_sampling
        self.backend.model_version='current';self.backend.save(update_fields=['model_version'])
        Prediction.objects.create(project=self.project,task=self.tasks[0],model_version='old',score=0.01,result=[])
        Prediction.objects.create(project=self.project,task=self.tasks[0],model_version='current',score=0.9,result=[])
        Prediction.objects.create(project=self.project,task=self.tasks[1],model_version='current',score=0.2,result=[])
        Prediction.objects.create(project=self.project,task=self.tasks[2],model_version='current',score=None,result=[])
        with patch('projects.functions.next_task._get_first_unlocked',side_effect=lambda qs,user:qs.first()), \
             patch('projects.functions.next_task._get_random_unlocked',side_effect=lambda qs,user,**kw:qs.first()):
            chosen=_try_uncertainty_sampling(self.project.tasks.all(),self.project,[],self.user,self.project.tasks.all())
        self.assertEqual(chosen.pk,self.tasks[1].pk)

    def test_training_failure_records_error_without_advancing_checkpoint(self):
        self.annotate()
        self.mock.stop()
        with patch.object(MLApi,'_request',return_value=SimpleNamespace(is_error=True,response={},error_message='synthetic failure')):
            process_backends()
        self.backend.refresh_from_db()
        self.assertNotIn('trained',self.backend.learning_state)
        self.assertIn('synthetic failure',self.backend.learning_state['error'])

    def test_configuration_survives_status_refresh(self):
        MLBackend.objects.filter(pk=self.backend.pk).update(base_model='/new/model')
        self.backend.update_state()
        self.backend.refresh_from_db()
        self.assertEqual(self.backend.base_model,'/new/model')

    def test_manual_request_runs_when_automatic_training_is_disabled(self):
        self.annotate()
        self.backend.active_learning=False
        self.backend.save(update_fields=['active_learning'])
        self.backend.train()
        process_backends()
        self.backend.refresh_from_db()
        self.assertFalse(self.backend.learning_state['requested'])
        self.assertIn('trained',self.backend.learning_state)
        self.assertEqual(len([c for c in self.calls if c[0]=='train']),1)

    def test_automatic_training_excludes_unreviewed_cases_and_holdout(self):
        run_round(self.backend)
        Annotation.objects.create(project=self.project,task=self.tasks[0],completed_by=self.user,
            result=[{'from_name':'category','to_name':'text','type':'choices','value':{'choices':['yes']}}])
        self.backend.refresh_from_db();run_round(self.backend)
        self.assertFalse(any(c[0]=='train' for c in self.calls))
        self.tasks[0].data['_workflow_split']='holdout';self.tasks[0].save()
        self.annotate()
        self.backend.refresh_from_db();run_round(self.backend)
        self.assertFalse(any(c[0]=='train' for c in self.calls))


if __name__=='__main__':unittest.main()
