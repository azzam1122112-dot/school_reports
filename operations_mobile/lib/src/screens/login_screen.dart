import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../design_system.dart';
import '../state.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});
  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _phone = TextEditingController();
  final _password = TextEditingController();
  final _otp = TextEditingController();
  bool _obscure = true;
  bool _submitting = false;

  @override
  void dispose() {
    _phone.dispose();
    _password.dispose();
    _otp.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _submitting = true);
    await ref
        .read(sessionProvider.notifier)
        .login(
          phone: _phone.text.trim(),
          password: _password.text,
          deviceName: 'تطبيق أندرويد',
          otp: _otp.text.trim(),
        );
    if (mounted) setState(() => _submitting = false);
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(sessionProvider);
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topRight,
            end: Alignment.bottomLeft,
            colors: [Color(0xFFE8F1EB), OpsColors.canvas, Color(0xFFF8F3E8)],
          ),
        ),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(20),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    PremiumPanel(
                      padding: const EdgeInsets.all(24),
                      gradient: const LinearGradient(
                        begin: Alignment.topRight,
                        end: Alignment.bottomLeft,
                        colors: [OpsColors.ink, OpsColors.forest],
                      ),
                      child: Column(
                        children: [
                          Container(
                            width: 76,
                            height: 76,
                            decoration: BoxDecoration(
                              color: Colors.white.withValues(alpha: .10),
                              borderRadius: BorderRadius.circular(24),
                              border: Border.all(
                                color: OpsColors.gold.withValues(alpha: .65),
                              ),
                            ),
                            child: const Icon(
                              Icons.hub_outlined,
                              size: 40,
                              color: Colors.white,
                            ),
                          ),
                          const SizedBox(height: 18),
                          const Text(
                            'مركز العمليات',
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 29,
                              fontWeight: FontWeight.w900,
                            ),
                          ),
                          const SizedBox(height: 6),
                          const Text(
                            'إدارة الخوادم والنشر من مكان واحد',
                            style: TextStyle(color: Color(0xFFC7D8D0)),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                    PremiumPanel(
                      child: Form(
                        key: _formKey,
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            const Text(
                              'تسجيل الدخول الآمن',
                              style: TextStyle(
                                fontSize: 19,
                                fontWeight: FontWeight.w900,
                              ),
                            ),
                            const SizedBox(height: 5),
                            const Text(
                              'استخدم حساب فريق العمليات المخوّل.',
                              style: TextStyle(color: OpsColors.slate),
                            ),
                            const SizedBox(height: 20),
                            TextFormField(
                              controller: _phone,
                              keyboardType: TextInputType.phone,
                              textDirection: TextDirection.ltr,
                              textAlign: TextAlign.right,
                              autofillHints: const [
                                AutofillHints.telephoneNumber,
                              ],
                              decoration: const InputDecoration(
                                labelText: 'رقم الجوال',
                                prefixIcon: Icon(Icons.phone_android),
                              ),
                              validator: (value) =>
                                  value == null || value.trim().isEmpty
                                  ? 'أدخل رقم الجوال'
                                  : null,
                            ),
                            const SizedBox(height: 14),
                            TextFormField(
                              controller: _password,
                              obscureText: _obscure,
                              textDirection: TextDirection.ltr,
                              textAlign: TextAlign.right,
                              autofillHints: const [AutofillHints.password],
                              onFieldSubmitted: (_) =>
                                  _submitting ? null : _submit(),
                              decoration: InputDecoration(
                                labelText: 'كلمة المرور',
                                prefixIcon: const Icon(Icons.lock_outline),
                                suffixIcon: IconButton(
                                  tooltip: _obscure
                                      ? 'إظهار كلمة المرور'
                                      : 'إخفاء كلمة المرور',
                                  onPressed: () =>
                                      setState(() => _obscure = !_obscure),
                                  icon: Icon(
                                    _obscure
                                        ? Icons.visibility_outlined
                                        : Icons.visibility_off_outlined,
                                  ),
                                ),
                              ),
                              validator: (value) =>
                                  value == null || value.isEmpty
                                  ? 'أدخل كلمة المرور'
                                  : null,
                            ),
                            if (state.otpRequired) ...[
                              const SizedBox(height: 14),
                              TextFormField(
                                controller: _otp,
                                keyboardType: TextInputType.number,
                                maxLength: 6,
                                textDirection: TextDirection.ltr,
                                textAlign: TextAlign.center,
                                decoration: const InputDecoration(
                                  labelText: 'رمز التحقق',
                                  prefixIcon: Icon(
                                    Icons.verified_user_outlined,
                                  ),
                                  counterText: '',
                                ),
                                validator: (value) =>
                                    state.otpRequired && (value?.length != 6)
                                    ? 'أدخل رمز التحقق المكون من 6 أرقام'
                                    : null,
                              ),
                            ],
                            if (state.error != null) ...[
                              const SizedBox(height: 14),
                              Semantics(
                                liveRegion: true,
                                child: Container(
                                  padding: const EdgeInsets.all(12),
                                  decoration: BoxDecoration(
                                    color: const Color(0xFFFFEBE8),
                                    borderRadius: BorderRadius.circular(14),
                                  ),
                                  child: Text(
                                    state.error!,
                                    style: const TextStyle(
                                      color: OpsColors.danger,
                                      fontWeight: FontWeight.w700,
                                    ),
                                    textAlign: TextAlign.center,
                                  ),
                                ),
                              ),
                            ],
                            const SizedBox(height: 20),
                            ElevatedButton.icon(
                              onPressed: _submitting ? null : _submit,
                              icon: _submitting
                                  ? const SizedBox(
                                      width: 20,
                                      height: 20,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        color: Colors.white,
                                      ),
                                    )
                                  : const Icon(Icons.login_rounded),
                              label: const Text('الدخول إلى المركز'),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          Icons.shield_outlined,
                          size: 18,
                          color: OpsColors.forest,
                        ),
                        SizedBox(width: 7),
                        Flexible(
                          child: Text(
                            'اتصال مشفر · جلسة محمية · صلاحيات دقيقة',
                            style: TextStyle(color: OpsColors.slate),
                            textAlign: TextAlign.center,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
