import 'package:flutter_test/flutter_test.dart';
import 'package:tawtheeq_operations/src/models.dart';

void main() {
  test(
    'maps backend health states without treating unknown values as healthy',
    () {
      expect(healthStatusFrom('healthy'), HealthStatus.healthy);
      expect(healthStatusFrom('down'), HealthStatus.down);
      expect(healthStatusFrom('unexpected'), HealthStatus.unknown);
    },
  );

  test('parses a dashboard inventory payload', () {
    final dashboard = DashboardData.fromJson({
      'summary': {
        'servers': 1,
        'projects': 1,
        'healthy_projects': 1,
        'open_incidents': 0,
      },
      'servers': [
        {
          'id': 1,
          'name': 'main',
          'provider': 'hetzner',
          'status': 'healthy',
          'projects': [
            {
              'id': 7,
              'name': 'Project',
              'slug': 'project',
              'base_url': 'https://example.com',
              'status': 'healthy',
              'services': [],
            },
          ],
        },
      ],
      'incidents': [],
      'generated_at': '2026-08-21T12:00:00Z',
    });

    expect(dashboard.serverCount, 1);
    expect(dashboard.servers.single.projects.single.id, 7);
    expect(dashboard.generatedAt, isNotNull);
  });
}
