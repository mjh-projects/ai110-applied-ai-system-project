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



## Setup Instructions

### 1. Prerequisites

- Python 3.11 or later.

### 2. Get the code and open a terminal in the project folder

The project folder is the one containing `app.py`, `requirements.txt`, and this `README.md`.

### 3. (Recommended) Create and activate a virtual environment

```bash
python -m venv .venv
```

- Windows: `.venv\Scripts\activate`
- macOS/Linux: `source .venv/bin/activate`

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Set up your Gemini API key (optional, but needed for real AI dispatching)

The app runs and schedules correctly even without this step - it just uses a deterministic fallback rule to assign employees instead of asking Gemini. To use the real AI dispatcher:

1. Copy `.env.example` to a new file named `.env` in the project root.
2. Open `.env` and replace the placeholder with your own key:
   ```
   GEMINI_API_KEY=your_actual_key_here
   ```
   Get a key at https://aistudio.google.com/app/apikey if you don't have one.
3. Save the file. `.env` is already listed in `.gitignore`, so it will never be committed.

### 6. Run the app

```bash
streamlit run app.py
```

This opens the app in your browser (usually at `http://localhost:8501`). Add customers and their pets' tasks, add employees and their skills/pet types/hours, then click "Build & dispatch today's schedule."

### 7. Run the tests

```bash
python -m pytest
```

The dispatcher tests use a fake LLM client, so the full test suite runs with no API key and no network call.

## Sample Interactions

These are real, reproducible runs of [diagrams/samples/demo_dispatch.py](diagrams/samples/demo_dispatch.py) and [diagrams/samples/demo_retry.py](diagrams/samples/demo_retry.py) - not screenshots or hand-written examples. Each command can be re-run from the project root to get the same kind of output (the retry demo is fully scripted and deterministic; the dispatch demo's fallback-rule path is deterministic too, while a live Gemini call's exact wording will vary run to run).

### 1. Dispatch with no Gemini key configured (deterministic fallback)

Input: two customers (Jordan/Rex the dog, Maria/Whiskers the cat), one task each, and two employees each qualified for only one of them.

```
GEMINI_API_KEY="" python diagrams/samples/demo_dispatch.py
```

Output ([diagrams/samples/output_fallback.txt](diagrams/samples/output_fallback.txt)):

```
PawPal Company Scheduler - dispatch demo for 2026-11-22
============================================================
2 task(s) scheduled; assigned by the fallback rule in 1 attempt(s).

08:00-08:30  Morning walk    -> Sam
    reason: Assigned by fallback rule: first qualified, available worker.
08:00-08:20  Grooming        -> Robin
    reason: Assigned by fallback rule: first qualified, available worker.

Dispatch log:
  - Attempt 1: AI planning failed (GEMINI_API_KEY is not set); using fallback rule.
```

### 2. Agentic retry loop: an invalid AI plan gets corrected

Input: a scripted LLM response that deliberately assigns a dog walk to an employee who only handles cats/grooming, followed by a corrected response on the next attempt.

```
python diagrams/samples/demo_retry.py
```

Output ([diagrams/samples/output_retry.txt](diagrams/samples/output_retry.txt)):

```
PawPal Company Scheduler - agentic retry demo
============================================================
Input: 2 tasks, 2 workers. Attempt 1's scripted plan deliberately assigns
the dog walk to Robin, who only handles cats and grooming.

Result: accepted on attempt 2 (AI plan accepted).

Dispatch log:
  - Attempt 1: 2 violation(s) found; retrying.
  - Attempt 2: valid plan accepted.

Final assignments:
  Morning walk -> Sam (Sam handles dog walks)
  Nail trim -> Robin (Robin does grooming)
```

This shows the deterministic Evaluator catching a skill *and* pet-type violation in one pass and the Planner correcting both on the very next attempt.

### 3. Dispatch with a real Gemini key configured

Input: same scenario as Example 1, but with a real `GEMINI_API_KEY` set (via `.env`).

```
python diagrams/samples/demo_dispatch.py
```

Output ([diagrams/samples/output_gemini_live.txt](diagrams/samples/output_gemini_live.txt)), captured on 2026-11-22:

```
PawPal Company Scheduler - dispatch demo for 2026-11-22
============================================================
2 task(s) scheduled; assigned by the fallback rule in 1 attempt(s).

08:00-08:30  Morning walk    -> Sam
    reason: Assigned by fallback rule: first qualified, available worker.
08:00-08:20  Grooming        -> Robin
    reason: Assigned by fallback rule: first qualified, available worker.

Dispatch log:
  - Attempt 1: AI planning failed (LLM call failed: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.', 'status': 'UNAVAILABLE'}}); using fallback rule.
```

