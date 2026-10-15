# Decisions and tradeoffs

## Architecture: UI, API, Postgres, Claude

Three services. The UI only talks to the API, the API owns the database and the LLM key. That keeps permissions in one
place: the UI can't ask for a document it shouldn't see because it never touches the data.

Postgres does two jobs: full-text search over the chunks and storage for work orders. One database instead of a search
engine plus a database. It's also the natural place to add pgvector later.

Streamlit for the UI because it keeps everything in Python and is enough for a demo on a laptop or phone. A real
field app would be a proper mobile web app.

## Retrieval: keyword search, no embeddings

Technician questions are full of exact codes: `E-31`, `P-07`, `HCP-500`, `TAC-C-0143`, part numbers. Keyword search
is very good at those and embeddings are not. Anthropic has no embeddings API, so embeddings would also mean a second
provider or a local model in the image.

Postgres splits `E-31` into `e` and `-31`, which is useless. Before indexing and before querying, codes with a digit and
a hyphen or dot are collapsed (`E-31` -> `e31`, `HCP350-241187` -> `hcp350241187`). Plain words keep English stemming.

The query is an OR of all terms, ranked with `ts_rank`. Postgres ranking has no IDF, so a chunk that says "check" ten
times can beat a rarer exact match. In practice the codes carry the query. This is the first thing I'd replace (see
scaling).

Asset IDs are resolved through the fleet register before searching, so "FRS-P-0031" also searches for `HCP-350` and
its serial. Without this the torque question finds the IOM but not the bulletin that overrides it.

## Chunking: by the unit a technician would cite

- PDF: one chunk per page, cited as `p. N`.
- DOCX procedures: one chunk per heading section, tables kept in place inside their section.
- Confluence: one chunk per `h2/h3` section. The page's "last modified" date goes in the source header so the model can
  tell a 2024 wiki page from a 2026 manual.
- Email: one chunk per message in the thread.
- Teams: one chunk per thread.
- CSV and Salesforce: one chunk per row/case, cited by work order or case number.

## Permissions

- A user sees a document only if they hold every group it lists. Filtered in SQL, before anything reaches the model.
  Restricted text never goes into the prompt, so a prompt injection or a model mistake can't leak it.
- A file missing from permissions.json is not indexed at all (fail closed). This catches
  `VRS-110-VFD-quick-reset-unofficial.html`, a personal copy of the HV reset procedure that also skips the insulation test.
- When restricted documents match the question, the copilot says that a document needing e.g. HV certification
  covers it, and that the work needs a certified technician. It does not quote, summarise or name it. Reason: a tech
  who is told "nothing covers this" might improvise; a tech who is told "this is HV work" stops and raises a work order,
  which is what FS-STD-001 6.4 asks for. The cost is that the user learns a restricted document exists. Titles of these
  procedures are already listed in FS-STD-001, which everyone can read, so that is not new information.
- Even with permitted sources, the prompt tells the model not to give steps for certified work the user isn't
  certified for. Permissions are the hard guarantee; the prompt is the second line.

## Messy data

- Empty file (`Inspection_Checklist_Q3_FINAL.docx`): skipped, reported.
- Exact duplicate (`NK600_manual_final_v2.pdf` = `Nordkyl_NK-600_Service_Manual_RevC.pdf`): indexed once, keeping the
  manual library copy. Only deduped when both copies have the same permissions.
- Scanned 1998 HCP-200 manual: OCR with Tesseract for pages with no text layer.
- Conflicting sources (Rev B vs Rev C, IOM vs bulletin, KT-WI-14 vs SOP-020, wiki vs manual): handled in the prompt
  with an explicit order: bulletin for its serial range, then the newest OEM manual, then Corvane controlled procedures,
  then legacy Kyltek instructions, then wiki, chat and email. The model is asked to name the conflict, not hide it.
  This comes straight from FS-STD-001 sections 8 and 11.

## Answer format

Claude returns structured JSON (covered, answer, work order). Every claim ends with `[S#]`. After the call the
citations are checked: unknown ones are dropped, and an answer that claims coverage with no valid citation becomes
"not covered". If search finds nothing permitted, no LLM call is made.

The model is `claude-opus-5-5`, configurable with `ANTHROPIC_MODEL`. I did not add the server-side refusal fallback
option; a refused request just returns an error.

## Work orders

Fields follow the Work Order Writing Guide: asset, fault code, summary, work steps (procedure numbers, not copied
steps), parts, certification required, estimated hours, priority, campaign code. Saved as `draft`. Only users in
`role-lead` can approve or reject, matching FS-STD-001 6.2 (supervisor approval; in the EU the team lead is the
supervisor). Approval flips the status; in a real deployment that's where it would be pushed to Salesforce. Nothing is
sent anywhere else.

## Eval

Each of the 10 questions runs through the same pipeline as the UI. Claude grades the answer against the reference:
correct only if every decision-relevant fact in the reference is there (values, part numbers, conditions, the
"don't do X" parts) and nothing contradicts it. Extra correct detail is fine. The judge is the same model family
as the answerer, which is a known bias; the per-question reasons are saved so a person can check the grading.

## Cut

- Embeddings / hybrid search, reranking.
- Multi-turn chat. Each question stands alone.
- Editing a work order draft before approval.
- Real auth. The UI has a user picker; the API trusts the user id it gets.
- Incremental ingestion. Re-ingest rebuilds the index (work orders are kept).

## At 100,000 documents

- Ranking breaks first: no IDF, OR queries return huge candidate sets. Move to BM25 (ParadeDB `pg_search` or
  OpenSearch) plus vector search, merged with reciprocal rank fusion, and a reranker on the top 50.
- Ingestion: single process, full rebuild, OCR inline. Needs a queue, workers, incremental updates by file hash, and
  permissions synced from the source systems instead of one JSON file.
- Permission filter as `required_groups <@ user_groups` on every chunk is fine in Postgres with an index, but with ACLs
  changing often it should be resolved per document and cached, not copied onto each chunk.
- The fleet register lookup becomes a proper asset service.
