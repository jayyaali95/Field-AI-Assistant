from copilot.ingest import ingest

PAGE = "<div id='main-content'><h2>E-31</h2><p>Reset needs HV.</p></div>"


def test_skips_unlisted_empty_and_duplicate_files(conn, corpus):
    data = corpus({
        "corpus/a/listed.html": (PAGE, ["cert-hv"]),
        "corpus/a/unlisted.html": (PAGE, None),  # not in permissions.json
        "corpus/a/empty.html": ("", []),
        "corpus/b/copy.html": (PAGE, ["cert-hv"]),  # same bytes and groups as listed.html
    })
    report = ingest(conn, data)

    assert report.indexed == {"corpus/a/listed.html": 1}
    assert "not in permissions.json" in report.skipped["corpus/a/unlisted.html"]
    assert report.skipped["corpus/a/empty.html"] == "empty file"
    assert report.skipped["corpus/b/copy.html"] == "duplicate of corpus/a/listed.html"
    assert conn.execute("SELECT required_groups FROM documents").fetchone()["required_groups"] == ["cert-hv"]


def test_same_bytes_with_different_permissions_are_both_kept(conn, corpus):
    data = corpus({"corpus/x.html": (PAGE, ["family-pumps"]), "corpus/y.html": (PAGE, ["role-lead"])})
    assert len(ingest(conn, data).indexed) == 2


def test_codes_are_searchable_after_ingest(conn, corpus):
    data = corpus({"corpus/p.html": ("<p>E-31 VFD overcurrent on TAC-C-0143.</p>", [])})
    ingest(conn, data)
    assert conn.execute("SELECT 1 FROM chunks WHERE tsv @@ to_tsquery('simple', 'e31 & tacc0143')").fetchone()


def test_reingest_replaces_documents_and_keeps_work_orders(conn, corpus):
    data = corpus({"corpus/p.html": ("<p>one</p>", [])})
    conn.execute("INSERT INTO work_orders (requested_by, question, draft) VALUES ('u01', 'q', '{}')")
    ingest(conn, data)
    ingest(conn, data)

    assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM work_orders").fetchone()["n"] == 1
