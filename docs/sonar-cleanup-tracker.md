# SonarCloud Zero-Smells Cleanup — Tracker

**Branch:** `chore/sonar-zero-cleanup` (off `develop`)
**Goal:** Drive SonarCloud code smells from **60 → 0** with zero behavior change.
**Baseline (start):** 0 bugs · 0 vulns · 0 hotspots · **60 code smells** · quality gate OK · 1755 tests passing.

> **Resumability:** Every cluster is committed + pushed independently. A different model/agent
> can resume by reading this tracker, checking `git log`, and continuing from the first
> unchecked cluster. Re-confirm "zero" only after CI's SonarCloud analysis runs on a push.

## Rules for any agent working this branch

1. **No behavior change.** S3776 cognitive-complexity fixes = extract private helpers only.
2. Validate before every commit: `uv run ruff check --fix src tests && uv run ruff format src tests`,
   `uv run pyright src`, `uv run python -m pytest -q`.
3. One commit per cluster. Push after each. Update the checkbox + commit SHA below.
4. SonarCloud re-analyzes on CI push — final "zero" is confirmed from the branch's Sonar measures, not locally.

## Clusters

- [x] **C1 — transport drivers** (13): `agentic.py`, `classic.py`, `factory.py` — done, pushed
- [x] **C2 — api** (9): `api/character.py`, `api/client.py`, `api/video.py` — done, pushed
- [x] **C3 — ui-automation** (14): `transports/ui_automation.py`, `transports/ui_automation_video.py` — done, pushed
- [x] **C4 — chain/manifests** (13): `chain.py`, `chain_manifest.py`, `composition.py`, `movie_manifest.py`, `data/recorder.py` — done, pushed

> **All 60 addressed.** Wave 2 (C3/C4) green: pyright 0 errors, ruff clean, 1755 tests pass.
> Final "zero" pending SonarCloud CI re-analysis on this branch / its PR.
- [x] **C5 — cli** (13): `cli.py`, `cli_image.py`, `cli_movie.py`, `cli_scene.py`, `cli_video.py` — done, pushed

> Wave 1 (C1/C2/C5) green: pyright 0 errors, ruff clean, 1755 tests pass. Two NOSONAR
> acceptances: `cli_image.t2i` (CLI-surface param count) and `cli_video._run_batch`
> (async required by asyncio.run convention). Driver interface params also NOSONAR.

## Issue inventory (60)

See `git show` of the first commit, or SonarCloud:
https://sonarcloud.io/project/issues?id=ffroliva_gflow-cli&types=CODE_SMELL

| File | Line | Rule | Severity |
|------|------|------|----------|
| api/character.py | 196 | S3776 | CRITICAL |
| api/character.py | 55 | S1135 | INFO |
| api/client.py | 1130 | S5655 | CRITICAL |
| api/client.py | 1271 | S5655 | CRITICAL |
| api/client.py | 1350 | S5655 | CRITICAL |
| api/client.py | 759 | S1192 | CRITICAL |
| api/client.py | 985 | S3776 | CRITICAL |
| transports/drivers/agentic.py | 179 | S1172 ×2 | MAJOR |
| transports/drivers/agentic.py | 179,208 | S7503 | MINOR |
| transports/drivers/agentic.py | 210,213,275,338 | S1172 | MAJOR |
| transports/drivers/classic.py | 196 | S7503 | MINOR |
| transports/drivers/classic.py | 198,199,201 | S1172 | MAJOR |
| transports/drivers/factory.py | 75 | S7632 | MAJOR |
| transports/ui_automation.py | 489,492,884 | S1192 | CRITICAL |
| transports/ui_automation.py | 611,836 | S3776 | CRITICAL |
| transports/ui_automation.py | 937 | S2638 | CRITICAL |
| transports/ui_automation_video.py | 1167 | S7503 | MINOR |
| transports/ui_automation_video.py | 1458,1659,558,731 | S3776 | CRITICAL |
| transports/ui_automation_video.py | 436,437 | S1192 | CRITICAL |
| transports/ui_automation_video.py | 844 | S7632 | MAJOR |
| api/video.py | 242 | S3776 | CRITICAL |
| api/video.py | 362 | S1192 | CRITICAL |
| chain.py | 155 | S3776 | CRITICAL |
| chain.py | 220 | S7632 | MAJOR |
| chain_manifest.py | 50 | S3776 | CRITICAL |
| cli.py | 169 | S3776 | CRITICAL |
| cli_image.py | 1245,856 | S3776 | CRITICAL |
| cli_image.py | 1246,657 | S107 | MAJOR |
| cli_movie.py | 260,295 | S3776 | CRITICAL |
| cli_movie.py | 537 | S3358 | MAJOR |
| cli_movie.py | 703 | S7632 | MAJOR |
| cli_scene.py | 51 | S1940 | MINOR |
| cli_video.py | 265 | S7503 | MINOR |
| cli_video.py | 324,753 | S3776 | CRITICAL |
| composition.py | 143,215 | S3776 | CRITICAL |
| data/recorder.py | 167,438 | S3776 | CRITICAL |
| movie_manifest.py | 106,81 | S1192 | CRITICAL |
| movie_manifest.py | 238,84 | S3776 | CRITICAL |
