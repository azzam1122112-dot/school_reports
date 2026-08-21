from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class DeploymentIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentState:
    repository: str
    branch: str
    workflow: str
    configured: bool
    latest_sha: str
    latest_message: str
    deployed_sha: str
    deployed_image: str
    up_to_date: bool
    repository_ahead: bool
    workflow_status: str
    workflow_conclusion: str
    workflow_url: str
    workflow_run_id: int | None
    action_required: str
    generated_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "branch": self.branch,
            "workflow": self.workflow,
            "configured": self.configured,
            "latest_sha": self.latest_sha,
            "latest_short_sha": self.latest_sha[:12],
            "latest_message": self.latest_message,
            "deployed_sha": self.deployed_sha,
            "deployed_short_sha": self.deployed_sha[:12],
            "deployed_image": self.deployed_image,
            "up_to_date": self.up_to_date,
            "repository_ahead": self.repository_ahead,
            "workflow_status": self.workflow_status,
            "workflow_conclusion": self.workflow_conclusion,
            "workflow_url": self.workflow_url,
            "workflow_run_id": self.workflow_run_id,
            "action_required": self.action_required,
            "generated_note": self.generated_note,
            "can_deploy": self.configured and self.repository_ahead and self.workflow_status != "in_progress",
        }


class GitHubDeploymentClient:
    api_base = "https://api.github.com"

    def __init__(self) -> None:
        self.repository = str(getattr(settings, "OPERATIONS_GITHUB_REPOSITORY", "") or "").strip()
        self.branch = str(getattr(settings, "OPERATIONS_GITHUB_BRANCH", "main") or "main").strip()
        self.workflow = str(getattr(settings, "OPERATIONS_GITHUB_WORKFLOW", "ci.yml") or "ci.yml").strip()
        self.token = str(getattr(settings, "OPERATIONS_GITHUB_TOKEN", "") or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.repository and self.branch and self.workflow and self.token)

    def headers(self, *, require_token: bool = False) -> dict[str, str]:
        if require_token and not self.token:
            raise DeploymentIntegrationError("يلزم ضبط OPERATIONS_GITHUB_TOKEN قبل تشغيل النشر من التطبيق.")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "TawtheeqOperations/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, *, require_token: bool = False, **kwargs) -> requests.Response:
        url = f"{self.api_base}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=self.headers(require_token=require_token),
                timeout=(4, 12),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise DeploymentIntegrationError("تعذر الاتصال بـ GitHub الآن.") from exc
        if response.status_code >= 400:
            logger.warning("GitHub deployment API failed status=%s path=%s body=%s", response.status_code, path, response.text[:500])
            if response.status_code in (401, 403):
                raise DeploymentIntegrationError("صلاحية GitHub غير كافية لقراءة أو تشغيل النشر.")
            if response.status_code == 404:
                raise DeploymentIntegrationError("تعذر العثور على المستودع أو ملف Workflow في GitHub.")
            raise DeploymentIntegrationError("رفض GitHub الطلب. راجع إعدادات المستودع والـ workflow.")
        return response

    def latest_commit(self) -> tuple[str, str]:
        data = self._request("GET", f"/repos/{self.repository}/commits/{self.branch}").json()
        sha = str(data.get("sha") or "")
        message = str(((data.get("commit") or {}).get("message") or "")).splitlines()[0][:160]
        return sha, message

    def latest_workflow_run(self) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{self.workflow}/runs",
            params={"branch": self.branch, "per_page": 1},
        )
        runs = response.json().get("workflow_runs") or []
        return dict(runs[0]) if runs else {}

    def deployment_state(self) -> DeploymentState:
        latest_sha = ""
        latest_message = ""
        workflow_run: dict[str, Any] = {}
        note = ""
        try:
            latest_sha, latest_message = self.latest_commit()
            workflow_run = self.latest_workflow_run()
        except DeploymentIntegrationError as exc:
            note = str(exc)

        deployed_sha = str(getattr(settings, "RELEASE_SHA", "") or "").strip()
        deployed_image = str(getattr(settings, "RELEASE_IMAGE", "") or "").strip()
        deployed_known = bool(deployed_sha and deployed_sha != "unknown")
        up_to_date = bool(latest_sha and deployed_known and latest_sha == deployed_sha)
        repository_ahead = bool(latest_sha and deployed_known and latest_sha != deployed_sha)
        action_required = "لا يلزم إجراء."
        if not self.configured:
            action_required = "اضبط OPERATIONS_GITHUB_TOKEN بصلاحية Actions قبل النشر من التطبيق."
        elif not deployed_known:
            action_required = "أعد نشر نسخة واحدة لتثبيت RELEASE_SHA داخل الحاوية ثم أعد المقارنة."
        elif repository_ahead:
            action_required = "اضغط زر النشر لتشغيل GitHub Actions ومتابعة التنفيذ حتى فحص الصحة النهائي."
        elif (workflow_run.get("conclusion") or "") == "failure":
            action_required = "راجع آخر فشل في GitHub Actions قبل إعادة النشر."

        return DeploymentState(
            repository=self.repository,
            branch=self.branch,
            workflow=self.workflow,
            configured=self.configured,
            latest_sha=latest_sha,
            latest_message=latest_message,
            deployed_sha=deployed_sha,
            deployed_image=deployed_image,
            up_to_date=up_to_date,
            repository_ahead=repository_ahead,
            workflow_status=str(workflow_run.get("status") or "unknown"),
            workflow_conclusion=str(workflow_run.get("conclusion") or ""),
            workflow_url=str(workflow_run.get("html_url") or ""),
            workflow_run_id=workflow_run.get("id"),
            action_required=action_required,
            generated_note=note,
        )

    def trigger_deploy(self) -> None:
        self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{self.workflow}/dispatches",
            require_token=True,
            json={"ref": self.branch},
        )