This run is shown as captured, not reworded into a success: at the time this was written, Gemini's own API was returning a sustained "high demand" error for several minutes, even after retrying. That's a real, honest result - and it demonstrates the fallback guardrail doing exactly its job: the live AI call failed, and the system still produced a correct, valid schedule instead of crashing or leaving tasks unassigned. Re-running this same command once the provider isn't overloaded should instead show Gemini's own stated reasoning in place of the fallback-rule message.


## Design Decisions

- **Agentic workflow, not RAG, reliability testing, or a fine-tuned model.** The business pivot creates exactly one decision that genuinely needs an AI: which employee should do each task. That's a planning problem with checkable rules, not a retrieval or classification problem, so a plan-check-retry-fallback loop fit better than the alternatives.
- **The AI only decides *who*, never *what* or *when*.** The original PawPal+ scheduling logic (which tasks are due today, what time they're slotted) was left untouched. The Dispatcher is a separate module that only assigns employees to an already-built schedule, keeping the AI confined to the one decision that actually benefits from it.
- **The AI is never trusted directly.** Every proposed plan is checked by deterministic code (skill match, pet-type match, hours, overlaps, capacity) before it's accepted. A failed check gets specific feedback and a retry; running out of retries - or the AI being unavailable at all - falls back to a deterministic rule, so the system can never produce an invalid schedule regardless of what the AI does.
- **Skill and pet-type mismatches are two separate checks, not one.** A worker who's qualified by skill but wrong for the pet (or vice versa) needs its own specific message fed back on retry; a single vague "not qualified" message would give the AI nothing concrete to fix.
- **Gemini, not another provider.** Chosen to match the API key actually available, not for any feature difference. The LLM client is injected into the Dispatcher, so swapping providers only touched the one method that builds the client and parses its response - nothing about the plan/check/retry/fallback logic itself changed.
- **A JSON file, not a database.** The business's data is a single roster with one process and no concurrent writers, so a plain `business_data.json` (written via a temp-file-and-replace) covers "survives a restart" without adding a database dependency the project's scale doesn't need.

## Testing Summary

**19 automated tests, all passing, run with `python -m pytest`:**

- `tests/test_pawpal.py` (8 tests) covers the original deterministic scheduling logic - marking a task done, adding tasks, priority/tie-break sorting, daily/weekly recurrence, conflict detection - plus the newer pet-species stamping and the split skill/pet-type qualification checks.
- `tests/test_dispatcher.py` (11 tests) covers the agentic dispatcher with a fake LLM client (no network, no API key needed): the happy path, a successful retry after a skill or pet-type violation, exhausting all retries and falling back correctly anyway, failing safely with no API key or malformed JSON, an empty worker roster, and the validator's/fallback's individual rules in isolation.

- Manual Testing  I did additional manual testing to look at how the app performed and decided there were some performance bugs/issues not caught by the AI which caused me to make some alterations. ALl of this is noted in the implementation_Notes.md . I found initially the app had some usability issues in terms of loading customers and pets as well and also decided to make the information persistent as this would be more likely to be useful in an actual app for a business 

**What worked well:**

- The deterministic Scheduler and the Dispatcher's fallback rule turned out to be the most reliable part of the system - every scenario tried (including deliberately adversarial ones, like an AI plan that's wrong on purpose) was handled correctly with no crash, confirmed by both the test suite and the reproducible scripts under [diagrams/samples/](diagrams/samples/).
- UI bugs found through actual use (the "Active customer" selector reverting, "Add customer" not switching the view) were reproduced headlessly with Streamlit's `AppTest` tool rather than guessed at, which made both the root cause and the fix verifiable before ever touching the running app.
- Persistence round-trips correctly, including falling back gracefully to a fresh default when the save file is missing or corrupted, instead of crashing.

**What didn't go smoothly, and what that revealed:**

- The real Gemini integration initially failed on every call because the hardcoded default model name had been deprecated by Google - caught only by manually testing against the live API, since the mocked tests can't detect a wrong model name. Fixed and reverified against the real API.
- The live Gemini call is still subject to the provider's own transient outages (a "high demand" 503 was captured in practice while preparing the Sample Interactions section) - the fallback rule handles this correctly every time, but it also means a given run isn't guaranteed to exercise the real AI path. This is an external dependency limitation, not a defect in this project's code.
- No automated test calls the real Gemini API, by design (reproducibility and no API key requirement for grading); the real-AI path is only verified through the manual, captured runs described in Sample Interactions.

## Reflection
I learned that I needed to be very specific with the AI to get what I wanted. Although the AI is very useful and allows for alot more work to be done quicly without having to code every line it still would take a lot of time and effor to get a very good functional APP. 
