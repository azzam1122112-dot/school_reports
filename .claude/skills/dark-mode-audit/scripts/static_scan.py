# -*- coding: utf-8 -*-
"""Light surfaces / dark ink with no dark-theme counterpart.

Fast triage only — it reads CSS, so it cannot see a cascade. Run `sweep.cjs`
for the truth. Its value is catching a template that ships a light `<style>`
block with no dark rules at all, before the page is ever opened.

Known limits (each cost me a wrong conclusion):
  * `rgba(255,255,255,.96)` is an opaque surface, not decoration. Only alpha
    below ~0.3 is ignored here.
  * a class is treated as covered when ANY dark rule names it — even if that
    rule loses on specificity or order.
  * `@media print` blocks are intentionally light; they are not skipped, so
    verify before believing a hit.

    python .claude/skills/dark-mode-audit/scripts/static_scan.py
"""
import re
import pathlib
import sys

if hasattr(sys.stdout, 'reconfigure'):      # Windows consoles default to cp1256
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LIGHT_SURFACE = re.compile(
    r'background(?:-color)?\s*:\s*[^;}]*?'
    r'(#fff\b|#ffffff\b|#fefefe|#fdfdfd|#fcfcfc|#fbfbfb|#fafafa|#f9fafb|#f8fafc'
    r'|#f8f9fa|#f7fafc|#f6f8fa|#f5f7fa|#f4f6f8|#f3f4f6|#f1f5f9|#f0fdf4|#eef2f7'
    r'|#e9ecef|#e5e7eb|#e2e8f0|#eee\b|#ddd\b|\bwhite\b|rgba\(\s*255\s*,\s*255\s*,\s*255)',
    re.I)
DARK_TEXT = re.compile(
    r'(?<!-)\bcolor\s*:\s*(#0f172a|#111827|#1e293b|#1f2937|#0b1220|#0c1b16|#222\b'
    r'|#333\b|#334155|#343a40|#212529|#2c3e50|#000\b|#000000\b|\bblack\b)', re.I)
SKIP = re.compile(
    r'(pdf/|_print\.html|print\.html|archive_record_pdf|user_guide_pdf|emails/)')

CSS_FILES = [
    'dark-mode.css', 'app.css', 'royal-theme.css', 'mobile-professional.css',
    'landing.css', 'user-guide.css', 'legal.css', 'mansour-assistant.css',
    'platform-admin-dashboard.css', 'platform-complaints.css',
    'flexible-pricing.css', 'consumption-panel.css', 'storage-alert.css',
    'report-ai-improver.css', 'circulars-official.css', 'pwa-install.css',
]


def rules(css):
    """Yield (selector, body), descending into @media / @supports."""
    css = re.sub(r'/\*.*?\*/', ' ', css, flags=re.S)
    i, n = 0, len(css)
    while i < n:
        b = css.find('{', i)
        if b < 0:
            return
        sel = css[i:b].strip()
        depth, j = 1, b + 1
        while j < n and depth:
            if css[j] == '{':
                depth += 1
            elif css[j] == '}':
                depth -= 1
            j += 1
        body = css[b + 1:j - 1]
        if sel.startswith('@'):
            if sel.split()[0] in ('@media', '@supports', '@layer', '@container'):
                yield from rules(body)
        else:
            yield sel, body
        i = j


def classes_of(sel):
    return set(re.findall(r'\.([A-Za-z0-9_-]+)', sel))


def dark_covered(css):
    covered = set()
    for sel, _ in rules(css):
        for part in sel.split(','):
            if re.search(r'data-theme\s*=\s*["\']?dark', part):
                covered |= classes_of(part)
                covered |= set(re.findall(
                    r'(?:^|\s)(body|input|select|textarea|table|thead|tbody|td|th'
                    r'|hr|code|pre|dialog)\b', part))
    return covered


def problems_in(css, covered):
    out = []
    for sel, body in rules(css):
        if re.search(r'data-theme\s*=\s*["\']?dark', sel):
            continue
        clean = re.sub(r'rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*(?:0?\.[0-2]\d*)\s*\)',
                       'OVERLAY', body, flags=re.I)
        if re.search(r'background-clip\s*:\s*text', clean):
            clean = re.sub(r'background[^;]*;', '', clean)
        surf, txt = LIGHT_SURFACE.search(clean), DARK_TEXT.search(clean)
        if not surf and not txt:
            continue
        cls = classes_of(sel)
        if cls and cls & covered:
            continue
        kind = []
        if surf:
            kind.append('surface=' + surf.group(1))
        if txt:
            kind.append('ink=' + txt.group(1))
        out.append((sel.strip()[:74], ', '.join(kind)))
    return out


ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
global_dark = set()
for name in CSS_FILES:
    p = ROOT / 'static/css' / name
    if p.exists():
        global_dark |= dark_covered(p.read_text(encoding='utf-8', errors='replace'))

total = 0
print('-- templates --')
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
        probs = problems_in(css, global_dark | dark_covered(css))
        if probs:
            total += len(probs)
            print('\n=== %s  (%d)' % (p.as_posix(), len(probs)))
            for s, k in probs[:25]:
                print('   %-76s %s' % (s, k))

print('\n-- stylesheets --')
for name in CSS_FILES:
    p = ROOT / 'static/css' / name
    if not p.exists():
        continue
    css = p.read_text(encoding='utf-8', errors='replace')
    probs = problems_in(css, global_dark | dark_covered(css))
    if probs:
        total += len(probs)
        print('\n=== %s  (%d)' % (name, len(probs)))
        for s, k in probs[:25]:
            print('   %-76s %s' % (s, k))

print('\n\nCANDIDATES (verify each):', total)
