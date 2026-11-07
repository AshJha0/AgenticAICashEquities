"""Prompt registry (PLAN 3A.7): system prompts are versioned files reviewed like code.

Each prompt lives in ``llm/prompts/<id>.md`` and starts with a header line
``<!-- prompt: <id> | version: <n> -->``. Its identity is the sha256 of its *canonical bytes*
(UTF-8, ``\\n`` line endings, trailing whitespace stripped per line, one final newline), so a
checkout with CRLF line endings hashes the same as one with LF.

The version and hash travel with every model call (``LLMRequest.metadata["prompt"]``), into the
per-call ``LLMCallRecord`` and into the report (``InvestigationReport.prompts``). A golden snapshot
(``tests/cassettes/prompt_hashes.json``) pins every hash; changing a prompt fails CI until the
version is bumped, the snapshot updated and CHANGELOG names ``<id>@<version>``.

All prompts state the architectural boundary explicitly: the model reasons and explains, it
never computes metrics and never invents evidence ids.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

_HEADER = re.compile(r"^<!-- prompt: (?P<id>[a-z_]+) \| version: (?P<version>\d+) -->\n")
PROMPT_IDS = ("planner", "research_planner", "report", "research_report", "critic")


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    text: str
    sha256: str

    def ref(self) -> dict[str, str]:
        """What is recorded in metadata, call records, traces and reports."""
        return {"id": self.id, "version": self.version, "sha256": self.sha256}


def canonical_bytes(raw: str) -> bytes:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return ("\n".join(line.rstrip() for line in lines).strip("\n") + "\n").encode("utf-8")


def parse_prompt(raw: str, expected_id: str | None = None) -> Prompt:
    canonical = canonical_bytes(raw)
    text = canonical.decode("utf-8")
    match = _HEADER.match(text)
    if match is None:
        raise ValueError(f"prompt {expected_id!r} lacks the '<!-- prompt: id | version: n -->' header")
    if expected_id is not None and match["id"] != expected_id:
        raise ValueError(f"prompt file {expected_id!r} declares id {match['id']!r}")
    return Prompt(match["id"], match["version"], text[match.end() :], hashlib.sha256(canonical).hexdigest())


@cache
def get_prompt(prompt_id: str) -> Prompt:
    raw = resources.files("ceap.llm").joinpath("prompts", f"{prompt_id}.md").read_text(encoding="utf-8")
    return parse_prompt(raw, prompt_id)


def registry() -> dict[str, Prompt]:
    return {pid: get_prompt(pid) for pid in PROMPT_IDS}


PLANNER_SYSTEM_PROMPT = get_prompt("planner").text
RESEARCH_PLANNER_SYSTEM_PROMPT = get_prompt("research_planner").text
REPORT_SYSTEM_PROMPT = get_prompt("report").text
RESEARCH_REPORT_SYSTEM_PROMPT = get_prompt("research_report").text
CRITIC_SYSTEM_PROMPT = get_prompt("critic").text
