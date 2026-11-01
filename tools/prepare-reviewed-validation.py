"""Prepare private acceptance data; gold labels stay outside imported task data."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import pandas as pd

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('csv',type=Path)
parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'artifacts/reviewed-1492.json')
args=parser.parse_args()
frame=pd.read_csv(args.csv,keep_default_na=False)
required={'sms_text','single_category','review_status','template_id'}
if not required.issubset(frame.columns) or not frame.review_status.eq('reviewed').all():
    raise SystemExit('The input must contain only historically reviewed records with the expected columns.')
records=[]
for i,row in frame.iterrows():
    group=re.sub(r'\d+','#',re.sub(r'https?://\S+','<url>',row.sms_text)).strip().lower()
    group=re.sub(r'\s+',' ',group)
    split='holdout' if int(hashlib.sha256(group.encode()).hexdigest()[:8],16)%5==0 else 'train'
    records.append({'data':{'content':row.sms_text,'_workflow_split':split,'source_row':int(i)},
                    'label':row.single_category,'template_id':str(row.template_id)})
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'rows':len(records),'holdout':sum(r['data']['_workflow_split']=='holdout' for r in records),
                  'source_sha256':hashlib.sha256(args.csv.read_bytes()).hexdigest()}))
