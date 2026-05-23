```markdown
# gflow-cli Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches you the core development patterns, coding conventions, and workflows used in the `gflow-cli` Python project. You'll learn how to structure code, manage imports and exports, follow commit conventions, and execute key repository workflows such as synchronizing release changes across branches.

## Coding Conventions

### File Naming
- Use **snake_case** for all Python files and modules.
  - Example: `my_module.py`, `data_processor.py`

### Import Style
- Prefer **relative imports** within the package.
  - Example:
    ```python
    from .utils import parse_config
    from ..core import main_logic
    ```

### Export Style
- Use **named exports**; explicitly define what is exported from modules.
  - Example:
    ```python
    __all__ = ["main_function", "HelperClass"]
    ```

### Commit Patterns
- Mixed commit types, with common prefixes like `docs` and `refactor`.
- Commit messages are concise (average 66 characters).
  - Example:
    ```
    docs: update README with new usage instructions
    refactor: simplify argument parsing in cli.py
    ```

## Workflows

### Back-Merge Release Into Develop
**Trigger:** When a new release is made on `main` and `develop` needs to be updated with those changes.  
**Command:** `/back-merge-release`

1. **Merge `main` into `develop` after a release.**
   - Use your Git tool or CLI:
     ```bash
     git checkout develop
     git merge main
     ```
2. **Resolve any merge conflicts.**
   - Pay special attention to versioned files and documentation.
3. **Update versioned files:**
   - Files to check and update:
     - `CHANGELOG.md`
     - `pyproject.toml`
     - `src/gflow_cli/__init__.py`
     - `uv.lock`
4. **Update or add documentation files as needed:**
   - Files to review:
     - `README.md`
     - `AGENTS.md`
     - All files in `docs/`
5. **Commit the changes with a clear message:**
   ```bash
   git add .
   git commit -m "chore: back-merge release changes into develop"
   git push
   ```

## Testing Patterns

- **Framework:** Not explicitly detected; likely custom or lightweight.
- **Test File Pattern:** Test files are named using `*.test.*`.
  - Example: `foo.test.py`, `bar.test_utils.py`
- **Typical Test Structure:**
  - Place test files alongside or near the modules they test.
  - Example test function:
    ```python
    def test_parse_config():
        config = parse_config("test.yaml")
        assert config["key"] == "value"
    ```

## Commands

| Command             | Purpose                                                        |
|---------------------|----------------------------------------------------------------|
| /back-merge-release | Synchronize changes from main (after a release) into develop.  |
```
