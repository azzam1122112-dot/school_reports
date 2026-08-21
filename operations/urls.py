from django.urls import path

from . import views

app_name = "operations"

urlpatterns = [
    path("auth/login/", views.login, name="login"),
    path("auth/logout/", views.logout, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("deployment/status/", views.deployment_status, name="deployment-status"),
    path("deployment/deploy/", views.trigger_deployment, name="trigger-deployment"),
    path("projects/<int:project_id>/", views.project_detail, name="project-detail"),
    path("projects/<int:project_id>/actions/", views.create_action, name="create-action"),
    path("devices/", views.device_registration, name="device-registration"),
    path("incidents/<int:incident_id>/acknowledge/", views.acknowledge_incident, name="acknowledge-incident"),
]
