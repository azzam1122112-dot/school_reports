from __future__ import annotations

from django import forms

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
            raise forms.ValidationError("تعذّر إرسال النموذج.")
        return value

