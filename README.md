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
