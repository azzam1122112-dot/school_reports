# النشر التلقائي إلى Hetzner

كل دفعة (`push`) إلى `main` تمرّ بهذا المسار:

```
push → اختبارات CI (ruff, bandit, pip_audit, check --deploy, tests)
     → بناء صورة Docker ورفعها إلى ghcr.io بوسم الـ commit SHA
     → SSH إلى الخادم ونسخ ملفات compose
     → docker compose up -d
        └── خدمة migrate: migrate --noinput + collectstatic  ← الترحيلات تلقائية
        └── web/worker/beat تنتظر نجاح migrate قبل الإقلاع
     → انتظار healthcheck الخاص بـ web قبل اعتبار النشر ناجحاً
```

إذا فشل أي اختبار، لا يحدث نشر. وإذا فشل الترحيل، لا يقلع `web` أصلاً — فلا يُقدَّم تطبيق على قاعدة بيانات نصف مُرحَّلة.

الملفات: [.github/workflows/ci.yml](../.github/workflows/ci.yml) (وظيفة `deploy`)
و [deploy/hetzner/remote_deploy.sh](../deploy/hetzner/remote_deploy.sh) (ما يُنفَّذ على الخادم).

## الإعداد لمرة واحدة

### 1. مفتاح SSH للنشر

على جهازك:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/school_reports_deploy -N ""
ssh-copy-id -i ~/.ssh/school_reports_deploy.pub <USER>@<SERVER_IP>
ssh-keyscan -H <SERVER_IP>            # المخرجات تذهب إلى DEPLOY_KNOWN_HOSTS
```

### 2. أسرار GitHub

`Settings → Secrets and variables → Actions → New repository secret`

| الاسم | القيمة |
| --- | --- |
| `DEPLOY_SSH_HOST` | عنوان IP الخادم في Hetzner |
| `DEPLOY_SSH_USER` | مستخدم SSH (مثلاً `deploy` أو `root`) |
| `DEPLOY_SSH_KEY` | محتوى `~/.ssh/school_reports_deploy` كاملاً (المفتاح الخاص) |
| `DEPLOY_KNOWN_HOSTS` | مخرجات `ssh-keyscan -H <SERVER_IP>` |
| `DEPLOY_PATH` | اختياري — مسار المشروع على الخادم، الافتراضي `/opt/school_reports` |
| `DEPLOY_SSH_PORT` | اختياري — الافتراضي `22` |

`GITHUB_TOKEN` يُوفَّر تلقائياً ولا يحتاج إعداداً.

### 3. متطلبات الخادم

- Docker مع إضافة `compose` v2.
- `deploy/hetzner/env.production` موجود داخل `DEPLOY_PATH` — **لا يُرفع أبداً من CI** وهو
  الملف الوحيد الذي لا يلمسه النشر. أنشئه مرة واحدة من `env.production.example`.
- مستخدم SSH عضو في مجموعة `docker`.

للتحقق من الحالة الحالية قبل أول نشر:

```bash
ssh <USER>@<SERVER_IP> \
  'docker compose version; docker ps --format "{{.Names}}\t{{.Image}}"; \
   ls -d /opt/school_reports /srv/school_reports ~/school_reports 2>/dev/null'
```

المسار الذي يظهر فيه المشروع هو ما تضعه في `DEPLOY_PATH`.

## التراجع (rollback)

الصور موسومة بـ commit SHA، فالرجوع لأي إصدار سابق:

```bash
cd /opt/school_reports
APP_IMAGE=ghcr.io/<owner>/school_reports:<older-sha> bash deploy/hetzner/remote_deploy.sh
```

## نشر يدوي / خارج المسار

نفس السكربت يعمل يدوياً على الخادم بأي وسم صورة، وهو نفسه المستخدَم من CI —
فلا يوجد مسار نشر ثانٍ يمكن أن ينحرف عن الأول.
