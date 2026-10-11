"""Container timer for cached movie artwork and private network samples."""
import datetime as dt
import os
import signal
import threading
import time
from zoneinfo import ZoneInfo

from featured_movies import FeaturedMovies
from network_monitor import NetworkMonitor
from secrets_store import default_secret_store


def refresh_due(config, now):
    if not config.get('enabled') or not config.get('token_configured'):
        return False
    latest = now.replace(hour=6, minute=10, second=0, microsecond=0)
    if now < latest:
        latest -= dt.timedelta(days=1)
    return not config.get('last_success') or config['last_success'] < latest.timestamp()


def movie_tick(service, now):
    try:
        if refresh_due(service.config(), now):
            service.refresh()
    except Exception:
        # Upstream errors may contain credentials or private addresses.
        print('Movie refresh failed; keeping prior cache and retrying in five minutes.', flush=True)


def main():
    os.umask(0o077)
    data_dir = os.environ['MEDIADOCK_DATA_DIR']
    timezone = ZoneInfo(os.environ.get('MEDIADOCK_TIMEZONE', 'Asia/Shanghai'))
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    movies = FeaturedMovies(data_dir, default_secret_store())
    monitor = NetworkMonitor(data_dir)

    def movie_loop():
        while not stop.is_set():
            movie_tick(movies, dt.datetime.now(timezone))
            now = dt.datetime.now(timezone)
            boundary = now.replace(hour=6, minute=10, second=0, microsecond=0)
            if boundary <= now:
                boundary += dt.timedelta(days=1)
            stop.wait(min(300, (boundary - now).total_seconds()))

    threading.Thread(target=movie_loop, daemon=True, name='movie-refresh').start()
    while not stop.is_set():
        started = time.monotonic()
        try:
            monitor.collect()
        except Exception:
            print('Network sampling failed; retrying next minute.', flush=True)
        stop.wait(max(1, 60 - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
