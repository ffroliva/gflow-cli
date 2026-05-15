# Orchestration: Auth Login via Real Chrome

## Strategy
This plan follows a **Test-Driven Refactor** strategy. We first promote the `auth.py` module to a package and define a strict Protocol to ensure both strategies are interchangeable. We use the existing `browser_manager` idioms for Chrome detection to keep the implementation consistent with the rest of the project.

## Checkpoints
1. **Checkpoint A (After T1.3)**: Protocol defined + failing tests.
2. **Checkpoint B (After T2.4)**: Implementation complete + all tests green (GREEN phase).
3. **Checkpoint C (After T3.2)**: Docs aligned with implementation.
4. **Checkpoint D (After T4.2)**: Tagged and pushed.

## Risk Management
- **Risk**: Real Chrome detection fails on a specific OS (macOS/Linux).
  - **Mitigation**: Use the existing `browser_manager` probe which is already OS-aware.
- **Risk**: Optimistic polling is too fast and hits race conditions.
  - **Mitigation**: Ensure a minimum settle-time after cookie detection before declaring success.

## Resource Allocation
- **Primary Agent**: Implementation & TDD.
- **Council**: Spec review (DONE), Plan review (Next), Implementation review (Final).
