# reports/serializers.py
# -*- coding: utf-8 -*-
"""
DRF serializers for the reports app.
Provides a read-only API foundation for mobile/third-party integration.
"""
from __future__ import annotations

from rest_framework import serializers

from .models import (
    Department,
    Notification,
    Report,
    ReportType,
    School,
    Teacher,
    Ticket,
)


class SchoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ["id", "name", "code", "gender", "is_active"]
        read_only_fields = fields


class TeacherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = ["id", "name", "phone", "is_active"]
        read_only_fields = fields


class ReportTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportType
        fields = ["id", "name", "is_active"]
        read_only_fields = fields


class ReportListSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(read_only=True)
    category_name = serializers.CharField(source="category.name", default=None, read_only=True)

    class Meta:
        model = Report
        fields = [
            "id", "title", "report_date", "teacher_name",
            "category_name", "created_at",
        ]
        read_only_fields = fields


class TicketListSerializer(serializers.ModelSerializer):
    creator_name = serializers.CharField(source="creator.name", default="", read_only=True)

    class Meta:
        model = Ticket
        fields = [
            "id", "title", "status", "priority",
            "creator_name", "created_at",
        ]
        read_only_fields = fields


class NotificationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id", "title", "message", "is_important",
            "created_at",
        ]
        read_only_fields = fields
