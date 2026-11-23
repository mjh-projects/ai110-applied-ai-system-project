"""Reproducible demo: builds a small business and runs the AI dispatcher.

Usage (from the project root):
    python samples/demo_dispatch.py

Uses whatever GEMINI_API_KEY is set in the environment or a .env file in
the project root. With no key - or if the Gemini call fails for any
reason - the Dispatcher's own fallback rule takes over automatically;
the printed output says which path was actually taken.
"""

import sys
from datetime import date, time
from pathlib import Path

# Make dispatcher.py/pawpal_system.py importable regardless of the current
# working directory - `python path/to/script.py` puts the script's own
# folder on sys.path, not the project root where those modules live.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

from dispatcher import Dispatcher
from pawpal_system import Business, Owner, Pet, Priority, Task, Worker

load_dotenv()

business = Business()

jordan = Owner("Jordan", "Smith")
business.add_client(jordan)
rex = Pet("Rex", "dog", age=4)
jordan.add_pet(rex)
rex.add_task(
    Task("Morning walk", 30, Priority.HIGH, category="walking", preferred_time=time(8, 0))
)

maria = Owner("Maria", "Lopez")
business.add_client(maria)
whiskers = Pet("Whiskers", "cat", age=2)
maria.add_pet(whiskers)
whiskers.add_task(
    Task("Grooming", 20, Priority.MEDIUM, category="grooming", preferred_time=time(8, 0))
)

business.add_worker(
    Worker(
        "Sam", skills=["walking"], pet_types=["dog"],
        available_start=time(7, 0), available_end=time(12, 0),
    )
)
business.add_worker(
    Worker(
        "Robin", skills=["grooming"], pet_types=["cat"],
        available_start=time(7, 0), available_end=time(12, 0),
    )
)

today = date.today()
pooled = business.generate_daily_tasks(today)
result = Dispatcher().dispatch(pooled, business.workers)
for item in pooled:
    item.assigned_worker_id = result.assignments.get(item.task.task_id, "")

workers_by_id = {w.worker_id: w for w in business.workers}

print(f"PawPal Company Scheduler - dispatch demo for {today.isoformat()}")
print("=" * 60)
print(
    f"{len(pooled)} task(s) scheduled; assigned by "
    f"{'the fallback rule' if result.used_fallback else 'the AI planner'} "
    f"in {result.attempts} attempt(s).\n"
)

for item in sorted(pooled, key=lambda i: i.start_time):
    worker = workers_by_id.get(item.assigned_worker_id)
    who = worker.name if worker else "UNASSIGNED"
    print(f"{item.start_time:%H:%M}-{item.end_time:%H:%M}  {item.title:<15} -> {who}")
    reason = result.reasons.get(item.task.task_id)
    if reason:
        print(f"    reason: {reason}")

if result.unassigned:
    print(f"\nUnassigned: {len(result.unassigned)} task(s)")

print("\nDispatch log:")
for line in result.log:
    print(f"  - {line}")
