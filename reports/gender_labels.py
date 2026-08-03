from __future__ import annotations

from typing import Any


def school_gender_labels(school: Any = None) -> dict[str, str | bool]:
    """Return the complete Arabic terminology set for a school audience.

    Boys terminology is the safe fallback when no school context exists.  Keeping
    the grammatical forms here prevents templates, PDFs and forms from drifting
    apart when the active school changes.
    """

    is_girls = (getattr(school, "gender", "") or "").strip().lower() == "girls"
    if is_girls:
        return {
            "is_girls": True,
            "manager": "مديرة المدرسة",
            "manager_dative": "لمديرة المدرسة",
            "manager_short": "المديرة",
            "manager_short_dative": "للمديرة",
            "leader": "قائدة المدرسة",
            "teacher": "المعلمة",
            "teacher_dative": "للمعلمة",
            "teacher_indefinite": "معلمة",
            "teacher_indefinite_dative": "لمعلمة",
            "teachers": "المعلمات",
            "teachers_object": "المعلمات",
            "teachers_dative": "للمعلمات",
            "teachers_indefinite": "معلمات",
            "executor": "المنفّذة",
            "executors": "المنفّذات",
            "head": "رئيسة",
            "head_of_department": "رئيسة القسم",
            "admin_staff": "موظفة إدارية",
            "lab_tech": "محضرة مختبر",
            "student": "الطالبة",
            "students": "الطالبات",
            "beneficiary": "المستفيدة",
            "beneficiaries": "المستفيدات",
            "beneficiaries_object": "المستفيدات",
            "beneficiary_indefinite": "مستفيدة",
        }
    return {
        "is_girls": False,
        "manager": "مدير المدرسة",
        "manager_dative": "لمدير المدرسة",
        "manager_short": "المدير",
        "manager_short_dative": "للمدير",
        "leader": "قائد المدرسة",
        "teacher": "المعلم",
        "teacher_dative": "للمعلم",
        "teacher_indefinite": "معلم",
        "teacher_indefinite_dative": "لمعلم",
        "teachers": "المعلمون",
        "teachers_object": "المعلمين",
        "teachers_dative": "للمعلمين",
        "teachers_indefinite": "معلمين",
        "executor": "المنفّذ",
        "executors": "المنفّذون",
        "head": "رئيس",
        "head_of_department": "رئيس القسم",
        "admin_staff": "موظف إداري",
        "lab_tech": "محضر مختبر",
        "student": "الطالب",
        "students": "الطلاب",
        "beneficiary": "المستفيد",
        "beneficiaries": "المستفيدون",
        "beneficiaries_object": "المستفيدين",
        "beneficiary_indefinite": "مستفيد",
    }


def school_gender_template_context(school: Any = None) -> dict[str, str | bool]:
    labels = school_gender_labels(school)
    return {
        "IS_GIRLS_SCHOOL": labels["is_girls"],
        "SCHOOL_MANAGER_LABEL": labels["manager"],
        "SCHOOL_MANAGER_DATIVE_LABEL": labels["manager_dative"],
        "SCHOOL_MANAGER_SHORT_LABEL": labels["manager_short"],
        "SCHOOL_MANAGER_SHORT_DATIVE_LABEL": labels["manager_short_dative"],
        "SCHOOL_LEADER_LABEL": labels["leader"],
        "SCHOOL_TEACHER_LABEL": labels["teacher"],
        "SCHOOL_TEACHER_DATIVE_LABEL": labels["teacher_dative"],
        "SCHOOL_TEACHER_INDEFINITE_LABEL": labels["teacher_indefinite"],
        "SCHOOL_TEACHER_INDEFINITE_DATIVE_LABEL": labels["teacher_indefinite_dative"],
        "SCHOOL_TEACHERS_LABEL": labels["teachers"],
        "SCHOOL_TEACHERS_OBJ_LABEL": labels["teachers_object"],
        "SCHOOL_TEACHERS_DATIVE_LABEL": labels["teachers_dative"],
        "SCHOOL_TEACHERS_INDEFINITE_LABEL": labels["teachers_indefinite"],
        "SCHOOL_EXECUTOR_LABEL": labels["executor"],
        "SCHOOL_EXECUTORS_LABEL": labels["executors"],
        "SCHOOL_HEAD_LABEL": labels["head"],
        "SCHOOL_HEAD_OF_DEPARTMENT_LABEL": labels["head_of_department"],
        "SCHOOL_ADMIN_STAFF_LABEL": labels["admin_staff"],
        "SCHOOL_LAB_TECH_LABEL": labels["lab_tech"],
        "SCHOOL_STUDENT_LABEL": labels["student"],
        "SCHOOL_STUDENTS_LABEL": labels["students"],
        "SCHOOL_BENEFICIARY_LABEL": labels["beneficiary"],
        "SCHOOL_BENEFICIARIES_LABEL": labels["beneficiaries"],
        "SCHOOL_BENEFICIARIES_OBJ_LABEL": labels["beneficiaries_object"],
        "SCHOOL_BENEFICIARY_INDEFINITE_LABEL": labels["beneficiary_indefinite"],
    }
