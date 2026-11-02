"""Local Transformers prediction/training backend. No remote code or cloud inference."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import threading
import uuid
from collections import OrderedDict
from defusedxml.ElementTree import fromstring
from fastapi import FastAPI, HTTPException


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def control_for(xml, params):
    root = fromstring(xml)
    kind = 'Choices' if params['model_task'] == 'text-classification' else 'Labels'
    controls = [c for c in root.iter(kind) if not params.get('model_control') or c.get('name') == params['model_control']]
    if len(controls) != 1:
        raise ValueError('Set model_control to exactly one compatible labeling control.')
    control = controls[0]
    targets = [t for t in root.iter('Text') if t.get('name') == control.get('toName')]
    if len(targets) != 1 or not targets[0].get('value', '').startswith('$'):
        raise ValueError('The control must reference inline Text with value="$field".')
    target = targets[0]
    if target.get('valueType', 'text') != 'text' or target.get('encoding', 'none') != 'none':
        raise ValueError('Only unencoded inline text is supported.')
    if control.get('perRegion', 'false') == 'true' or control.get('visibleWhen'):
        raise ValueError('Conditional and per-region controls are not supported.')
    if kind == 'Choices' and control.get('choice', 'single') != 'single':
        raise ValueError('Only single-label text classification is supported.')
    children = list(control.iter('Choice' if kind == 'Choices' else 'Label'))
    labels = [c.get('value') for c in children]
    if not labels or len(labels) != len(set(labels)) or any(not label for label in labels) or any(c.get('alias') for c in children):
        raise ValueError('Use unique label values without aliases.')
    return dict(name=control.get('name'), target=target.get('name'), field=target.get('value')[1:], labels=labels, kind=kind)


class Runtime:
    def __init__(self, output: Path, device=None):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.lock = threading.RLock()
        self.cached = None
        self.training_status = {}
        self.cancellations = {}
        self.tokenizers = OrderedDict()
        self.tokenizer_lock = threading.Lock()

    def config(self, body):
        from transformers import AutoConfig, AutoTokenizer
        from huggingface_hub import snapshot_download
        params = body.get('extra_params') if 'extra_params' in body else body.get('params')
        params = dict(params or {})
        # Transport metadata is not model configuration. Setup/train/predict must
        # resolve to the same checkpoint even when LS adds credentials/context.
        for key in ('login', 'password', 'hostname', 'context'):
            params.pop(key, None)
        source = params.get('base_model')
        if not isinstance(source, str) or not source.strip():
            raise ValueError('base_model must be a local Transformers directory or cached Hugging Face model ID.')
        if params.get('model_task') not in ('text-classification', 'token-classification'):
            raise ValueError('Select text-classification or token-classification.')
        xml = body.get('schema') or body.get('label_config')
        if not isinstance(xml, str) or len(xml) > 100000:
            raise ValueError('A labeling XML configuration of at most 100 KB is required.')
        control = control_for(xml, params)
        path = Path(source).expanduser()
        if not path.is_dir():
            # Model acquisition is explicit: this service never downloads on a labeling request.
            path = Path(snapshot_download(source, revision=params.get('model_revision') or None, local_files_only=True))
        path = path.resolve()
        config = AutoConfig.from_pretrained(path, local_files_only=True, trust_remote_code=False)
        mapping = params.get('label_map') or {}
        if not isinstance(mapping, dict):
            raise ValueError('label_map must be an object mapping model labels to project labels.')
        labels = [mapping.get(config.id2label[i], config.id2label[i]) for i in range(config.num_labels)]
        expected = control['labels'] if control['kind'] == 'Choices' else ['O'] + [f'{prefix}-{label}' for label in control['labels'] for prefix in ('B', 'I')]
        initialize = params.get('initialize_head', False)
        if not isinstance(initialize, bool):
            raise ValueError('initialize_head must be boolean.')
        if initialize:
            labels = expected
        elif set(labels) != set(expected) or len(labels) != len(expected):
            raise ValueError('Model id2label does not match project labels. Supply label_map or explicitly set initialize_head=true for a new head.')
        if len(labels) != len(set(labels)):
            raise ValueError('Model labels must map one-to-one to project labels.')
        files = [(str(f.relative_to(path)), f.stat().st_size, f.stat().st_mtime_ns)
                 for f in sorted(path.rglob('*')) if f.is_file()]
        tokenizer_key = (str(path), digest(files))
        with self.tokenizer_lock:
            tokenizer = self.tokenizers.get(tokenizer_key)
            if tokenizer is None:
                tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False, use_fast=True)
                self.tokenizers[tokenizer_key] = tokenizer
                if len(self.tokenizers) > 4:
                    self.tokenizers.popitem(last=False)
            self.tokenizers.move_to_end(tokenizer_key)
        if not tokenizer.is_fast:
            raise ValueError('A fast tokenizer with character offsets is required.')
        identity_params = {k: params.get(k) for k in ('base_model','model_task','model_revision','model_control','backend_id','label_map','initialize_head','max_length')}
        identity = digest([str(body.get('project')), identity_params, xml, str(path), files])
        directory = self.output / identity
        pointer = directory / 'current.json'
        version = json.loads(pointer.read_text())['version'] if pointer.exists() else 'base'
        return dict(params=params, control=control, labels=labels, tokenizer=tokenizer, config=config,
                    source=path, directory=directory, version=version, identity=identity,
                    model_version=f'hf-{identity}-{version}', initialize=initialize)

    def load(self, cfg, training=False):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoModelForTokenClassification
        if not training and self.cached and self.cached[0] == cfg['model_version']:
            return self.cached[1]
        self.cached = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        model_cls = AutoModelForSequenceClassification if cfg['control']['kind'] == 'Choices' else AutoModelForTokenClassification
        # Full replay from the base makes a repeated round deterministic in data scope.
        path = cfg['source'] if training or cfg['version'] == 'base' else cfg['directory'] / cfg['version']
        options = dict(local_files_only=True, trust_remote_code=False, use_safetensors=True)
        if cfg['initialize'] and (training or cfg['version'] == 'base'):
            options.update(num_labels=len(cfg['labels']), id2label=dict(enumerate(cfg['labels'])),
                           label2id={label:i for i,label in enumerate(cfg['labels'])}, ignore_mismatched_sizes=True)
        model, info = model_cls.from_pretrained(path, output_loading_info=True, **options)
        if not cfg['initialize'] and (info['missing_keys'] or info['mismatched_keys']):
            raise ValueError('Checkpoint lacks the required trained task head; initialize_head=true is required to create one.')
        if cfg['initialize'] and (training or cfg['version'] == 'base'):
            # Even a same-sized pretrained head may represent unrelated labels.
            # Reset only modules outside the pretrained encoder/decoder backbone.
            backbone = set(model.base_model.modules())
            for module in model.modules():
                if module not in backbone and callable(getattr(module, 'reset_parameters', None)):
                    module.reset_parameters()
        device = self.device or ('cuda' if torch.cuda.is_available() else 'cpu')
        model.to(device).eval()
        self.cached = (cfg['model_version'], model)
        return model

    def encode(self, cfg, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError('Task text must be non-empty and at most 12000 characters.')
        encoded = cfg['tokenizer'](text, return_offsets_mapping=True, return_tensors='pt', truncation=False)
        maximum = min(int(cfg['params'].get('max_length', 512)),
                      getattr(cfg['config'], 'max_position_embeddings', 512), cfg['tokenizer'].model_max_length)
        if maximum < 8 or encoded['input_ids'].shape[1] > maximum:
            raise ValueError(f'Text exceeds model token limit {maximum}; split long documents before annotation. No text was truncated.')
        offsets = encoded.pop('offset_mapping')[0].tolist()
        return encoded, offsets

    def setup(self, body):
        # Metadata remains available while GPU training holds the model lock.
        cfg = self.config(body)
        return {'model_version': cfg['model_version']}

    def predict(self, body):
        import torch
        with self.lock:
            cfg = self.config(body)
            if cfg['initialize'] and cfg['version'] == 'base':
                raise ValueError('New task head requires human training samples before prediction.')
            model = self.load(cfg)
            tasks = body.get('tasks')
            if not isinstance(tasks, list) or not 1 <= len(tasks) <= 512:
                raise ValueError('Provide 1 to 512 tasks.')
            results = []
            c = cfg['control']
            for task in tasks:
                text = task['data'].get(c['field'])
                encoded, offsets = self.encode(cfg, text)
                with torch.inference_mode():
                    probabilities = model(**{k:v.to(model.device) for k,v in encoded.items()}).logits.softmax(-1)[0].cpu()
                regions = []
                common = {'from_name':c['name'], 'to_name':c['target']}
                if c['kind'] == 'Choices':
                    score, index = probabilities.max(-1)
                    score = float(score)
                    regions.append({**common, 'type':'choices', 'value':{'choices':[cfg['labels'][int(index)]]}})
                else:
                    scores, indices = probabilities.max(-1)
                    valid_scores = []
                    current = None
                    for (start,end), index, confidence in zip(offsets, indices.tolist(), scores.tolist()):
                        if start == end:
                            continue
                        valid_scores.append(confidence)
                        tag = cfg['labels'][index]
                        if tag == 'O':
                            current = None
                            continue
                        prefix, label = tag.split('-',1)
                        if prefix == 'I' and current and current['value']['labels'] == [label]:
                            current['value'].update(end=end, text=text[current['value']['start']:end])
                            current['score'] = min(current['score'], confidence)
                        else:
                            current = {**common, 'id':uuid.uuid4().hex[:10], 'type':'labels', 'score':confidence,
                                       'value':{'start':start,'end':end,'text':text[start:end],'labels':[label]}}
                            regions.append(current)
                    # Least certain real token includes uncertainty in predicted outside tokens.
                    score = min(valid_scores) if valid_scores else 0.0
                if not math.isfinite(score):
                    raise ValueError('Model returned a non-finite confidence score.')
                results.append({'model_version':cfg['model_version'],'result':regions,'score':score})
            return {'results':results}

    def training_rows(self, cfg, body):
        c = cfg['control']; label_ids = {label:i for i,label in enumerate(cfg['labels'])}
        rows = []
        tasks = body.get('annotations')
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 10000:
            raise ValueError('Training requires 1 to 10000 annotated tasks.')
        for task in tasks:
            text = task['data'].get(c['field'])
            encoded, offsets = self.encode(cfg, text)
            for annotation in task.get('annotations', []):
                if annotation.get('was_cancelled'):
                    continue
                regions = [r for r in annotation.get('result',[]) if r.get('from_name') == c['name'] and r.get('to_name') == c['target']]
                if c['kind'] == 'Choices':
                    if len(regions) != 1 or regions[0].get('type') != 'choices' or len(regions[0]['value'].get('choices',[])) != 1:
                        raise ValueError(f'Task {task.get("id")} needs exactly one submitted classification for {c["name"]}.')
                    target = label_ids[regions[0]['value']['choices'][0]]
                else:
                    spans = sorted((r['value']['start'], r['value']['end'], r['value']['labels'][0], r['value'].get('text')) for r in regions if r.get('type') == 'labels')
                    for i,(start,end,label,original) in enumerate(spans):
                        if not 0 <= start < end <= len(text) or label not in c['labels'] or (original is not None and original != text[start:end]):
                            raise ValueError('Training annotation has an invalid entity span or label.')
                        if i and start < spans[i-1][1]:
                            raise ValueError('BIO training cannot represent overlapping entities.')
                    target = []; seen = set()
                    for start,end in offsets:
                        if start == end:
                            target.append(-100); continue
                        matches = [i for i,(a,b,_,_) in enumerate(spans) if start < b and a < end]
                        if not matches:
                            target.append(label_ids['O']); continue
                        i = matches[0]; a,b,label,_ = spans[i]
                        if len(matches) > 1 or start < a or end > b:
                            raise ValueError('Entity boundaries split tokenizer tokens; correct the span or use a compatible tokenizer.')
                        target.append(label_ids[f'{"I" if i in seen else "B"}-{label}']); seen.add(i)
                    if len(seen) != len(spans):
                        raise ValueError('An entity has no corresponding token.')
                group = re.sub(r'\d+', '#', re.sub(r'https?://\S+', '<url>', text)).strip().lower()
                group = re.sub(r'\s+', ' ', group)
                rows.append((encoded,target,digest(group)))
        if not rows:
            raise ValueError('No usable human annotations for this labeling control.')
        return rows

    def status(self, body):
        cfg = self.config(body)
        return self.training_status.get(cfg['identity'], {'status':'idle'})

    def snapshot(self, body):
        with self.lock:
            cfg=self.config(body)
            if cfg['initialize'] and cfg['version']=='base':raise ValueError('Train and publish this model before reusing its weights.')
            self.load(cfg)  # Check that a trained task head is actually available.
            source=cfg['source'] if cfg['version']=='base' else cfg['directory']/cfg['version']
            return {'base_model':str(source),'model_version':cfg['model_version'],'model_task':cfg['params']['model_task']}

    def cancel(self, body):
        cfg = self.config(body)
        event = self.cancellations.get(cfg['identity'])
        if event: event.set()
        return {'cancel_requested': bool(event)}

    def promote(self, body):
        with self.lock:
            cfg = self.config(body)
            full = body.get('version','')
            prefix = f'hf-{cfg["identity"]}-'
            if not full.startswith(prefix): raise ValueError('Candidate belongs to another project or model configuration.')
            version = full[len(prefix):]
            if not re.fullmatch(r'[a-f0-9]{16}',version): raise ValueError('Invalid candidate version.')
            candidate = cfg['directory']/version
            if not (candidate/'training-report.json').is_file(): raise ValueError('Candidate is incomplete or unavailable.')
            report = json.loads((candidate/'training-report.json').read_text())
            pointer = cfg['directory']/'current.json';temporary=pointer.with_suffix('.tmp')
            temporary.write_text(json.dumps({'version':version,'samples':report['samples']}));os.replace(temporary,pointer)
            self.cached=None
            return {'model_version':full,'published':True}

    def train(self, body):
        import torch
        with self.lock:
            cfg = self.config(body)
            try:
                rows = self.training_rows(cfg, body)
            except (ValueError, KeyError) as exc:
                self.training_status[cfg['identity']]={'status':'failed','error':str(exc)}
                raise
            epochs = int(cfg['params'].get('epochs', 1))
            rate = float(cfg['params'].get('learning_rate', 2e-5))
            split = float(cfg['params'].get('validation_split',0))
            if not 1 <= epochs <= 10 or not math.isfinite(rate) or not 0 < rate <= 0.01 or not 0<=split<=0.4:
                raise ValueError('Invalid epochs, learning_rate or validation_split.')
            training = [row for row in rows if int(row[2][:8],16)%10000>=split*10000]
            validation = [row for row in rows if int(row[2][:8],16)%10000<split*10000]
            if not training: raise ValueError('No training groups remain after validation split.')
            torch.manual_seed(42)
            model = self.load(cfg, training=True)
            event = threading.Event();self.cancellations[cfg['identity']]=event
            state={'status':'training','completed_steps':0,'total_steps':len(training)*epochs}
            self.training_status[cfg['identity']]=state
            def evaluate():
                from sklearn.metrics import accuracy_score, f1_score
                truth=[];predicted=[];model.eval()
                with torch.inference_mode():
                    for encoded,target,_ in validation:
                        logits=model(**{k:v.to(model.device) for k,v in encoded.items()}).logits[0].cpu()
                        if isinstance(target,list):
                            for y,p in zip(target,logits.argmax(-1).tolist()):
                                if y!=-100:truth.append(y);predicted.append(p)
                        else:truth.append(target);predicted.append(int(logits.argmax()))
                if not truth:return {'samples':0,'accuracy':None,'macro_f1':None}
                return {'samples':len(validation),'accuracy':accuracy_score(truth,predicted),
                        'macro_f1':f1_score(truth,predicted,average='macro',zero_division=0)}
            before=evaluate();optimizer=torch.optim.AdamW(model.parameters(),lr=rate)
            try:
                model.train()
                for _ in range(epochs):
                    for index in torch.randperm(len(training)).tolist():
                        encoded,target,group=training[index]
                        if event.is_set():raise ValueError('Training cancelled; previous model retained.')
                        optimizer.zero_grad(set_to_none=True)
                        labels=torch.tensor([target],device=model.device)
                        loss=model(**{k:v.to(model.device) for k,v in encoded.items()},labels=labels).loss
                        if not torch.isfinite(loss):raise ValueError('Non-finite training loss; previous checkpoint retained.')
                        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.0);optimizer.step()
                        state['completed_steps']+=1;state['loss']=float(loss.detach())
                after=evaluate();version=uuid.uuid4().hex[:16];destination=cfg['directory']/version
                if event.is_set():raise ValueError('Training cancelled; previous model retained.')
                model.save_pretrained(destination,safe_serialization=True);cfg['tokenizer'].save_pretrained(destination)
                report={'model_version':f'hf-{cfg["identity"]}-{version}','samples':len(training),'validation_samples':len(validation),
                        'before':before,'after':after,'project':str(body.get('project')),'split_policy':'normalized template groups',
                        'published':False,'epochs':epochs}
                (destination/'training-report.json').write_text(json.dumps(report))
                if cfg['params'].get('publish',True):
                    self.promote({**body,'version':report['model_version']});report['published']=True
                state.update(status='completed',model_version=report['model_version'])
                return report
            except Exception as exc:
                state.update(status='cancelled' if event.is_set() else 'failed',error=str(exc))
                raise
            finally:
                self.cached=None;self.cancellations.pop(cfg['identity'],None)
                del optimizer,model
                if torch.cuda.is_available():torch.cuda.empty_cache()


def create_app(output, device=None):
    # Import before readiness so the first /setup fits LS's short setup timeout.
    from transformers import AutoConfig, AutoTokenizer
    runtime = Runtime(Path(output), device)
    app = FastAPI(title='Local Transformers active learning backend')
    app.state.runtime = runtime

    @app.get('/health')
    def health():
        return {'status':'UP', 'model_class':'Transformers', 'training':True}

    def endpoint(method):
        def call(body: dict):
            try:
                if method in ('predict','promote','snapshot') and any(s.get('status')=='training' for s in runtime.training_status.values()):
                    raise HTTPException(503, 'GPU training is in progress. Existing saved predictions remain available; retry after training completes.')
                return getattr(runtime, method)(body)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        return call
    for path, method in [('setup','setup'),('predict','predict'),('train','train'),('training-status','status'),('cancel-training','cancel'),('promote','promote'),('snapshot','snapshot')]:
        app.post('/'+path)(endpoint(method))
    return app


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=9092)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'data')
    parser.add_argument('--device',default=None)
    args = parser.parse_args()
    uvicorn.run(create_app(args.output,args.device),host='127.0.0.1',port=args.port,workers=1)
