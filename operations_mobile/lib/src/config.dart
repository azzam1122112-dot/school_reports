class AppConfig {
  static const apiBaseUrl = String.fromEnvironment(
    'OPS_API_BASE_URL',
    defaultValue: 'https://tawtheeq-ksa.com/api/operations/v1',
  );
}
