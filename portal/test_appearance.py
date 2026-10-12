import base64,io,tempfile
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app import create_app
from secrets_store import MemorySecrets

@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory() as d:
        app=create_app(Path(d),MemorySecrets())
        with TestClient(app,base_url='http://127.0.0.1:59189') as c:
            yield c,app,d

def test_config_auth_validation_and_persistence(fixture):
    c,app,d=fixture
    assert c.post('/api/admin/settings',json={'appearance':{'site_name':'影厅'}}).status_code==401
    c.post('/api/auth/setup',json={'password':'test-appearance-password'})
    assert c.post('/api/admin/settings',json={'appearance':{'site_name':'独立影厅','theme_mode':'auto','day_start':'08:00'}}).status_code==200
    assert c.get('/api/public/appearance').json()['site_name']=='独立影厅'
    assert c.get('/api/public/catalog').json()['appearance']['theme_mode']=='auto'
    assert '<title>独立影厅' in c.get('/').text
    for bad in [{'theme_mode':'invalid'},{'day_start':'19:00'},{'logo_url':'javascript:alert(1)'},{'favicon_url':'//evil.test/a'},{'hero_images':[{'url':'/static/a.png','title':'x','subtitle':'x','evil':True}]}]:
        assert c.post('/api/admin/settings',json={'appearance':bad,'redemption_url':'https://changed.example'}).status_code==400
        assert c.get('/api/public/catalog').json()['redemption_url']==''
    assert c.post('/api/admin/settings',json={'appearance':{'site_name':'<script>bad</script>'}}).status_code==200
    assert '<title>&lt;script&gt;bad&lt;/script&gt;' in c.get('/').text
    assert c.post('/api/admin/settings',headers={'Origin':'https://evil.test'},json={'appearance':{'site_name':'bad'}}).status_code==403

def test_raster_upload_authenticated_reencoded_and_served(fixture):
    c,app,d=fixture
    buf=io.BytesIO();Image.new('RGB',(8,8),'gold').save(buf,format='PNG')
    body={'filename':'../../secret.svg','data':base64.b64encode(buf.getvalue()).decode()}
    assert c.post('/api/admin/appearance-assets',json=body).status_code==401
    c.post('/api/auth/setup',json={'password':'test-appearance-password'})
    result=c.post('/api/admin/appearance-assets',json=body)
    assert result.status_code==200,result.text
    url=result.json()['url'];assert url.endswith('.png') and 'secret' not in url
    response=c.get(url);assert response.status_code==200 and response.headers['content-type']=='image/png'
    assert response.headers['x-content-type-options']=='nosniff'
    assert c.post('/api/admin/appearance-assets',json={'data':base64.b64encode(b'<svg onload="evil"/>').decode()}).status_code==400
    assert c.post('/api/admin/appearance-assets',json={'data':'x'*2800001}).status_code==400
    assert c.post('/api/admin/appearance-assets',headers={'Origin':'https://evil.test'},json=body).status_code==403
    assert c.post('/api/admin/settings',json={'appearance':{'logo_url':url}}).status_code==200
    reloaded=TestClient(create_app(Path(d),app.state.vault),base_url='http://127.0.0.1:59189')
    assert reloaded.get('/api/public/appearance').json()['logo_url']==url
    assert reloaded.get(url).content==response.content
    reloaded.close()
