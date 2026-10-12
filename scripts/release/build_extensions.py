"""Compile private application modules; emit only extensions and CLI loaders."""
from pathlib import Path
import os
import sys

from Cython.Build import cythonize
from Cython.Compiler import Options
from setuptools import Extension, setup

ROOT = Path.cwd()
SOURCE = ROOT / 'portal'
MODULES = (
    'app', 'appearance', 'core', 'emby_library', 'emby_stats', 'featured_movies',
    'network_monitor', 'nextemby', 'nextfind_catalog', 'purchase_reviews',
    'secrets_store', 'subscription_requests', 'xhs_automation', 'xianyu_proxy',
    'xiaohongshu', 'scheduler', 'save_secret',
)
WRAPPERS = {
    'scheduler.py': 'from _mediadock_scheduler import main\nif __name__ == "__main__":\n    main()\n',
    'save_secret.py': 'import _mediadock_save_secret\n',
}
found = {p.stem for p in SOURCE.glob('*.py') if not p.name.startswith('test_')}
if found != set(MODULES):
    raise SystemExit('Release module allowlist is out of date: ' + repr(sorted(found ^ set(MODULES))))
Options.docstrings = False
extensions = [Extension('_mediadock_' + name if name in {'scheduler', 'save_secret'} else name,
                        [str(SOURCE / (name + '.py'))]) for name in MODULES]
setup(
    name='mediadock-release',
    ext_modules=cythonize(
        extensions, build_dir=str(ROOT / 'generated'),
        nthreads=max(1, min(2, os.cpu_count() or 1)),
        compiler_directives={
            'language_level': 3,
            'annotation_typing': False,
            'binding': True,
            'infer_types': False,
            'emit_code_comments': False,
            'embedsignature': False,
        },
    ),
)
if '--build-lib' not in sys.argv:
    raise SystemExit('An explicit --build-lib destination is required')
destination = Path(sys.argv[sys.argv.index('--build-lib') + 1])
for name, content in WRAPPERS.items():
    (destination / name).write_text(content, encoding='utf-8')
