# AI Use Log (Green – AI Integrated)

| Date (UTC) | Checkpoint | Tool | Purpose & Output | Verification & Corrections |
| :--- | :--- | :--- | :--- | :--- |
| 2026-11-14 | CP1 (Catch-up) | Gemini | Reviewed draft 7-table SQLite schema, scaffolded `schema.sql`, `src/database.py`, `src/validation.py`, `src/collector.py`, and initial `pytest` suite. | Ran `python -m src.database` (verified 5 locations, 420 slots, 415 opportunities in DB Browser), ran live `python -m src.collector` (observed `late_rejected` on expired 12:00 UTC slot, adjusted MVP demo slot selection to next open slot), and ran `pytest -v` (11 passed). |