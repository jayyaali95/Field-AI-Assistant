import json

from copilot.ingest import ingest


def make_corpus(tmp_path, files: dict[str, str], permissions: dict[str, dict]):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (tmp_path / "permissions.json").write_text(json.dumps({"documents": permissions}))
    return tmp_path


def perm(groups, system="confluence"):
    return {"title": "T", "source_system": system, "required_groups": groups}


def test_skips_unlisted_empty_and_duplicate_files(conn, tmp_path):
    page = "<div id='main-content'><h2>E-31</h2><p>Reset needs HV.</p></div>"
    data = make_corpus(
        tmp_path,
        {
            "corpus/a/listed.html": page,
            "corpus/a/unlisted.html": page,  # not in permissions.json
            "corpus/a/empty.html": "",
            "corpus/b/copy.html": page,  # same bytes and groups as listed.html
        },
        {
            "corpus/a/listed.html": perm(["cert-hv"], "manual_library"),
            "corpus/a/empty.html": perm([]),
            "corpus/b/copy.html": perm(["cert-hv"], "sharepoint"),
        },
    )
    report = ingest(conn, data)

    assert report.indexed == {"corpus/a/listed.html": 1}
    assert "not in permissions.json" in report.skipped["corpus/a/unlisted.html"]
    assert report.skipped["corpus/a/empty.html"] == "empty file"
    assert report.skipped["corpus/b/copy.html"] == "duplicate of corpus/a/listed.html"
    row = conn.execute("SELECT required_groups FROM documents").fetchone()
    assert row["required_groups"] == ["cert-hv"]


def test_same_bytes_with_different_permissions_are_both_kept(conn, tmp_path):
    page = "<div id='main-content'><p>Torque 38 N-m.</p></div>"
    data = make_corpus(
        tmp_path,
        {"corpus/x.html": page, "corpus/y.html": page},
        {"corpus/x.html": perm(["family-pumps"]), "corpus/y.html": perm(["role-lead"])},
    )
    assert len(ingest(conn, data).indexed) == 2


def test_codes_are_searchable_after_ingest(conn, tmp_path):
    data = make_corpus(
        tmp_path,
        {"corpus/p.html": "<div id='main-content'><p>E-31 VFD overcurrent on TAC-C-0143.</p></div>"},
        {"corpus/p.html": perm([])},
    )
    ingest(conn, data)
    hit = conn.execute("SELECT 1 FROM chunks WHERE tsv @@ to_tsquery('simple', 'e31 & tacc0143')").fetchone()
    assert hit


def test_reingest_replaces_documents_and_keeps_work_orders(conn, tmp_path):
    data = make_corpus(tmp_path, {"corpus/p.html": "<p>one</p>"}, {"corpus/p.html": perm([])})
    conn.execute("INSERT INTO work_orders (requested_by, question, draft) VALUES ('u01', 'q', '{}')")
    ingest(conn, data)
    ingest(conn, data)

    assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM work_orders").fetchone()["n"] == 1
