# PawPal Company Scheduler

## Summary

PawPal Company Scheduler is based on the original PawPal+ project, which let a pet owner schedule care tasks for their own pet. This version turns PawPal+ into a tool for a pet-care business.

Instead of an owner doing the tasks themselves, the business now:

- Keeps information on its customers and their pets, and the care tasks those pets need (walks, feeding, meds, enrichment, grooming, etc.)
- Keeps information on its employees, including what task types and what kinds of pets they're able to handle, and when they're available
- Assigns each pet's care tasks to an employee, instead of leaving it to the pet owner
- Produces a daily plan of who is doing what, and explains why it assigned tasks the way it did
- Saves customers, their pets and tasks, and employees to a JSON file, so they're still there after a restart or a new browser session

## Architectural Overview

The diagram in [diagrams/architecure_flow_finalproject.mmd](diagrams/architecure_flow_finalproject.mmd) lays out how a day's tasks become worker assignments:

- **Input**: customer/pet/task data and the employee roster (skills, which pet types they can handle, hours, daily capacity), loaded at startup from a `business_data.json` file that gets saved again after any change - so this input isn't just session memory, it's the business's actual saved roster.
- **Scheduler (deterministic)**: decides which tasks are due today and slots them into times, the same way the original PawPal+ logic did.
- **Dispatcher Agent**: an LLM Planner proposes which employee should do each task; a deterministic Evaluator checks that proposal against real rules (skill match, pet-type match, working hours, no overlapping tasks, daily capacity). If it fails, the Planner gets another attempt with that feedback; if it keeps failing, a deterministic Fallback rule assigns tasks instead, so the system never produces an invalid schedule.
- **Output**: the final dispatch plan is logged (every attempt, violation, and fallback) and shown in the Streamlit UI with the AI's stated reasoning.
- **Checks on the AI**: business staff review the plan and can mark tasks done/skipped or override an assignment, and an automated test suite feeds the Evaluator and Fallback rule deliberately invalid plans to verify they catch problems correctly.
