"""Managed server entry point. Control stays on localhost and requires a private token."""
import argparse
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'launcher'))
from core import activate_dependencies, control_token, current_version
DEPS = activate_dependencies(ROOT)
sys.path.insert(0, str(ROOT / 'backend'))
os.chdir(ROOT)
os.environ['PYTHONPATH'] = os.pathsep.join([str(ROOT / 'backend'), str(DEPS)])
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8001)
parser.add_argument('--maintenance', action='store_true')
args = parser.parse_args()
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
WORKER_ENABLED = os.getenv('V3_EMBEDDED_WORKER', '1')
if args.maintenance:
    os.environ['V3_EMBEDDED_WORKER'] = '0'
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from app.main import app, write_lock
from app.db import Session, Record
from app.v3_models import Job
from app.v3_worker import start_embedded, stop_embedded
import uvicorn

TOKEN = control_token(ROOT)
ROOT_ID = hashlib.sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()
maintenance = args.maintenance
manual_worker = None
previous_lifespan = app.router.lifespan_context


@asynccontextmanager
async def managed_lifespan(application):
    try:
        async with previous_lifespan(application):
            yield
    finally:
        stop_embedded(manual_worker)


app.router.lifespan_context = managed_lifespan


@app.middleware('http')
async def update_gate(request, call_next):
    if maintenance and request.url.path.startswith('/api') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return JSONResponse({'detail': '正在更新，请稍后再试'}, status_code=503)
    return await call_next(request)


def state():
    with Session() as db:
        busy = any(r.data.get('status') in ('queued', 'running') for r in db.scalars(select(Record).where(Record.kind == 'task')))
        busy = busy or bool(db.scalar(select(Job.id).where(Job.status.in_(('queued', 'running'))).limit(1)))
    return {'root_id': ROOT_ID, 'version': current_version(ROOT)['version'], 'pid': os.getpid(),
            'busy': busy, 'maintenance': maintenance}


def authorized(request):
    return request.client.host in ('127.0.0.1', '::1') and secrets.compare_digest(request.headers.get('X-Launcher-Token', ''), TOKEN)


@app.get('/_launcher/status')
async def status(request: Request):
    if not authorized(request):
        return JSONResponse({'detail': 'Unauthorized'}, status_code=403)
    return state()


@app.post('/_launcher/stop')
async def stop(request: Request):
    global maintenance
    if not authorized(request):
        return JSONResponse({'detail': 'Unauthorized'}, status_code=403)
    async with write_lock:
        result = state()
        if result['busy']:
            return JSONResponse({'detail': '任务运行中'}, status_code=409)
        maintenance = True
        server.should_exit = True
    return result


@app.post('/_launcher/activate')
async def activate(request: Request):
    global maintenance, manual_worker
    if not authorized(request):
        return JSONResponse({'detail': 'Unauthorized'}, status_code=403)
    if maintenance:
        os.environ['V3_EMBEDDED_WORKER'] = WORKER_ENABLED
        manual_worker = start_embedded()
        maintenance = False
    return state()


# The app has a catch-all static mount. Management routes must precede that mount.
management_routes = [route for route in app.router.routes if getattr(route, 'path', '').startswith('/_launcher/')]
app.router.routes = management_routes + [route for route in app.router.routes if route not in management_routes]
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='warning'))
server.run()
