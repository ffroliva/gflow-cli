```markdown
# gflow-cli Development Patterns

> Auto-generated skill from repository analysis

## Overview

This skill teaches you the core development patterns, coding conventions, and collaborative workflows used in the `gflow-cli` Python project. You'll learn how to contribute new features, fix bugs, and update documentation in a way that aligns with the team's established practices. The guide covers file organization, commit conventions, code style, and the step-by-step processes for feature development, bugfixing, and documentation updates.

---

## Coding Conventions

### File Naming

- **Python modules and files:** Use `snake_case`.
  - Example: `my_module.py`, `auth_utils.py`
- **Test files:** Use `snake_case`, typically mirroring the module under test.
  - Example: `test_auth.py`

### Import Style

- **Relative imports** are preferred within the package.
  ```python
  # In src/gflow_cli/commands/run.py
  from ..utils import parse_config
  ```

### Export Style

- **Named exports**: Explicitly define what is exported from each module using `__all__` where appropriate.
  ```python
  __all__ = ["run_command", "parse_config"]
  ```

### Commit Messages

- **Conventional commit format** with prefixes:
  - `feat:` for new features
  - `fix:` for bug fixes
  - `docs:` for documentation changes
- **Average commit message length:** ~55 characters
  - Example: `feat(auth): add OAuth2 token refresh support`

---

## Workflows

### Feature Development with Spec, Plan, and Tests

**Trigger:** When adding a significant new feature or fixing a complex bug (especially auth or API), following a spec/plan/test-driven process.  
**Command:** `/new-feature-with-spec`

1. **Write Design Spec and Plan**
   - Add a design spec in `docs/superpowers/specs/` (e.g., `my_feature_spec.md`)
   - Add an implementation plan in `docs/superpowers/plans/` (e.g., `my_feature_plan.md`)
2. **Implement the Feature**
   - Update or add code in `src/gflow_cli/` (may span multiple modules)
   - Use relative imports and snake_case naming
3. **Write or Update Tests**
   - Add or update tests in `tests/` (unit and end-to-end as needed)
4. **Update Documentation**
   - Edit `docs/USAGE.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `KNOWN_ISSUES.md`, etc., to reflect the new feature or fix

**Example:**
```python
# src/gflow_cli/auth.py
def refresh_token():
    """Refresh OAuth2 token."""
    pass  # Implementation here

# tests/test_auth.py
def test_refresh_token():
    assert refresh_token() is not None
```
---

### Documentation and Process Update

**Trigger:** When clarifying or updating development process, contribution guidelines, or documentation routing.  
**Command:** `/update-process-docs`

1. **Edit or Add Documentation Files**
   - Update `docs/DEVELOPMENT.md`, `CONTRIBUTING.md`, `docs/INDEX.md`, or `.github/PULL_REQUEST_TEMPLATE.md`
2. **Update References or Cross-links**
   - Ensure all relevant docs are linked and references are current
3. **Clarify or Correct Instructions**
   - Revise test or process instructions as needed

**Example:**
```markdown
<!-- docs/DEVELOPMENT.md -->
## Running Tests
To run all tests:
```bash
pytest tests/
```
```
---

### Bugfix with Tests and Docs

**Trigger:** When fixing a bug and ensuring it is tested and documented.  
**Command:** `/bugfix-with-tests`

1. **Fix Implementation**
   - Make changes in `src/gflow_cli/` to resolve the bug
2. **Write or Update Tests**
   - Add or update relevant tests in `tests/` to cover the bugfix
3. **Update Documentation**
   - Amend `docs/USAGE.md`, `CHANGELOG.md`, etc., to document the fix

**Example:**
```python
# src/gflow_cli/auth.py
def validate_token(token):
    if not token:
        raise ValueError("Token is required")
    # ...rest of validation...

# tests/test_auth.py
def test_validate_token_raises_on_empty():
    with pytest.raises(ValueError):
        validate_token("")
```
---

## Testing Patterns

- **Test files** are placed in the `tests/` directory and use `snake_case` naming.
- **Framework:** Not explicitly specified, but Python standard testing frameworks like `pytest` are likely.
- **Test structure:** Each test covers a discrete function or feature, with both unit and end-to-end tests as needed.

**Example:**
```python
# tests/test_utils.py
def test_parse_config_valid():
    config = parse_config("config.yaml")
    assert config["version"] == "1.0"
```

---

## Commands

| Command                 | Purpose                                                      |
|-------------------------|--------------------------------------------------------------|
| /new-feature-with-spec  | Start a new feature or major fix using spec/plan/test-driven workflow |
| /update-process-docs    | Update or clarify documentation and process files            |
| /bugfix-with-tests      | Fix a bug with accompanying tests and documentation updates  |
```
