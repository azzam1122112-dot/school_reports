import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Icon(
                    Icons.dns_outlined,
                    size: 64,
                    color: Color(0xFF006C35),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    'مركز العمليات',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'دخول مشرف النظام لمتابعة الخادم والمشاريع',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Color(0xFF677381), fontSize: 16),
                  ),
                  const SizedBox(height: 28),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Form(
                        key: _formKey,
                        child: Column(
                          children: [
                            TextFormField(
                              controller: _phone,
                              keyboardType: TextInputType.phone,
                              textDirection: TextDirection.ltr,
                              textAlign: TextAlign.right,
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
                                child: Text(
                                  state.error!,
                                  style: const TextStyle(
                                    color: Color(0xFFC5362F),
                                    fontWeight: FontWeight.w700,
                                  ),
                                  textAlign: TextAlign.center,
                                ),
                              ),
                            ],
                            const SizedBox(height: 20),
                            SizedBox(
                              width: double.infinity,
                              child: ElevatedButton.icon(
                                onPressed: _submitting ? null : _submit,
                                icon: _submitting
                                    ? const SizedBox(
                                        width: 20,
                                        height: 20,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      )
                                    : const Icon(Icons.login),
                                label: const Text('دخول آمن'),
                              ),
                            ),
                          ],
                        ),
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
                        color: Color(0xFF677381),
                      ),
                      SizedBox(width: 7),
                      Text(
                        'الاتصال مشفر والصلاحية محصورة بمشرف النظام',
                        style: TextStyle(color: Color(0xFF677381)),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
