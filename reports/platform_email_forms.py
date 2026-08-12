from __future__ import annotations

import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import PlatformEmailConfiguration


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)] if data else []


def _email_list(value: str, *, required: bool = False) -> list[str]:
    values = [item.strip().lower() for item in re.split(r"[,;\n]+", value or "") if item.strip()]
    if required and not values:
        raise ValidationError("أدخل بريد مستلم واحدًا على الأقل.")
    if len(values) > 50:
        raise ValidationError("الحد الأعلى 50 عنوانًا في الرسالة الواحدة.")
    unique = []
    for address in values:
        validate_email(address)
        if address not in unique:
            unique.append(address)
    return unique


class PlatformEmailComposeForm(forms.Form):
    to = forms.CharField(label="إلى", max_length=4000)
    cc = forms.CharField(label="نسخة", max_length=4000, required=False)
    bcc = forms.CharField(label="نسخة مخفية", max_length=4000, required=False)
    subject = forms.CharField(label="الموضوع", max_length=500)
    body = forms.CharField(label="نص الرسالة", max_length=50000, widget=forms.Textarea)
    attachments = MultipleFileField(
        label="المرفقات",
        required=False,
    )

    def clean_to(self):
        return _email_list(self.cleaned_data.get("to", ""), required=True)

    def clean_cc(self):
        return _email_list(self.cleaned_data.get("cc", ""))

    def clean_bcc(self):
        return _email_list(self.cleaned_data.get("bcc", ""))

    def clean_body(self):
        value = (self.cleaned_data.get("body") or "").strip()
        if not value:
            raise ValidationError("اكتب نص الرسالة.")
        return value


class PlatformEmailReplyForm(forms.Form):
    body = forms.CharField(label="الرد", max_length=50000, widget=forms.Textarea)

    def clean_body(self):
        value = (self.cleaned_data.get("body") or "").strip()
        if not value:
            raise ValidationError("اكتب نص الرد.")
        return value


class PlatformEmailConfigurationForm(forms.ModelForm):
    class Meta:
        model = PlatformEmailConfiguration
        fields = (
            "sender_name",
            "sender_email",
            "inbound_email",
            "reply_to_email",
            "is_sending_enabled",
            "is_receiving_enabled",
            "retention_days",
        )
        widgets = {
            "retention_days": forms.NumberInput(attrs={"min": 30, "max": 3650}),
        }

    def clean_retention_days(self):
        value = int(self.cleaned_data.get("retention_days") or 0)
        if not 30 <= value <= 3650:
            raise ValidationError("مدة الاحتفاظ يجب أن تكون بين 30 و3650 يومًا.")
        return value
