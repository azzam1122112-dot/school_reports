from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import CustomerComplaint


class CustomerComplaintForm(forms.ModelForm):
    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "tabindex": "-1",
                "aria-hidden": "true",
            }
        ),
    )

    class Meta:
        model = CustomerComplaint
        fields = ("name", "email", "phone", "order_reference", "subject", "message")
        # The public complaint form is part of the bilingual layer, so its
        # labels are restated here rather than inherited from the model's
        # Arabic verbose names — which the admin and internal screens still use.
        labels = {
            "name": _("الاسم"),
            "email": _("البريد الإلكتروني"),
            "phone": _("رقم الجوال"),
            "order_reference": _("مرجع الطلب أو الاشتراك"),
            "subject": _("موضوع الشكوى"),
            "message": _("تفاصيل الشكوى"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr"}),
            "phone": forms.TextInput(
                attrs={"autocomplete": "tel", "inputmode": "tel", "dir": "ltr"}
            ),
            "order_reference": forms.TextInput(attrs={"dir": "ltr"}),
            "message": forms.Textarea(attrs={"rows": 6}),
        }

    def clean_website(self):
        value = (self.cleaned_data.get("website") or "").strip()
        if value:
            raise forms.ValidationError(_("تعذّر إرسال النموذج."))
        return value

class CustomerComplaintUpdateForm(forms.ModelForm):
    class Meta:
        model = CustomerComplaint
        fields = ("status", "internal_notes")
        widgets = {
            "status": forms.Select(attrs={"class": "complaint-select"}),
            "internal_notes": forms.Textarea(
                attrs={
                    "class": "complaint-textarea",
                    "rows": 7,
                    "placeholder": "سجّل ما تم اتخاذه، ونتيجة التواصل، والخطوة التالية...",
                }
            ),
        }
