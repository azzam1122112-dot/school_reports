import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart' show DateFormat;

import '../api_client.dart';
import '../models.dart';
import '../state.dart';
import '../widgets/status_widgets.dart';
import 'project_screen.dart';

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dashboard = ref.watch(dashboardProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'مركز العمليات',
              style: TextStyle(fontWeight: FontWeight.w900, fontSize: 20),
            ),
            Text(
              'school-reports-prod',
              style: TextStyle(fontSize: 12, color: Color(0xFF677381)),
            ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'تحديث الحالة',
            onPressed: () => ref.read(dashboardProvider.notifier).refresh(),
            icon: const Icon(Icons.refresh),
          ),
          PopupMenuButton<String>(
            tooltip: 'خيارات الحساب',
            onSelected: (value) {
              if (value == 'logout') {
                ref.read(sessionProvider.notifier).logout();
              }
            },
            itemBuilder: (_) => const [
              PopupMenuItem(
                value: 'logout',
                child: ListTile(
                  leading: Icon(Icons.logout),
                  title: Text('تسجيل الخروج'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ],
          ),
        ],
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1),
        ),
      ),
      body: dashboard.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => _ErrorView(
          message: error is ApiException
              ? error.message
              : 'تعذر تحميل حالة الخادم.',
          onRetry: () => ref.read(dashboardProvider.notifier).refresh(),
        ),
        data: (data) => RefreshIndicator(
          onRefresh: () => ref.read(dashboardProvider.notifier).refresh(),
          child: LayoutBuilder(
            builder: (context, constraints) {
              final wide = constraints.maxWidth >= 900;
              final content = <Widget>[
                _Overview(data: data),
                const SizedBox(height: 16),
                const _DeploymentPanel(),
                const SizedBox(height: 16),
                if (data.servers.isEmpty)
                  const Card(
                    child: EmptyState(
                      icon: Icons.dns_outlined,
                      title: 'لا يوجد خادم مسجل',
                      message: 'أضف جرد الخادم من لوحة Django.',
                    ),
                  )
                else
                  ...data.servers.map(
                    (server) => Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: _ServerPanel(server: server),
                    ),
                  ),
              ];
              final incidents = _IncidentsPanel(data: data);
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: EdgeInsets.all(wide ? 24 : 16),
                children: [
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1380),
                    child: wide
                        ? Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Expanded(
                                flex: 7,
                                child: Column(children: content),
                              ),
                              const SizedBox(width: 18),
                              Expanded(flex: 3, child: incidents),
                            ],
                          )
                        : Column(children: [...content, incidents]),
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}

