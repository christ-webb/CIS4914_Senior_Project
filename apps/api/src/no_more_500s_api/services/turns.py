"""Per-turn projection derived from the append-only run event log.

Session lifecycle, agent turn status and task outcome are separate concepts:

* A turn reaches a terminal status only from authoritative evidence (Cursor
  ``stop``, Codex ``Stop``/``Interrupt``, an SDK finalize) and is never
  rewritten afterwards by later tool activity or session lifecycle events.
* Cursor ``afterAgentResponse`` fires after every assistant message, including
  intermediate ones, so it only marks a turn ``response_observed``
  (provisional) until ``stop`` arrives.
* Task outcome is never inferred from turn completion. It is ``unverified``
  unless an event explicitly reports (agent self-report) or verifies
  (independent verifier) it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

EXECUTING = "executing"
RESPONSE_OBSERVED = "response_observed"
COMPLETED = "completed"
FAILED = "failed"
INTERRUPTED = "interrupted"

TERMINAL_TURN_STATUSES = (COMPLETED, FAILED, INTERRUPTED)
TURN_STOP_PROGRESS = {"stop", "Stop", "agentStop"}
TURN_RESPONSE_PROGRESS = {"afterAgentResponse"}
TURN_THOUGHT_PROGRESS = {"afterAgentThought"}
TURN_PROMPT_PROGRESS = {"UserPromptSubmit", "beforeSubmitPrompt"}
TURN_INTERRUPT_PROGRESS = {"Interrupt"}

MAX_TURN_DETAILS = 200

REPORTED_OUTCOMES = {"success": "reported_success", "failure": "reported_failure"}
VERIFIED_OUTCOMES = {
    "passed": "verified_success",
    "success": "verified_success",
    "failed": "verified_failure",
    "failure": "verified_failure",
}


@dataclass
class Turn:
    key: str
    identity: str
    status: str
    evidence: str
    provenance: str
    started_at: datetime
    ordinal: int
    ended_at: datetime | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_TURN_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_key": self.key,
            "identity": self.identity,
            "status": self.status,
            "evidence": self.evidence,
            "provenance": self.provenance,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


# Cursor reports per-step IDs such as "<generation uuid>-12-5l8b" on
# afterAgentThought; they belong to the generation's turn, not a new one.
_STEP_SUFFIXED_ID = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-\d+-[0-9a-z]+$",
    re.IGNORECASE,
)


def turn_identity(attributes: dict[str, Any] | None) -> str | None:
    if not isinstance(attributes, dict):
        return None
    for key in ("nm5.turn_id", "nm5.generation_id"):
        value = attributes.get(key)
        if isinstance(value, str) and value:
            step = _STEP_SUFFIXED_ID.match(value)
            return step.group(1) if step else value
    return None


def _stop_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"aborted", "cancelled", "canceled", "interrupted"}:
        return INTERRUPTED
    if value in {"error", "failed", "failure"}:
        return FAILED
    if value in {"", "completed", "complete", "ok", "success"}:
        return COMPLETED
    # An unrecognised stop status ends the loop but is not normal completion.
    return INTERRUPTED


@dataclass
class TurnLedger:
    source: str
    turns: dict[str, Turn] = field(default_factory=dict)
    reported_outcome: str | None = None
    verified_outcome: str | None = None
    verifier: str | None = None
    _ordinal: int = 0

    def _create(self, identity: str | None, at: datetime, status: str, evidence: str) -> Turn:
        self._ordinal += 1
        key = f"id:{identity}" if identity else f"ordinal:{self._ordinal}"
        turn = Turn(
            key=key,
            identity="provider" if identity else "ordinal",
            status=status,
            evidence=evidence,
            provenance="provisional" if status == RESPONSE_OBSERVED else "observed",
            started_at=at,
            ordinal=self._ordinal,
        )
        self.turns[key] = turn
        return turn

    def _open_turn(self) -> Turn | None:
        open_turns = [turn for turn in self.turns.values() if not turn.terminal]
        return max(open_turns, key=lambda turn: turn.ordinal) if open_turns else None

    def _resolve(self, identity: str | None) -> Turn | None:
        if identity:
            return self.turns.get(f"id:{identity}")
        return self._open_turn()

    def observe_work(self, attributes: dict[str, Any] | None, at: datetime) -> None:
        """Tool or model work only opens or resumes a turn when it names one."""
        identity = turn_identity(attributes)
        if not identity:
            return
        turn = self.turns.get(f"id:{identity}")
        if turn is None:
            self._create(identity, at, EXECUTING, f"{self.source}.work")
        elif turn.status == RESPONSE_OBSERVED:
            turn.status = EXECUTING

    def observe_heartbeat(
        self, progress: str | None, attributes: dict[str, Any] | None, at: datetime
    ) -> None:
        attrs = attributes if isinstance(attributes, dict) else {}
        self._observe_outcome(attrs)
        if not progress:
            return
        identity = turn_identity(attrs)
        evidence = f"{self.source}.{progress}"
        if progress in TURN_PROMPT_PROGRESS:
            if identity and f"id:{identity}" in self.turns:
                return
            self._create(identity, at, EXECUTING, evidence)
            return
        if progress in TURN_THOUGHT_PROGRESS:
            turn = self._resolve(identity)
            if turn is None:
                if identity:
                    self._create(identity, at, EXECUTING, evidence)
                return
            if turn.status == RESPONSE_OBSERVED:
                turn.status = EXECUTING
            return
        if progress in TURN_RESPONSE_PROGRESS:
            turn = self._resolve(identity)
            if turn is None:
                self._create(identity, at, RESPONSE_OBSERVED, evidence)
                return
            if turn.terminal:
                return
            turn.status = RESPONSE_OBSERVED
            turn.evidence = evidence
            turn.provenance = "provisional"
            return
        if progress in TURN_STOP_PROGRESS or progress in TURN_INTERRUPT_PROGRESS:
            interrupt = progress in TURN_INTERRUPT_PROGRESS
            status = INTERRUPTED if interrupt else _stop_status(attrs.get("nm5.turn_status"))
            turn = self._resolve(identity)
            if turn is None:
                if interrupt and not identity:
                    return
                turn = self._create(identity, at, status, evidence)
            elif turn.terminal:
                return
            turn.status = status
            turn.evidence = evidence
            turn.provenance = "observed"
            turn.ended_at = at

    def observe_finalize(
        self, status: str, attributes: dict[str, Any] | None, at: datetime, *, hook: bool
    ) -> None:
        self._observe_outcome(attributes if isinstance(attributes, dict) else {})
        for turn in self.turns.values():
            if turn.status == EXECUTING:
                turn.status = INTERRUPTED
                turn.evidence = "session.finalize"
                turn.provenance = "inferred"
                turn.ended_at = at
        if hook or self.turns:
            return
        if status not in {"completed", "failed"}:
            return
        turn_status = COMPLETED if status == "completed" else FAILED
        turn = self._create(None, at, turn_status, "sdk.finalize")
        turn.provenance = "reported"
        turn.ended_at = at

    def mark_stale(self, at: datetime) -> None:
        for turn in self.turns.values():
            if turn.status == EXECUTING:
                turn.status = INTERRUPTED
                turn.evidence = "inactivity"
                turn.provenance = "inferred"
                turn.ended_at = turn.ended_at or at

    def _observe_outcome(self, attributes: dict[str, Any]) -> None:
        reported = str(attributes.get("nm5.task_outcome") or "").strip().lower()
        if reported in REPORTED_OUTCOMES:
            self.reported_outcome = REPORTED_OUTCOMES[reported]
        verified = str(attributes.get("nm5.task_verification") or "").strip().lower()
        if verified in VERIFIED_OUTCOMES:
            self.verified_outcome = VERIFIED_OUTCOMES[verified]
            verifier = attributes.get("nm5.task_verifier")
            self.verifier = str(verifier) if verifier else "verifier"

    def ordered(self) -> list[Turn]:
        return sorted(self.turns.values(), key=lambda turn: turn.ordinal)

    def summary_attributes(self) -> dict[str, Any]:
        turns = self.ordered()
        counts = dict.fromkeys((*TERMINAL_TURN_STATUSES, EXECUTING, RESPONSE_OBSERVED), 0)
        for turn in turns:
            counts[turn.status] += 1
        latest = turns[-1] if turns else None
        completed_at = [
            turn.ended_at for turn in turns if turn.status == COMPLETED and turn.ended_at
        ]
        identities = {turn.identity for turn in turns}
        if self.verified_outcome:
            outcome, outcome_source = self.verified_outcome, self.verifier
        elif self.reported_outcome:
            outcome, outcome_source = self.reported_outcome, "agent_report"
        else:
            outcome, outcome_source = "unverified", None
        identity: str | None = None
        if identities:
            identity = next(iter(identities)) if len(identities) == 1 else "mixed"
        return {
            "nm5.turns_total": len(turns),
            "nm5.turns_completed": counts[COMPLETED],
            "nm5.turns_failed": counts[FAILED],
            "nm5.turns_interrupted": counts[INTERRUPTED],
            "nm5.turns_executing": counts[EXECUTING],
            "nm5.turns_provisional": counts[RESPONSE_OBSERVED],
            "nm5.latest_turn_status": latest.status if latest else None,
            "nm5.latest_turn_evidence": latest.evidence if latest else None,
            "nm5.latest_turn_completed_at": max(completed_at).isoformat() if completed_at else None,
            "nm5.turn_identity": identity,
            "nm5.task_outcome_status": outcome,
            "nm5.task_outcome_source": outcome_source,
            "nm5.turns": [turn.as_dict() for turn in turns[-MAX_TURN_DETAILS:]],
        }
