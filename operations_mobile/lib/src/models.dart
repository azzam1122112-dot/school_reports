enum HealthStatus { healthy, degraded, down, maintenance, unknown }

HealthStatus healthStatusFrom(String? value) => switch (value) {
  'healthy' => HealthStatus.healthy,
  'degraded' => HealthStatus.degraded,
  'down' => HealthStatus.down,
  'maintenance' => HealthStatus.maintenance,
  _ => HealthStatus.unknown,
};

double? _double(dynamic value) =>
    value == null ? null : double.tryParse('$value');
DateTime? _date(dynamic value) =>
    value == null ? null : DateTime.tryParse('$value')?.toLocal();

class ServiceInfo {
  const ServiceInfo({
    required this.id,
    required this.name,
    required this.key,
    required this.kindLabel,
    required this.status,
    required this.restartAllowed,
  });
  final int id;
  final String name;
  final String key;
  final String kindLabel;
  final HealthStatus status;
  final bool restartAllowed;

  factory ServiceInfo.fromJson(Map<String, dynamic> json) => ServiceInfo(
    id: json['id'] as int,
    name: '${json['name'] ?? ''}',
    key: '${json['service_key'] ?? ''}',
    kindLabel: '${json['kind_label'] ?? ''}',
    status: healthStatusFrom('${json['status'] ?? ''}'),
    restartAllowed: json['restart_allowed'] == true,
  );
}

class ProjectInfo {
  const ProjectInfo({
    required this.id,
    required this.name,
    required this.slug,
    required this.baseUrl,
    required this.status,
    required this.failures,
    required this.alertsEnabled,
    required this.services,
    this.latencyMs,
    this.lastCheckedAt,
  });
  final int id;
  final String name;
  final String slug;
  final String baseUrl;
  final HealthStatus status;
  final int? latencyMs;
  final int failures;
  final bool alertsEnabled;
  final DateTime? lastCheckedAt;
  final List<ServiceInfo> services;

  factory ProjectInfo.fromJson(Map<String, dynamic> json) => ProjectInfo(
    id: json['id'] as int,
    name: '${json['name'] ?? ''}',
    slug: '${json['slug'] ?? ''}',
    baseUrl: '${json['base_url'] ?? ''}',
    status: healthStatusFrom('${json['status'] ?? ''}'),
    latencyMs: (json['last_latency_ms'] as num?)?.toInt(),
    failures: (json['consecutive_failures'] as num?)?.toInt() ?? 0,
    alertsEnabled: json['alerts_enabled'] == true,
    lastCheckedAt: _date(json['last_checked_at']),
    services: (json['services'] as List? ?? const [])
        .map(
          (item) =>
              ServiceInfo.fromJson(Map<String, dynamic>.from(item as Map)),
        )
        .toList(),
  );
}

class ServerInfo {
  const ServerInfo({
    required this.id,
    required this.name,
    required this.provider,
    required this.status,
    required this.projects,
    this.publicIp,
    this.serverType,
    this.cpu,
    this.memory,
    this.disk,
    this.lastCheckedAt,
  });
  final int id;
  final String name;
  final String provider;
  final String? publicIp;
  final String? serverType;
  final HealthStatus status;
  final double? cpu;
  final double? memory;
  final double? disk;
  final DateTime? lastCheckedAt;
  final List<ProjectInfo> projects;

  factory ServerInfo.fromJson(Map<String, dynamic> json) => ServerInfo(
    id: json['id'] as int,
    name: '${json['name'] ?? ''}',
    provider: '${json['provider'] ?? ''}',
    publicIp: json['public_ip']?.toString(),
    serverType: json['server_type']?.toString(),
    status: healthStatusFrom('${json['status'] ?? ''}'),
    cpu: _double(json['cpu_percent']),
    memory: _double(json['memory_percent']),
    disk: _double(json['disk_percent']),
    lastCheckedAt: _date(json['last_checked_at']),
    projects: (json['projects'] as List? ?? const [])
        .map(
          (item) =>
              ProjectInfo.fromJson(Map<String, dynamic>.from(item as Map)),
        )
        .toList(),
  );
}