class _Overview extends StatelessWidget {
  const _Overview({required this.data});
  final DashboardData data;
  @override
  Widget build(BuildContext context) {
    final allHealthy =
        data.projectCount > 0 &&
        data.healthyProjectCount == data.projectCount &&
        data.openIncidentCount == 0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: allHealthy
                ? const Color(0xFFEAF7EF)
                : const Color(0xFFFFF5E0),
            border: Border.all(
              color: allHealthy
                  ? const Color(0xFFB8DFC6)
                  : const Color(0xFFF0D18D),
            ),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Row(
            children: [
              Icon(
                allHealthy
                    ? Icons.check_circle_outline
                    : Icons.warning_amber_rounded,
                color: allHealthy
                    ? const Color(0xFF138A4B)
                    : const Color(0xFFA66B00),
                size: 32,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      allHealthy
                          ? 'جميع المشاريع تعمل بصورة طبيعية'
                          : 'توجد حالة تحتاج إلى المتابعة',
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                    if (data.generatedAt != null)
                      Text(
                        'آخر مزامنة ${DateFormat('HH:mm:ss').format(data.generatedAt!)}',
                        style: const TextStyle(color: Color(0xFF677381)),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            Expanded(
              child: _SummaryItem(
                label: 'المشاريع',
                value: '${data.projectCount}',
                icon: Icons.apps_outlined,
                color: const Color(0xFF356AA0),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _SummaryItem(
                label: 'السليمة',
                value: '${data.healthyProjectCount}',
                icon: Icons.health_and_safety_outlined,
                color: const Color(0xFF138A4B),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _SummaryItem(
                label: 'الحوادث',
                value: '${data.openIncidentCount}',
                icon: Icons.notification_important_outlined,
                color: data.openIncidentCount > 0
                    ? const Color(0xFFC5362F)
                    : const Color(0xFF7C8793),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _DeploymentPanel extends ConsumerWidget {
  const _DeploymentPanel();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final deployment = ref.watch(deploymentProvider);
    return deployment.when(
      loading: () => const Card(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Row(
            children: [
              SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              SizedBox(width: 12),
              Text('جاري فحص توافق المستودع مع الخادم...'),
            ],
          ),
        ),
      ),
      error: (error, _) => Card(
        child: EmptyState(
          icon: Icons.hub_outlined,
          title: 'تعذر فحص النشر',
          message: error is ApiException
              ? error.message
              : 'تعذر قراءة حالة GitHub والخادم.',
        ),
      ),
      data: (overview) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Icon(
                    overview.repositoryAheadCount > 0
                        ? Icons.system_update_alt
                        : Icons.verified_outlined,
                    color: overview.repositoryAheadCount > 0
                        ? const Color(0xFFA66B00)
                        : const Color(0xFF138A4B),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      overview.repositoryAheadCount > 0
                          ? 'يوجد ${overview.repositoryAheadCount} مشروع يحتاج مراجعة نشر'
                          : 'المشاريع المطابقة محدثة',
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                  ),
                  IconButton(
                    tooltip: 'تحديث حالة النشر',
                    onPressed: () => ref.invalidate(deploymentProvider),
                    icon: const Icon(Icons.refresh),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              if (overview.deployments.isEmpty)
                const EmptyState(
                  icon: Icons.hub_outlined,
                  title: 'لا توجد مستودعات مرتبطة',
                  message: 'اربط كل مشروع بمستودعه من إعدادات العمليات.',
                )
              else
                ...overview.deployments.map(
                  (info) => _DeploymentRow(
                    info: info,
                    workflowLabel: _workflowLabel(info),
                    onDeploy: info.canDeploy
                        ? () => _confirmDeploy(context, ref, info)
                        : null,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  static String _workflowLabel(DeploymentInfo info) {
    if (info.workflowStatus == 'in_progress') return 'قيد التنفيذ';
    if (info.workflowConclusion == 'success') return 'نجح';
    if (info.workflowConclusion == 'failure') return 'فشل';
    return info.workflowStatus.isEmpty ? 'غير معروف' : info.workflowStatus;
  }

  Future<void> _confirmDeploy(
    BuildContext context,
    WidgetRef ref,
    DeploymentInfo info,
  ) async {
    final controller = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('تأكيد النشر'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('سيتم تشغيل GitHub Actions لنشر الإصدار التالي:'),
            const SizedBox(height: 10),
            SelectableText(
              info.latestShortSha,
              textDirection: TextDirection.ltr,
              style: const TextStyle(fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: controller,
              textDirection: TextDirection.ltr,
              decoration: const InputDecoration(labelText: 'رقم الإصدار'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('إلغاء'),
          ),
          ElevatedButton.icon(
            onPressed: () => Navigator.pop(
              dialogContext,
              controller.text.trim() == info.latestShortSha,
            ),
            icon: const Icon(Icons.rocket_launch_outlined),
            label: const Text('تشغيل النشر'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (confirmed != true || !context.mounted) return;
    try {
      await ref
          .read(apiProvider)
          .triggerDeployment(
            projectId: info.projectId,
            confirmation: info.latestShortSha,
          );
      ref.invalidate(deploymentProvider);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تم تشغيل النشر. تابع الحالة من البطاقة.')),
        );
      }
    } on ApiException catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }
}

class _DeploymentRow extends StatelessWidget {
  const _DeploymentRow({
    required this.info,
    required this.workflowLabel,
    required this.onDeploy,
  });

  final DeploymentInfo info;
  final String workflowLabel;
  final VoidCallback? onDeploy;

  @override
  Widget build(BuildContext context) {
    final accent = info.repositoryAhead
        ? const Color(0xFFA66B00)
        : info.upToDate
        ? const Color(0xFF138A4B)
        : const Color(0xFF596674);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        border: Border.all(color: const Color(0xFFD6DEE6)),
        borderRadius: BorderRadius.circular(8),
        color: const Color(0xFFF8FAFC),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(
                info.repositoryAhead
                    ? Icons.outbound_outlined
                    : Icons.check_circle_outline,
                color: accent,
              ),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  info.projectName,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ),
              if (!info.deploymentEnabled)
                const Tooltip(
                  message: 'النشر اليدوي من التطبيق غير مفعل لهذا المشروع',
                  child: Icon(
                    Icons.lock_outline,
                    size: 19,
                    color: Color(0xFF7C8793),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _ReleaseChip(
                label: 'GitHub',
                value: info.latestShortSha.isEmpty
                    ? 'غير معروف'
                    : info.latestShortSha,
                icon: Icons.commit,
              ),
              _ReleaseChip(
                label: 'الخادم',
                value: info.deployedShortSha.isEmpty
                    ? 'غير معروف'
                    : info.deployedShortSha,
                icon: Icons.dns_outlined,
              ),
              _ReleaseChip(
                label: 'Actions',
                value: workflowLabel,
                icon: Icons.playlist_play,
              ),
            ],
          ),
          if (info.latestMessage.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              info.latestMessage,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: Color(0xFF596674),
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
          const SizedBox(height: 9),
          Text(
            info.actionRequired,
            style: const TextStyle(color: Color(0xFF596674)),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Text(
                  '${info.repository} · ${info.branch}',
                  textDirection: TextDirection.ltr,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Color(0xFF7C8793),
                    fontSize: 12,
                  ),
                ),
              ),
              ElevatedButton.icon(
                onPressed: onDeploy,
                icon: const Icon(Icons.rocket_launch_outlined),
                label: const Text('نشر'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ReleaseChip extends StatelessWidget {
  const _ReleaseChip({
    required this.label,
    required this.value,
    required this.icon,
  });
  final String label;
  final String value;
  final IconData icon;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
    decoration: BoxDecoration(
      border: Border.all(color: const Color(0xFFD6DEE6)),
      borderRadius: BorderRadius.circular(8),
      color: const Color(0xFFF8FAFC),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 18, color: const Color(0xFF356AA0)),
        const SizedBox(width: 7),
        Text(
          '$label: ',
          style: const TextStyle(color: Color(0xFF677381), fontSize: 12),
        ),
        Text(
          value,
          textDirection: TextDirection.ltr,
          style: const TextStyle(fontWeight: FontWeight.w900),
        ),
      ],
    ),
  );
}

class _SummaryItem extends StatelessWidget {
  const _SummaryItem({
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
  });
  final String label;
  final String value;
  final IconData icon;
  final Color color;
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
      child: Column(
        children: [
          Icon(icon, color: color),
          const SizedBox(height: 7),
          Text(
            value,
            style: const TextStyle(fontSize: 23, fontWeight: FontWeight.w900),
          ),
          Text(label, style: const TextStyle(color: Color(0xFF677381))),
        ],
      ),
    ),
  );
}

class _ServerPanel extends StatelessWidget {
  const _ServerPanel({required this.server});
  final ServerInfo server;
  @override
  Widget build(BuildContext context) => Card(
    child: Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              const Icon(
                Icons.dns_outlined,
                color: Color(0xFF006C35),
                size: 28,
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      server.name,
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                    Text(
                      [
                        server.serverType,
                        server.publicIp,
                      ].where((value) => value?.isNotEmpty == true).join(' · '),
                      textDirection: TextDirection.ltr,
                      textAlign: TextAlign.right,
                      style: const TextStyle(color: Color(0xFF677381)),
                    ),
                  ],
                ),
              ),
              StatusDot(server.status),
            ],
          ),
        ),
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsets.all(16),
          child: LayoutBuilder(
            builder: (context, box) {
              final bars = [
                UsageBar(
                  label: 'المعالج',
                  value: server.cpu,
                  icon: Icons.memory,
                ),
                UsageBar(
                  label: 'الذاكرة',
                  value: server.memory,
                  icon: Icons.storage_outlined,
                ),
                UsageBar(
                  label: 'القرص',
                  value: server.disk,
                  icon: Icons.disc_full_outlined,
                ),
              ];
              return box.maxWidth > 650
                  ? Row(
                      children: [
                        for (var i = 0; i < bars.length; i++) ...[
                          Expanded(child: bars[i]),
                          if (i < bars.length - 1) const SizedBox(width: 24),
                        ],
                      ],
                    )
                  : Column(
                      children: [
                        for (final bar in bars)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 14),
                            child: bar,
                          ),
                      ],
                    );
            },
          ),
        ),
        const Divider(height: 1),
        ...server.projects.map((project) => _ProjectRow(project: project)),
      ],
    ),
  );
}

class _ProjectRow extends StatelessWidget {
  const _ProjectRow({required this.project});
  final ProjectInfo project;
  @override
  Widget build(BuildContext context) => InkWell(
    onTap: () => Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) =>
            ProjectScreen(projectId: project.id, projectName: project.name),
      ),
    ),
    child: Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: [
          StatusDot(project.status, showLabel: false),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  project.name,
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
                Text(
                  project.baseUrl,
                  textDirection: TextDirection.ltr,
                  textAlign: TextAlign.right,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Color(0xFF677381),
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
          if (project.latencyMs != null)
            Text(
              '${project.latencyMs} ms',
              textDirection: TextDirection.ltr,
              style: const TextStyle(
                color: Color(0xFF596674),
                fontWeight: FontWeight.w700,
              ),
            ),
          const SizedBox(width: 8),
          const Icon(Icons.chevron_left, color: Color(0xFF7C8793)),
        ],
      ),
    ),
  );
}

