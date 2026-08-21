import 'dart:async';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'api_client.dart';

class NotificationService {
  NotificationService(this._api);
  final OperationsApi _api;
  final _local = FlutterLocalNotificationsPlugin();
  StreamSubscription<String>? _refreshSubscription;
  bool _initialized = false;

  Future<void> initialize() async {
    if (_initialized) return;
    _initialized = true;
    await Firebase.initializeApp();
    await _local.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ),
    );
    const channel = AndroidNotificationChannel(
      'operations_alerts',
      'تنبيهات الخادم',
      description: 'تنبيهات الأعطال والحالات الحرجة للمشاريع',
      importance: Importance.max,
    );
    await _local
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >()
        ?.createNotificationChannel(channel);
    await FirebaseMessaging.instance.requestPermission(
      alert: true,
      badge: true,
      sound: true,
    );
    final token = await FirebaseMessaging.instance.getToken();
    if (token != null) await _register(token);
    _refreshSubscription = FirebaseMessaging.instance.onTokenRefresh.listen(
      _register,
    );
    FirebaseMessaging.onMessage.listen((message) {
      final notification = message.notification;
      if (notification == null) return;
      _local.show(
        id: notification.hashCode,
        title: notification.title,
        body: notification.body,
        notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails(
            'operations_alerts',
            'تنبيهات الخادم',
            channelDescription: 'تنبيهات الأعطال والحالات الحرجة للمشاريع',
            importance: Importance.max,
            priority: Priority.high,
          ),
        ),
      );
    });
  }

  Future<void> _register(String token) async {
    final info = await DeviceInfoPlugin().androidInfo;
    await _api.registerDevice(
      deviceId: info.id,
      name: '${info.brand} ${info.model}'.trim(),
      fcmToken: token,
    );
  }

  void dispose() => _refreshSubscription?.cancel();
}
