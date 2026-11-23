# Implementation Notes

A running log of each implementation step for the PawPal Company Scheduler
pivot (worker dispatch + agentic AI assignment), with an explanation of what
each change does and why. Written alongside the code, one step at a time.

---

## Step 1 — Domain model: `Worker` and `Business` (`pawpal_system.py`)

**What changed:**

- `ScheduledTask` gained one field: `assigned_worker_id: str = ""`. This is
  where the Dispatcher (step 2) records which employee is doing this
  specific scheduled instance of a task. Defaults to empty ("not yet
  assigned") so every existing `ScheduledTask(...)` call still works.
- `Owner` gained three fields, all with defaults so `Owner("Jordan",
  "Smith")` still works exactly as before:
  - `day_start` / `daily_minutes`: the time window the business plans this
    client's pets' tasks within. Previously typed into the Streamlit UI by
    hand each run; now stored on the client so a business can regenerate
    every client's schedule automatically.
  - `client_id`: a unique id (same reasoning as `Pet.pet_id`) so a
    `Business` holding a *list* of clients can find or remove one, even if
    two clients share a name.
- New **`Worker`** class: a staff member — `name`, `skills` (task
  categories they can do; empty list means generalist), working hours
  (`available_start`/`available_end`), and `max_minutes_per_day` (daily
  capacity). Two helper methods, `is_qualified_for(task)` and
  `is_available_for(start, end)`, are the exact checks the Dispatcher's
  Evaluator reuses in step 2 — defined on `Worker` itself instead of
  duplicated inside the dispatcher.
- New **`Business`** class: holds the `workers` roster and the `clients`
  list, with `add_worker`/`remove_worker`/`add_client`/`remove_client`
  following the existing "raise `ValueError` if the id isn't found"
  pattern already used by `Pet`/`Owner`. Its one real method,
  `generate_daily_tasks(on)`, runs the *existing, untouched* `Scheduler`
  for every client and pools all their scheduled tasks into one list for
  the day — still 100% deterministic, no AI involved. That pooled list is
  what gets handed to the AI dispatcher in step 2.

**Verification:** all 5 existing tests in `tests/test_pawpal.py` still
pass; `app.py` still runs unchanged in Streamlit (visually identical,
since the UI hasn't been wired to the new classes yet).

---

## Step 2 — The agentic dispatcher (`dispatcher.py`)

**What it does:** takes the pooled `ScheduledTask`s from
`Business.generate_daily_tasks()` and the `Worker` roster, and decides
*who* does each task, using an LLM as the planner but never trusting its
output blindly. `Dispatcher.dispatch(scheduled_tasks, workers)` is the one
entry point; it returns a `DispatchResult` (assignments, the AI's stated
reasons, any unassigned tasks, whether a fallback fired, and a
human-readable trace).

**The plan -> check -> retry -> fallback loop**, matching the "Dispatcher
Agent" box in `diagrams/architecure_flow_finalproject.mmd`:

1. **Plan** (`_plan_assignments` / `_build_prompt`) - builds a prompt
   listing the day's tasks (id, title, category, time slot, duration) and
   the worker roster (id, skills, hours, capacity), and asks the LLM for a
   JSON array: `[{"task_id", "worker_id", "reason"}]`. `_parse_plan`
   strips markdown code fences if present and checks every id in the
   response actually exists before trusting it.
2. **Check** (`_validate`) - the deterministic Evaluator. Reuses
   `Worker.is_qualified_for()` and `Worker.is_available_for()` from step 1
   for the per-task checks, then groups the plan's assignments by worker
   to catch the two checks that need more than one task at a time: no two
   tasks assigned to the same worker overlap in time, and a worker's total
   assigned minutes stay under their `max_minutes_per_day`.
3. **Retry** - any violations found are fed back into the next prompt
   ("worker X isn't qualified for task Y, try again"), bounded by
   `MAX_ATTEMPTS = 3` so it can never retry forever.
4. **Fallback** (`_fallback`) - a deterministic greedy rule used when the
   LLM call fails outright (`DispatchError`: no package, no API key,
   network/auth error) or the retry budget runs out: tasks are sorted by
   priority (reusing the same priority/duration tie-break as
   `Scheduler.sort_by_priority`), and each is given to the first worker who
   is qualified, available, under capacity, and not already double-booked.
   Anything that still doesn't fit goes to `unassigned` instead of being
   forced onto someone invalid. This is the guardrail that guarantees the
   system never crashes or produces an invalid schedule just because the
   AI had a bad response.

**Logging:** every attempt, each violation found, and whether the
fallback fired is written to `dispatch.log` via Python's `logging` module
(now excluded from git via `.gitignore`, alongside `.env`, since both are
local runtime/secret files, not project source).

**Testability:** the LLM call goes through `Dispatcher(client=...)`, an
injectable parameter. Production code leaves it `None` and a real
`anthropic.Anthropic` client is built lazily on first use (so importing
this module never requires an API key); tests (step 4) will pass in a fake
object instead, so the suite runs with no network call and no key.

**Verification:** smoke-tested by hand with no `anthropic` package and no
API key installed - it correctly logged `"the anthropic package is not
installed"` and fell back to the deterministic rule, assigning a walking
task to a walking-qualified worker and a grooming task to a
grooming-qualified worker, with `used_fallback=True` and zero unassigned
tasks. All 5 existing tests in `tests/test_pawpal.py` still pass.

---

## Step 3 — Wiring the UI to `Business`/`Worker`/`Dispatcher` (`app.py`)

**What changed:** `app.py` was rewritten around `Business` instead of a
single `Owner`:

- **Customers** section: `business.clients` is now a list, not one fixed
  owner. An "Add customer" form appends a new `Owner`; a selectbox picks
  which customer's pets/tasks are shown below (`active_client_id` in
  `session_state` remembers the choice across reruns); its day-start and
  daily-minutes are editable there too (previously typed into the
  scheduler form on every run).
- **Pets and Tasks**: the same CRUD UI as the original PawPal+ app, now
  scoped to the active customer. One real addition: a **Category**
  selectbox was added to the Add/Edit Task forms, backed by a shared
  `TASK_CATEGORIES` list. This field existed on `Task` already but the
  original UI never set it; it has to now, because a `Worker`'s `skills`
  are matched against it - without a category, no worker could ever be
  "qualified" for anything.
- **Employees** section (new): add/edit/remove `Worker`s - name, skills
  (multiselect over `TASK_CATEGORIES`; none selected = generalist),
  available hours, and daily capacity. Same Edit/Remove row pattern as
  Pets, but mutates the `Worker`'s fields in place rather than swapping
  the object, since nothing else holds a reference to a `Worker` that
  needs preserving (unlike `Pet.edit_task`, which must keep the same
  `task_id` for any `ScheduledTask` already pointing at it).
- **Dispatch Today's Schedule** section (replaces the old "Build
  Schedule"): one button runs `business.generate_daily_tasks(today)`
  (deterministic - builds and saves every customer's own `Schedule`),
  then `Dispatcher().dispatch(pooled, business.workers)` (agentic). The
  returned `assignments` dict is written onto each `ScheduledTask.
  assigned_worker_id` here in `app.py`, not inside `dispatcher.py` -
  keeping the dispatcher itself free of domain-object mutation, same
  separation as `Scheduler.generate_plan()` building a `Schedule` that the
  caller still has to explicitly save with `set_schedule()`. The result
  is stored in `session_state["dispatch"]` (keyed by date) so Done/Skip
  clicks don't regenerate and lose state, the same pattern the original
  app used for `owner.get_schedule(date.today())`.
- The dispatch table shows each task's assigned employee (or
  "Unassigned"), a warning banner if `result.used_fallback` fired, a table
  of any `result.unassigned` tasks, the per-customer tasks that didn't fit
  their time budget (`Schedule.skipped`, unchanged from the original), and
  a "Why these assignments?" expander with the dispatcher's trace log plus
  each task's stated reason. The old single-timeline
  `Schedule.has_conflict()` display was dropped: conflicts are now a
  per-worker concept the Dispatcher's Evaluator already enforces before a
  plan is accepted, so a *different* worker's overlapping task is no
  longer a conflict at all.

**Verification:** `python -m py_compile app.py` passes; all 5 existing
tests still pass; the Streamlit server starts with no errors in its log.
A standalone script exercising the exact same calls the "Build & dispatch"
button makes (`generate_daily_tasks` -> `Dispatcher().dispatch` -> the
table/lookup code) ran end-to-end with no exceptions, correctly assigning
a walking task to a walking-skilled worker and a grooming task to a
grooming-skilled worker. No browser tool was available to click through
the UI itself in this environment - that should still be checked by hand
in the browser before considering this step fully verified.

---

## Step 4 — Tests for the dispatcher (`tests/test_dispatcher.py`)

**What it does:** a `FakeLLMClient` stands in for a real LLM client - it
queues canned response strings and exposes the same `client.messages.
create(...)` shape `Dispatcher._plan_assignments` calls, so `Dispatcher
(client=fake)` drives the real plan/check/retry/fallback loop with no
network call and no API key. 9 tests:

- **Happy path**: a valid first plan is accepted on attempt 1.
- **Retry**: an invalid first plan (wrong skill) gets one retry and
  succeeds on attempt 2, proving the Evaluator's feedback actually reaches
  the next prompt.
- **Exhausted retries**: every attempt keeps the same violation for
  `MAX_ATTEMPTS` tries -> `used_fallback=True`, and the fallback rule
  still produces the *correct* assignment even though the AI never did.
- **LLM unavailable**: no client/API key -> fails safely on attempt 1
  (no crash), fallback fires, the log says why.
- **Malformed JSON**: garbage text back from the "model" -> immediate
  fallback rather than a retry loop, since there's nothing to give
  feedback on.
- **No workers on the roster**: everything ends up `unassigned`, and the
  LLM is never even called (`attempts == 0`) - no wasted call for an
  impossible request.
- **`_validate()` unit tests** (two): directly confirm an unqualified-worker
  assignment is flagged, and that double-booking the same worker /
  exceeding their daily cap are each flagged with a distinct message.
- **`_fallback()` unit test**: when two tasks need the same single worker
  and only one slot is available, the higher-priority task wins and the
  lower-priority one is left unassigned - proving the fallback rule sorts
  by priority itself rather than relying on input order.

**Verification:** all 14 tests (5 original + 9 new) pass.

**Note for step 5:** these tests mock the `anthropic`-shaped client
interface (`client.messages.create(...)` returning `.content[0].text`),
because that's what `dispatcher.py` currently calls. Step 5 will swap the
LLM backend to Gemini (the user has a Gemini API key, not an Anthropic
one), which has a different SDK call shape. `FakeLLMClient`/`_FakeMessage`
in this test file will need to be updated to match whatever `_llm_client()`
/ `_plan_assignments()` call after that refactor - the *behavior* being
tested (retry on violation, fallback on exhaustion/failure, validation
rules) stays the same regardless of which provider is behind it.

---

## Step 5 — Swap the LLM backend from Anthropic to Gemini; setup files

**Why:** step 2 was written against the `anthropic` SDK as a placeholder,
but the user only has a Gemini API key, not an Anthropic one. This step
swaps the provider without touching the plan/check/retry/fallback logic
itself - the Dispatcher's behavior is provider-agnostic by design (the
LLM client is an injectable parameter), so only the parts that actually
talk to a specific SDK needed to change.

