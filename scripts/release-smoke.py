"""Build-time verification of compiled release artifacts, without live services."""
import base64
import datetime as dt
import importlib
import importlib.machinery
import io
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else '/app').resolve()
PORTAL = ROOT / 'portal'
EXPECTED_MODULES = {
    'app', 'appearance', 'core', 'emby_library', 'emby_stats', 'featured_movies',
    'network_monitor', 'nextemby', 'nextfind_catalog', 'purchase_reviews',
    'secrets_store', 'subscription_requests', 'xhs_automation', 'xianyu_proxy',
    'xiaohongshu', '_mediadock_scheduler', '_mediadock_save_secret',
}
WRAPPERS = {
    'scheduler.py': 'from _mediadock_scheduler import main\nif __name__ == "__main__":\n    main()\n',
    'save_secret.py': 'import _mediadock_save_secret\n',
}

def check(condition, message):
    if not condition:
        raise AssertionError(message)


for path in ROOT.rglob('*'):
    check(not path.is_symlink(), 'Unexpected release symlink: ' + str(path.relative_to(ROOT)))
    if not path.is_file():
        continue
    relative = path.relative_to(ROOT)
    check(not any(part.startswith('.') or part in {'tests', '__pycache__', 'node_modules', 'data', 'secrets', 'backups'}
                  for part in relative.parts), 'Unexpected private directory or hidden file: ' + str(relative))
    check(not (path.name.startswith('test_') or path.suffix.lower() in
               {'.pyc', '.pyo', '.pyx', '.pxd', '.c', '.h', '.map', '.db', '.sqlite', '.sqlite3', '.key', '.pem', '.log'}),
          'Unexpected source/private artifact: ' + str(relative))
    if path.suffix == '.py':
        check(path.parent == PORTAL and path.name in WRAPPERS and path.read_text() == WRAPPERS[path.name],
              'Application Python source in release: ' + str(relative))
    elif path.parent == PORTAL:
        check(any(path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES),
              'Unexpected application artifact: ' + str(relative))
    elif path.parent == ROOT:
        check(path.name in {'product-mapping.json', 'LICENSE', 'NOTICE', 'VERSION', 'entrypoint.sh'},
              'Unexpected release root artifact: ' + str(relative))

sys.path.insert(0, str(PORTAL))
os.environ.update(MEDIADOCK_REQUIRE_HTTPS='0', MEDIADOCK_UNIFIED_ADMIN='0',
                  MEDIADOCK_PUBLIC_BASE_URL='http://127.0.0.1:59189', MEDIADOCK_ADMIN_USERNAME='release_smoke_admin')
modules = {}
for name in EXPECTED_MODULES - {'_mediadock_save_secret'}:
    module = importlib.import_module(name)
    check(Path(module.__file__).parent == PORTAL and
          any(module.__file__.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES),
          'Module did not load from compiled release: ' + name)
    modules[name] = module
check(any(PORTAL.glob('_mediadock_save_secret*.so')), 'Missing compiled credential CLI')

from fastapi.testclient import TestClient
from PIL import Image
from app import create_app
from secrets_store import FileSecrets

with tempfile.TemporaryDirectory(prefix='mediadock-release-check-') as temporary:
    directory = Path(temporary)
    secret_dir = directory / 'secrets'
    secret_dir.mkdir(mode=0o700)
    vault = FileSecrets(secret_dir)
    data_dir = directory / 'data'
    app = create_app(data_dir, vault)
    password = secrets.token_urlsafe(32)
    with TestClient(app, base_url='http://127.0.0.1:59189') as client:
        check(client.get('/health').json()['status'] == 'ok', 'Health route failed')
        check(client.get('/api/admin/overview').status_code == 401, 'Admin route is not protected')
        check(client.post('/api/admin/settings', json={'appearance': {'site_name': 'Forbidden'}}).status_code == 401,
              'Settings write is not protected')
        check(client.get('/api/auth/status').json() == {'initialized': False, 'authenticated': False},
              'Unexpected initial authentication state')
        response = client.post('/api/auth/setup', json={'password': password})
        check(response.status_code == 200, 'Administrator setup failed')
        check(client.post('/api/auth/setup', json={'password': password}).status_code == 409,
              'Repeated administrator setup allowed')
        check(client.post('/api/auth/logout', json={}).status_code == 200, 'Logout failed')
        check(client.post('/api/auth/login', json={'username': 'release_smoke_admin', 'password': password}).status_code == 200,
              'Administrator login failed')
        guide = {'texts': {'heading-t0': 'Release verification guide'}}
        response = client.post('/api/admin/settings', json={
            'appearance': {'site_name': 'Release verification', 'theme_mode': 'auto', 'day_start': '08:00'},
            'guide': guide,
        })
        check(response.status_code == 200, 'Appearance / tutorial settings write failed')
        check(client.get('/api/public/appearance').json()['site_name'] == 'Release verification', 'Appearance read failed')
        check(client.get('/api/public/catalog').json()['guide'] == guide, 'Tutorial read failed')
        check(client.post('/api/admin/settings', json={'appearance': {'theme_mode': 'invalid'}}).status_code == 400,
              'Invalid theme was accepted')
        check(client.post('/api/admin/settings', json={'guide': guide}, headers={'Origin': 'https://untrusted.example'}).status_code == 403,
              'Cross-origin settings write was allowed')
        raster = io.BytesIO()
        Image.new('RGB', (4, 4), 'gold').save(raster, format='PNG')
        response = client.post('/api/admin/appearance-assets', json={'filename': 'logo.png', 'data': base64.b64encode(raster.getvalue()).decode()})
        check(response.status_code == 200, 'Image decoding / upload failed')
        response = client.get(response.json()['url'])
        check(response.status_code == 200 and response.headers['content-type'] == 'image/png', 'Image serving failed')
        for route in ('/', '/plans', '/library', '/discover', '/guide', '/admin'):
            response = client.get(route)
            check(response.status_code == 200 and '<title>Release verification' in response.text, 'Page rendering failed: ' + route)
        for asset in ('app.js', 'theme.js', 'theme.css', 'appearance-editor.js', 'guide.js'):
            response = client.get('/static/' + asset)
            check(response.status_code == 200 and not response.headers['content-type'].startswith('text/html'), 'Static asset failed: ' + asset)
    with TestClient(create_app(data_dir, vault), base_url='http://127.0.0.1:59189') as client:
        check(client.get('/api/public/catalog').json()['guide'] == guide, 'Settings persistence failed')
    scheduler = modules['_mediadock_scheduler']
    check(callable(scheduler.main), 'Scheduler entrypoint is unavailable')
    check(not scheduler.refresh_due({'enabled': False}, dt.datetime.now(dt.timezone.utc)), 'Scheduler disabled case failed')
    check(scheduler.refresh_due({'enabled': True, 'token_configured': True}, dt.datetime.now(dt.timezone.utc)),
          'Scheduler refresh scheduling failed')

print('Compiled release verified: source allowlist, imports, auth, settings, images, pages and scheduler.')
