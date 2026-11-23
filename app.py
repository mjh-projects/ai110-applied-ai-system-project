from datetime import date, time

import streamlit as st
from dotenv import load_dotenv

from dispatcher import Dispatcher
from pawpal_system import Business, Owner, Pet, Priority, Task, Worker

# Picks up GEMINI_API_KEY from a local .env file if one exists; does
# nothing (not an error) when there isn't one, so the app still runs - the
# Dispatcher's own fallback guardrail handles a genuinely missing key.
load_dotenv()

# Shared vocabulary between a Task's category and a Worker's skills, so the
# dispatcher can match them by exact string. A text field would let a typo
# ("walkng") silently make every worker "unqualified".
TASK_CATEGORIES = ["walking", "feeding", "meds", "grooming", "enrichment", "training", "other"]

st.set_page_config(page_title="PawPal Company Scheduler", page_icon="🐾", layout="centered")

st.title("🐾 PawPal Company Scheduler")

st.markdown(
    """
PawPal Company Scheduler turns PawPal+ into a tool for a pet-care **business**: instead of an
owner doing care tasks themselves, the business assigns each task to an employee.

Add customers and their pets' tasks below, add employees and their skills/hours, then build
today's dispatch: an AI planner proposes who does what, a deterministic check makes sure every
assignment is actually valid (right skill, inside their hours, not double-booked), and the
final plan is shown with the AI's reasoning.
"""
)

st.divider()

# Business holds every customer (Owner) and employee (Worker). Streamlit
# re-runs this whole file on every click, so it's kept in session_state -
# without this check, every click would start over with an empty Business.
if "business" not in st.session_state:
    st.session_state.business = Business()
    st.session_state.business.add_client(Owner("Jordan", "Smith"))

business = st.session_state.business

if st.button("Start over"):
    st.session_state.business = Business()
    st.session_state.business.add_client(Owner("Jordan", "Smith"))
    st.session_state.editing_task = None
    st.session_state.editing_pet = None
    st.session_state.editing_worker = None
    st.session_state.active_client_id = None
    st.session_state.dispatch = None
    st.rerun()

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
st.subheader("Customers")

col_add_first, col_add_last, col_add_btn = st.columns([2, 2, 1])
with col_add_first:
    new_client_first = st.text_input("First name", value="", key="new_client_first")
with col_add_last:
    new_client_last = st.text_input("Last name", value="", key="new_client_last")
with col_add_btn:
    st.write("")
    st.write("")
    if st.button("Add customer") and (new_client_first or new_client_last):
        business.add_client(Owner(new_client_first, new_client_last))
        st.rerun()

if not business.clients:
    st.info("No customers yet. Add one above.")
    st.stop()

# `active_client_id` remembers which customer's pets/tasks are shown below,
# across reruns triggered by unrelated buttons elsewhere on the page.
default_client_id = st.session_state.get("active_client_id") or business.clients[0].client_id
default_index = next(
    (i for i, c in enumerate(business.clients) if c.client_id == default_client_id), 0
)
client_index = st.selectbox(
    "Active customer",
    range(len(business.clients)),
    index=default_index,
    format_func=lambda i: business.clients[i].full_name(),
)
client = business.clients[client_index]
st.session_state.active_client_id = client.client_id

col_cfirst, col_clast = st.columns(2)
with col_cfirst:
    client.first_name = st.text_input(
        "First name", value=client.first_name, key=f"cfirst_{client.client_id}"
    )
with col_clast:
    client.last_name = st.text_input(
        "Last name", value=client.last_name, key=f"clast_{client.client_id}"
    )

col_cstart, col_cminutes, col_cremove = st.columns([1.5, 1.5, 1])
with col_cstart:
    client.day_start = st.time_input(
        "Day starts at", value=client.day_start, key=f"cstart_{client.client_id}"
    )
