# Decisions and tradeoffs

## Architecture: UI, API, Postgres, LLM

Three services, run only through docker compose (tests too: `docker compose run --rm api pytest`). Same environment
for everyone, and Tesseract and Postgres come with it. The UI only talks to the API, the API owns the database and the LLM key. That keeps permissions in one
place: the UI can't ask for a document it shouldn't see because it never touches the data.

Postgres does two jobs: full-text search over the chunks and storage for work orders. One database instead of a search
engine plus a database. It's also the natural place to add pgvector later.

Streamlit for the UI because it keeps everything in Python and is enough for a demo on a laptop or phone. A real
field app would be a proper mobile web app.

## Retrieval: keyword search, no embeddings

Technician questions are full of exact codes: `E-31`, `P-07`, `HCP-500`, `TAC-C-0143`, part numbers. Keyword search
is very good at those and embeddings are not. Embeddings would also need a second model (an embedding API or a
local model in the image) for a corpus this small.

Postgres splits `E-31` into `e` and `-31`, which is useless. Before indexing and before querying, codes with a digit and
a hyphen or dot are collapsed (`E-31` -> `e31`, `HCP350-241187` -> `hcp350241187`). Plain words keep English stemming.

The query is an OR of all terms. Ranking is BM25, written in SQL on top of the Postgres full-text index:
- Postgres' own `ts_rank` has no IDF. With it, "casing" (stemmed to "case") matched every Salesforce case as strongly
  as `hcp200` matched the HCP-200 manual, and the clearance question got case records. Term document counts come from
  `ts_stat`, snapshotted into a materialized view at ingest.
- Term frequency comes from the positions stored in the tsvector.
- A term that also appears in the title or section heading counts double (BM25F-style), so "HCP-200" prefers the
  HCP-200 manual over HCP-350 pages that say "clearance" more often.
- Length normalization is low (b = 0.2). Chunks range from one CSV row to a whole procedure section, and with the
  usual 0.75 the HV-PROC-07 procedure section lost to its own revision history.
- At most 4 chunks per document, 20 in total, so one big export can't fill the context.

These settings were picked by checking, for each eval question, whether the sources the reference answer depends on
are retrieved for that user. That check is a test (`tests/test_retrieval_corpus.py`) so ranking changes can't quietly
break it. Ten questions is a small set to tune on; the risk of overfitting is real, which is why I kept the changes
to standard BM25 ideas instead of per-question tricks.

Asset IDs are resolved through the fleet register before searching, so "FRS-P-0031" also searches for `HCP-350` and
its serial. Without this the torque question finds the IOM but not the bulletin that overrides it. The last four
digits are searched too, because people write "0142 tripped again" in chat and site notes.

## Chunking: by the unit a technician would cite

- PDF: one chunk per page, cited as `p. N`.
- DOCX procedures: one chunk per top-level section ("6. Procedure"), with subsections and step tables kept inside it
  in order. Splitting at 6.1, 6.2... scattered a procedure across chunks and the reset steps got lost.
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
- When a document restricted by a certification the user lacks matches the question better than anything they can
  see, the copilot is told which certification is missing (e.g. `cert-hv`), nothing else. It says the work needs that
  certification and drafts a work order for a certified technician. It does not quote, summarise or name the document.
  Reason: a tech who is told "nothing covers this" might improvise; a tech who is told "this is HV work" stops and
  raises a work order, which is what FS-STD-001 6.4 asks for. The cost is that the user learns a restricted procedure
  exists; their titles are already listed in FS-STD-001, which everyone can read.
- Only certifications are announced. Region, equipment family and team-lead restrictions are not: telling a conveyor
  tech that "a pump document matches" is noise, and lead-only pages name customers and individual jobs.
- Even with permitted sources, the prompt tells the model not to give steps for certified work the user isn't
  certified for. Permissions are the hard guarantee; the prompt is the second line.

## Messy data

- Empty file (`Inspection_Checklist_Q3_FINAL.docx`): skipped, reported.
- Exact duplicate (`NK600_manual_final_v2.pdf` = `Nordkyl_NK-600_Service_Manual_RevC.pdf`): indexed once, keeping the
  manual library copy. Only deduped when both copies have the same permissions.