**`dispatcher.py` changes:**

- `Dispatcher.__init__`: `model` now defaults to `None` and is resolved
  inside `__init__` to `model or os.environ.get("GEMINI_MODEL") or
  DEFAULT_MODEL` (`DEFAULT_MODEL = "gemini-2.5-flash"`). Resolving it in
  the body rather than as a parameter default matters: a parameter default
  like `model=os.environ.get(...)` is only evaluated once, when the
  module is first imported, so it would freeze whatever `GEMINI_MODEL`
  happened to be set to at that moment instead of reading it fresh each
  time a `Dispatcher` is created.
- `_llm_client()`: imports `from google import genai` instead of
  `anthropic`, reads `GEMINI_API_KEY` instead of `ANTHROPIC_API_KEY`, and
  builds `genai.Client(api_key=...)` instead of `anthropic.Anthropic(...)`.
  Same `DispatchError` guardrail on either missing package or missing key.
- `_plan_assignments()`: calls `client.models.generate_content(model=
  self._model, contents=prompt)` and reads `response.text`, replacing
  Anthropic's `client.messages.create(...)` / `message.content[0].text`.
  Everything downstream of getting that `text` string (`_parse_plan`,
  `_build_prompt`, `_validate`, `_fallback`) is unchanged - the prompt
  format and the JSON contract asked of the model didn't need to change,
  since that's a plain-text instruction, not an SDK detail.

