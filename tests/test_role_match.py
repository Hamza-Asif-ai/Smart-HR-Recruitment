import pytest

from src import role_match as rm

SUBJECTS = {
    "Application for Software Engineer Position – Zeeshan Haider": "Software Engineer",
    "Application for AI Engineer Position – Rabia Anwar": "AI Engineer",
    "Application for Backend Software Engineer Position – Ayesha Khan": "Backend Software Engineer",
    "Application for Machine Learning Engineer Role – Hassan Javed": "Machine Learning Engineer",
    "Application for Junior AI/ML Developer Position – Iqra Farid": "Junior AI/ML Developer",
    "Application for Junior Software Developer Position – Moiz Ansari": "Junior Software Developer",
    "Re: Application for AI Engineer - Sara Khan": "AI Engineer",
    "Job Application - Data Analyst": "Data Analyst",
    "Applying for: Full-Stack Developer": "Full-Stack Developer",
}


@pytest.mark.parametrize("subject,expected", SUBJECTS.items())
def test_detects_role_from_subject(subject, expected):
    assert rm.detect_applied_role(subject)[0] == expected


def test_ai_engineer_only_matches_ai_engineer():
    hits = {s for s in SUBJECTS if rm.role_matches("AI Engineer", s, "I will email my CV and maintain")[0]}
    assert hits == {
        "Application for AI Engineer Position – Rabia Anwar",
        "Re: Application for AI Engineer - Sara Khan",
    }


def test_alias_and_extra_words():
    assert rm.role_matches("AI Engineer", "Application for Artificial Intelligence Engineer role")[0]
    assert rm.role_matches("Machine Learning Engineer", "Application for ML Engineer position")[0]
    assert rm.role_matches("Software Engineer", "Application for Backend Software Engineer Position")[0]
    assert not rm.role_matches("Backend Software Engineer", "Application for Software Engineer Position")[0]


def test_seniority_is_ignored():
    assert rm.role_matches("Senior AI Engineer", "Application for Junior AI Engineer Position")[0]


def test_body_used_only_when_subject_has_no_role():
    body = "Dear Hiring Manager, I am writing to express my interest in the Backend Software Engineer position at your organization."
    assert rm.detect_applied_role("Resume attached", body) == ("Backend Software Engineer", "body")
    assert not rm.role_matches("AI Engineer", "Resume attached", body)[0]
    # subject wins over body
    assert rm.detect_applied_role("Application for AI Engineer Position", body)[1] == "subject"


def test_no_role_falls_back_to_whole_words_not_substrings():
    assert not rm.role_matches("AI Engineer", "Resume", "please email me, I maintain servers")[0]
    assert rm.role_matches("AI Engineer", "Resume", "I am an AI engineer with 3 years")[0]
