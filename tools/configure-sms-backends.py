"""Run inside Label Studio to connect the user's local SMS inference service.

Example: python /tmp/configure-sms-backends.py --project 4 \
    --service-url http://host.docker.internal:9091
This preserves label configs, task data, annotations, drafts and the selected model.
"""

import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--project", type=int, action="append", required=True)
parser.add_argument("--service-url", required=True)
args = parser.parse_args()

sys.path.insert(0, "/label-studio/label_studio")
import django

django.setup()
from django.db.models.signals import post_save
from ml.models import MLBackend, create_ml_webhook
from projects.models import Project

report = []
# The service is inference-only; do not create annotation/training webhooks.
# Disconnect only in this admin process, leaving server SSRF protection intact.
disconnected = post_save.disconnect(create_ml_webhook, sender=MLBackend)
try:
    for project_id in args.project:
        project = Project.objects.get(pk=project_id)
        original_selection = project.model_version
        for suffix, title in [
            ("", "本地规则 + Qwen3 + GLiNER"),
            ("/classification", "短信分类（规则 + Qwen3）"),
            ("/ner", "金融实体（GLiNER）"),
        ]:
            backend, created = MLBackend.objects.get_or_create(
                project=project,
                url=args.service_url.rstrip("/") + suffix,
                defaults={
                    "title": title,
                    "is_interactive": False,
                    "timeout": 180,
                    "extra_params": {"ner_threshold": 0.4},
                },
            )
            # New upstream skips batch prelabeling for interactive-only backends.
            if backend.is_interactive:
                backend.is_interactive = False
                backend.save(update_fields=["is_interactive"])
            backend.update_state()
            if backend.not_ready:
                raise RuntimeError(f"Model connection failed: {backend.title}")
            report.append(
                {
                    "project": project_id,
                    "id": backend.pk,
                    "title": backend.title,
                    "state": backend.state,
                    "interactive": backend.is_interactive,
                    "created": created,
                }
            )
        project.refresh_from_db()
        assert (
            project.model_version == original_selection
        ), "Selected model unexpectedly changed"
finally:
    if disconnected:
        post_save.connect(create_ml_webhook, sender=MLBackend)
print(json.dumps(report, ensure_ascii=False))
