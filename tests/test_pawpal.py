from datetime import date, time, timedelta

from pawpal_system import Owner, Pet, Priority, Schedule, Scheduler, ScheduledTask, Status, Task, Worker



def test_scheduled_task_mark_done():
    # mark_done() should change the scheduled task's status to DONE and
    # record the completion date on the underlying Task.
    task = Task("Morning walk", 30, Priority.HIGH)
    item = ScheduledTask(task, time(8, 0), time(8, 30))
    today = date(2026, 10, 21)
    assert item.status is Status.PENDING

    item.mark_done(today)

    assert item.status is Status.DONE
    assert item.is_done
    assert task.last_completed == today


def test_task_addition():
    # Adding a task to a Pet should increase that pet's task count.
    pet = Pet("Rex", "dog")
    assert len(pet.tasks) == 0

    pet.add_task(Task("Feeding", 10, Priority.HIGH))

    assert len(pet.tasks) == 1


def test_sort_by_priority_orders_high_to_low_and_breaks_ties_by_duration():
    # sort_by_priority() should put HIGH before MEDIUM before LOW, and when
    # two tasks share a priority, the shorter one should come first.
    low = Task("Play time", 15, Priority.LOW)
    medium = Task("Brushing", 10, Priority.MEDIUM)
    high_long = Task("Long walk", 45, Priority.HIGH)
    high_short = Task("Quick potty break", 5, Priority.HIGH)

    # Deliberately out of order going in, so the result proves sorting
    # happened rather than just echoing insertion order.
    tasks = [low, high_long, medium, high_short]

    ordered = Scheduler().sort_by_priority(tasks)

    assert ordered == [high_short, high_long, medium, low]
    # The original list should be left untouched.
    assert tasks == [low, high_long, medium, high_short]


def test_task_is_due_recurrence_logic():
    # Daily tasks are due again the day after they were completed, but not
    # on the same day they were completed. Weekly tasks (no pinned weekday)
    # only come due again once 7 full days have passed.
    today = date(2026, 10, 21)

    daily = Task("Feeding", 10, Priority.HIGH, frequency="daily")
    assert daily.is_due(today)  # never completed -> due

    daily.mark_complete(today)
    assert not daily.is_due(today)  # already done today -> not due
    assert daily.is_due(date(2026, 10, 22))  # due again the next day

    weekly = Task("Nail trim", 10, Priority.LOW, frequency="weekly")
    weekly.mark_complete(today)
    assert not weekly.is_due(today + timedelta(days=6))
    assert weekly.is_due(today + timedelta(days=7))


def test_schedule_find_conflicts_detects_overlap():
    # Two scheduled tasks whose times overlap should be reported as a
    # conflict; tasks that merely touch end-to-end should not.
    walk = ScheduledTask(
        Task("Morning walk", 30, Priority.HIGH), time(8, 0), time(8, 30)
    )
    breakfast = ScheduledTask(
        Task("Breakfast", 15, Priority.HIGH), time(8, 15), time(8, 30)
    )
    play = ScheduledTask(
        Task("Play time", 15, Priority.LOW), time(8, 30), time(8, 45)
    )

    schedule = Schedule(date=date(2026, 10, 21), start_time=time(8, 0), available_minutes=90)
    schedule.scheduled = [walk, breakfast, play]

    conflicts = schedule.find_conflicts()

    assert conflicts == [(walk, breakfast)]
    assert schedule.has_conflict()


def test_add_task_stamps_pet_species():
    # A task's pet_species is denormalized from its pet at add time, so a
    # Worker can be matched against it without a separate pet lookup.
    pet = Pet("Rex", "dog")
    task = Task("Morning walk", 30, Priority.HIGH, category="walking")

    pet.add_task(task)

    assert task.pet_species == "dog"


def test_edit_pet_restamps_species_on_existing_tasks():
    # Changing a pet's species must update the denormalized pet_species on
    # every task it already has, or those tasks would keep matching
    # workers against the pet's old species.
    owner = Owner("Jordan", "Smith")
    pet = Pet("Whiskers", "cat")
    owner.add_pet(pet)
    task = Task("Grooming", 20, Priority.MEDIUM, category="grooming")
    pet.add_task(task)
    assert task.pet_species == "cat"

    owner.edit_pet(pet.pet_id, "Whiskers", "other", age=3)

    assert task.pet_species == "other"


def test_worker_qualification_checks_skill_and_pet_type_separately():
    # A dog-walking generalist-on-skills worker who only handles cats
    # should fail the pet-type check but pass the skill check, and the
    # reverse should hold for a dog-only, non-walking worker.
    dog_task = Task("Morning walk", 30, Priority.HIGH, category="walking")
    dog_task.pet_species = "dog"

    cat_sitter = Worker("Robin", skills=[], pet_types=["cat"])
    assert cat_sitter.is_skilled_for(dog_task)  # generalist on skills
    assert not cat_sitter.is_suited_for(dog_task)  # wrong species
    assert not cat_sitter.is_qualified_for(dog_task)

    groomer = Worker("Sam", skills=["grooming"], pet_types=["dog"])
    assert not groomer.is_skilled_for(dog_task)  # wrong category
    assert groomer.is_suited_for(dog_task)  # right species
    assert not groomer.is_qualified_for(dog_task)

    dog_walker = Worker("Alex", skills=["walking"], pet_types=["dog"])
    assert dog_walker.is_qualified_for(dog_task)

