# reports/api_schema.py
# -*- coding: utf-8 -*-
"""وثيقة OpenAPI للـAPI العام.

**لماذا مكتوبةٌ بيدٍ لا مولَّدة.** المولِّدات (``drf-spectacular`` وأخواتها)
تعني اعتمادية جديدة، وإعادةَ توليدٍ لملف قفلٍ من 2150 سطراً، وتمريرةَ
``pip-audit`` جديدة — وهي قرارُ سلسلة إمدادٍ يخصّ صاحب المنصة لا أداةً تُضاف
بالمرور. والـAPI هنا صغيرٌ ومنسَّق (خمسة موارد)، فوصفُه اليدوي أدقّ من
المولَّد: يشرح **قواعد العزل والنطاق** التي لا يستنتجها أي مولِّد من التواقيع.

**وما يمنعه من التقادم** هو ``test_api_contract.py``: يقارن مسارات الموجِّه
الفعلية بما في هذه الوثيقة، ويفشل إن أُضيف مسارٌ ولم يُوثَّق. فالانحراف الذي
تخشاه من الوثائق اليدوية مُمسَكٌ باختبار، لا متروكٌ للانضباط.
"""
from __future__ import annotations

from django.conf import settings

API_VERSION = "1.0.0"


def _server_url() -> str:
    base = str(getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}/api/v1" if base else "/api/v1"


_TENANT_NOTE = (
    "كل مسار محصور بالمدرسة النشطة. ومع مفتاح التكامل تُشتقّ المدرسة من "
    "المفتاح نفسه ولا تُقبل من الطلب، فلا يمكن لمفتاح مدرسةٍ قراءةُ أو كتابةُ "
    "بيانات مدرسةٍ أخرى."
)

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"detail": {"type": "string"}},
}

_PAGINATED = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "next": {"type": "string", "nullable": True, "format": "uri"},
        "previous": {"type": "string", "nullable": True, "format": "uri"},
        "results": {"type": "array", "items": {}},
    },
}


def _list_response(ref: str) -> dict:
    schema = {
        "type": "object",
        "properties": dict(_PAGINATED["properties"]),
    }
    schema["properties"]["results"] = {
        "type": "array",
        "items": {"$ref": f"#/components/schemas/{ref}"},
    }
    return {
        "200": {
            "description": "قائمة مُصفَّحة",
            "content": {"application/json": {"schema": schema}},
        }
    }


def _detail_response(ref: str) -> dict:
    return {
        "200": {
            "description": "عنصر واحد",
            "content": {
                "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
            },
        },
        "404": {"description": "غير موجود، أو خارج نطاق المدرسة النشطة"},
    }


