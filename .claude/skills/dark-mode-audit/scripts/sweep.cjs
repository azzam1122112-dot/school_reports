#!/usr/bin/env node
/* Runtime dark-mode sweep.
 *
 * Logs in, walks every route, and reports for each page:
 *   BRIGHT    a computed-light surface larger than 10000px²
 *   CONTRAST  text below 4.5:1 (3:1 for large text), with its effective backdrop
 *
 * Why a browser: the final colour comes from a cascade of rules and variables,
 * not from one written value. Reading CSS cannot see a scoped token shadowing a
 * global dark one, nor a later rule winning on specificity.
 *
 * Elements painted with a background-image (gradient) are skipped for contrast:
 * we cannot know the pixel behind the text, and guessing produces false alarms.
 *
 * Usage:
 *   node sweep.cjs [--base URL] [--urls path.json] [--phone P] [--password P]
 *                  [--chrome PATH] [--shots DIR]
 */
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BASE = arg('base', 'http://127.0.0.1:8811');
const URLS_FILE = arg('urls', path.join(process.cwd(), 'tmp', 'urls.json'));
const PHONE = arg('phone', '500000999');
const PASSWORD = arg('password', 'darkmode-check-1');
const SHOTS = arg('shots', null);
const CHROME = arg('chrome', process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe');

const AUDIT = () => {
  const srgb = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const L = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
  const parse = s => {
    const m = String(s).match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
    return m ? { c: [+m[1], +m[2], +m[3]], a: m[4] === undefined ? 1 : parseFloat(m[4]) } : null;
  };
  // nearest ancestor with an opaque background; null when a gradient hides it
  const effBg = el => {
    let n = el;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const p = parse(cs.backgroundColor);
      if (p && p.a >= 0.5) return p.c;
      n = n.parentElement;
    }
    const rootBg = parse(getComputedStyle(document.body).backgroundColor);
    return rootBg && rootBg.a >= 0.5 ? rootBg.c : [11, 23, 18];
  };
  const ratio = (a, b) => {
    const l1 = L(a), l2 = L(b);
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (hi + 0.05) / (lo + 0.05);
  };

  const bright = [], lowContrast = [];
  const seenB = new Set(), seenC = new Set();
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const cls = (el.className || '').toString().trim().split(/\s+/).slice(0, 3).join('.');
    const key = el.tagName + '|' + cls;

    const bg = parse(cs.backgroundColor);
    const hasImg = cs.backgroundImage && cs.backgroundImage !== 'none';
    if (!hasImg && bg && bg.a >= 0.5 && L(bg.c) > 0.55 && r.width * r.height > 10000) {
      if (!seenB.has(key)) {
        seenB.add(key);
        bright.push({ k: key, bg: cs.backgroundColor, w: Math.round(r.width), h: Math.round(r.height) });
      }
    }

    const txt = (el.textContent || '').trim();
    const ownText = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
    if (ownText && txt.length > 1) {
      const fg = parse(cs.color);
      if (fg && fg.a >= 0.5) {
        const eb = effBg(el);
        if (!eb) continue;
        const cr = ratio(fg.c, eb);
        const size = parseFloat(cs.fontSize);
        const big = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700);
        if (cr < (big ? 3 : 4.5) && !seenC.has(key)) {
          seenC.add(key);
          lowContrast.push({
            k: key, cr: +cr.toFixed(2), color: cs.color,
            on: 'rgb(' + eb.join(',') + ')', t: txt.slice(0, 34),
          });
        }
      }
    }
  }
  return { bright, lowContrast };
};

(async () => {
  if (!fs.existsSync(CHROME)) {
    console.error('Chrome not found at', CHROME, '\nPass --chrome PATH or set CHROME_PATH.');
    process.exit(2);
  }
  const urls = JSON.parse(fs.readFileSync(URLS_FILE, 'utf8'));
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--lang=ar'],
    defaultViewport: { width: 1440, height: 1000 },
  });
  const page = await browser.newPage();
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }]);

  const host = new URL(BASE).hostname;
  await page.goto(BASE + '/login/', { waitUntil: 'networkidle2' });
  await page.setCookie({ name: 'theme', value: 'dark', domain: host, path: '/' });
  await page.evaluate(() => localStorage.setItem('theme', 'dark'));
  await page.type('input[name="phone"]', PHONE).catch(() => {});
  await page.type('input[name="password"]', PASSWORD).catch(() => {});
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => {}),
    page.click('button[type="submit"], input[type="submit"]').catch(() => {}),
  ]);
  if (!/login/.test(page.url())) console.log('logged in ->', page.url());
  else console.log('WARNING: still on /login/ — check credentials; most pages will 302.');

  // the passkey prompt covers every page until dismissed once
  await page.evaluate(() => {
    const b = document.getElementById('passkeyPromptLater');
    if (b) b.click();
  }).catch(() => {});
  await new Promise(r => setTimeout(r, 1200));

  let pagesWithIssues = 0, i = 0;
  for (const [name, url] of urls) {
    i++;
    try {
      const resp = await page.goto(BASE + url, { waitUntil: 'networkidle2', timeout: 25000 });
      const st = resp ? resp.status() : 0;
      if (st >= 400) { console.log(`SKIP ${name} (${st})`); continue; }
      const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
      if (theme !== 'dark') console.log(`WARNING ${name}: data-theme=${theme}`);
      const { bright, lowContrast } = await page.evaluate(AUDIT);
      if (SHOTS) {
        await page.screenshot({
          path: path.join(SHOTS, `${String(i).padStart(3, '0')}-${name.replace(/[:\\/]/g, '_')}.png`),
        });
      }
      if (bright.length || lowContrast.length) {
        pagesWithIssues++;
        console.log(`\n### ${name}  ${url}`);
        for (const b of bright) console.log(`   BRIGHT  ${b.k}  ${b.bg}  ${b.w}x${b.h}`);
        for (const c of lowContrast) {
          console.log(`   CONTRAST ${c.cr}  ${c.k}  ${c.color} on ${c.on}  "${c.t}"`);
        }
      }
    } catch (e) {
      console.log(`FAIL ${name}: ${e.message}`);
    }
  }
  console.log(`\n\nPAGES WITH ISSUES: ${pagesWithIssues} / ${urls.length}`);
  await browser.close();
})();