class _IncidentsPanel extends ConsumerWidget {
  const _IncidentsPanel({required this.data});
  final DashboardData data;
  @override
  Widget build(BuildContext context, WidgetRef ref) => Card(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Padding(
          padding: EdgeInsets.all(16),
          child: Row(
            children: [
              Icon(
                Icons.notifications_active_outlined,
                color: Color(0xFFC5362F),
              ),
              SizedBox(width: 9),
              Text(
                'التنبيهات المفتوحة',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w900),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        if (data.incidents.isEmpty)
          const EmptyState(
            icon: Icons.notifications_none,
            title: 'لا توجد تنبيهات',
            message: 'ستظهر هنا أي حالة تحتاج إلى تدخل.',
          )
        else
          ...data.incidents.map(
            (incident) => Padding(
              padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
              child: Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: incident.severity == 'critical'
                      ? const Color(0xFFFFECEA)
                      : const Color(0xFFFFF5E0),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      incident.title,
                      style: const TextStyle(fontWeight: FontWeight.w900),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      incident.message,
                      style: const TextStyle(color: Color(0xFF596674)),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            incident.projectName,
                            style: const TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                        TextButton.icon(
                          onPressed: () async {
                            try {
                              await ref
                                  .read(dashboardProvider.notifier)
                                  .acknowledge(incident.id);
                            } on ApiException catch (error) {
                              if (context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(content: Text(error.message)),
                                );
                              }
                            }
                          },
                          icon: const Icon(Icons.done, size: 18),
                          label: const Text('تم الاطلاع'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        const SizedBox(height: 10),
      ],
    ),
  );
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.cloud_off_outlined,
            size: 54,
            color: Color(0xFFC5362F),
          ),
          const SizedBox(height: 14),
          Text(
            message,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 18),
          ElevatedButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh),
            label: const Text('إعادة المحاولة'),
          ),
        ],
      ),
    ),
  );
}
