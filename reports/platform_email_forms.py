from __future__ import annotations

import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import PlatformEmailConfiguration, School


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
    selected_schools = forms.ModelMultipleChoiceField(
        label="اختيار مدارس",
        queryset=School.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 8, "class": "mail-school-select"}),
    )
    to = forms.CharField(label="إلى", max_length=4000, required=False)
    cc = forms.CharField(label="نسخة", max_length=4000, required=False)
    bcc = forms.CharField(label="نسخة مخفية", max_length=4000, required=False)
    subject = forms.CharField(label="الموضوع", max_length=500)
    body = forms.CharField(label="نص الرسالة", max_length=50000, widget=forms.Textarea)
    attachments = MultipleFileField(
        label="المرفقات",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_schools"].queryset = (
            School.objects.filter(is_active=True)
            .exclude(email="")
            .order_by("name", "id")
        )
        self.fields["selected_schools"].label_from_instance = (
            lambda school: f"{school.name} — {school.code} — {school.email}"
        )

    def clean_to(self):
        return _email_list(self.cleaned_data.get("to", ""))

    def clean_cc(self):
        return _email_list(self.cleaned_data.get("cc", ""))

    def clean_bcc(self):
        return _email_list(self.cleaned_data.get("bcc", ""))

    def clean_body(self):
        value = (self.cleaned_data.get("body") or "").strip()
        if not value:
            raise ValidationError("اكتب نص الرسالة.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        manual_recipients = cleaned_data.get("to") or []
        selected_schools = cleaned_data.get("selected_schools") or []
        school_recipients = []
        for school in selected_schools:
            address = (school.email or "").strip().lower()
            if address:
                validate_email(address)
                school_recipients.append(address)

        recipients = []
        for address in [*school_recipients, *manual_recipients]:
            if address not in recipients:
                recipients.append(address)

        if not recipients:
            raise ValidationError("اختر مدرسة لديها بريد إلكتروني أو اكتب بريد مستلم واحدًا على الأقل.")
        if len(recipients) > 50:
            raise ValidationError("الحد الأعلى 50 عنوانًا في الرسالة الواحدة.")
        cleaned_data["to"] = recipients
        return cleaned_data


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