with col_cminutes:
    client.daily_minutes = int(
        st.number_input(
            "Minutes of care/day",
            min_value=1,
            max_value=1440,
            value=client.daily_minutes,
            key=f"cmin_{client.client_id}",
        )
    )
with col_cremove:
    st.write("")
    st.write("")
    if st.button("Remove customer", key=f"cremove_{client.client_id}"):
        business.remove_client(client.client_id)
        st.session_state.active_client_id = None
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Pets and tasks for the active customer
# ---------------------------------------------------------------------------
st.markdown(f"### {client.full_name()}'s Pets")

col_pet, col_species = st.columns(2)
with col_pet:
    pet_name = st.text_input("Pet name", value="Mochi")
with col_species:
    species = st.selectbox("Species", ["dog", "cat", "other"])

if st.button("Add pet"):
    client.add_pet(Pet(pet_name, species))
    st.rerun()

if client.pets:
    st.write("Pets:")
    for pet in client.pets:
        col_pet_info, col_pet_edit, col_pet_remove = st.columns([4, 1.2, 1.4])
        col_pet_info.write(
            f"**{pet.name}** ({pet.species}, age {pet.age}): {len(pet.tasks)} tasks"
        )
        if col_pet_edit.button("Edit", key=f"edit_pet_{pet.pet_id}"):
            st.session_state.editing_pet = pet.pet_id
            st.rerun()
        if col_pet_remove.button("Remove", key=f"remove_pet_{pet.pet_id}"):
            client.remove_pet(pet.pet_id)
            if st.session_state.get("editing_pet") == pet.pet_id:
                st.session_state.editing_pet = None
            st.rerun()
else:
    st.info("No pets yet. Add one above.")

editing_pet_id = st.session_state.get("editing_pet")
editing_pet = next((p for p in client.pets if p.pet_id == editing_pet_id), None)
if editing_pet is not None:
    st.markdown("#### Edit pet")
    pkey = editing_pet.pet_id
    new_pet_name = st.text_input("Pet name", value=editing_pet.name, key=f"pname_{pkey}")
    col_sp, col_age = st.columns(2)
    with col_sp:
        species_options = ["dog", "cat", "other"]
        sp_index = (
            species_options.index(editing_pet.species)
            if editing_pet.species in species_options
            else 2
        )
        new_species = st.selectbox(
            "Species", species_options, index=sp_index, key=f"pspecies_{pkey}"
        )
    with col_age:
        new_age = st.number_input(
            "Age", min_value=0, max_value=40, value=editing_pet.age, key=f"page_{pkey}"
        )
    col_pet_save, col_pet_cancel = st.columns(2)
    if col_pet_save.button("Save pet"):
        client.edit_pet(editing_pet.pet_id, new_pet_name, new_species, int(new_age))
        st.session_state.editing_pet = None
        st.rerun()
    if col_pet_cancel.button("Cancel", key="cancel_pet"):
        st.session_state.editing_pet = None
        st.rerun()

st.markdown("### Tasks")
st.caption("Pick a pet, then add a task for it. Category is what a worker's skills are matched against.")

