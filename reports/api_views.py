# reports/api_views.py
# -*- coding: utf-8 -*-
"""
DRF ViewSets – read-only API for mobile/third-party consumers.
All endpoints are tenant-isolated: they only return data for the
active school stored in the user's session.
"""
from __future__ import annotations

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    Department,
    Notification,
    NotificationRecipient,
    Report,
    ReportType,
    School,
    SchoolMembership,
    Ticket,
)
from .serializers import (
    NotificationListSerializer,
    ReportListSerializer,
    ReportTypeSerializer,
    SchoolSerializer,
    TicketListSerializer,
)


# ── Helpers ──────────────────────────────────────────────────────────
def _active_school(request) -> School | None:
    """Return the school selected in the user's session."""
    try:
        sid = request.session.get("active_school_id")
        if sid:
            return School.objects.filter(pk=sid, is_active=True).first()
    except Exception:
        pass
    return None


class IsTenantMember(permissions.BasePermission):
    """Deny if the user has no active school in session."""

    def has_permission(self, request, view):
        return _active_school(request) is not None


# ── ViewSets ─────────────────────────────────────────────────────────
class SchoolViewSet(viewsets.ReadOnlyModelViewSet):
    """List schools the authenticated user belongs to."""
    serializer_class = SchoolSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return School.objects.filter(
            memberships__teacher=self.request.user,
            memberships__is_active=True,
            is_active=True,
        ).distinct()


class ReportViewSet(viewsets.ReadOnlyModelViewSet):
    """Reports scoped to the active school."""
    serializer_class = ReportListSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        return Report.objects.filter(school=school).select_related("category").order_by("-created_at")


class ReportTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """Active report types for the school."""
    serializer_class = ReportTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        qs = ReportType.objects.all()
        if hasattr(ReportType, "school"):
            qs = qs.filter(school=school)
        if hasattr(ReportType, "is_active"):
            qs = qs.filter(is_active=True)
        return qs


class TicketViewSet(viewsets.ReadOnlyModelViewSet):
    """Tickets scoped to the active school."""
    serializer_class = TicketListSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        return Ticket.objects.filter(
            school=school, is_platform=False,
        ).select_related("creator").order_by("-created_at")


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Notifications for the authenticated user in the active school."""
    serializer_class = NotificationListSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        return Notification.objects.filter(
            recipients__teacher=self.request.user,
            school=school,
        ).order_by("-created_at").distinct()

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        school = _active_school(request)
        count = NotificationRecipient.objects.filter(
            teacher=request.user,
            is_read=False,
            notification__school=school,
        ).count()
        return Response({"count": count})
