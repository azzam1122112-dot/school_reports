# -*- coding: utf-8 -*-
"""Find page-scoped tokens that SHADOW a global dark value.

`dark-mode.css` flips e.g. `--card-bg` under `html[data-theme="dark"]`. When a
template re-declares `--card-bg` inside `.some-scope` with a light literal, that
scoped declaration wins for everything under `.some-scope` — so the dark value
never applies, no matter that it exists.

This is invisible to a "is the token covered?" check: the token IS covered, just
not where it matters. It is how a whole page (circulars_sent.html) stayed white
through an audit that reported the project clean.

    python .claude/skills/dark-mode-audit/scripts/shadow_scan.py
"""
import re
import pathlib
import sys

if hasattr(sys.stdout, 'reconfigure'):      # Windows consoles default to cp1256
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LIGHT = re.compile(
    r'^\s*(?:#fff|#ffffff|#fefefe|#fdfdfd|#fafafa|#f9fafb|#f8fafc|#f8f9fa|#f7fafc'
    r'|#f5f7fa|#f4f7f3|#f3f4f6|#f1f5f9|#edf2f7|#eef2f7|#e9ecef|#e5e7eb|#e2e8f0'
    r'|#e3ebe5|#dce7e0|white'
    r'|#0f172a|#111827|#1a202c|#10251f|#1e293b|#1f2937|#4a5568|#334155|#333|#222'
    r'|#000|black)\s*$', re.I)

SKIP = re.compile(
    r'(pdf/|_print\.html|print\.html|archive_record_pdf|user_guide_pdf|emails/)')

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
dark_css = (ROOT / 'static/css/dark-mode.css').read_text(encoding='utf-8')
GLOBAL_DARK = set()
for m in re.finditer(r'html\[data-theme="dark"\]\s*\{(.*?)\}', dark_css, re.S):
    GLOBAL_DARK |= set(re.findall(r'(--[A-Za-z0-9_-]+)\s*:', m.group(1)))

total = 0
for base in ['reports/templates', 'maintenance/templates']:
    d = ROOT / base
    if not d.exists():
        continue
    for p in sorted(d.rglob('*.html')):
        if SKIP.search(p.as_posix()):
            continue
        css = '\n'.join(re.findall(
            r'<style[^>]*>(.*?)</style>',
            p.read_text(encoding='utf-8', errors='replace'), re.S))
        if not css:
            continue
        css = re.sub(r'/\*.*?\*/', ' ', css, flags=re.S)

        local_dark = set()
        for m in re.finditer(r'\[data-theme\s*=\s*["\']?dark["\']?\]([^{]*)\{(.*?)\}',
                             css, re.S):
            local_dark |= set(re.findall(r'(--[A-Za-z0-9_-]+)\s*:', m.group(2)))

        bad = []
        for m in re.finditer(r'([^{}@]+)\{([^{}]*)\}', css):
            sel, body = m.group(1).strip(), m.group(2)
            if 'data-theme' in sel or '--' not in body:
                continue
            scoped = sel.startswith('.') or sel.startswith('#')   # not :root
            for name, val in re.findall(r'(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);', body):
                if not LIGHT.match(val) or name in local_dark:
                    continue
                if name in GLOBAL_DARK and scoped:
                    bad.append('%-22s %-12s shadows global dark  in %s'
                               % (name, val.strip(), sel[:44]))
        if bad:
            total += len(bad)
            print('\n=== %s  (%d)' % (p.as_posix(), len(bad)))
            for b in bad:
                print('    ' + b)

print('\n\nSHADOWED TOKENS:', total)
