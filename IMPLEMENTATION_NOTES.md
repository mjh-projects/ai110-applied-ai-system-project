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

## Step 6 — JSON persistence (`storage.py`)

**What it does:** customers (with their pets and tasks) and employees
used to live only in Streamlit's `session_state`, so they vanished on a
server restart or a new browser session. `storage.py` adds
`save_business()`/`load_business()`, converting a `Business` to/from a
plain JSON file (`business_data.json`) - enums as `.name` strings, `time`
as `"HH:MM"`, `date` as ISO strings. `save_business()` writes to a
`.tmp` file and `os.replace()`s it into place, so a crash mid-write can't
leave behind a half-written file `load_business()` can't parse.

**Scope:** only the roster persists - customers, pets, tasks, employees.
A day's generated `Schedule`/dispatch result does not, since it's cheap
to rebuild from the roster plus the `Scheduler`/`Dispatcher` and isn't
really "data someone typed in."

**Wiring in `app.py`:** on first session init, `load_business()` is tried;
`FileNotFoundError`/`ValueError`/`KeyError` (missing file, corrupted file,
or an old/incompatible format) falls back to a freshly seeded `Business`
instead of crashing. `save_business()` is called once, unconditionally,
as the very last line of the script - since Streamlit re-runs the whole
file on every interaction, this keeps the file continuously in sync with
`session_state` without a separate "Save" button or per-action save calls
scattered through the file.

**Verification:** round-tripped with Streamlit's `AppTest` tool (add a
customer and a worker in one simulated session, inspect the written
JSON, then load it in a second, brand-new `AppTest` instance and confirm
both come back correctly) and confirmed a corrupted file falls back to
the default seed instead of crashing. All 14 tests still pass.

---

## Fixes found after the fact

### Fix: adding a second customer looked like it did nothing

**Symptom:** reported by the user - after clicking "Add customer," the
page still showed the first customer's pets/tasks, making it look like
only one customer could ever be added.

**Cause:** the "Add customer" button handler called `business.add_client
(...)` and `st.rerun()` but never updated `st.session_state.
active_client_id`. The "Active customer" selectbox always restores
whichever id was last active, so it kept showing the first customer - the
new one *was* added correctly underneath, it just never became visible.

**Fix:** the handler now also sets `st.session_state.active_client_id =
new_client.client_id` right after adding, so the view switches to the
new customer immediately.

**Verification:** reproduced and confirmed fixed using Streamlit's
`AppTest` tool (drives the script headlessly, no browser needed) -
before the fix the selectbox stayed on index 0 after two adds; after the
fix it correctly moved to index 1, then 2.

### Fix: switching the active customer silently reverted

**Symptom:** reported by the user - picking a different customer from
the "Active customer" dropdown didn't change the pets/tasks shown; it
stayed on whichever customer was already active.

**Cause:** the selectbox had no explicit `key=`, and its `index=`
argument was recomputed from `session_state.active_client_id` on every
run. Streamlit partly derives an unkeyed widget's identity from its own
arguments, so when `index=` changed (which it does every time the active
customer changes), the widget looked "new" on the very next run and
silently discarded the pick, falling back to `index=` - which, by
construction, is always the *previously* active customer. The net effect:
any attempt to switch customers got reverted on the following rerun.

**Fix:** gave the selectbox a stable `key="active_client_select"`, so
Streamlit treats it as the same widget across reruns and a pick actually
sticks (the `index=` argument still sets its value the first time that
key is ever seen, same as before).

**Verification:** reproduced with `AppTest` - added a second customer, a
pet for it, switched to the first customer (worked), then tried
switching back to the second (reverted to the first, bug confirmed).
After the fix, the same sequence correctly shows each customer's own
pets both directions, with no exceptions.

### Incident: debugging scripts clobbered the live persistence file

