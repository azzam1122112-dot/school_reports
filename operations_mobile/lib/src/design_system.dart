import 'package:flutter/material.dart';

abstract final class OpsColors {
  static const ink = Color(0xFF10231C);
  static const forest = Color(0xFF07583A);
  static const emerald = Color(0xFF0B8A5A);
  static const mint = Color(0xFFE7F4ED);
  static const gold = Color(0xFFC49A48);
  static const goldSoft = Color(0xFFF7EDD8);
  static const canvas = Color(0xFFF3F6F2);
  static const surface = Color(0xFFFCFDFC);
  static const line = Color(0xFFDCE6E0);
  static const slate = Color(0xFF66766F);
  static const danger = Color(0xFFB42318);
  static const warning = Color(0xFFB7791F);
  static const info = Color(0xFF236AA3);
}

class PremiumPanel extends StatelessWidget {
  const PremiumPanel({
    required this.child,
    super.key,
    this.padding = const EdgeInsets.all(18),
    this.gradient,
    this.color,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final Gradient? gradient;
  final Color? color;

  @override
  Widget build(BuildContext context) => Container(
    padding: padding,
    decoration: BoxDecoration(
      color: gradient == null ? (color ?? OpsColors.surface) : null,
      gradient: gradient,
      borderRadius: BorderRadius.circular(22),
      border: Border.all(
        color: gradient == null
            ? OpsColors.line
            : Colors.white.withValues(alpha: .09),
      ),
      boxShadow: const [
        BoxShadow(
          color: Color(0x120B2A1E),
          blurRadius: 28,
          offset: Offset(0, 12),
        ),
      ],
    ),
    child: child,
  );
}

class SectionHeading extends StatelessWidget {
  const SectionHeading({
    required this.title,
    required this.icon,
    super.key,
    this.subtitle,
    this.trailing,
  });

  final String title;
  final String? subtitle;
  final IconData icon;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      Container(
        width: 44,
        height: 44,
        decoration: BoxDecoration(
          color: OpsColors.mint,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Icon(icon, color: OpsColors.forest),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              title,
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w900),
            ),
            if (subtitle != null)
              Text(
                subtitle!,
                style: const TextStyle(color: OpsColors.slate, fontSize: 12),
              ),
          ],
        ),
      ),
      ?trailing,
    ],
  );
}
