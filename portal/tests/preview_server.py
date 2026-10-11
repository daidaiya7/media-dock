"""Loopback-only visual fixture. Synthetic data; no real APIs or message sends."""
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = json.loads((ROOT / 'product-mapping.json').read_text())['products']
SELECTED = ''
CONVERSATIONS = [{'id': f'preview_{i}', 'name': f'体验顾客 {i:02d}', 'kind': 'c2c',
                  'summary': '想了解有网盘和无网盘的套餐区别。', 'time': '10:30', 'active': False}
                 for i in range(1, 26)]
ORDERS = [{'order_id': f'PREVIEW-{i:03d}', 'source': source, 'product_id': PRODUCTS[i % 5]['id'],
           'product': PRODUCTS[i % 5], 'status': status, 'delivery_mode': 'claim' if source == 'web' else 'direct',
           'buyer_note': f'展示买家 {i}', 'created_at': '2026-10-11T09:00:00Z'}
          for i, (source, status) in enumerate(zip(
              ['web', 'xianyu', 'xiaohongshu', 'manual', 'web', 'xianyu'],
              ['pending_review', 'eligible', 'issued', 'processing', 'needs_review', 'issued']))]
MEDIA = [{'id': f'preview-{i}', 'title': title + ' · 演示', 'type': 'movie' if i % 3 == 0 else 'tv',
          'year': 2024 + i % 3, 'overview': '本地视觉演示内容。故事从一个安静的夜晚开始，在城市与远方之间，寻找值得留住的时光。',
          'genres': ['剧情', '生活'], 'poster_url': f'/api/public/library/poster/preview-{i}',
          'in_library': i % 3 != 1, 'subscribed': i % 2 == 0,
          'local_episodes': [3, 0, 12, 7][i % 4], 'total_episodes': [12, None, 12, 24][i % 4]}
         for i, title in enumerate(['落日之后', '光影长廊', '城市来信', '远山回响', '星光旅人', '静谧之海', '雨中的故事', '理想生活', '夜色温柔', '下一次相遇', '时间的回声', '春日序曲'])]
LIBRARIES = [{'id': 'preview-movies', 'name': '电影 · 演示'},
             {'id': 'preview-series', 'name': '剧集 · 演示'},
             {'id': 'preview-documentaries', 'name': '纪录片 · 演示'},
             {'id': 'preview-uncovered', 'name': '待更新片库 · 演示'}]


def media_fixture(path):
    parts = urlsplit(path)
    query = parse_qs(parts.query)
    title = query.get('query', [''])[0].strip()
    kind = query.get('type', ['all'])[0]
    rows = [row for row in MEDIA if (kind == 'all' or row['type'] == kind) and (not title or title in row['title'])]
    discovery = parts.path.endswith('/discovery')
    if not discovery:
        library = query.get('library', [''])[0]
        if library == 'preview-series':
            rows = [row for row in rows if row['type'] == 'tv']
        elif library == 'preview-documentaries':
            rows = rows[:3]
        elif library != 'preview-movies':
            rows = []
        sort = query.get('sort', ['name'])[0]
        rows = sorted(rows, key=lambda row: row['year'] if sort == 'year' else row['id'] if sort == 'newest' else row['title'], reverse=sort != 'name')
    if discovery and not title:
        rows = [row for row in rows if row['subscribed']]
    try:
        page = max(1, int(query.get('page', ['1'])[0]))
    except ValueError:
        page = 1
    keys = ['id', 'title', 'type', 'year', 'poster_url'] + (['in_library', 'subscribed', 'local_episodes', 'total_episodes'] if discovery else ['overview', 'genres'])
    selected = rows if discovery else rows[(page-1)*8:page*8]
    result = {'configured': True, 'items': [{key: row[key] for key in keys} for row in selected],
              'stale': False, 'updated_at': '2026-10-11T12:00:00Z'}
    if discovery:
        result['mode'] = 'search' if title else 'subscriptions'
    else:
        result.update(page=page, has_more=len(rows) > page*8)
    return result


def status():
    return {'state': 'ready', 'configured': True, 'available': True, 'browser_running': True,
            'account_id': 'preview_account', 'account_name': '演示店铺', 'conversation_id': SELECTED,
            'session_id': '00000000-0000-4000-8000-000000000001'}