def build_openapi_schema() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "منصة توثيق — واجهة برمجة التطبيقات",
            "version": API_VERSION,
            "description": (
                "واجهة للتكامل مع أنظمة المدارس الخارجية.\n\n"
                f"**العزل:** {_TENANT_NOTE}\n\n"
                "**الصلاحيات:** المفتاح يعمل بصلاحيات الشخص المرتبط به "
                "(`acting_as`) ولا يتجاوزها. والكتابة تحتاج مفتاحاً بنطاق "
                "`write`؛ والقراءة هي الافتراض.\n\n"
                "**الحدود:** 600 طلب في الدقيقة لكل مفتاح."
            ),
        },
        "servers": [{"url": _server_url()}],
        "security": [{"ApiKeyAuth": []}],
        "tags": [
            {"name": "schools", "description": "المدارس التي يصلها المفتاح"},
            {"name": "reports", "description": "التقارير — قراءة وإنشاء"},
            {"name": "report-types", "description": "أنواع التقارير المعرَّفة"},
            {"name": "tickets", "description": "الطلبات والتذاكر"},
            {"name": "notifications", "description": "الإشعارات الواصلة"},
        ],
        "paths": {
            "/schools/": {
                "get": {
                    "tags": ["schools"],
                    "summary": "المدارس المتاحة",
                    "responses": _list_response("School"),
                }
            },
            "/schools/{id}/": {
                "get": {
                    "tags": ["schools"],
                    "summary": "مدرسة واحدة",
                    "parameters": [_path_id()],
                    "responses": _detail_response("School"),
                }
            },
            "/reports/": {
                "get": {
                    "tags": ["reports"],
                    "summary": "قائمة التقارير",
                    "description": (
                        "تقارير المدرسة النشطة ضمن نطاق رؤية الهوية المصادَق بها: "
                        "المدير يرى تقارير مدرسته، والمعلّم يرى تقاريره."
                    ),
                    "responses": _list_response("ReportListItem"),
                },
                "post": {
                    "tags": ["reports"],
                    "summary": "إنشاء تقرير",
                    "description": (
                        "يتطلّب مفتاحاً بنطاق `write`. حقلا المدرسة والمُعِدّ "
                        "غير مقبولين في الحمولة: يُشتقّان من المفتاح."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ReportCreate"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "أُنشئ",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ReportCreate"}
                                }
                            },
                        },
                        "400": {
                            "description": "حمولة غير صالحة",
                            "content": {"application/json": {"schema": _ERROR_SCHEMA}},
                        },
                        "403": {
                            "description": "المفتاح للقراءة فقط",
                            "content": {"application/json": {"schema": _ERROR_SCHEMA}},
                        },
                    },
                },
            },
            "/reports/{id}/": {
                "get": {
                    "tags": ["reports"],
                    "summary": "تقرير واحد",
                    "parameters": [_path_id()],
                    "responses": _detail_response("ReportListItem"),
                }
            },
            "/report-types/": {
                "get": {
                    "tags": ["report-types"],
                    "summary": "أنواع التقارير",
                    "responses": _list_response("ReportType"),
                }
            },
            "/report-types/{id}/": {
                "get": {
                    "tags": ["report-types"],
                    "summary": "نوع واحد",
                    "parameters": [_path_id()],
                    "responses": _detail_response("ReportType"),
                }
            },
            "/tickets/": {
                "get": {
                    "tags": ["tickets"],
                    "summary": "قائمة الطلبات",
                    "responses": _list_response("Ticket"),
                }
            },
            "/tickets/{id}/": {
                "get": {
                    "tags": ["tickets"],
                    "summary": "طلب واحد",
                    "parameters": [_path_id()],
                    "responses": _detail_response("Ticket"),
                }
            },
            "/notifications/": {
                "get": {
                    "tags": ["notifications"],
                    "summary": "الإشعارات الواصلة للهوية المصادَق بها",
                    "responses": _list_response("Notification"),
                }
            },
            "/notifications/{id}/": {
                "get": {
                    "tags": ["notifications"],
                    "summary": "إشعار واحد",
                    "parameters": [_path_id()],
                    "responses": _detail_response("Notification"),
                }
            },
            "/notifications/unread_count/": {
                "get": {
                    "tags": ["notifications"],
                    "summary": "عدد غير المقروء",
                    "responses": {
                        "200": {
                            "description": "العدد",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"count": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "Authorization",
                    "description": (
                        "بالصيغة `Api-Key twq_<id>_<secret>`. يُنشأ من شاشة "
                        "«مفاتيح التكامل» لدى مدير المدرسة، ويُعرض السرّ مرة "
                        "واحدة عند الإنشاء ولا يمكن استرجاعه."
                    ),
                }
            },
            "schemas": {
                "School": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "code": {"type": "string"},
                    },
                },
                "ReportType": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "code": {"type": "string"},
                    },
                },
                "ReportListItem": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "report_date": {"type": "string", "format": "date"},
                        "teacher_name": {"type": "string"},
                        "category_name": {"type": "string", "nullable": True},
                        "created_at": {"type": "string", "format": "date-time"},
                    },
                },
                "ReportCreate": {
                    "type": "object",
                    "required": ["title", "report_date"],
                    "properties": {
                        "id": {"type": "integer", "readOnly": True},
                        "title": {"type": "string", "maxLength": 200},
                        "report_date": {"type": "string", "format": "date"},
                        "category": {
                            "type": "integer",
                            "nullable": True,
                            "description": "من أنواع هذه المدرسة وحدها.",
                        },
                        "idea": {"type": "string"},
                        "goal": {"type": "string"},
                        "implementation_method": {"type": "string"},
                        "results": {"type": "string"},
                        "recommendations": {"type": "string"},
                        "beneficiaries_count": {"type": "integer", "nullable": True},
                        "academic_year": {"type": "string"},
                    },
                },
                "Ticket": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "status": {"type": "string"},
                        "creator_name": {"type": "string"},
                    },
                },
                "Notification": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                    },
                },
            },
        },
    }


def _path_id() -> dict:
    return {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "integer"},
    }
