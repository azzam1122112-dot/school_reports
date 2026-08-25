"""Shared limits for the printable report-details field."""

REPORT_DETAILS_RECOMMENDED_LENGTH = 450
REPORT_DETAILS_MAX_LENGTH = 600


def report_details_length_error() -> str:
    return (
        f"تفاصيل التقرير لا تتجاوز {REPORT_DETAILS_MAX_LENGTH} حرفًا. "
        f"اختصر النص مع الحفاظ على أهم معلومات التنفيذ."
    )