def messages():
    return {'account_id': 'preview_account', 'conversation_id': SELECTED, 'header': '体验顾客',
            'composer_ready': True, 'pending_receipt': None, 'latest_receipt': None,
            'messages': [{'id': f'm_{i}', 'text': text, 'direction': 'incoming' if i % 2 == 0 else 'outgoing',
                          'time': '10:30', 'status': 'observed', 'content_type': '1'} for i, text in enumerate([
                '你好，请问有网盘和无网盘的套餐有什么区别？',
                '自备 115 网盘选择 SVIP 年卡；无需自备网盘可以选择 VVIP 年卡。',
                '明白了，我先看看试用套餐，谢谢。'])]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, value, code=200, content_type='application/json; charset=utf-8'):
        raw = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, (dict, list)) else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path.startswith('/static/'):
            target = ROOT / 'portal' / path.lstrip('/')
            if target.parent == ROOT / 'portal/static' and target.is_file():
                return self.reply(target.read_bytes(), content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        if path == '/status/':
            self.send_response(308)
            self.send_header('Location', '/status')
            self.end_headers()
            return
        if path in {'/', '/admin', '/status', '/claim', '/guide', '/library', '/discover', '/plans'}:
            return self.reply((ROOT / 'portal/static/index.html').read_bytes(), content_type='text/html; charset=utf-8')
        if path == '/api/public/featured-movies':
            return self.reply({'movies': []})
        if path == '/api/admin/featured-movies':
            return self.reply({'enabled': False, 'token_configured': False, 'last_success': None, 'movie_count': 0})
        if path in {'/api/public/library', '/api/public/discovery'}:
            return self.reply(media_fixture(self.path))
        if path == '/api/public/library/search':
            from urllib.parse import urlencode
            groups = []
            query = parse_qs(urlsplit(self.path).query).get('query', [''])[0]
            for library in LIBRARIES:
                result = media_fixture('/api/public/library?' + urlencode({'library': library['id'], 'query': query}))
                if result['items']:
                    groups.append({'library': library, 'items': result['items'][:6], 'has_more': len(result['items']) > 6 or result['has_more']})
            return self.reply({'configured': True, 'groups': groups, 'partial': False, 'stale': False, 'updated_at': '2026-10-11T12:00:00Z'})
        if path == '/api/public/libraries':
            return self.reply({'configured': True, 'libraries': [{**row, 'cover_url': '/api/public/library/cover/' + row['id']} for row in LIBRARIES], 'stale': False, 'updated_at': '2026-10-11T12:00:00Z'})
        if path == '/api/admin/emby-library':
            return self.reply({'enabled': True, 'token_configured': True, 'base_url': 'https://preview.example', 'library_ids': ['preview-movies', 'preview-series']})
        if path == '/api/admin/emby-library/libraries':
            return self.reply({'libraries': LIBRARIES})
        if path == '/api/admin/nextfind-catalog':
            return self.reply({'enabled': True, 'token_configured': True, 'base_url': 'https://preview.example'})
        if path.startswith('/api/public/library/poster/preview-'):
            try:
                index = int(path.rsplit('-', 1)[-1])
                asset = ['home-cinema.jpg', 'home-library.jpg', 'home-devices.jpg'][index % 3]
                return self.reply((ROOT / 'portal/static' / asset).read_bytes(), content_type='image/jpeg')
            except (ValueError, OSError):
                return self.reply(b'', 404, 'image/jpeg')
        if path.startswith('/api/public/library/cover/preview-'):
            name = path.rsplit('/', 1)[-1]
            asset = {'preview-movies': 'home-cinema.jpg', 'preview-series': 'home-library.jpg', 'preview-documentaries': 'home-devices.jpg'}.get(name)
            return self.reply((ROOT / 'portal/static' / asset).read_bytes(), content_type='image/jpeg') if asset else self.reply(b'', 404, 'image/jpeg')
        if path == '/api/auth/status':
            return self.reply({'authenticated': True, 'initialized': True})
        if path == '/api/admin/overview':
            return self.reply({'products': PRODUCTS, 'orders': ORDERS, 'settings': {'xianyu_account_id': 'preview'}, 'connections': {}})
        if path.endswith('/xiaohongshu/status'):
            return self.reply(status())
        if path.endswith('/xiaohongshu/conversations'):
            return self.reply({'account_id': 'preview_account', 'conversations': [{**c, 'active': c['id'] == SELECTED} for c in CONVERSATIONS]})
        if path.endswith('/xiaohongshu/messages'):
            return self.reply(messages())
        fixture = Path('/tmp/mediadock-preview-api.json')
        if fixture.exists():
            data = json.loads(fixture.read_text())
            if path in data:
                return self.reply(data[path])
        return self.reply({'error': {'message': '演示中未配置此只读接口'}}, 404)

    def do_POST(self):
        global SELECTED
        if self.path.endswith('/xiaohongshu/conversations/select'):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            SELECTED = body['conversation_id']
            return self.reply(messages())
        return self.reply({'error': {'message': '隔离演示环境禁止发送消息和业务写入'}}, 409)


if __name__ == '__main__':
    print('Synthetic preview listening on http://127.0.0.1:59191', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 59191), Handler).serve_forever()
