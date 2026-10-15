import re

# Codes like E-31, HCP-500, TAC-C-0143, 41-2207, HCP350-241187: letters/digits joined by "-" or ".",
# with at least one digit somewhere.
CODE = re.compile(r"\b[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)+\b")


def normalize(text: str) -> str:
    """Collapse codes into one token so Postgres full-text search keeps them whole.

    Postgres turns "E-31" into "e" and "-31", which matches almost anything. "e31" stays one word.
    The same function runs on documents and on questions, so both sides agree.
    """
    return CODE.sub(lambda m: re.sub(r"[-.]", "", m.group(0)) if any(c.isdigit() for c in m.group(0)) else m.group(0), text)
