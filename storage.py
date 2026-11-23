"""Simple JSON persistence for the business's roster.

Saves and loads customers (with their pets and tasks) and employees - the
data someone actually typed in. A day's generated Schedule/dispatch result
is NOT persisted here: it's quick to rebuild from this roster plus the
Dispatcher, so only what can't be regenerated needs to survive a restart.
"""

from __future__ import annotations

import json
import os
from datetime import date, time

from pawpal_system import Business, Owner, Pet, Priority, Task, Worker

DEFAULT_PATH = "business_data.json"


def _time_to_str(value: time) -> str:
    return value.strftime("%H:%M")


def _str_to_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _task_to_dict(task: Task) -> dict:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "duration_minutes": task.duration_minutes,
        "priority": task.priority.name,
        "category": task.category,
        "frequency": task.frequency,
        "days_of_week": list(task.days_of_week),
        "preferred_time": _time_to_str(task.preferred_time) if task.preferred_time else None,
        "pet_id": task.pet_id,
        "pet_species": task.pet_species,
        "last_completed": task.last_completed.isoformat() if task.last_completed else None,
    }


def _task_from_dict(data: dict) -> Task:
    return Task(
        title=data["title"],
        duration_minutes=data["duration_minutes"],
        priority=Priority[data["priority"]],
        category=data.get("category", ""),
        frequency=data.get("frequency", "daily"),
        days_of_week=list(data.get("days_of_week", [])),
        preferred_time=(
            _str_to_time(data["preferred_time"]) if data.get("preferred_time") else None
        ),
        task_id=data["task_id"],
        pet_id=data.get("pet_id", ""),
        # ADDED (pet-type matching): defaults to "" for a file saved before
        # this field existed, same as every other .get(..., default) here.
        pet_species=data.get("pet_species", ""),
        last_completed=(
            date.fromisoformat(data["last_completed"]) if data.get("last_completed") else None
        ),
    )


def _pet_to_dict(pet: Pet) -> dict:
    return {
        "pet_id": pet.pet_id,
        "name": pet.name,
        "species": pet.species,
        "age": pet.age,
        "tasks": [_task_to_dict(t) for t in pet.tasks],
    }


def _pet_from_dict(data: dict) -> Pet:
    pet = Pet(
        name=data["name"],
        species=data["species"],
        age=data.get("age", 0),
        pet_id=data["pet_id"],
    )
    pet.tasks = [_task_from_dict(t) for t in data.get("tasks", [])]
    return pet


def _client_to_dict(client: Owner) -> dict:
    return {
        "client_id": client.client_id,
        "first_name": client.first_name,
        "last_name": client.last_name,
        "day_start": _time_to_str(client.day_start),
        "daily_minutes": client.daily_minutes,
        "pets": [_pet_to_dict(p) for p in client.pets],
    }


def _client_from_dict(data: dict) -> Owner:
    client = Owner(
        first_name=data["first_name"],
        last_name=data["last_name"],
        day_start=_str_to_time(data.get("day_start", "07:00")),
        daily_minutes=data.get("daily_minutes", 90),
        client_id=data["client_id"],
    )
    client.pets = [_pet_from_dict(p) for p in data.get("pets", [])]
    return client


def _worker_to_dict(worker: Worker) -> dict:
    return {
        "worker_id": worker.worker_id,
        "name": worker.name,
        "skills": list(worker.skills),
        "pet_types": list(worker.pet_types),
        "available_start": _time_to_str(worker.available_start),
        "available_end": _time_to_str(worker.available_end),
        "max_minutes_per_day": worker.max_minutes_per_day,
    }


def _worker_from_dict(data: dict) -> Worker:
    return Worker(
        name=data["name"],
        skills=list(data.get("skills", [])),
        pet_types=list(data.get("pet_types", [])),
        available_start=_str_to_time(data.get("available_start", "08:00")),
        available_end=_str_to_time(data.get("available_end", "18:00")),
        max_minutes_per_day=data.get("max_minutes_per_day", 480),
        worker_id=data["worker_id"],
    )


def save_business(business: Business, path: str = DEFAULT_PATH) -> None:
    """Write the business's customers, pets, tasks, and employees to a JSON file."""
    data = {
        "name": business.name,
        "clients": [_client_to_dict(c) for c in business.clients],
        "workers": [_worker_to_dict(w) for w in business.workers],
    }
    # Write to a temp file and swap it in, so a crash mid-write can't leave
    # behind a half-written file that load_business() can't parse.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_business(path: str = DEFAULT_PATH) -> Business:
    """Read a business's roster back from a JSON file.

    Raises FileNotFoundError if the file doesn't exist yet, or
    (ValueError, KeyError) if it's missing fields or corrupted - callers
    decide what to fall back to rather than this crashing the app.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    business = Business(name=data.get("name", "PawPal Company Scheduler"))
    business.clients = [_client_from_dict(c) for c in data.get("clients", [])]
    business.workers = [_worker_from_dict(w) for w in data.get("workers", [])]
    return business
