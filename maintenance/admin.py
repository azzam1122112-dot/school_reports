from django.contrib import admin

from .models import SchoolYearResetJob


@admin.register(SchoolYearResetJob)
class SchoolYearResetJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "created_by", "created_at", "started_at", "finished_at", "delete_files")
    list_filter = ("status", "delete_files", "include_reports", "include_tickets", "include_achievements", "include_notifications")
    search_fields = ("created_by__name", "created_by__phone", "error_message")
    filter_horizontal = ("schools",)
    readonly_fields = ("created_at", "started_at", "finished_at", "dry_run_summary", "execution_summary", "file_manifest")