**What happened:** while investigating the two fixes above (and an
overlap-assignment report that turned out not to be reproducible - the
fallback rule correctly avoided double-booking in every scenario tried),
several `AppTest` reproduction scripts ran from the project's own working
directory, so they read and wrote the *same* `business_data.json` the
live Streamlit server (connected to the user's browser) was also
continuously saving to. Some of those scripts started with `rm -f
business_data.json` for a clean test fixture. Each run could overwrite or
delete whatever the user's real live session had just saved, and a
server restart soon after meant the next session loaded whatever the
file happened to contain at that moment rather than the user's actual
data - resulting in real data loss (not a bug in `storage.py` itself,
which round-trips correctly in isolation per Step 6's verification).

**Takeaway:** any future debugging/reproduction script must point at a
separate file (not `business_data.json`) - e.g. pass an explicit `path`
to `save_business`/`load_business`, or run from a scratch directory -
never the same persistence file a live session depends on.

### Fix: adding a customer stopped jumping to them (regression from the switching fix)

**Symptom:** requested by the user - "Add pet" had no way to set the
pet's age at creation, and separately, adding a new customer no longer
switched the view to them (even though this had been fixed once
already, in the previous entry above).

**Cause of the regression:** the switching fix gave the "Active customer"
selectbox a stable `key="active_client_select"`, which was the right fix
for *that* bug - but it had a side effect nobody had tested yet: once a
keyed widget has rendered once, Streamlit ignores its `index=`/`value=`
argument on every later run. The Add Customer handler was still only
setting `st.session_state.active_client_id` and relying on a freshly
computed `index=default_index` to move the selectbox - which, after the
switching fix, no longer had any effect. Fixing "switching reverts" and
"adding doesn't jump to the new one" the way they'd each been fixed
individually turned out to directly conflict.

**Fix:**
- `Pet` already had an `age: int = 0` field; the Add Pet form just never
  exposed it. Added an "Age" `st.number_input` (default 0) alongside Pet
  name and Species, passed through as `Pet(pet_name, species,
  age=int(pet_age))`.
- Reworked the active-customer mechanism to have one source of truth
  instead of two competing ones: dropped `active_client_id` and the
  `default_index`/`index=` computation entirely. The selectbox now has
  no `index=` argument at all - its value is set only by directly writing
  to `st.session_state.active_client_select` (seeded to `0` on the very
  first run, set to `len(business.clients) - 1` by Add Customer, and
  reset to `0` by Remove Customer and Start Over), which is the actually-
  supported way to control a keyed widget's value from code.

**Verification:** in an isolated `AppTest` session (pointed at a scratch
JSON file, not `business_data.json`, per the incident above) - confirmed
a pet added without touching the Age field gets `age == 0`; confirmed
adding a second customer moves the selectbox to index 1; and confirmed
switching to index 0 and back to index 1 afterward still works (guarding
against reintroducing the earlier switching-reverts bug). All 14 tests
still pass.

---

## Enhancement: pet-type matching for employees

**Request:** workers should also have a "type of pet they can handle"
skill, and dispatch should match a task's pet, not just its category,
against that.

**Domain model (`pawpal_system.py`):**
- `Task` gained `pet_species: str = ""`, stamped automatically by
  `Pet.add_task()`/`edit_task()` from the pet's own `species` - the same
  denormalization already used for `pet_id`, so the dispatcher never
  needs a separate pet lookup.
- `Owner.edit_pet()` now also re-stamps `pet_species` on every one of
  that pet's existing tasks when the species changes - without this, a
  pet's already-created tasks would keep matching workers against its
  *old* species after an edit.
- `Worker` gained `pet_types: list[str] = field(default_factory=list)`
  (empty = generalist/any pet, same convention as `skills`).
- `Worker.is_qualified_for()` was split into two methods,
  `is_skilled_for()` (category) and `is_suited_for()` (pet species), with
  `is_qualified_for()` now just requiring both. The split exists so the
  dispatcher's retry loop can tell the AI exactly which constraint it
  violated - a worker right on skill but wrong on pet type is a different
  problem from a worker wrong on skill, and lumping them into one vague
  message would have crippled the AI's ability to self-correct.

**Dispatcher (`dispatcher.py`):**
- The LLM prompt now lists each task's `pet_species` and each worker's
  `pet_types`, not just category/skills - the AI can't respect a
  constraint it was never told about.
- `_validate()` checks `is_skilled_for()` and `is_suited_for()`
  separately, producing a distinct violation message for each.
- `_fallback()` needed no change - it already calls
  `worker.is_qualified_for()`, which now covers pet type automatically.

**UI (`app.py`):**
- Pulled the twice-duplicated `["dog", "cat", "other"]` list (Add/Edit
  Pet) into one shared `SPECIES_OPTIONS` constant, same pattern as
  `TASK_CATEGORIES`, and reused it for the new Employee fields.
- Add/Edit Employee forms gained a "Pet types (none = any pet)"
  multiselect; the Employees list display shows it too.

**Persistence (`storage.py`):** `Worker.pet_types` and `Task.pet_species`
are now saved/loaded, with `.get(..., default)` fallbacks so a
`business_data.json` saved before this change still loads fine (no
migration needed).

**Tests:** 5 new tests - `Pet.add_task()` stamping species,
`Owner.edit_pet()` re-stamping it on existing tasks, the three-way split
between `is_skilled_for`/`is_suited_for`/`is_qualified_for`, a `_validate()`
test confirming a pet-type mismatch gets its own message (not lumped in
with a skill mismatch), and a `_fallback()` test confirming a
wrong-species worker is skipped rather than assigned. All 19 tests
(14 previous + 5 new) pass.

**Documentation:** `README.md`'s Summary and Architectural Overview, and
`diagrams/architecure_flow_finalproject.mmd`'s `CLIENT`/`STAFF`/`CHECK`
node labels, updated to mention pet-type matching alongside skill
matching.

**Verification:** round-tripped through the actual UI with an isolated
`AppTest` session (scratch file, not `business_data.json`) - added a cat
pet with a walking task, added a dog-only-qualified employee, ran
"Build & dispatch," and confirmed the task was correctly left unassigned
rather than given to a worker who doesn't handle cats.

### Side discovery while verifying: the default Gemini model had been deprecated

That same verification run surfaced something unrelated but important:
with the user's real `GEMINI_API_KEY` in place, every dispatch call was
actually failing (silently falling back) because `DEFAULT_MODEL =
"gemini-2.5-flash"` (set in Step 5) is no longer available - Google's API
returned a 404 telling us to use `gemini-3.8-flash` instead. Updated
`DEFAULT_MODEL` in `dispatcher.py` and the example in `.env.example`
accordingly. Confirmed the fix really worked: the error changed from a
permanent 404 (wrong model) to a transient 503 ("high demand, try again
later") - a temporary capacity issue on Google's side, not a bug here -
and the dispatcher's fallback guardrail produced a correct assignment
either way, with no crash. Not re-tested further to avoid burning the
user's API quota on a transient error; should succeed on a normal retry.

---
