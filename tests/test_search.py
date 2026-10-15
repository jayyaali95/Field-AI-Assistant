import pytest

from copilot import config
from copilot.ingest import ingest
from copilot.search import retrieve, search

TECH = ["region-na", "family-compressors"]  # like Priya: no HV certification
HV_TECH = TECH + ["cert-hv"]


def page(text):
    return f"<div id='main-content'><p>{text}</p></div>"


@pytest.fixture
def indexed(conn, corpus):
    def build(files):
        ingest(conn, corpus(files))
        return conn
    return build


def test_user_never_gets_chunks_from_documents_they_cannot_see(indexed):
    conn = indexed({
        "corpus/hv-proc.html": (page("E-31 reset: hold RESET on the drive keypad for 3 s, P9.01 = 1."), ["cert-hv", "family-compressors"]),
        "corpus/playbook.html": (page("E-31 needs an HV-certified technician. Do not open the cabinet."), ["family-compressors"]),
        "corpus/leads.html": (page("E-31 lessons learned on reset."), ["role-lead"]),
    })
    hits = search(conn, "How do I reset E-31?", TECH)
    assert [h.path for h in hits] == ["corpus/playbook.html"]
    assert all("P9.01" not in h.text for h in hits)

    assert {h.path for h in search(conn, "How do I reset E-31?", HV_TECH)} == {"corpus/hv-proc.html", "corpus/playbook.html"}


def test_documents_without_required_groups_are_visible_to_everyone(indexed):
    conn = indexed({"corpus/guide.html": (page("Work order fields: asset, fault code, E-31."), [])})
    assert len(search(conn, "E-31 work order", [])) == 1


def test_missing_certification_reported_when_restricted_document_matches_better(indexed):
    conn = indexed({
        "corpus/hv-proc.html": (page("E-31 reset procedure. Reset the E-31 fault at the drive. Reset steps."), ["cert-hv"]),
        "corpus/playbook.html": (page("Compressor notes: E-31 happens after grid dips."), []),
    })
    assert retrieve(conn, "How do I reset E-31?", TECH).missing_certifications == ["cert-hv"]
    assert retrieve(conn, "How do I reset E-31?", HV_TECH).missing_certifications == []


def test_non_certification_restrictions_are_not_announced(indexed):
    conn = indexed({
        "corpus/leads.html": (page("E-31 reset reset reset lessons."), ["role-lead"]),
        "corpus/playbook.html": (page("E-31 after grid dips."), []),
    })
    assert retrieve(conn, "How do I reset E-31?", TECH).missing_certifications == []


def test_asset_id_pulls_in_model_and_serial_from_fleet_register(indexed):
    conn = indexed({
        config.FLEET_REGISTER: ("asset_id,model,serial\nFRS-P-0031,HCP-350,HCP350-241187\n", []),
        "corpus/bulletin.html": (page("HCP-350 from serial HCP350-240001: casing bolt torque 38 N-m."), []),
        "corpus/other.html": (page("Bolt torque for the gearbox."), []),
    })
    hits = retrieve(conn, "What torque for the casing bolts on FRS-P-0031?", TECH).hits
    assert hits[0].path == config.FLEET_REGISTER  # the asset row itself is always included
    assert hits[1].path == "corpus/bulletin.html"


def test_asset_short_number_matches_how_people_write_it(indexed):
    conn = indexed({"corpus/chat.html": (page("0142 tripped E-14 again overnight"), [])})
    assert retrieve(conn, "TAC-C-0142 has E-14, seen before?", TECH).hits


def test_rare_codes_outweigh_common_words(indexed):
    common = " ".join(["check the unit and check the case"] * 5)
    files = {f"corpus/filler{i}.html": (page(f"{common} page {i}"), []) for i in range(5)}  # unique, so not deduped
    files["corpus/spec.html"] = (page("HCP-200 impeller clearance 0.38 - 0.50 mm"), [])
    conn = indexed(files)
    assert search(conn, "Check the casing clearance on the HCP-200", [])[0].path == "corpus/spec.html"


def test_per_document_cap(indexed):
    rows = "\n".join(f"{n},E-14 on TAC-C-0142" for n in range(10))
    conn = indexed({"corpus/history.csv": (f"wo,notes\n{rows}\n", []), "corpus/manual.html": (page("E-14 discharge temperature"), [])})
    hits = search(conn, "E-14 TAC-C-0142", [], per_doc=4)
    assert sum(h.path == "corpus/history.csv" for h in hits) == 4
    assert any(h.path == "corpus/manual.html" for h in hits)


def test_empty_query_returns_nothing(indexed):
    conn = indexed({"corpus/p.html": (page("anything"), [])})
    assert search(conn, "the and of", []) == []
