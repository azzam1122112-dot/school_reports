from __future__ import annotations

from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("year-reset/", views.school_year_reset, name="school_year_reset"),
]
