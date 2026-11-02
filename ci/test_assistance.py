"""Contract/security regressions. Run only in the disposable synthetic CI instance."""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, "/label-studio/label_studio")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
import django

django.setup()
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from projects.models import Project
from tasks.models import Task
from tasks.api import TaskAPI
from tasks import translation as tr
from users.models import User

# Independently generated ACS3 fixture for fixed UTF-8 text, timestamp and nonce.
ALIYUN_SIGNATURE_FIXTURE = (
    "ACS3-HMAC-SHA256 Credential=test-id,SignedHeaders=content-type;host;x-acs-action;"
    "x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version,"
    "Signature=d996be7e106516e15cca4341f63ee42e7052f869b86e47d81ea4a8fc329f74ae"
)


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = override_settings(
            BASE_DATA_DIR=self.directory.name, SECRET_KEY="synthetic-test-only"
        )
        self.settings.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.settings.disable)
        self.owner = User.objects.get(email="ci@example.invalid")
        self.org = self.owner.active_organization_id
        self.factory = APIRequestFactory()

    def api(self, view, method, data=None, user=None, **kwargs):
        request = getattr(self.factory, method)("/test/", data=data, format="json")
        if user is not False:
            force_authenticate(request, user=user or self.owner)
        return view.as_view()(request, **kwargs)

    def enable(self, engine="azure", **extra):
        tr.save_config(self.org, engine, {"key": "synthetic-key-not-real", **extra})

    def test_defaults_configuration_encryption_and_removal(self):
        self.assertEqual(tr.capabilities(self.org)["default_engine"], "azure")
        self.enable("google")
        self.assertEqual(tr.capabilities(self.org)["default_engine"], "google")
        response = self.api(
            tr.TranslationOptionsAPI,
            "post",
            {"engine": "azure", "key": "synthetic-secret", "region": "eastasia"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["default_engine"], "azure")
        self.assertNotIn("synthetic-secret", json.dumps(response.data))
        con = tr._cache()
        stored = con.execute("SELECT value FROM provider_config").fetchone()[0]
        con.close()
        self.assertNotIn(b"synthetic-secret", stored)
        self.assertEqual(tr.read_config(self.org)["azure"]["key"], "synthetic-secret")
        tr.save_config(self.org, "azure", {"key": "", "monthly_limit": 15})
        self.assertEqual(tr.read_config(self.org)["azure"]["key"], "synthetic-secret")
        tr.save_config(self.org, "azure", {"clear": True})
        self.assertNotIn("azure", tr.read_config(self.org))
        self.assertEqual(tr.read_config(self.org + 10000), {})

    def test_authentication_and_owner_only_key_configuration(self):
        self.assertIn(
            self.api(tr.TranslationOptionsAPI, "get", user=False).status_code,
            (401, 403),
        )
        member = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            id=-100,
            active_organization=self.owner.active_organization,
        )
        response = self.api(
            tr.TranslationOptionsAPI,
            "post",
            {"engine": "azure", "key": "x"},
            user=member,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(tr.read_config(self.org), {})

    def test_task_uses_original_text_and_preserves_annotations(self):
        self.enable()
        task = Task.objects.get(pk=1)
        before = (task.data, task.annotations.count(), task.drafts.count())
        with patch.object(
            tr,
            "call_provider",
            return_value={"translation": "合成译文", "detected_source": "en"},
        ) as call:
            result = self.api(
                tr.TaskTranslationAPI,
                "post",
                {"engine": "azure", "source": "auto", "target": "zh"},
                pk=task.pk,
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(call.call_args.args[2], task.data["content"])
            self.assertEqual(call.call_args.args[3:], ("auto", "zh"))
        task.refresh_from_db()
        self.assertEqual(
            (task.data, task.annotations.count(), task.drafts.count()), before
        )
        bad = self.api(
            tr.TaskTranslationAPI, "post", {"field": "private-other-field"}, pk=task.pk
        )
        self.assertEqual(bad.status_code, 400)

    def test_cross_organization_task_is_hidden(self):
        stranger = SimpleNamespace(is_authenticated=True, active_organization=None)
        result = self.api(tr.TaskTranslationAPI, "post", {}, user=stranger, pk=1)
        self.assertEqual(result.status_code, 404)

    def test_cache_is_scoped_and_local_cap_counts_unicode(self):
        self.enable(monthly_limit=4)
        with patch.object(
            tr,
            "call_provider",
            return_value={"translation": "你好", "detected_source": "th"},
        ) as call:
            first = tr.translate_text("ก😀", "azure", "auto", "zh", self.org)
            second = tr.translate_text("ก😀", "azure", "auto", "zh", self.org)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(call.call_count, 1)
            with self.assertRaisesRegex(tr.TranslationFailure, "monthly"):
                tr.translate_text("ab", "azure", "auto", "zh", self.org)
            tr.translate_text("c", "azure", "auto", "zh", self.org)
            with self.assertRaisesRegex(tr.TranslationFailure, "not configured"):
                tr.translate_text("ก😀", "azure", "auto", "zh", self.org + 10000)

    def test_failures_are_sanitized_and_release_pending_reservation(self):
        self.enable()
        with patch.object(tr.requests, "Session") as session:
            session.return_value.post.side_effect = tr.requests.RequestException(
                "secret-provider-response"
            )
            for _ in range(2):
                with self.assertRaises(tr.TranslationFailure) as error:
                    tr.translate_text("synthetic", "azure", "auto", "zh", self.org)
                self.assertNotIn("secret-provider-response", str(error.exception))
                self.assertEqual(error.exception.status, 502)

    def test_official_provider_request_and_detection_contracts(self):
        cases = [
            (
                "azure",
                {"key": "fake", "region": "eastasia"},
                [
                    {
                        "detectedLanguage": {"language": "th"},
                        "translations": [{"text": "测试"}],
                    }
                ],
                "https://api.cognitive.microsofttranslator.com/translate",
            ),
            (
                "google",
                {"key": "fake"},
                {
                    "data": {
                        "translations": [
                            {
                                "translatedText": "A &amp; B",
                                "detectedSourceLanguage": "th",
                            }
                        ]
                    }
                },
                "https://translation.googleapis.com/language/translate/v2",
            ),
            (
                "baidu",
                {"app_id": "fake-app", "secret": "fake-secret"},
                {"from": "th", "trans_result": [{"dst": "测试"}]},
                "https://fanyi-api.baidu.com/api/trans/vip/translate",
            ),
        ]
        for engine, cfg, payload, url in cases:
            with self.subTest(engine=engine), patch.object(
                tr.requests, "Session"
            ) as factory:
                response = factory.return_value.post.return_value
                response.status_code = 200
                response.json.return_value = payload
                result = tr.call_provider(engine, cfg, "ทดสอบ", "auto", "zh")
                self.assertEqual(result["detected_source"], "th")
                args, kwargs = factory.return_value.post.call_args
                self.assertEqual(args[0], url)
                self.assertFalse(kwargs["allow_redirects"])
                self.assertNotIn("fake", args[0])
                if engine == "azure":
                    self.assertNotIn("from", kwargs["params"])
                    self.assertEqual(kwargs["params"]["to"], "zh-Hans")
                    self.assertEqual(kwargs["json"], [{"Text": "ทดสอบ"}])
                elif engine == "google":
                    self.assertNotIn("source", kwargs["json"])
                    self.assertEqual(kwargs["json"]["target"], "zh-CN")
                    self.assertEqual(result["translation"], "A & B")
                    self.assertEqual(kwargs["headers"]["X-Goog-Api-Key"], "fake")
                else:
                    body = kwargs["data"]
                    self.assertEqual(body["from"], "auto")
                    expected = hashlib.md5(
                        ("fake-appทดสอบ" + body["salt"] + "fake-secret").encode()
                    ).hexdigest()
                    self.assertEqual(body["sign"], expected)

    def test_input_validation_and_explicit_language(self):
        for args in [
            ("x", "evil", "auto", "zh"),
            ("x", "baidu", "auto", "uz"),
            ("", "azure", "auto", "zh"),
        ]:
            with self.assertRaises(tr.TranslationFailure):
                tr.translate_text(*args, self.org)
        self.assertEqual(
            tr.translate_text("original", "azure", "en", "en", self.org)["translation"],
            "original",
        )
        with self.assertRaises(tr.TranslationFailure):
            tr.save_config(self.org, "azure", {"monthly_limit": 2000001})
        with patch.object(tr.requests, "Session") as session:
            session.return_value.post.return_value.status_code = 200
            session.return_value.post.return_value.json.return_value = [
                {"translations": [{"text": "test"}]}
            ]
            tr.call_provider("azure", {"key": "fake"}, "你好", "zh", "en")
            self.assertEqual(
                session.return_value.post.call_args.kwargs["params"]["from"], "zh-Hans"
            )
            tr.call_provider("azure", {"key": "fake"}, "synthetic", "tl", "zh")
            self.assertEqual(
                session.return_value.post.call_args.kwargs["params"]["from"], "fil"
            )

    def test_added_provider_credentials_are_encrypted_preserved_and_owner_only(self):
        for engine, credentials in [
            ("aliyun", {"access_key_id": "synthetic-id", "access_key_secret": "synthetic-secret"}),
            ("deepl", {"key": "synthetic-deepl:fx", "api_server": "free"}),
            ("youdao", {"app_id": "synthetic-youdao-id", "secret": "synthetic-youdao-secret"}),
        ]:
            with self.subTest(engine=engine):
                result = self.api(tr.TranslationOptionsAPI, "post", {"engine": engine, **credentials})
                self.assertEqual(result.status_code, 200)
                provider = next(p for p in result.data["providers"] if p["id"] == engine)
                self.assertTrue(provider["configured"])
                self.assertTrue({"th", "zh", "zh-TW", "tl", "uz", "pa"} <= provider["languages"].keys())
                self.assertNotIn("synthetic-", json.dumps(result.data))
                con = tr._cache()
                self.assertNotIn(b"synthetic-", con.execute("SELECT value FROM provider_config").fetchone()[0])
                con.close()
                tr.save_config(self.org, engine, {k: "" for k in credentials if k != "api_server"})
                self.assertEqual(tr.read_config(self.org)[engine], credentials)
                member = SimpleNamespace(is_authenticated=True, is_superuser=False, id=-100, active_organization=self.owner.active_organization)
                denied = self.api(tr.TranslationOptionsAPI, "post", {"engine": engine, "clear": True}, user=member)
                self.assertEqual(denied.status_code, 403)
                tr.save_config(self.org, engine, {"clear": True})
                self.assertNotIn(engine, tr.read_config(self.org))
        self.assertFalse(tr.configured("aliyun", {"access_key_id": "incomplete"}))
        with self.assertRaises(tr.TranslationFailure):
            tr.save_config(self.org, "deepl", {"api_server": "https://untrusted.invalid"})

    def test_youdao_v3_signature_boundaries_and_response_mapping(self):
        # Fixed UTF-8 fixtures follow Youdao's official Python v3 signing contract.
        cases = [
            ("12345678901234567890", "5ddad6c359efb2b624c7d598532b69b932c62c0d5c0f2d0e4cc658acaff9a017"),
            ("123456789012345678901", "20a8e3e542ee12b3af9ba94b08ecc62ddcf23e6eef12ff1bd75d134d1eaa2e35"),
            ("123456789😀abcdefghij中文", "222d2fefffba745c0b67eb38365d251e16f05d4136400d09375f7a4cff6535a6"),
        ]
        cfg = {"app_id": "test-app", "secret": "test-secret"}
        for text, signature in cases:
            with self.subTest(text=text), patch.object(tr.requests, "Session") as factory, patch.object(tr.time, "time", return_value=1700000000), patch.object(tr.secrets, "token_hex", return_value="test-nonce"):
                response = factory.return_value.post.return_value
                response.status_code = 200
                response.json.return_value = {"errorCode": "0", "translation": ["你好", "世界"], "l": "EN2zh-CHT"}
                result = tr.call_provider("youdao", cfg, text, "auto", "zh-TW")
                args, request = factory.return_value.post.call_args
                self.assertEqual(args, ("https://openapi.youdao.com/api",))
                self.assertEqual(request["data"], {
                    "q": text, "from": "auto", "to": "zh-CHT", "appKey": "test-app",
                    "salt": "test-nonce", "curtime": "1700000000", "signType": "v3",
                    "sign": signature, "strict": "true",
                })
                self.assertFalse(request["allow_redirects"])
                self.assertEqual(request["timeout"], (10, 45))
                self.assertNotIn("test-secret", json.dumps(request))
                self.assertEqual(result["translation"], "你好\n世界")
                self.assertEqual(result["detected_source"], "en")
                factory.return_value.close.assert_called_once()
        with patch.object(tr.requests, "Session") as factory:
            response = factory.return_value.post.return_value
            response.status_code = 200
            for detected, expected in [("zh-CHS", "zh"), ("zh-CHT", "zh-TW"), ("th", "th"), ("tl", "tl")]:
                response.json.return_value = {"errorCode": 0, "translation": ["test"], "l": detected + "2en"}
                result = tr.call_provider("youdao", cfg, "原文", "zh", "en")
                self.assertEqual(factory.return_value.post.call_args.kwargs["data"]["from"], "zh-CHS")
                self.assertEqual(result["detected_source"], expected)

    def test_youdao_business_errors_and_malformed_responses_are_sanitized(self):
        cfg = {"app_id": "test-app", "secret": "test-secret"}
        with patch.object(tr.requests, "Session") as factory:
            response = factory.return_value.post.return_value
            response.status_code = 200
            cases = [("401", 429), ("411", 429), ("412", 429), ("108", 503), ("110", 503),
                     ("111", 503), ("202", 503), ("203", 503), ("205", 503), ("206", 503),
                     ("207", 503), ("102", 502), ("303", 502)]
            for code, status in cases:
                with self.subTest(code=code):
                    response.json.return_value = {"errorCode": code, "errorMessage": "test-secret private text"}
                    with self.assertRaises(tr.TranslationFailure) as error:
                        tr.call_provider("youdao", cfg, "text", "auto", "zh")
                    self.assertEqual(error.exception.status, status)
                    self.assertNotIn("test-secret", str(error.exception))
                    self.assertNotIn("private text", str(error.exception))
            for payload in [None, [], {}, {"errorCode": "0", "translation": "wrong shape"},
                            {"errorCode": "0", "translation": []}, {"errorCode": "0", "translation": [None]},
                            {"errorCode": "0", "translation": [" "]}, {"errorCode": "0", "translation": ["ok"], "l": {}}]:
                response.json.return_value = payload
                with self.subTest(payload=payload), self.assertRaises(tr.TranslationFailure):
                    tr.call_provider("youdao", cfg, "text", "auto", "zh")

    def test_youdao_app_identity_cache_and_limits(self):
        self.assertFalse(tr.configured("youdao", {"app_id": "only-id"}))
        self.assertFalse(tr.configured("youdao", {"secret": "only-secret"}))
        self.assertEqual(tr.limit_for("youdao", {}), 500000)
        tr.save_config(self.org, "youdao", {"app_id": "first", "secret": "test-secret", "monthly_limit": 5})
        with patch.object(tr, "call_provider", return_value={"translation": "你好", "detected_source": "en"}) as call:
            self.assertFalse(tr.translate_text("hello", "youdao", "auto", "zh", self.org)["cached"])
            self.assertTrue(tr.translate_text("hello", "youdao", "auto", "zh", self.org)["cached"])
            self.assertEqual(call.call_count, 1)
            with self.assertRaisesRegex(tr.TranslationFailure, "monthly"):
                tr.translate_text("new", "youdao", "auto", "zh", self.org)
            tr.save_config(self.org, "youdao", {"app_id": "second"})
            self.assertFalse(tr.translate_text("hello", "youdao", "auto", "zh", self.org)["cached"])
            self.assertEqual(call.call_count, 2)
            with self.assertRaisesRegex(tr.TranslationFailure, "request limit"):
                tr.translate_text("x" * 5001, "youdao", "auto", "zh", self.org)

    def test_aliyun_signed_form_contract_and_business_errors(self):
        cfg = {"access_key_id": "test-id", "access_key_secret": "test-secret"}
        with patch.object(tr.requests, "Session") as factory, patch.object(tr, "datetime") as clock, patch.object(tr.secrets, "token_hex", return_value="test-nonce"):
            clock.now.return_value = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)
            response = factory.return_value.post.return_value
            response.status_code = 200
            response.json.return_value = {"Code": 200, "Data": {"Translated": "测试", "DetectedLanguage": "fil"}}
            result = tr.call_provider("aliyun", cfg, "ทดสอบ & + 😀", "auto", "zh-TW")
            self.assertEqual(result["detected_source"], "tl")
            args, request = factory.return_value.post.call_args
            self.assertEqual(args[0], "https://mt.cn-hangzhou.aliyuncs.com/")
            self.assertFalse(request["allow_redirects"])
            self.assertEqual(parse_qs(request["data"].decode()), {
                "FormatType": ["text"], "Scene": ["general"], "SourceLanguage": ["auto"],
                "SourceText": ["ทดสอบ & + 😀"], "TargetLanguage": ["zh-tw"],
            })
            headers = request["headers"]
            self.assertEqual(headers["x-acs-action"], "TranslateGeneral")
            self.assertEqual(headers["x-acs-version"], "2018-10-12")
            self.assertEqual(headers["x-acs-content-sha256"], hashlib.sha256(request["data"]).hexdigest())
            self.assertEqual(headers["Authorization"], ALIYUN_SIGNATURE_FIXTURE)
            self.assertNotIn("test-secret", str(request))
            tr.call_provider("aliyun", cfg, "test", "tl", "zh")
            self.assertEqual(parse_qs(factory.return_value.post.call_args.kwargs["data"].decode())["SourceLanguage"], ["fil"])
            response.json.return_value = {"Code": 10004, "Message": "private provider detail"}
            with self.assertRaises(tr.TranslationFailure) as error:
                tr.call_provider("aliyun", cfg, "test", "auto", "zh")
            self.assertNotIn("private", str(error.exception))

    def test_deepl_servers_language_mapping_and_quota(self):
        with patch.object(tr.requests, "Session") as factory:
            response = factory.return_value.post.return_value
            response.status_code = 200
            response.json.return_value = {"translations": [{"text": "测试", "detected_source_language": "TH"}]}
            for cfg, host in [
                ({"key": "test:fx"}, "api-free.deepl.com"),
                ({"key": "test"}, "api.deepl.com"),
                ({"key": "test:fx", "api_server": "standard"}, "api.deepl.com"),
                ({"key": "test", "api_server": "free"}, "api-free.deepl.com"),
            ]:
                result = tr.call_provider("deepl", cfg, "ทดสอบ", "auto", "zh")
                args, request = factory.return_value.post.call_args
                self.assertEqual(args[0], f"https://{host}/v2/translate")
                self.assertFalse(request["allow_redirects"])
                self.assertEqual(request["headers"]["Authorization"], "DeepL-Auth-Key " + cfg["key"])
                self.assertEqual(request["json"], {"text": ["ทดสอบ"], "target_lang": "ZH-HANS", "model_type": "quality_optimized"})
                self.assertEqual(result["detected_source"], "th")
            tr.call_provider("deepl", {"key": "test"}, "你好", "zh-TW", "en")
            self.assertEqual(factory.return_value.post.call_args.kwargs["json"]["source_lang"], "ZH")
            self.assertEqual(factory.return_value.post.call_args.kwargs["json"]["target_lang"], "EN-US")
            for target, expected in [("zh-TW", "ZH-HANT"), ("pt", "PT-PT"), ("pa", "PA")]:
                tr.call_provider("deepl", {"key": "test"}, "test", "en", target)
                self.assertEqual(factory.return_value.post.call_args.kwargs["json"]["target_lang"], expected)
            for status, expected in [(456, 429), (403, 503), (429, 429), (400, 400), (302, 502)]:
                response.status_code = status
                with self.assertRaises(tr.TranslationFailure) as error:
                    tr.call_provider("deepl", {"key": "test"}, "test", "auto", "zh")
                self.assertEqual(error.exception.status, expected)

    def test_added_provider_cache_and_usage_are_account_scoped(self):
        for engine, credentials in [("aliyun", {"access_key_id": "account-a", "access_key_secret": "secret"}), ("deepl", {"key": "account-a:fx"})]:
            tr.save_config(self.org, engine, {**credentials, "monthly_limit": 3})
            with patch.object(tr, "call_provider", return_value={"translation": "测试", "detected_source": "th"}) as call:
                self.assertFalse(tr.translate_text("ก😀", engine, "auto", "zh", self.org)["cached"])
                self.assertTrue(tr.translate_text("ก😀", engine, "auto", "zh", self.org)["cached"])
                self.assertEqual(call.call_count, 1)
                with self.assertRaises(tr.TranslationFailure):
                    tr.translate_text("new", engine, "auto", "zh", self.org)
                identity = "access_key_id" if engine == "aliyun" else "key"
                tr.save_config(self.org, engine, {identity: "account-b"})
                self.assertFalse(tr.translate_text("ก😀", engine, "auto", "zh", self.org)["cached"])


class SelectedModelTests(unittest.TestCase):
    def setUp(self):
        self.project = Project.objects.get(pk=1)
        self.original = self.project.model_version
        self.addCleanup(
            lambda: Project.objects.filter(pk=1).update(model_version=self.original)
        )
        self.backends = list(self.project.ml_backends.order_by("id"))
        self.assertGreaterEqual(len(self.backends), 2)

    def test_selected_backend_drives_prediction_evaluation(self):
        from data_manager.functions import evaluate_predictions
        from ml.models import MLBackend

        self.project.model_version = self.backends[1].title
        self.assertEqual(self.project.ml_backend.pk, self.backends[1].pk)
        task = Task.objects.get(pk=1)
        task.project = self.project
        with patch.object(
            MLBackend, "predict_tasks", autospec=True, return_value=None
        ) as call:
            evaluate_predictions([task])
            self.assertEqual(call.call_args.args[0].pk, self.backends[1].pk)

    def test_selected_model_executes_batch_prediction(self):
        from ml.models import MLBackend
        from tasks.models import Prediction

        self.project.model_version = self.backends[1].title
        task = Task.objects.get(pk=1)
        task.project = self.project
        before = (task.annotations.count(), task.drafts.count(), task.data)
        version = self.backends[1].model_version
        self.assertFalse(self.backends[1].is_interactive)
        result = {
            "task": task.pk,
            "project": self.project.pk,
            "model_version": version,
            "score": 0.9,
            "result": [
                {
                    "from_name": "category",
                    "to_name": "sms",
                    "type": "choices",
                    "value": {"choices": ["Customer category"]},
                }
            ],
        }
        self.addCleanup(
            lambda: Prediction.objects.filter(task_id=1, model_version=version).delete()
        )
        api = TaskAPI()
        api.task = task

        def connected(backend):
            backend.state = "CO"
            return version

        with patch.object(
            MLBackend, "update_state", autospec=True, side_effect=connected
        ), patch.object(
            MLBackend, "_get_predictions_from_ml_backend", return_value=[result]
        ) as predict:
            api.maybe_evaluate_predictions(self.project, APIRequestFactory().get("/"))
            predict.assert_called_once()
        self.assertEqual(task.predictions.filter(model_version=version).count(), 1)
        self.assertEqual(task.predictions_score, 0.9)
        task.refresh_from_db()
        self.assertEqual(
            (task.annotations.count(), task.drafts.count(), task.data), before
        )

    def test_old_model_predictions_do_not_block_selected_model(self):
        self.project.model_version = self.backends[1].title
        task = Task.objects.get(pk=1)
        task.project = self.project
        api = TaskAPI()
        api.task = task
        with patch("tasks.api.evaluate_predictions") as evaluate, patch.object(
            api, "refresh_predictions"
        ):
            api.maybe_evaluate_predictions(self.project, None)
            evaluate.assert_called_once_with([task])
        # Selecting an existing stored version never calls a live model.
        self.project.model_version = "synthetic-ci"
        with patch("tasks.api.evaluate_predictions") as evaluate:
            api.maybe_evaluate_predictions(self.project, None)
            evaluate.assert_not_called()

    def test_selected_cached_prediction_is_reused(self):
        self.project.model_version = self.backends[0].title
        self.backends[0].model_version = "synthetic-ci"
        self.backends[0].save(update_fields=["model_version"])
        self.addCleanup(
            lambda: type(self.backends[0])
            .objects.filter(pk=self.backends[0].pk)
            .update(model_version="ci-first")
        )
        task = Task.objects.get(pk=1)
        task.project = self.project
        api = TaskAPI()
        api.task = task
        with patch("tasks.api.evaluate_predictions") as evaluate:
            api.maybe_evaluate_predictions(self.project, None)
            evaluate.assert_not_called()


class TranslationConnectionTests(unittest.TestCase):
    """Provider tests use an isolated encrypted store and never write the business database."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = override_settings(BASE_DATA_DIR=self.directory.name, SECRET_KEY="connection-tests-only")
        self.settings.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.settings.disable)
        self.org = SimpleNamespace(id=999999, created_by_id=1)
        self.user = SimpleNamespace(id=1, is_authenticated=True, is_superuser=False, active_organization=self.org)
        self.factory = APIRequestFactory()
        tr.save_config(self.org.id, "youdao", {"app_id": "test-app", "secret": "test-secret", "monthly_limit": 100})

    def request(self, user=None, **body):
        request = self.factory.post("/test/", {"engine": "youdao", "test": True, **body}, format="json")
        force_authenticate(request, user=user or self.user)
        return tr.TranslationOptionsAPI.as_view()(request)

    def test_connection_bypasses_cache_and_never_saves_submitted_credentials(self):
        before = tr.read_config(self.org.id)
        with patch.object(tr, "call_provider", return_value={"translation": "你好", "detected_source": "en"}) as call:
            self.assertEqual(self.request().status_code, 200)
            self.assertEqual(self.request(secret="must-not-save", clear=True).status_code, 200)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args.args[2:], ("Hello", "en", "zh"))
        self.assertEqual(tr.read_config(self.org.id), before)

    def test_connection_respects_local_limit_even_with_cached_result(self):
        tr.save_config(self.org.id, "youdao", {"monthly_limit": 5})
        with patch.object(tr, "call_provider", return_value={"translation": "你好", "detected_source": "en"}) as call:
            self.assertEqual(self.request().status_code, 200)
            self.assertEqual(self.request().status_code, 429)
            self.assertEqual(call.call_count, 1)

    def test_connection_is_owner_only(self):
        member = SimpleNamespace(id=2, is_authenticated=True, is_superuser=False, active_organization=self.org)
        with patch.object(tr, "call_provider") as call:
            self.assertEqual(self.request(user=member).status_code, 403)
            call.assert_not_called()

    def test_connection_reports_saved_credential_failure_instead_of_cached_success(self):
        with patch.object(tr.requests, "Session") as factory:
            response = factory.return_value.post.return_value
            response.status_code = 200
            response.json.return_value = {"errorCode": "0", "translation": ["你好"], "l": "en2zh-CHS"}
            self.assertEqual(self.request().status_code, 200)
            for code, message in [("202", "signature verification failed (202)"), ("110", "not enabled for this application (110)")]:
                response.json.return_value = {"errorCode": code, "errorMessage": "test-secret"}
                result = self.request()
                self.assertEqual(result.status_code, 503)
                self.assertIn(message, result.data['detail'])
                self.assertNotIn("test-secret", str(result.data))

    def test_connection_requires_saved_configuration(self):
        tr.save_config(self.org.id, "youdao", {"clear": True})
        with patch.object(tr, "call_provider") as call:
            self.assertEqual(self.request().status_code, 503)
            self.assertEqual(self.request(engine="invalid").status_code, 400)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