class IncidentInfo {
  const IncidentInfo({
    required this.id,
    required this.title,
    required this.message,
    required this.severity,
    required this.status,
    required this.projectName,
    required this.openedAt,
  });
  final int id;
  final String title;
  final String message;
  final String severity;
  final String status;
  final String projectName;
  final DateTime? openedAt;

  factory IncidentInfo.fromJson(Map<String, dynamic> json) => IncidentInfo(
    id: json['id'] as int,
    title: '${json['title'] ?? ''}',
    message: '${json['message'] ?? ''}',
    severity: '${json['severity'] ?? 'warning'}',
    status: '${json['status'] ?? 'open'}',
    projectName: '${json['project_name'] ?? ''}',
    openedAt: _date(json['opened_at']),
  );
}

class DashboardData {
  const DashboardData({
    required this.servers,
    required this.incidents,
    required this.serverCount,
    required this.projectCount,
    required this.healthyProjectCount,
    required this.openIncidentCount,
    required this.generatedAt,
  });
  final List<ServerInfo> servers;
  final List<IncidentInfo> incidents;
  final int serverCount;
  final int projectCount;
  final int healthyProjectCount;
  final int openIncidentCount;
  final DateTime? generatedAt;

  factory DashboardData.fromJson(Map<String, dynamic> json) {
    final summary = Map<String, dynamic>.from(
      json['summary'] as Map? ?? const {},
    );
    return DashboardData(
      servers: (json['servers'] as List? ?? const [])
          .map(
            (item) =>
                ServerInfo.fromJson(Map<String, dynamic>.from(item as Map)),
          )
          .toList(),
      incidents: (json['incidents'] as List? ?? const [])
          .map(
            (item) =>
                IncidentInfo.fromJson(Map<String, dynamic>.from(item as Map)),
          )
          .toList(),
      serverCount: (summary['servers'] as num?)?.toInt() ?? 0,
      projectCount: (summary['projects'] as num?)?.toInt() ?? 0,
      healthyProjectCount: (summary['healthy_projects'] as num?)?.toInt() ?? 0,
      openIncidentCount: (summary['open_incidents'] as num?)?.toInt() ?? 0,
      generatedAt: _date(json['generated_at']),
    );
  }
}

class DeploymentInfo {
  const DeploymentInfo({
    required this.projectId,
    required this.projectSlug,
    required this.projectName,
    required this.repository,
    required this.branch,
    required this.workflow,
    required this.configured,
    required this.deploymentEnabled,
    required this.latestSha,
    required this.latestShortSha,
    required this.latestMessage,
    required this.deployedSha,
    required this.deployedShortSha,
    required this.deployedImage,
    required this.upToDate,
    required this.repositoryAhead,
    required this.workflowStatus,
    required this.workflowConclusion,
    required this.workflowUrl,
    required this.actionRequired,
    required this.canDeploy,
    this.workflowRunId,
  });

  final int projectId;
  final String projectSlug;
  final String projectName;
  final String repository;
  final String branch;
  final String workflow;
  final bool configured;
  final bool deploymentEnabled;
  final String latestSha;
  final String latestShortSha;
  final String latestMessage;
  final String deployedSha;
  final String deployedShortSha;
  final String deployedImage;
  final bool upToDate;
  final bool repositoryAhead;
  final String workflowStatus;
  final String workflowConclusion;
  final String workflowUrl;
  final String actionRequired;
  final bool canDeploy;
  final int? workflowRunId;

