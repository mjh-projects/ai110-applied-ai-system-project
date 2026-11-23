"""Reproducible demo of the agentic retry loop: a scripted LLM first
proposes an invalid plan, the deterministic Evaluator catches it, and the
corrected plan on the next attempt is accepted - with no network call and
no API key, so this always runs exactly the same way.

Usage (from the project root):
    python samples/demo_retry.py
"""

import json
import sys
from datetime import time
from pathlib import Path
from types import SimpleNamespace

# Make dispatcher.py/pawpal_system.py importable regardless of the current
# working directory - see the same note in samples/demo_dispatch.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dispatcher import Dispatcher
from pawpal_system import Priority, ScheduledTask, Task, Worker


class ScriptedLLMClient:
    """Stands in for a real google-genai client: returns one queued
    response per call, exactly like the fakes used in tests/test_dispatcher.py."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.models = self

    def generate_content(self, **kwargs):
        return SimpleNamespace(text=self._responses.pop(0))


walk = Task("Morning walk", 30, Priority.HIGH, category="walking")
walk.pet_species = "dog"
walk_item = ScheduledTask(walk, time(7, 30), time(8, 0))

groom = Task("Nail trim", 15, Priority.LOW, category="grooming")
groom.pet_species = "cat"
groom_item = ScheduledTask(groom, time(9, 0), time(9, 15))

sam = Worker("Sam", skills=["walking"], pet_types=["dog"], available_start=time(7, 0), available_end=time(12, 0))
robin = Worker("Robin", skills=["grooming"], pet_types=["cat"], available_start=time(7, 0), available_end=time(12, 0))

# Attempt 1 (scripted to be wrong on purpose): the walk is given to Robin,
# who isn't skilled or suited for it. Attempt 2: corrected.
attempt_1 = json.dumps(
    [
        {"task_id": walk.task_id, "worker_id": robin.worker_id, "reason": "wrong on purpose"},
        {"task_id": groom.task_id, "worker_id": robin.worker_id, "reason": "Robin does grooming"},
    ]
)
attempt_2 = json.dumps(
    [
        {"task_id": walk.task_id, "worker_id": sam.worker_id, "reason": "Sam handles dog walks"},
        {"task_id": groom.task_id, "worker_id": robin.worker_id, "reason": "Robin does grooming"},
    ]
)

fake_client = ScriptedLLMClient([attempt_1, attempt_2])
result = Dispatcher(client=fake_client).dispatch([walk_item, groom_item], [sam, robin])

print("PawPal Company Scheduler - agentic retry demo")
print("=" * 60)
print(f"Input: 2 tasks, 2 workers. Attempt 1's scripted plan deliberately assigns")
print("the dog walk to Robin, who only handles cats and grooming.\n")

print(f"Result: accepted on attempt {result.attempts} "
      f"({'fallback rule used' if result.used_fallback else 'AI plan accepted'}).\n")

print("Dispatch log:")
for line in result.log:
    print(f"  - {line}")

print("\nFinal assignments:")
names = {sam.worker_id: "Sam", robin.worker_id: "Robin"}
for task_id, worker_id in result.assignments.items():
    title = walk.title if task_id == walk.task_id else groom.title
    print(f"  {title} -> {names[worker_id]} ({result.reasons[task_id]})")
