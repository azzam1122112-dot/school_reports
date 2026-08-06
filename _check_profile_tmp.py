import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings
from django.test import Client
from django.test.utils import setup_test_environment, teardown_test_environment
from django.test.runner import DiscoverRunner

settings.ALLOWED_HOSTS = ["*"]
setup_test_environment()
runner = DiscoverRunner(verbosity=0, interactive=False)
old_config = runner.setup_databases()

from reports.models import Teacher, WebAuthnCredential
from reports.webauthn import credential_hash

user = Teacher.objects.create_user(phone="0555000999", name="فاحص", password="pass-1234")
cid = b"profile-render-credential"
WebAuthnCredential.objects.create(
    teacher=user,
    credential_id=cid,
    credential_id_hash=credential_hash(cid),
    public_key_cose=b"key",
    device_name="آيفون · Safari",
    transports=["internal"],
)

c = Client()
c.force_login(user)
r = c.get("/profile/")
print("profile status", r.status_code)
html = r.content.decode("utf-8")
for probe in ["passkeyDeviceList", "data-passkey-remove", "آيفون · Safari", "/profile/passkey/", "لم يُستخدم للدخول بعد"]:
    print(("OK  " if probe in html else "MISS"), probe)

login_html = Client().get("/login/").content.decode("utf-8")
for probe in ["username webauthn", "isConditionalMediationAvailable", "passkeyDivider", "mediation: 'conditional'"]:
    print(("OK  " if probe in login_html else "MISS"), "login:", probe)

runner.teardown_databases(old_config)
teardown_test_environment()
