import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'api_client.dart';
import 'models.dart';
import 'notifications.dart';

final apiProvider = Provider<OperationsApi>((ref) => OperationsApi());

enum SessionStatus { loading, signedOut, signedIn }

class SessionState {
  const SessionState({required this.status, this.error});
  final SessionStatus status;
  final String? error;
}

class SessionController extends StateNotifier<SessionState> {
  SessionController(this._api)
    : super(const SessionState(status: SessionStatus.loading)) {
    _restore();
  }
  final OperationsApi _api;

  Future<void> _restore() async {
    if (!await _api.restoreSession()) {
      state = const SessionState(
        status: SessionStatus.signedOut,
        error: 'هذه النسخة غير مهيأة برمز الجهاز.',
      );
      return;
    }
    try {
      await _api.dashboard();
      state = const SessionState(status: SessionStatus.signedIn);
    } catch (error) {
      if (error is ApiException &&
          (error.statusCode == 401 || error.statusCode == 403)) {
        await _api.clearSession();
      }
      state = SessionState(
        status: SessionStatus.signedOut,
        error: error is ApiException
            ? error.message
            : 'تعذر الاتصال بمركز العمليات.',
      );
    }
  }

  Future<void> retry() async {
    state = const SessionState(status: SessionStatus.loading);
    await _restore();
  }

  Future<void> expired() async {
    await _api.clearSession();
    state = const SessionState(
      status: SessionStatus.signedOut,
      error: 'انتهت صلاحية رمز الجهاز. أعد تثبيت النسخة الشخصية.',
    );
  }
}

final sessionProvider = StateNotifierProvider<SessionController, SessionState>((
  ref,
) {
  return SessionController(ref.watch(apiProvider));
});

class DashboardController extends StateNotifier<AsyncValue<DashboardData>> {
  DashboardController(this._api, this._onUnauthorized)
    : super(const AsyncLoading()) {
    refresh();
    _timer = Timer.periodic(
      const Duration(seconds: 60),
      (_) => refresh(silent: true),
    );
  }
  final OperationsApi _api;
  final Future<void> Function() _onUnauthorized;
  Timer? _timer;

  Future<void> refresh({bool silent = false}) async {
    if (!silent || !state.hasValue) state = const AsyncLoading();
    try {
      state = AsyncData(await _api.dashboard());
    } catch (error, stack) {
      if (error is ApiException &&
          (error.statusCode == 401 || error.statusCode == 403)) {
        await _onUnauthorized();
      }
      state = AsyncError(error, stack);
    }
  }

  Future<void> acknowledge(int incidentId) async {
    await _api.acknowledgeIncident(incidentId);
    await refresh(silent: true);
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}

final dashboardProvider =
    StateNotifierProvider.autoDispose<
      DashboardController,
      AsyncValue<DashboardData>
    >((ref) {
      return DashboardController(
        ref.watch(apiProvider),
        ref.read(sessionProvider.notifier).expired,
      );
    });

final projectProvider = FutureProvider.autoDispose.family<ProjectDetails, int>((
  ref,
  id,
) {
  return ref.watch(apiProvider).project(id);
});

final deploymentProvider = FutureProvider.autoDispose<DeploymentOverview>((
  ref,
) {
  ref.watch(dashboardProvider);
  return ref.watch(apiProvider).deploymentStatus();
});

final accountsProvider = FutureProvider.autoDispose<List<OperationsAccount>>((
  ref,
) {
  return ref.watch(apiProvider).accounts();
});

final notificationProvider = Provider<NotificationService>((ref) {
  final service = NotificationService(ref.watch(apiProvider));
  ref.onDispose(service.dispose);
  return service;
});