if client.pets:
    pet_index = st.selectbox(
        "Pet",
        range(len(client.pets)),
        format_func=lambda i: client.pets[i].name,
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        task_title = st.text_input("Task title", value="Morning walk")
    with col2:
        duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
    with col3:
        priority = st.selectbox("Priority", ["low", "medium", "high"], index=2)

    col4, col5, col6 = st.columns(3)
    with col4:
        category = st.selectbox("Category", TASK_CATEGORIES)
    with col5:
        frequency = st.selectbox("Repeats", ["daily", "weekly"])
    with col6:
        pin = st.checkbox("Pin to a specific time")
        pinned_time = st.time_input("Preferred time", value=time(8, 0), disabled=not pin)

    if st.button("Add task"):
        client.pets[pet_index].add_task(
            Task(
                task_title,
                int(duration),
                Priority[priority.upper()],
                category=category,
                frequency=frequency,
                preferred_time=pinned_time if pin else None,
            )
        )
        st.rerun()
else:
    st.info("Add a pet first, then you can add tasks for it.")

client_pet_names = {pet.pet_id: pet.name for pet in client.pets}
client_pets_by_id = {pet.pet_id: pet for pet in client.pets}
client_tasks = client.get_all_tasks()
if client_tasks:
    st.write("Current tasks:")
    for task in client_tasks:
        col_info, col_edit, col_remove = st.columns([4, 1.2, 1.4])
        pinned_text = f", at {task.preferred_time:%H:%M}" if task.preferred_time else ""
        col_info.write(
            f"**{task.title}** for {client_pet_names.get(task.pet_id, '')}: "
            f"{task.duration_minutes} min, {task.priority.name.lower()}, "
            f"{task.category or 'uncategorized'}, {task.frequency}{pinned_text}"
        )
        if col_edit.button("Edit", key=f"edit_{task.task_id}"):
            st.session_state.editing_task = task.task_id
            st.rerun()
        if col_remove.button("Remove", key=f"remove_{task.task_id}"):
            client_pets_by_id[task.pet_id].remove_task(task.task_id)
            if st.session_state.get("editing_task") == task.task_id:
                st.session_state.editing_task = None
            st.rerun()
else:
    st.info("No tasks yet. Add one above.")

editing_id = st.session_state.get("editing_task")
editing = next((t for t in client_tasks if t.task_id == editing_id), None)
if editing is not None:
    st.markdown("#### Edit task")
    key = editing.task_id
    new_title = st.text_input("Title", value=editing.title, key=f"title_{key}")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        new_duration = st.number_input(
            "Minutes", min_value=1, max_value=240,
            value=editing.duration_minutes, key=f"dur_{key}",
        )
    with col_b:
        levels = ["low", "medium", "high"]
        new_priority = st.selectbox(
            "Priority", levels,
            index=levels.index(editing.priority.name.lower()), key=f"pri_{key}",
        )
    with col_c:
        freqs = ["daily", "weekly"]
        new_frequency = st.selectbox(
            "Repeats", freqs,
            index=freqs.index(editing.frequency), key=f"freq_{key}",
        )
    new_category = st.selectbox(
        "Category",
        TASK_CATEGORIES,
        index=TASK_CATEGORIES.index(editing.category) if editing.category in TASK_CATEGORIES else 0,
        key=f"cat_{key}",
    )
    new_pin = st.checkbox(
        "Pin to a specific time",
        value=editing.preferred_time is not None, key=f"pin_{key}",
    )
    new_time = st.time_input(
        "Preferred time",
        value=editing.preferred_time or time(8, 0),
        disabled=not new_pin, key=f"time_{key}",
    )
    col_save, col_cancel = st.columns(2)
    if col_save.button("Save changes"):
        client_pets_by_id[editing.pet_id].edit_task(
            editing.task_id,
            Task(
                new_title,
                int(new_duration),
                Priority[new_priority.upper()],
                category=new_category,
                frequency=new_frequency,
                days_of_week=editing.days_of_week,
                preferred_time=new_time if new_pin else None,
                last_completed=editing.last_completed,
            ),
        )
        st.session_state.editing_task = None
        st.rerun()
    if col_cancel.button("Cancel"):
        st.session_state.editing_task = None
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------
st.subheader("Employees")
st.caption("Staff who will be assigned pet-care tasks by the dispatcher.")

col_wname, col_wskills = st.columns([1.5, 2.5])
with col_wname:
    worker_name = st.text_input("Employee name", value="", key="new_worker_name")
with col_wskills:
    worker_skills = st.multiselect(
        "Skills (none selected = can do any task)", TASK_CATEGORIES, key="new_worker_skills"
    )

col_wstart, col_wend, col_wcap = st.columns(3)
with col_wstart:
    worker_start = st.time_input("Available from", value=time(8, 0), key="new_worker_start")
with col_wend:
    worker_end = st.time_input("Available until", value=time(18, 0), key="new_worker_end")
with col_wcap:
    worker_cap = st.number_input(
        "Max minutes/day", min_value=1, max_value=1440, value=480, key="new_worker_cap"
    )

if st.button("Add employee") and worker_name:
    business.add_worker(
        Worker(
            worker_name,
            skills=list(worker_skills),
            available_start=worker_start,
            available_end=worker_end,
            max_minutes_per_day=int(worker_cap),
        )
    )
    st.rerun()

if business.workers:
    st.write("Employees:")
    for worker in business.workers:
        col_info, col_edit, col_remove = st.columns([4, 1.2, 1.4])
        skills_text = ", ".join(worker.skills) if worker.skills else "any task"
        col_info.write(
            f"**{worker.name}** - {skills_text} - "
            f"{worker.available_start:%H:%M}-{worker.available_end:%H:%M}, "
            f"max {worker.max_minutes_per_day} min/day"
        )
        if col_edit.button("Edit", key=f"edit_worker_{worker.worker_id}"):
            st.session_state.editing_worker = worker.worker_id
            st.rerun()
        if col_remove.button("Remove", key=f"remove_worker_{worker.worker_id}"):
            business.remove_worker(worker.worker_id)
            if st.session_state.get("editing_worker") == worker.worker_id:
                st.session_state.editing_worker = None
            st.rerun()
else:
    st.info("No employees yet. Add one above - the dispatcher has nothing to assign without one.")

editing_worker_id = st.session_state.get("editing_worker")
editing_worker = next((w for w in business.workers if w.worker_id == editing_worker_id), None)
if editing_worker is not None:
    st.markdown("#### Edit employee")
    wkey = editing_worker.worker_id
    new_worker_name = st.text_input("Employee name", value=editing_worker.name, key=f"wname_{wkey}")
    new_worker_skills = st.multiselect(
        "Skills (none selected = can do any task)",
        TASK_CATEGORIES,
        default=editing_worker.skills,
        key=f"wskills_{wkey}",
    )
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        new_worker_start = st.time_input(
            "Available from", value=editing_worker.available_start, key=f"wstart_{wkey}"
        )
    with col_b:
        new_worker_end = st.time_input(
            "Available until", value=editing_worker.available_end, key=f"wend_{wkey}"
        )
    with col_c:
        new_worker_cap = st.number_input(
            "Max minutes/day", min_value=1, max_value=1440,
            value=editing_worker.max_minutes_per_day, key=f"wcap_{wkey}",
        )
    col_save, col_cancel = st.columns(2)
    if col_save.button("Save employee"):
        editing_worker.name = new_worker_name
        editing_worker.skills = list(new_worker_skills)
        editing_worker.available_start = new_worker_start
        editing_worker.available_end = new_worker_end
        editing_worker.max_minutes_per_day = int(new_worker_cap)
        st.session_state.editing_worker = None
        st.rerun()
    if col_cancel.button("Cancel", key="cancel_worker"):
        st.session_state.editing_worker = None
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Dispatch: build every customer's plan, then let the AI assign employees.
# ---------------------------------------------------------------------------
st.subheader("Dispatch Today's Schedule")
st.caption(
    "Builds each customer's plan (deterministic), then an AI planner proposes who does each "
    "task; a deterministic check validates the proposal and a fallback rule takes over if it "
    "can't be made valid."
)

if st.button("Build & dispatch today's schedule"):
    today = date.today()
    # Business.generate_daily_tasks() is pure scheduling logic (no AI); it
    # also saves each customer's own Schedule so get_schedule() keeps working.
    pooled = business.generate_daily_tasks(today)
    result = Dispatcher().dispatch(pooled, business.workers)
    # dispatcher.py returns data only; writing the chosen worker onto each
    # ScheduledTask here keeps the dispatcher itself free of domain mutation.
    for item in pooled:
        item.assigned_worker_id = result.assignments.get(item.task.task_id, "")
    st.session_state.dispatch = {"date": today, "pooled": pooled, "result": result}

dispatch_state = st.session_state.get("dispatch")
if dispatch_state and dispatch_state["date"] == date.today():
    pooled = dispatch_state["pooled"]
    result = dispatch_state["result"]

    workers_by_id = {w.worker_id: w for w in business.workers}
    pet_names = {pet.pet_id: pet.name for c in business.clients for pet in c.pets}

    if result.used_fallback:
        st.warning(
            f"The AI planner couldn't produce a valid assignment after {result.attempts} "
            "attempt(s), so a deterministic fallback rule assigned these tasks instead."
        )

    total_minutes = sum(item.task.duration_minutes for item in pooled)
    st.success(f"{len(pooled)} task(s) scheduled today, {total_minutes} minute(s) of care.")

    ordered = sorted(pooled, key=lambda item: item.start_time)
    if ordered:
        st.markdown("#### Today's Dispatch")
        st.table(
            [
                {
                    "Time": f"{item.start_time:%H:%M}-{item.end_time:%H:%M}",
                    "Task": item.title,
                    "Pet": pet_names.get(item.task.pet_id, ""),
                    "Assigned to": (
                        workers_by_id[item.assigned_worker_id].name
                        if item.assigned_worker_id in workers_by_id
                        else "Unassigned"
                    ),
                    "Priority": item.task.priority.name.title(),
                    "Duration (min)": item.duration_minutes,
                    "Status": item.status.value.title(),
                }
                for item in ordered
            ]
        )

        st.caption("Update a task's status:")
        for item in ordered:
            col_time, col_task, col_worker, col_done, col_skip = st.columns(
                [1.5, 3, 2, 1.2, 1.2]
            )
            col_time.write(f"{item.start_time:%H:%M}-{item.end_time:%H:%M}")
            col_task.write(f"**{item.title}** for {pet_names.get(item.task.pet_id, '')}")
            assigned_worker = workers_by_id.get(item.assigned_worker_id)
            col_worker.write(assigned_worker.name if assigned_worker else "Unassigned")
            if col_done.button("Done", key=f"done_{item.task.task_id}"):
                item.mark_done(date.today())
                st.rerun()
            if col_skip.button("Skip", key=f"skip_{item.task.task_id}"):
                item.mark_skipped()
                st.rerun()
    else:
        st.info("Nothing was scheduled today.")

    if result.unassigned:
        tasks_by_id = {item.task.task_id: item for item in pooled}
        st.warning(f"{len(result.unassigned)} task(s) could not be assigned to any employee:")
        st.table(
            [
                {
                    "Task": tasks_by_id[tid].title,
                    "Pet": pet_names.get(tasks_by_id[tid].task.pet_id, ""),
                    "Priority": tasks_by_id[tid].task.priority.name.title(),
                }
                for tid in result.unassigned
            ]
        )

    all_skipped = [
        (c, task)
        for c in business.clients
        for task in (c.get_schedule(date.today()).skipped if c.get_schedule(date.today()) else [])
    ]
    if all_skipped:
        st.warning(f"{len(all_skipped)} task(s) didn't fit a customer's time budget today:")
        st.table(
            [
                {
                    "Customer": c.full_name(),
                    "Task": task.title,
                    "Priority": task.priority.name.title(),
                    "Duration (min)": task.duration_minutes,
                }
                for c, task in all_skipped
            ]
        )

    with st.expander("Why these assignments?"):
        for line in result.log:
            st.text(line)
        if any(result.reasons.values()):
            st.markdown("---")
            for item in ordered:
                reason = result.reasons.get(item.task.task_id)
                if reason:
                    st.text(f"{item.title}: {reason}")
else:
    st.info('Click "Build & dispatch today\'s schedule" to generate today\'s plan.')
