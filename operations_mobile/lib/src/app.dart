import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'design_system.dart';
import 'screens/dashboard_screen.dart';
import 'state.dart';

class OperationsApp extends ConsumerWidget {
  const OperationsApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.listen<SessionState>(sessionProvider, (previous, next) {
      if (next.status == SessionStatus.signedIn &&
          previous?.status != SessionStatus.signedIn) {
        ref.read(notificationProvider).initialize();
      }
    });
    final session = ref.watch(sessionProvider);
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'مركز العمليات',
      locale: const Locale('ar'),
      builder: (context, child) =>
          Directionality(textDirection: TextDirection.rtl, child: child!),
      theme: _theme(),
      home: switch (session.status) {
        SessionStatus.loading => const _StartupScreen(),
        SessionStatus.signedOut => _ProvisioningErrorScreen(
          message: session.error,
          onRetry: () => ref.read(sessionProvider.notifier).retry(),
        ),
        SessionStatus.signedIn => const DashboardScreen(),
      },
    );
  }

  ThemeData _theme() {
    final scheme = ColorScheme.fromSeed(
      seedColor: OpsColors.forest,
      primary: OpsColors.forest,
      secondary: OpsColors.gold,
      surface: OpsColors.surface,
      brightness: Brightness.light,
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: OpsColors.canvas,
      fontFamily: 'sans-serif',
      textTheme: ThemeData.light().textTheme.apply(
        bodyColor: OpsColors.ink,
        displayColor: OpsColors.ink,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: OpsColors.ink,
        foregroundColor: Colors.white,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        centerTitle: false,
      ),
      cardTheme: const CardThemeData(
        color: OpsColors.surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(20)),
          side: BorderSide(color: OpsColors.line),
        ),
      ),
      inputDecorationTheme: const InputDecorationTheme(
        filled: true,
        fillColor: OpsColors.surface,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(16)),
          borderSide: BorderSide(color: OpsColors.line),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(16)),
          borderSide: BorderSide(color: OpsColors.line),
        ),
        contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: OpsColors.forest,
          foregroundColor: Colors.white,
          minimumSize: const Size(48, 54),
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: OpsColors.forest,
          foregroundColor: Colors.white,
          minimumSize: const Size(48, 54),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        ),
      ),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: OpsColors.ink,
        contentTextStyle: TextStyle(color: Colors.white),
      ),
      tooltipTheme: const TooltipThemeData(
        waitDuration: Duration(milliseconds: 450),
      ),
    );
  }
}

class _ProvisioningErrorScreen extends StatelessWidget {
  const _ProvisioningErrorScreen({
    required this.message,
    required this.onRetry,
  });

  final String? message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: PremiumPanel(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.phonelink_lock_outlined,
                size: 54,
                color: OpsColors.danger,
              ),
              const SizedBox(height: 14),
              const Text(
                'تعذر فتح مركز العمليات',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                message ?? 'تحقق من اتصال الخادم ثم أعد المحاولة.',
                style: const TextStyle(color: OpsColors.slate),
                textAlign: TextAlign.center,
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
      ),
    ),
  );
}

class _StartupScreen extends StatelessWidget {
  const _StartupScreen();
  @override
  Widget build(BuildContext context) => const Scaffold(
    body: Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.monitor_heart_outlined,
            size: 54,
            color: Color(0xFF006C35),
          ),
          SizedBox(height: 20),
          CircularProgressIndicator(),
        ],
      ),
    ),
  );
}