  factory DeploymentInfo.fromJson(Map<String, dynamic> json) =>
      DeploymentInfo(
        projectId: (json['project_id'] as num?)?.toInt() ?? 0,
        projectSlug: '${json['project_slug'] ?? ''}',
        projectName: '${json['project_name'] ?? ''}',
        repository: '${json['repository'] ?? ''}',
        branch: '${json['branch'] ?? ''}',
        workflow: '${json['workflow'] ?? ''}',
        configured: json['configured'] == true,
        deploymentEnabled: json['deployment_enabled'] == true,
        latestSha: '${json['latest_sha'] ?? ''}',
        latestShortSha: '${json['latest_short_sha'] ?? ''}',
        latestMessage: '${json['latest_message'] ?? ''}',
        deployedSha: '${json['deployed_sha'] ?? ''}',
        deployedShortSha: '${json['deployed_short_sha'] ?? ''}',
        deployedImage: '${json['deployed_image'] ?? ''}',
        upToDate: json['up_to_date'] == true,
        repositoryAhead: json['repository_ahead'] == true,
        workflowStatus: '${json['workflow_status'] ?? ''}',
        workflowConclusion: '${json['workflow_conclusion'] ?? ''}',
        workflowUrl: '${json['workflow_url'] ?? ''}',
        actionRequired: '${json['action_required'] ?? ''}',
        canDeploy: json['can_deploy'] == true,
        workflowRunId: (json['workflow_run_id'] as num?)?.toInt(),
      );
}

class DeploymentOverview {
  const DeploymentOverview({
    required this.deployments,
    required this.repositoryAheadCount,
    required this.canDeployCount,
  });

  final List<DeploymentInfo> deployments;
  final int repositoryAheadCount;
  final int canDeployCount;

  factory DeploymentOverview.fromJson(Map<String, dynamic> json) =>
      DeploymentOverview(
        deployments: (json['deployments'] as List? ?? const [])
            .map(
              (item) => DeploymentInfo.fromJson(
                Map<String, dynamic>.from(item as Map),
              ),
            )
            .toList(),
        repositoryAheadCount:
            (json['repository_ahead_count'] as num?)?.toInt() ?? 0,
        canDeployCount: (json['can_deploy_count'] as num?)?.toInt() ?? 0,
      );
}

class MetricPoint {
  const MetricPoint({this.cpu, this.memory, this.disk, this.capturedAt});
  final double? cpu;
  final double? memory;
  final double? disk;
  final DateTime? capturedAt;
  factory MetricPoint.fromJson(Map<String, dynamic> json) => MetricPoint(
    cpu: _double(json['cpu_percent']),
    memory: _double(json['memory_percent']),
    disk: _double(json['disk_percent']),
    capturedAt: _date(json['captured_at']),
  );
}

class CheckPoint {
  const CheckPoint({
    required this.ok,
    this.statusCode,
    this.latencyMs,
    this.errorCode = '',
    this.checkedAt,
  });
  final bool ok;
  final int? statusCode;
  final int? latencyMs;
  final String errorCode;
  final DateTime? checkedAt;
  factory CheckPoint.fromJson(Map<String, dynamic> json) => CheckPoint(
    ok: json['ok'] == true,
    statusCode: (json['status_code'] as num?)?.toInt(),
    latencyMs: (json['latency_ms'] as num?)?.toInt(),
    errorCode: '${json['error_code'] ?? ''}',
    checkedAt: _date(json['checked_at']),
  );
}

class ProjectDetails {
  const ProjectDetails({
    required this.project,
    required this.metrics,
    required this.checks,
  });
  final ProjectInfo project;
  final List<MetricPoint> metrics;
  final List<CheckPoint> checks;
  factory ProjectDetails.fromJson(Map<String, dynamic> json) => ProjectDetails(
    project: ProjectInfo.fromJson(json),
    metrics: (json['metrics'] as List? ?? const [])
        .map(
          (item) =>
              MetricPoint.fromJson(Map<String, dynamic>.from(item as Map)),
        )
        .toList(),
    checks: (json['checks'] as List? ?? const [])
        .map(
          (item) => CheckPoint.fromJson(Map<String, dynamic>.from(item as Map)),
        )
        .toList(),
  );
}
