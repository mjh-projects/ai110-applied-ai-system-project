"""Agentic worker-dispatch layer.

An LLM proposes which Worker should do each of the day's ScheduledTasks.
Deterministic code checks that proposal against real constraints (skill
match, working hours, no double-booking, daily capacity), asks the LLM to
fix any violations it finds, and - if the AI still can't produce a valid
plan - falls back to a simple deterministic rule instead of guessing or
crashing. Every attempt is logged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import time

from pawpal_system import ScheduledTask, Worker

logger = logging.getLogger("pawpal.dispatcher")
if not logger.handlers:
    _handler = logging.FileHandler("dispatch.log")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# Guardrail: bounds how many times the AI gets to try again before the
# deterministic fallback takes over. Never retried unboundedly.
MAX_ATTEMPTS = 3


class DispatchError(Exception):
    """Raised when the LLM call itself can't be completed (no key, network,
    bad SDK response, unparseable output) - as opposed to a *valid* response
    that just violates a constraint, which is handled as a validation
    failure instead."""


@dataclass
class DispatchResult:
    """What a dispatch run produced, plus a trace of how it got there."""

    assignments: dict[str, str] = field(default_factory=dict)  # task_id -> worker_id
    reasons: dict[str, str] = field(default_factory=dict)  # task_id -> stated reason
    unassigned: list[str] = field(default_factory=list)  # task_ids left unassigned
    used_fallback: bool = False
    attempts: int = 0
    log: list[str] = field(default_factory=list)  # human-readable trace for the UI


DEFAULT_MODEL = "gemini-2.5-flash"


class Dispatcher:
    """Plans, validates, and (if needed) corrects worker assignments for a day's tasks."""

    def __init__(self, client=None, model: str | None = None):
        # `client` lets tests inject a fake LLM object instead of a real
        # Gemini client. Left as None in production; built lazily on first
        # use so importing this module never requires an API key.
        self._client = client
        # Resolved here (not as a `def __init__(..., model=os.environ...)`
        # default) so GEMINI_MODEL is read fresh each time a Dispatcher is
        # created, not frozen at import time.
        self._model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL

    def _llm_client(self):
        """Return the LLM client, building a real one on first use."""
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:
            raise DispatchError("the google-genai package is not installed") from exc
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise DispatchError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        return self._client

    def dispatch(
        self, scheduled_tasks: list[ScheduledTask], workers: list[Worker]
    ) -> DispatchResult:
        """Assign a worker to each scheduled task, or explain why one couldn't be."""
        result = DispatchResult()
        if not scheduled_tasks:
            return result
        if not workers:
            result.unassigned = [st.task.task_id for st in scheduled_tasks]
            result.log.append("No workers on the roster; nothing could be assigned.")
            logger.warning(
                "dispatch called with no workers for %d task(s)", len(scheduled_tasks)
            )
            return result

        feedback: list[str] | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result.attempts = attempt
            try:
                plan = self._plan_assignments(scheduled_tasks, workers, feedback)
            except (DispatchError, ValueError, json.JSONDecodeError) as exc:
                result.log.append(
                    f"Attempt {attempt}: AI planning failed ({exc}); using fallback rule."
                )
                logger.warning(
                    "attempt %d: AI planning failed (%s); falling back", attempt, exc
                )
                return self._fallback(scheduled_tasks, workers, result)

            violations = self._validate(plan, scheduled_tasks, workers)
            if not violations:
                result.assignments = {tid: worker_id for tid, (worker_id, _) in plan.items()}
                result.reasons = {tid: reason for tid, (_, reason) in plan.items()}
                result.unassigned = [
                    st.task.task_id
                    for st in scheduled_tasks
                    if st.task.task_id not in result.assignments
                ]
                result.log.append(f"Attempt {attempt}: valid plan accepted.")
                logger.info(
                    "dispatch succeeded on attempt %d (%d task(s))",
                    attempt,
                    len(scheduled_tasks),
                )
                return result

            result.log.append(
                f"Attempt {attempt}: {len(violations)} violation(s) found; retrying."
            )
            logger.info("attempt %d: %d violation(s): %s", attempt, len(violations), violations)
            feedback = violations

        result.log.append(f"Gave up after {MAX_ATTEMPTS} attempt(s); using fallback rule.")
        logger.warning("dispatch exhausted %d attempt(s); falling back", MAX_ATTEMPTS)
        return self._fallback(scheduled_tasks, workers, result)

    # ------------------------------------------------------------------
    # Plan: ask the LLM for a task -> worker assignment.
    # ------------------------------------------------------------------

    def _plan_assignments(
        self,
        scheduled_tasks: list[ScheduledTask],
        workers: list[Worker],
        feedback: list[str] | None,
    ) -> dict[str, tuple[str, str]]:
        """Call the LLM and return {task_id: (worker_id, reason)}."""
        client = self._llm_client()
        prompt = self._build_prompt(scheduled_tasks, workers, feedback)
        try:
            response = client.models.generate_content(model=self._model, contents=prompt)
            text = response.text
        except DispatchError:
            raise
        except Exception as exc:  # the SDK's own errors (auth, network, rate limit, ...)
            raise DispatchError(f"LLM call failed: {exc}") from exc
        return self._parse_plan(text, scheduled_tasks, workers)

    def _build_prompt(
        self,
        scheduled_tasks: list[ScheduledTask],
        workers: list[Worker],
        feedback: list[str] | None,
    ) -> str:
        task_lines = []
        for st in scheduled_tasks:
            task = st.task
            task_lines.append(
                f"- id={task.task_id} title={task.title!r} category={task.category!r} "
                f"start={st.start_time:%H:%M} end={st.end_time:%H:%M} "
                f"duration_minutes={task.duration_minutes}"
            )

        worker_lines = []
        for worker in workers:
            skills = ", ".join(worker.skills) if worker.skills else "any (generalist)"
            worker_lines.append(
                f"- id={worker.worker_id} name={worker.name!r} skills=[{skills}] "
                f"hours={worker.available_start:%H:%M}-{worker.available_end:%H:%M} "
                f"max_minutes_per_day={worker.max_minutes_per_day}"
            )

        prompt = (
            "You are dispatching staff for a pet-care business. Assign each task "
            "below to one qualified, available worker.\n\n"
            "Tasks:\n" + "\n".join(task_lines) + "\n\n"
            "Workers:\n" + "\n".join(worker_lines) + "\n\n"
            "Rules: a worker's skills list must include the task's category, unless "
            "the worker is a generalist. The task's start/end time must fall inside "
            "the worker's hours. A worker cannot be assigned two tasks that overlap "
            "in time, and a worker's total assigned minutes cannot exceed their "
            "max_minutes_per_day. It is fine to leave a task unassigned if no worker "
            "fits.\n\n"
            "Respond with ONLY a JSON array, no other text, in this exact shape:\n"
            '[{"task_id": "...", "worker_id": "...", "reason": "..."}]\n'
        )
        if feedback:
            prompt += (
                "\nYour previous plan had these problems - fix them in your next plan:\n"
                + "\n".join(f"- {item}" for item in feedback)
                + "\n"
            )
        return prompt

    def _parse_plan(
        self, text: str, scheduled_tasks: list[ScheduledTask], workers: list[Worker]
    ) -> dict[str, tuple[str, str]]:
        """Parse the LLM's JSON response into {task_id: (worker_id, reason)}."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("expected a JSON array of assignments")

        known_task_ids = {st.task.task_id for st in scheduled_tasks}
        known_worker_ids = {w.worker_id for w in workers}
        plan: dict[str, tuple[str, str]] = {}
        for item in parsed:
            if not isinstance(item, dict) or "task_id" not in item or "worker_id" not in item:
                raise ValueError(f"malformed assignment: {item!r}")
            task_id = item["task_id"]
            worker_id = item["worker_id"]
            if task_id not in known_task_ids:
                raise ValueError(f"unknown task_id in response: {task_id!r}")
            if worker_id not in known_worker_ids:
                raise ValueError(f"unknown worker_id in response: {worker_id!r}")
            plan[task_id] = (worker_id, str(item.get("reason", "")))
        return plan

    # ------------------------------------------------------------------
    # Check: validate a proposed plan against real constraints.
    # ------------------------------------------------------------------

    def _validate(
        self,
        plan: dict[str, tuple[str, str]],
        scheduled_tasks: list[ScheduledTask],
        workers: list[Worker],
    ) -> list[str]:
        """Return a list of human-readable violations, empty if the plan is valid."""
        tasks_by_id = {st.task.task_id: st for st in scheduled_tasks}
        workers_by_id = {w.worker_id: w for w in workers}
        violations: list[str] = []

        # Group this plan's assignments by worker so overlap/capacity can be
        # checked per worker, not per task.
        by_worker: dict[str, list[ScheduledTask]] = {}
        for task_id, (worker_id, _reason) in plan.items():
            st = tasks_by_id[task_id]
            worker = workers_by_id[worker_id]

            if not worker.is_qualified_for(st.task):
                violations.append(
                    f"worker {worker.name!r} is not qualified for task {st.task.title!r} "
                    f"(category {st.task.category!r})"
                )
            if not worker.is_available_for(st.start_time, st.end_time):
                violations.append(
                    f"worker {worker.name!r}'s hours "
                    f"({worker.available_start:%H:%M}-{worker.available_end:%H:%M}) don't "
                    f"cover task {st.task.title!r} ({st.start_time:%H:%M}-{st.end_time:%H:%M})"
                )
            by_worker.setdefault(worker_id, []).append(st)

        for worker_id, assigned in by_worker.items():
            worker = workers_by_id[worker_id]

            total_minutes = sum(st.task.duration_minutes for st in assigned)
            if total_minutes > worker.max_minutes_per_day:
                violations.append(
                    f"worker {worker.name!r} is assigned {total_minutes} minutes, over "
                    f"their daily cap of {worker.max_minutes_per_day}"
                )

            ordered = sorted(assigned, key=lambda st: st.start_time)
            for earlier, later in zip(ordered, ordered[1:]):
                if later.start_time < earlier.end_time:
                    violations.append(
                        f"worker {worker.name!r} is double-booked: "
                        f"{earlier.task.title!r} ({earlier.start_time:%H:%M}-"
                        f"{earlier.end_time:%H:%M}) overlaps {later.task.title!r} "
                        f"({later.start_time:%H:%M}-{later.end_time:%H:%M})"
                    )

        return violations

    # ------------------------------------------------------------------
    # Fallback: deterministic greedy assignment, used when the AI can't
    # produce a valid plan at all.
    # ------------------------------------------------------------------

    def _fallback(
        self,
        scheduled_tasks: list[ScheduledTask],
        workers: list[Worker],
        result: DispatchResult,
    ) -> DispatchResult:
        """Greedily assign each task to the first qualified, available, free worker."""
        result.used_fallback = True
        minutes_used: dict[str, int] = {w.worker_id: 0 for w in workers}
        booked: dict[str, list[tuple[time, time]]] = {w.worker_id: [] for w in workers}

        ordered = sorted(
            scheduled_tasks,
            key=lambda st: (-st.task.priority_value(), st.task.duration_minutes),
        )
        for st in ordered:
            task = st.task
            for worker in workers:
                fits_capacity = (
                    minutes_used[worker.worker_id] + task.duration_minutes
                    <= worker.max_minutes_per_day
                )
                no_overlap = not any(
                    st.start_time < end and start < st.end_time
                    for start, end in booked[worker.worker_id]
                )
                if (
                    worker.is_qualified_for(task)
                    and worker.is_available_for(st.start_time, st.end_time)
                    and fits_capacity
                    and no_overlap
                ):
                    result.assignments[task.task_id] = worker.worker_id
                    result.reasons[task.task_id] = (
                        "Assigned by fallback rule: first qualified, available worker."
                    )
                    minutes_used[worker.worker_id] += task.duration_minutes
                    booked[worker.worker_id].append((st.start_time, st.end_time))
                    break
            else:
                result.unassigned.append(task.task_id)

        logger.info(
            "fallback assigned %d/%d task(s)",
            len(result.assignments),
            len(scheduled_tasks),
        )
        return result
