"""Windowless, supervised Windows entrypoint using the existing Anaconda base."""
import argparse
from datetime import datetime
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--port',type=int,default=9092)
args=parser.parse_args()
logs=root/'ml_backend/data/logs';logs.mkdir(parents=True,exist_ok=True)
sys.stdout=(logs/f'inference-{datetime.now():%Y%m%d-%H%M%S}.log').open('a',encoding='utf-8',buffering=1)
sys.stderr=sys.stdout
sys.path.insert(0,str(root/'ml_backend'))
from transformers_service import create_app
import uvicorn
uvicorn.run(create_app(root/'ml_backend/data'),host='127.0.0.1',port=args.port,workers=1,access_log=False)
