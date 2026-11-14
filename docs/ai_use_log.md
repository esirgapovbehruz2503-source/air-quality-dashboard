# AI Use Log (Green – AI Integrated)
```markdown
| 2026-11-14 | CP2 | Gemini | Added `src/queries.py` (5 stakeholder SQL queries + monitoring summary), `collect_due_slots()` and `--mode assessed` separation, and `tests/test_queries.py`. | Fixed syntax error in `src/queries.py` import, verified all 14 `pytest -v` tests pass offline, and initialized `data/assessed/air_quality_assessed.db` with 420 slots. |
| Date (UTC) | Checkpoint | Tool | Purpose & Output | Verification & Corrections |
| :--- | :--- | :--- | :--- | :--- |
| 2026-11-14 | CP1 (Catch-up) | Gemini | Reviewed draft 7-table SQLite schema, scaffolded `schema.sql`, `src/database.py`, `src/validation.py`, `src/collector.py`, and initial `pytest` suite. | Ran `python -m src.database` (verified 5 locations, 420 slots, 415 opportunities in DB Browser), ran live `python -m src.collector` (observed `late_rejected` on expired 12:00 UTC slot, adjusted MVP demo slot selection to next open slot), and ran `pytest -v` (11 passed). |