**Test changes (`tests/test_dispatcher.py`):** `FakeLLMClient` now exposes
`.models.generate_content(...)` returning a plain object with a `.text`
attribute, matching Gemini's response shape, instead of the Anthropic
`.messages.create(...)` / `.content[0].text` shape. The `_FakeMessage`
wrapper class was removed since Gemini's response doesn't need the extra
nesting. The "LLM unavailable" test now unsets `GEMINI_API_KEY`. No test's
*assertions* changed - they test behavior, not wire format.

**Setup changes:**

- `requirements.txt`: replaced the implicit `anthropic` dependency with
  `google-genai>=0.3.0`, and added `python-dotenv>=1.0`.
- New `.env.example`: documents `GEMINI_API_KEY` (required) and
  `GEMINI_MODEL` (optional override), with a comment pointing at
  https://aistudio.google.com/app/apikey and a reminder that `.env` itself
  is git-ignored and should never hold a real key in the example file.
- `app.py`: added `from dotenv import load_dotenv` and a `load_dotenv()`
  call at the top, so a local `.env` file is picked up automatically
  without the user having to export the variable by hand each session.
  Doing nothing when `.env` doesn't exist isn't an error - the
  Dispatcher's own fallback guardrail is what actually handles a missing
  key at dispatch time.

**Verification:** installed `google-genai` and `python-dotenv`; both
`dispatcher.py` and `app.py` still compile; all 14 tests pass. Manually
confirmed the no-API-key path with the real `google-genai` package
installed (previously it failed on the *import*, now it correctly gets
past the import and fails on the *missing key* instead, with the log
line now reading `"GEMINI_API_KEY is not set"`) - dispatch still
completed successfully via the fallback rule, with no crash. Dispatching
with a real Gemini API key has not been tested, since that would require
the user's own key.

---
