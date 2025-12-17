import os
import django
from django.urls import reverse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

try:
    print("SchoolSubscription:", reverse("admin:reports_schoolsubscription_changelist"))
    print("SubscriptionPlan:", reverse("admin:reports_subscriptionplan_changelist"))
    print("Payment:", reverse("admin:reports_payment_changelist"))
    print("Ticket:", reverse("admin:reports_ticket_changelist"))
except Exception as e:
    print("Error:", e)
