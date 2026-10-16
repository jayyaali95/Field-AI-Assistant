"""Answer a question for one technician from the sources they may see."""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal

import psycopg
from pydantic import BaseModel, Field

from . import access, llm
from .access import User
from .search import Hit, retrieve

NOT_COVERED = "None of the documents you have access to cover this."


class WorkOrderDraft(BaseModel):
    asset_id: str | None = Field(description="Asset ID from the fleet register, e.g. FRS-P-0031. Null if unknown.")
    fault_code: str | None = Field(description="Fault code exactly as on the controller, e.g. E-31. Null if none.")
    summary: str = Field(description="One line: what's wrong and what is proposed.")
    work_steps: list[str] = Field(description="Expected steps in order, citing procedure numbers. Never copy a controlled procedure's steps.")
    parts: list[str] = Field(description="Part numbers with quantities, only as given in the sources, e.g. '63-1180 x2'. Empty list if none.")
    certification_required: list[Literal["None", "HV", "Refrigerant", "Confined space"]]
    estimated_hours: float | None = Field(description="Hours on site, if the sources give a basis for it.")
    priority: Literal["Low", "Medium", "High", "Emergency"]
    campaign_code: str | None = Field(description="OEM campaign code if a bulletin applies to this serial.")


class ModelAnswer(BaseModel):
    covered: bool = Field(description="True only if the sources answer the question for the equipment asked about.")
    answer: str = Field(description="The answer for the technician, with [S#] after every factual claim.")
    work_order: WorkOrderDraft | None = Field(description="Draft when follow-up is needed, otherwise null.")


@dataclass
class Citation:
    marker: str  # "S3"
    title: str
    location: str
    path: str
    source_system: str
    excerpt: str


@dataclass
class Answer:
    covered: bool
    text: str
    citations: list[Citation] = field(default_factory=list)
    missing_certifications: list[str] = field(default_factory=list)
    work_order: WorkOrderDraft | None = None


SYSTEM = """You are a field service copilot for Corvane technicians. The technician is in front of a machine,
often on a phone, and needs to know what's wrong and what to do next. A wrong answer can hurt someone or break a
machine, so they only trust what they can check.

Rules:
1. Use only the numbered sources you are given, never your own knowledge.
2. Put the source marker after every factual claim, like [S3] or [S2][S5]. If you can't cite it, don't say it.
3. If the sources don't answer the question for the exact model, serial or asset asked about, set covered=false and
   say plainly that no document they have access to covers it. Do not answer with values for a different model.
4. When sources disagree, this order decides:
   a service bulletin for the serial range it names; then the newest revision of the OEM manual; then Corvane
   controlled procedures (SOP-, HV-, CS-, FS-STD-); then legacy Kyltek work instructions (KT-WI-); then wiki pages,
   chat, email and case notes. Check the asset's serial from the fleet register against bulletin serial ranges.
   Name the conflict: which documents, what each says, which one applies and why.
5. When the question names an asset, read its case and maintenance records in the sources. If a past job on it
   matters here, say it in one line with the date and case number: it failed after using the wrong value, or work
   was done by the procedure that doesn't apply (old torque, a top-up without a leak test or campaign check).
   For "has this happened before" questions give dates, case or work order numbers, what was found and what was
   done. If the records or site notes point at why it keeps coming back (where the unit sits, how a harness is
   routed), say so and what would stop it.
6. With a limit or spec, also give what the source says to do when it's out of limit, and name the document the
   key value comes from.
7. Certified work: do not give the steps of work that needs a certification the technician does not hold, even if a
   source describes them. HV = inside a VFD cabinet, starter panel or motor terminal box. Refrigerant = connecting
   gauges, recovering or charging refrigerant. Confined space = entering a permit space. Say what they may do
   themselves and that a certified technician is needed. If the prompt says a document needing a certification they
   lack matches the question, say the work needs that certification; never guess what that document says.
8. Draft a work order when follow-up is needed: parts to order, a certified technician or specialist, a return visit,
   an OEM campaign, or an escalation. Otherwise work_order is null. Follow the Work Order Writing Guide: asset ID from
   the fleet register, the fault code as shown, procedure numbers instead of copied steps, part numbers only from the
   sources, the certification for the worst case, priority by the rules. Leave a field null or empty if the sources
   don't give it.
9. Write for a technician on a phone: lead with what it means and what to do first, then short numbered steps.
   Exact values, units and part numbers. No filler."""


def build_prompt(user: User, question: str, hits: list[Hit], missing_certifications: list[str]) -> str:
    groups = access.group_descriptions()
    lines = [
        f"Technician: {user.name}, {user.title}",
        "Groups: " + "; ".join(f"{g} ({groups.get(g, g)})" for g in user.groups),
        f"Today: {date.today().isoformat()}",
    ]
    if missing_certifications:
        lines.append(
            "A document restricted to technicians with "
            + ", ".join(f"{g} ({groups.get(g, g)})" for g in missing_certifications)
            + " matches this question. The technician does not hold that certification and cannot see it."
        )
    lines += ["", f"Question: {question}", "", "Sources:"]
    for i, hit in enumerate(hits, 1):
        meta = ", ".join(x for x in (hit.source_system, hit.note) if x)
        lines += [f"[S{i}] {hit.title} | {hit.location} ({meta})", hit.text.strip(), ""]
    return "\n".join(lines)


def answer(
    conn: psycopg.Connection,
    user: User,
    question: str,
    ask_llm: Callable[[str, str], ModelAnswer] | None = None,
) -> Answer:
    ask_llm = ask_llm or (lambda system, prompt: llm.structured(system, prompt, ModelAnswer))
    found = retrieve(conn, question, list(user.groups))
    if not found.hits:  # nothing to ground an answer in; don't ask the model to guess
        return Answer(False, NOT_COVERED, missing_certifications=found.missing_certifications)

    result = ask_llm(SYSTEM, build_prompt(user, question, found.hits, found.missing_certifications))
    text, citations = _check_citations(result.answer, found.hits)
    covered = result.covered and bool(citations)
    work_order = result.work_order
    if result.covered and not citations:  # claims coverage but cites nothing we gave it
        text, work_order = NOT_COVERED, None  # a draft built on that answer is just as ungrounded
    return Answer(covered, text, citations, found.missing_certifications, work_order)


def _check_citations(text: str, hits: list[Hit]) -> tuple[str, list[Citation]]:
    """Keep markers that point at a source we actually sent, drop the rest."""
    used: dict[int, Citation] = {}

    def keep(match: re.Match) -> str:
        n = int(match.group(1))
        if not 1 <= n <= len(hits):
            return ""
        if n not in used:
            h = hits[n - 1]
            used[n] = Citation(f"S{n}", h.title, h.location, h.path, h.source_system, h.text[:600])
        return match.group(0)

    # "[S1, S3]" -> "[S1][S3]", so both styles are checked the same way
    text = re.sub(r"\[(S\d+(?:\s*,\s*S\d+)+)\]", lambda m: "".join(f"[{s.strip()}]" for s in m.group(1).split(",")), text)
    text = re.sub(r"\[S(\d+)\]", keep, text)
    return text.strip(), list(used.values())