- Scanned 1998 HCP-200 manual: OCR with Tesseract for pages with no text layer. Grayscale plus a median filter to
  remove scanner speckle, and `--psm 6` so table rows stay on one line. With the default settings the value column of
  the technical data table was lost, including the clearance spec. Still imperfect: the casing torque value on p. 3
  doesn't survive. Better OCR (or a vision model) is on the "another week" list.
- Partly corrupt PDF (`Vastra_SB-2026-015`, page 2 won't decode): readable pages are indexed, the bad page is reported.
- Conflicting sources (Rev B vs Rev C, IOM vs bulletin, KT-WI-14 vs SOP-020, wiki vs manual): handled in the prompt
  with an explicit order: bulletin for its serial range, then the newest OEM manual, then Corvane controlled procedures,
  then legacy Kyltek instructions, then wiki, chat and email. The model is asked to name the conflict, not hide it.
  This comes straight from FS-STD-001 sections 8 and 11.

## Answer format

The LLM returns structured JSON (covered, answer, work order). Every claim ends with `[S#]`. After the call the
citations are checked: unknown ones are dropped, and an answer that claims coverage with no valid citation becomes
"not covered". If search finds nothing permitted, no LLM call is made.

## LLM provider

Provider-agnostic through LangChain: `init_chat_model(LLM_MODEL)` with `LLM_MODEL="<provider>:<model>"`, and
`with_structured_output(<pydantic model>, method="json_schema")` for the JSON. Switching provider is a `.env` change.
Anthropic and OpenAI adapters are installed; another provider is one extra `langchain-<provider>` package.

Why LangChain and not LiteLLM or a hand-written adapter: we only need "chat call that returns a validated Pydantic
object", and LangChain's structured output does that the same way across providers. LiteLLM is lighter, but structured
output support differs per provider and we'd be validating JSON ourselves. A hand-written adapter is fine for two
providers and becomes our problem for the third. Only `langchain-core` features are used, no chains or agents.

`method="json_schema"` uses each provider's native structured output instead of a forced tool call; recent Claude
models reject forced tool calls.

Default is `anthropic:claude-opus-5-5`, since I had to pick one for the placeholder key.

## Work orders

Fields follow the Work Order Writing Guide: asset, fault code, summary, work steps (procedure numbers, not copied
steps), parts, certification required, estimated hours, priority, campaign code. Saved as `draft`. Only users in
`role-lead` can approve or reject, matching FS-STD-001 6.2 (supervisor approval; in the EU the team lead is the
supervisor). Approval flips the status; in a real deployment that's where it would be pushed to Salesforce. Nothing is
sent anywhere else.

## Eval

Each of the 10 questions runs through the same pipeline as the UI. An LLM grades the answer against the reference:
correct only if every decision-relevant fact in the reference is there (values, part numbers, conditions, the
"don't do X" parts) and nothing contradicts it. Extra correct detail is fine. By default the judge is the same model
as the answerer, which is a known bias (`JUDGE_MODEL` can point it elsewhere); the per-question reasons are saved so a
person can check the grading.

## Cut

- Embeddings / hybrid search, reranking.
- Multi-turn chat. Each question stands alone.
- Editing a work order draft before approval.
- Real auth. The UI has a user picker; the API trusts the user id it gets.
- Incremental ingestion. Re-ingest rebuilds the index (work orders are kept).

## At 100,000 documents

- Ranking breaks first. The hand-written BM25 unnests every matching chunk's tsvector per query, and OR queries
  match a large share of the corpus. Fine for 1,500 chunks, not for millions. Move to a real BM25 index (ParadeDB
  `pg_search` or OpenSearch) plus vector search, merged with reciprocal rank fusion, and a reranker on the top 50.
- Ingestion: single process, full rebuild, OCR inline. Needs a queue, workers, incremental updates by file hash, and
  permissions synced from the source systems instead of one JSON file.
- Permission filter as `required_groups <@ user_groups` on every chunk is fine in Postgres with an index, but with ACLs
  changing often it should be resolved per document and cached, not copied onto each chunk.
- The fleet register lookup becomes a proper asset service.
