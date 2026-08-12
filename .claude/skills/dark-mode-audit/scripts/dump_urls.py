# -*- coding: utf-8 -*-
"""Emit every GET-able page route as JSON [[name, url], ...] for sweep.cjs.

Detail routes are reversed with id=1, which the seed script guarantees exists.
Skipped: destructive verbs, downloads, print/PDF views (intentionally light),
and DRF's browsable API (not our templates — it is light by design and would
bury the real findings).

    python .claude/skills/dark-mode-audit/scripts/dump_urls.py > tmp/urls.json
"""
import json
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django  # noqa: E402
django.setup()

from django.urls import get_resolver, reverse, NoReverseMatch  # noqa: E402

SKIP_NAME = ('logout', 'export', 'download', 'delete', 'pdf', 'print', 'api',
             'webhook', 'dismiss', 'toggle', 'execute', 'search', 'stream',
             'signatures', 'moyasar', 'callback', 'manifest')
SKIP_PATH = ('/api/',)


def walk(resolver, prefix=''):
    for p in resolver.url_patterns:
        if hasattr(p, 'url_patterns'):
            ns = (p.namespace + ':') if getattr(p, 'namespace', None) else ''
            yield from walk(p, prefix + ns)
        elif getattr(p, 'name', None):
            yield prefix + p.name


out, seen = [], set()
for name in walk(get_resolver()):
    if name in seen or any(s in name.lower() for s in SKIP_NAME):
        continue
    seen.add(name)
    for args in ((), (1,)):
        try:
            url = reverse(name, args=args)
        except NoReverseMatch:
            continue
        if not any(url.startswith(s) for s in SKIP_PATH):
            out.append([name, url])
        break

out.sort(key=lambda r: r[0])
print(json.dumps(out, ensure_ascii=False))
