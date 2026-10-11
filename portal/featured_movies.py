"""Daily TMDB film metadata cache; visitor requests never contact the provider."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import datetime as dt
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import tempfile
import time
import unicodedata

from secrets_store import default_secret_store


SOURCE = 'TMDB 本周热门电影'
SOURCE_URL = 'https://www.themoviedb.org/trending/movie/week'
API_HOST = 'api.themoviedb.org'
API_PATH = '/3/trending/movie/week?language=zh-CN'
POSTER_PREFIX = 'https://image.tmdb.org/t/p/w500'
BACKDROP_PREFIX = 'https://image.tmdb.org/t/p/w1280'
MOVIE_PREFIX = 'https://www.themoviedb.org/movie/'
SECRET_NAME = 'featured-tmdb'
MAX_MOVIES = 6
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CACHE_BYTES = 32 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_PREFIX = '/api/public/featured-images/'
IMAGE_NAME = re.compile(r'[a-f0-9]{64}\.(?:jpg|png|webp)\Z')
TIMEOUT_SECONDS = 12
STALE_SECONDS = 36 * 3600
ERROR_MESSAGE = '热门影片更新失败，已保留上次成功的内容；请检查 TMDB 凭据或稍后重试。'


class FeaturedMoviesError(ValueError):
    """Safe for administrator display; never includes tokens or upstream bodies."""


def image_type(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_IMAGE_BYTES:
        raise FeaturedMoviesError(ERROR_MESSAGE)
    if body.startswith(b'\xff\xd8\xff'):
        return 'jpg', 'image/jpeg'
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png', 'image/png'
    if body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        return 'webp', 'image/webp'
    raise FeaturedMoviesError(ERROR_MESSAGE)


def fetch_image(url):
    if not isinstance(url, str) or not re.fullmatch(r'https://image\.tmdb\.org/t/p/(?:w500|w1280)/[A-Za-z0-9_-]{1,160}\.(?:jpg|png|webp)', url):
        raise FeaturedMoviesError(ERROR_MESSAGE)
    connection = http.client.HTTPSConnection('image.tmdb.org', timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        connection.request('GET', url.removeprefix('https://image.tmdb.org'), headers={'Accept': 'image/jpeg,image/png,image/webp', 'Accept-Encoding': 'identity'})
        sock = connection.sock
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise FeaturedMoviesError(ERROR_MESSAGE)
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_IMAGE_BYTES - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise FeaturedMoviesError(ERROR_MESSAGE)
            chunks.append(chunk)
        body = b''.join(chunks)
        _, mime = image_type(body)
        if response.getheader('Content-Type', '').split(';')[0].strip().lower() != mime:
            raise FeaturedMoviesError(ERROR_MESSAGE)
        return body
    except Exception:
        raise FeaturedMoviesError(ERROR_MESSAGE) from None
    finally:
        connection.close()


def _token(value):
    if not isinstance(value, str):
        raise FeaturedMoviesError('TMDB 凭据格式不正确')
    value = value.strip()
    if value.startswith('Bearer '):
        value = value[7:]
    if not re.fullmatch(r'[A-Za-z0-9._~-]{20,4096}', value):
        raise FeaturedMoviesError('请填写 TMDB API Read Access Token')
    return value


def _text(value, maximum=200):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(char in value for char in '<>')
            or any(unicodedata.category(char).startswith('C') for char in value)):
        return None
    return value.strip()


def _release_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _movie(row, *, cached=False):
    if not isinstance(row, dict) or type(row.get('id')) is not int or not 0 < row['id'] < 10 ** 12:
        return None
    if row.get('adult') is True or row.get('media_type', 'movie') != 'movie':
        return None
    title = _text(row.get('title'))
    path = row.get('poster_path')
    if cached:
        poster = row.get('poster_url')
        if not isinstance(poster, str) or not poster.startswith(POSTER_PREFIX):
            return None
        path = poster[len(POSTER_PREFIX):]
        if row.get('url') != MOVIE_PREFIX + str(row['id']):
            return None
    if not title or not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_-]{1,160}\.(?:jpg|png|webp)', path):
        return None
    movie = {'id': row['id'], 'title': title, 'poster_url': POSTER_PREFIX + path,
             'url': MOVIE_PREFIX + str(row['id'])}
    # Backdrops are optional: older cached feeds and movies without one still
    # retain their validated poster. Never accept provider-controlled origins.
    backdrop_path = row.get('backdrop_path')
    if cached:
        backdrop = row.get('backdrop_url')
        backdrop_path = (backdrop[len(BACKDROP_PREFIX):]
                         if isinstance(backdrop, str) and backdrop.startswith(BACKDROP_PREFIX)
                         else None)
    if (isinstance(backdrop_path, str)
            and re.fullmatch(r'/[A-Za-z0-9_-]{1,160}\.(?:jpg|png|webp)', backdrop_path)):
        movie['backdrop_url'] = BACKDROP_PREFIX + backdrop_path
    release = _release_date(row.get('release_date'))
    if release:
        movie['release_date'] = release
    return movie


def parse_movies(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list) or len(payload['results']) > 100:
        raise FeaturedMoviesError(ERROR_MESSAGE)
    movies, ids = [], set()
    for row in payload['results']:
        movie = _movie(row)
        if movie and movie['id'] not in ids:
            ids.add(movie['id'])
            movies.append(movie)
            if len(movies) == MAX_MOVIES:
                break
    if not movies:
        raise FeaturedMoviesError(ERROR_MESSAGE)
    return movies


def fetch_movies(token):
    """Fixed HTTPS origin, no redirect following, bounded body and timeout."""
    token = _token(token)
    connection = http.client.HTTPSConnection(API_HOST, timeout=TIMEOUT_SECONDS,
                                              context=ssl.create_default_context())
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        connection.request('GET', API_PATH, headers={'Authorization': 'Bearer ' + token,
                           'Accept': 'application/json', 'User-Agent': 'MediaDock-Featured-Movies/1.0'})
        response = connection.getresponse()
        if response.status != 200:
            raise FeaturedMoviesError(ERROR_MESSAGE)
        chunks, total = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise FeaturedMoviesError(ERROR_MESSAGE)
            chunks.append(chunk)
        return parse_movies(json.loads(b''.join(chunks).decode('utf-8')))
    except Exception:
        raise FeaturedMoviesError(ERROR_MESSAGE) from None
    finally:
        connection.close()


def _read_json(path, limit):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            return {}
        result = json.loads(data)
        return result if isinstance(result, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _timestamp(value, now):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= now + 5:
        return None
    try:
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError, OSError):
        return None


class FeaturedMovies:
    def __init__(self, data_dir, secret_store, *, now=time.time, fetch=fetch_movies, fetch_art=fetch_image):
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / 'featured-movies-config.json'
        self.cache_path = self.data_dir / 'featured-movies.json'
        self.vault, self.now, self.fetch = secret_store, now, fetch
        self.fetch_art = fetch_art
        self.image_dir = self.data_dir / 'featured-images'

    def image(self, name):
        if not isinstance(name, str) or not IMAGE_NAME.fullmatch(name):
            raise FeaturedMoviesError('图片不存在')
        try:
            fd = os.open(self.image_dir / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                body = source.read(MAX_IMAGE_BYTES + 1)
            ext, mime = image_type(body)
            if name != hashlib.sha256(body).hexdigest() + '.' + ext:
                raise ValueError()
            return body, mime
        except Exception:
            raise FeaturedMoviesError('图片不存在') from None

    def _store_image(self, url):
        body = self.fetch_art(url)
        ext, _ = image_type(body)
        name = hashlib.sha256(body).hexdigest() + '.' + ext
        fd, temporary = tempfile.mkstemp(dir=self.image_dir, prefix='.download-')
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(body); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.image_dir / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return url, name

    @contextmanager
    def _lock(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.data_dir / 'featured-movies.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _enabled(self):
        return _read_json(self.config_path, 1024).get('enabled') is True

    def _cached(self, data=None):
        data = _read_json(self.cache_path, MAX_CACHE_BYTES) if data is None else data
        now = self.now()
        updated = _timestamp(data.get('updated_at'), now)
        rows = data.get('movies')
        if not updated or not isinstance(rows, list) or len(rows) > MAX_MOVIES:
            return {'movies': [], 'updated_at': None, 'stale': True}
        movies, ids = [], set()
        for row in rows:
            movie = _movie(row, cached=True)
            if movie and movie['id'] not in ids:
                ids.add(movie['id'])
                movies.append(movie)
        return {'movies': movies, 'updated_at': updated if movies else None,
                'stale': not movies or now - data['updated_at'] > STALE_SECONDS}

    def public(self):
        snapshot = _read_json(self.cache_path, MAX_CACHE_BYTES)
        data = self._cached(snapshot) if self._enabled() else {'movies': [], 'updated_at': None, 'stale': True}
        assets = snapshot.get('assets', {})
        if not isinstance(assets, dict):
            assets = {}
        movies = []
        for movie in data['movies']:
            local = dict(movie)
            for key in ('poster_url', 'backdrop_url'):
                if key not in local:
                    continue
                name = assets.get(local[key])
                if isinstance(name, str) and IMAGE_NAME.fullmatch(name) and (self.image_dir / name).is_file():
                    local[key] = IMAGE_PREFIX + name
                else:
                    local.pop(key)
            if 'poster_url' in local:
                movies.append(local)
        data = {**data, 'movies': movies}
        return {**data, 'source': SOURCE, 'source_url': SOURCE_URL}

    def config(self):
        cached = self._cached()
        return {'enabled': self._enabled(), 'token_configured': bool(self.vault.get(SECRET_NAME)),
                'last_success': cached['updated_at'], 'movie_count': len(cached['movies'])}

    def save_config(self, body):
        if (not isinstance(body, dict) or not body or not set(body) <= {'enabled', 'token', 'clear_token'}
                or ('enabled' in body and type(body['enabled']) is not bool)
                or ('clear_token' in body and type(body['clear_token']) is not bool)
                or ('token' in body and not isinstance(body['token'], str))):
            raise FeaturedMoviesError('热门影片配置格式不正确')
        token = _token(body['token']) if body.get('token', '').strip() else None
        clear = body.get('clear_token', False)
        if clear and token:
            raise FeaturedMoviesError('不能同时设置和清除 TMDB 凭据')
        with self._lock():
            enabled = body.get('enabled', self._enabled())
            if clear:
                enabled = False
            available = token or (None if clear else self.vault.get(SECRET_NAME))
            if enabled and not available:
                raise FeaturedMoviesError('请先配置 TMDB API Read Access Token，再启用每日更新')
            if token:
                self.vault.set(SECRET_NAME, token)
            if clear:
                self.vault.delete(SECRET_NAME)
            _atomic_json(self.config_path, {'enabled': enabled})
        return self.config()

    def refresh(self):
        with self._lock():
            token = self.vault.get(SECRET_NAME)
            if not self._enabled() or not token:
                return False
            try:
                rows = self.fetch(_token(token))
                if not isinstance(rows, list) or len(rows) > MAX_MOVIES:
                    raise FeaturedMoviesError(ERROR_MESSAGE)
                movies = [_movie(row, cached=True) for row in rows]
                if not movies or any(movie is None for movie in movies) or len({movie['id'] for movie in movies}) != len(movies):
                    raise FeaturedMoviesError(ERROR_MESSAGE)
                self.image_dir.mkdir(mode=0o700, exist_ok=True)
                urls = {movie[key] for movie in movies for key in ('poster_url', 'backdrop_url') if key in movie}
                with ThreadPoolExecutor(max_workers=3) as pool:
                    assets = dict(pool.map(self._store_image, sorted(urls)))
                _atomic_json(self.cache_path, {'updated_at': self.now(), 'movies': movies, 'assets': assets})
                # Keep prior images for open pages; cleanup is limited to our content-addressed files.
                for path in self.image_dir.iterdir():
                    try:
                        if IMAGE_NAME.fullmatch(path.name) and path.name not in assets.values() and path.stat().st_mtime < self.now() - 7 * 86400:
                            path.unlink()
                    except OSError:
                        pass
            except Exception:
                raise FeaturedMoviesError(ERROR_MESSAGE) from None
        return True


def main():
    os.umask(0o077)
    data_dir = os.environ.get('MEDIADOCK_DATA_DIR') or '/var/lib/mediadock'
    try:
        service = FeaturedMovies(data_dir, default_secret_store())
        refreshed = service.refresh()
        print('Featured movies refreshed.' if refreshed else 'Featured movies disabled or not configured; skipped.')
        return 0
    except Exception:
        print('Featured movies could not be refreshed; existing cache preserved.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
