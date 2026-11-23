import json
from datetime import time
from types import SimpleNamespace

from dispatcher import Dispatcher, DispatchResult, MAX_ATTEMPTS
from pawpal_system import Priority, ScheduledTask, Task, Worker


class FakeLLMClient:
    """Stands in for a real google-genai client: queues canned responses so a
    test can drive the Dispatcher's plan/retry loop with no network call and
    no API key."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.models = self  # so `client.models.generate_content(...)` reaches us

    def generate_content(self, **kwargs):
        self.calls += 1
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of queued responses")
        return SimpleNamespace(text=self._responses.pop(0))


def _plan_json(assignments):
    """assignments: list of (task_id, worker_id, reason) -> the JSON text an LLM would send."""
    return json.dumps(
        [{"task_id": t, "worker_id": w, "reason": r} for t, w, r in assignments]
    )


def _walk_and_groom():
    """One walking task, one grooming task, and a worker qualified for each."""
    walk = Task("Morning walk", 30, Priority.HIGH, category="walking")
    walk_item = ScheduledTask(walk, time(7, 30), time(8, 0))
    groom = Task("Nail trim", 15, Priority.LOW, category="grooming")
    groom_item = ScheduledTask(groom, time(9, 0), time(9, 15))

    sam = Worker("Sam", skills=["walking"], available_start=time(7, 0), available_end=time(12, 0))
    robin = Worker(
        "Robin", skills=["grooming"], available_start=time(7, 0), available_end=time(12, 0)
    )
    return [walk_item, groom_item], [sam, robin]


def test_dispatch_accepts_a_valid_plan_on_the_first_attempt():
    tasks, workers = _walk_and_groom()
    walk_item, groom_item = tasks
    sam, robin = workers

    fake = FakeLLMClient(
        [
            _plan_json(
                [
                    (walk_item.task.task_id, sam.worker_id, "Sam can walk dogs"),
                    (groom_item.task.task_id, robin.worker_id, "Robin does grooming"),
                ]
            )
        ]
    )

    result = Dispatcher(client=fake).dispatch(tasks, workers)

    assert result.assignments == {
        walk_item.task.task_id: sam.worker_id,
        groom_item.task.task_id: robin.worker_id,
    }
    assert result.unassigned == []
    assert result.used_fallback is False
    assert result.attempts == 1
    assert fake.calls == 1


def test_dispatch_retries_with_feedback_then_succeeds():
    # Attempt 1 wrongly gives the walk to the groomer, which the validator
    # must catch as a skill violation. Attempt 2 fixes it.
    tasks, workers = _walk_and_groom()
    walk_item, groom_item = tasks
    sam, robin = workers

    fake = FakeLLMClient(
        [
            _plan_json(
                [
                    (walk_item.task.task_id, robin.worker_id, "wrong on purpose"),
                    (groom_item.task.task_id, robin.worker_id, "Robin does grooming"),
                ]
            ),
            _plan_json(
                [
                    (walk_item.task.task_id, sam.worker_id, "Sam can walk dogs"),
                    (groom_item.task.task_id, robin.worker_id, "Robin does grooming"),
                ]
            ),
        ]
    )

    result = Dispatcher(client=fake).dispatch(tasks, workers)

    assert result.attempts == 2
    assert result.used_fallback is False
    assert result.assignments[walk_item.task.task_id] == sam.worker_id
    assert fake.calls == 2


def test_dispatch_falls_back_after_exhausting_retries():
    # The AI never corrects itself, so the retry budget runs out and the
    # deterministic fallback rule has to take over instead.
    tasks, workers = _walk_and_groom()
    walk_item, groom_item = tasks
    sam, robin = workers

    bad_plan = _plan_json(
        [
            (walk_item.task.task_id, robin.worker_id, "still wrong"),
            (groom_item.task.task_id, robin.worker_id, "Robin does grooming"),
        ]
    )
    fake = FakeLLMClient([bad_plan] * MAX_ATTEMPTS)

    result = Dispatcher(client=fake).dispatch(tasks, workers)

    assert result.attempts == MAX_ATTEMPTS
    assert result.used_fallback is True
    # The fallback rule gets it right even though the AI never did.
    assert result.assignments[walk_item.task.task_id] == sam.worker_id
    assert result.assignments[groom_item.task.task_id] == robin.worker_id
    assert fake.calls == MAX_ATTEMPTS


def test_dispatch_falls_back_when_the_llm_is_unavailable(monkeypatch):
    # No client injected, and no API key/package available in this
    # environment -> must fail safely on the first attempt, not crash.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    tasks, workers = _walk_and_groom()
    walk_item, _groom_item = tasks

    result = Dispatcher(client=None).dispatch(tasks, workers)

    assert result.attempts == 1
    assert result.used_fallback is True
    assert result.assignments[walk_item.task.task_id] == workers[0].worker_id
    assert any("failed" in line.lower() for line in result.log)


def test_dispatch_falls_back_on_malformed_json():
    tasks, workers = _walk_and_groom()
    fake = FakeLLMClient(["not even json"])

    result = Dispatcher(client=fake).dispatch(tasks, workers)

    assert result.used_fallback is True
    assert result.attempts == 1
    assert fake.calls == 1


def test_dispatch_with_no_workers_leaves_everything_unassigned():
    tasks, _workers = _walk_and_groom()
    walk_item, groom_item = tasks

    result = Dispatcher(client=FakeLLMClient([])).dispatch(tasks, [])

    assert set(result.unassigned) == {walk_item.task.task_id, groom_item.task.task_id}
    assert result.assignments == {}
    assert result.attempts == 0  # never even tried to call the LLM


def test_validate_flags_an_unqualified_worker():
    tasks, workers = _walk_and_groom()
    walk_item, _groom_item = tasks
    _sam, robin = workers

    plan = {walk_item.task.task_id: (robin.worker_id, "wrong")}
    violations = Dispatcher()._validate(plan, [walk_item], workers)

    assert any("not qualified" in v for v in violations)


def test_validate_flags_double_booking_and_over_capacity():
    sam = Worker(
        "Sam", skills=[], available_start=time(7, 0), available_end=time(12, 0),
        max_minutes_per_day=20,
    )
    first = ScheduledTask(Task("A", 15, Priority.HIGH, category="walking"), time(8, 0), time(8, 15))
    second = ScheduledTask(Task("B", 15, Priority.HIGH, category="walking"), time(8, 10), time(8, 25))

    plan = {
        first.task.task_id: (sam.worker_id, "r1"),
        second.task.task_id: (sam.worker_id, "r2"),
    }
    violations = Dispatcher()._validate(plan, [first, second], [sam])

    assert any("double-booked" in v for v in violations)
    assert any("daily cap" in v for v in violations)


def test_fallback_prefers_higher_priority_when_only_one_worker_fits():
    sam = Worker("Sam", skills=["walking"], available_start=time(7, 0), available_end=time(12, 0))
    high = ScheduledTask(
        Task("Urgent walk", 30, Priority.HIGH, category="walking"), time(8, 0), time(8, 30)
    )
    low = ScheduledTask(
        Task("Casual walk", 30, Priority.LOW, category="walking"), time(8, 0), time(8, 30)
    )

    # Passed in low-first on purpose: _fallback must sort by priority itself.
    result = Dispatcher()._fallback([low, high], [sam], DispatchResult())

    assert result.assignments[high.task.task_id] == sam.worker_id
    assert low.task.task_id in result.unassigned
