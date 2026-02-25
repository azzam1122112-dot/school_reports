# reports/api_urls.py
# -*- coding: utf-8 -*-
"""
DRF router for the reports API (v1).
Mount at  /api/v1/  in the root URL config.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    NotificationViewSet,
    ReportTypeViewSet,
    ReportViewSet,
    SchoolViewSet,
    TicketViewSet,
)

router = DefaultRouter()
router.register("schools", SchoolViewSet, basename="school")
router.register("reports", ReportViewSet, basename="report")
router.register("report-types", ReportTypeViewSet, basename="reporttype")
router.register("tickets", TicketViewSet, basename="ticket")
router.register("notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    path("", include(router.urls)),
]
