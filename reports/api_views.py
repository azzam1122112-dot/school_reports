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

from django.db import models as db_models

from .models import (
    Department,
    DepartmentMembership,
    Notification,
    NotificationRecipient,
    Report,
    ReportType,
    School,
    SchoolMembership,
    Ticket,
)
from .permissions import (
    is_platform_admin,
    is_school_manager,
    platform_allowed_schools_qs,
    restrict_queryset_for_user,
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
    """Return the school selected in the user's session.

    Reuses ``request.active_school`` set by middleware when available,
    avoiding a redundant DB query per API call.
    """
    cached = getattr(request, "active_school", None)
    if cached is not None:
        return cached
    try:
        sid = request.session.get("active_school_id")
        if sid:
            school = School.objects.filter(pk=sid, is_active=True).first()
            request.active_school = school
            return school
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
        if getattr(self.request.user, "is_superuser", False):
            return School.objects.filter(is_active=True)
        if is_platform_admin(self.request.user):
            return platform_allowed_schools_qs(self.request.user)
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
        qs = Report.objects.filter(school=school).select_related("category", "teacher")
        qs = restrict_queryset_for_user(qs, self.request.user, school)
        return qs.order_by("-created_at")


class ReportTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """Active report types for the school."""
    serializer_class = ReportTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        qs = ReportType.objects.filter(
            db_models.Q(school=school) | db_models.Q(school__isnull=True)
        )
        if hasattr(ReportType, "is_active"):
            qs = qs.filter(is_active=True)
        return qs


class TicketViewSet(viewsets.ReadOnlyModelViewSet):
    """Tickets scoped to the active school."""
    serializer_class = TicketListSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        qs = Ticket.objects.filter(
            school=school, is_platform=False,
        ).select_related("creator", "assignee", "department")

        user = self.request.user
        if getattr(user, "is_superuser", False) or is_platform_admin(user) or is_school_manager(user, active_school=school):
            return qs.order_by("-created_at")

        dept_ids = []
        try:
            dept_ids = list(
                DepartmentMembership.objects.filter(
                    teacher=user,
                    department__school=school,
                    department__is_active=True,
                ).values_list("department_id", flat=True)
            )
        except Exception:
            dept_ids = []

        return (
            qs.filter(
                db_models.Q(creator=user)
                | db_models.Q(assignee=user)
                | db_models.Q(recipients=user)
                | db_models.Q(department_id__in=dept_ids)
            )
            .distinct()
            .order_by("-created_at")
        )


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Notifications for the authenticated user in the active school."""
    serializer_class = NotificationListSerializer
    permission_classes = [permissions.IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        school = _active_school(self.request)
        return Notification.objects.filter(
            recipients__teacher=self.request.user,
            school=school,
        ).select_related("school", "created_by").order_by("-created_at").distinct()

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        school = _active_school(request)
        count = NotificationRecipient.objects.filter(
            teacher=request.user,
            is_read=False,
            notification__school=school,
        ).count()
        return Response({"count": count})
