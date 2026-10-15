"""Retrieval recall on the real corpus: for each eval question, as its user, the sources the reference answer
depends on must be retrieved. Slower than the other tests (about a minute, mostly OCR)."""
import json

import pytest

from copilot import access, config, db
from copilot.ingest import ingest
from copilot.search import retrieve

# question id -> (title contains, location contains) that must be in the results
NEEDED = {
    "q01": [("HCP-500 IOM Rev B", "p. 9")],
    "q02": [("HCP-200", "p. 3")],
    "q03": [("VRS-75 Service Manual Rev C", "p. 2")],
    "q04": [("SB-2025-009", ""), ("Salesforce cases NA", "00041988"), ("Fleet register", "FRS-P-0031")],
    "q05": [("HV-PROC-07", "6. Procedure"), ("HV-PROC-07", "7. Repeat trips")],
    "q06": [("Compressor Troubleshooting Playbook", "E-31")],
    "q07": [("Belton BX-12 O&M Manual", "p. 8")],
    "q08": [("SB-2026-003", ""), ("Teams", "2026-07-02"), ("Tacoma Site Notes", ""),
            ("Salesforce cases NA", "00041207"), ("Salesforce cases NA", "00042388"), ("Salesforce cases NA", "00044010")],
    "q09": [("SB-2025-014", ""), ("FW: RTM-H-0007", "Sofie"), ("KT-WI-14", ""), ("Fleet register", "RTM-H-0007")],
    "q10": [("RE: BX-12 tracking", "Henrik Sauer")],
}

QUESTIONS = {q["id"]: q for q in json.loads((config.DATA_DIR / "eval" / "questions.json").read_text())}


@pytest.fixture(scope="module")
def real_index(database):
    with db.connect() as conn:
        conn.execute("TRUNCATE documents, chunks RESTART IDENTITY CASCADE")
        ingest(conn, config.DATA_DIR)
        yield conn


@pytest.mark.parametrize("qid", sorted(NEEDED))
def test_eval_question_finds_its_sources(real_index, qid):
    question = QUESTIONS[qid]
    user = access.users()[question["user_id"]]
    hits = retrieve(real_index, question["question"], list(user.groups)).hits
    missing = [n for n in NEEDED[qid] if not any(n[0] in h.title and n[1] in h.location for h in hits)]
    assert not missing


def test_restricted_procedure_is_flagged_not_returned(real_index):
    # Same question, two users: Priya (no HV) gets the playbook and a cert-hv flag, Daniel gets the procedure.
    q = QUESTIONS["q06"]["question"]
    priya = retrieve(real_index, q, list(access.users()["u03"].groups))
    daniel = retrieve(real_index, q, list(access.users()["u02"].groups))

    assert priya.missing_certifications == ["cert-hv"]
    assert not any("HV-PROC-07" in h.title for h in priya.hits)
    assert daniel.missing_certifications == []
    assert any("HV-PROC-07" in h.title for h in daniel.hits)


def test_unofficial_copy_of_hv_procedure_is_never_returned(real_index):
    hits = retrieve(real_index, "VRS-110 VFD quick reset E-31 P9.01", list(access.users()["u08"].groups)).hits
    assert hits and not any("unofficial" in h.path for h in hits)
