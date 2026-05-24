```markdown
# gflow-cli Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches the core development patterns and conventions used in the `gflow-cli` Python repository. You'll learn about file naming, import/export styles, commit message conventions, and how to write and organize tests. This guide is ideal for contributors looking to maintain consistency and quality in the codebase.

## Coding Conventions

### File Naming
- Use **snake_case** for all Python files.
  - **Example:**  
    ```python
    # Correct
    my_module.py

    # Incorrect
    MyModule.py
    myModule.py
    ```

### Import Style
- Use **relative imports** within the package.
  - **Example:**  
    ```python
    # In foo/bar.py
    from . import utils
    from ..core import base
    ```

### Export Style
- Use **named exports** by explicitly listing exported symbols in `__all__`.
  - **Example:**  
    ```python
    __all__ = ["main_function", "HelperClass"]
    ```

### Commit Messages
- Follow the **conventional commit** format.
- Use the `feat` prefix for new features.
- Keep commit messages concise (average ~79 characters).
  - **Example:**  
    ```
    feat: add support for custom workflow configuration
    ```

## Workflows

### Adding a New Feature
**Trigger:** When you want to introduce new functionality  
**Command:** `/add-feature`

1. Create a new Python file using snake_case if needed.
2. Implement the feature using relative imports for internal modules.
3. Add named exports to `__all__` if applicable.
4. Write or update tests in a corresponding `*.test.*` file.
5. Commit your changes using the conventional commit format with `feat` prefix.
   - Example: `feat: implement advanced filtering for workflows`
6. Open a pull request for review.

### Running Tests
**Trigger:** When you need to verify code correctness  
**Command:** `/run-tests`

1. Identify test files matching the `*.test.*` pattern.
2. Run the test suite using your preferred Python test runner (e.g., pytest, unittest).
   - Example:  
     ```bash
     pytest
     ```
3. Review test results and fix any failures before merging.

## Testing Patterns

- Test files follow the `*.test.*` naming pattern (e.g., `foo.test.py`).
- The specific testing framework is not enforced, but common Python test runners like `pytest` or `unittest` are recommended.
- Place tests alongside or near the modules they test for clarity.

**Example test file:**
```python
# foo.test.py

from .foo import my_function

def test_my_function():
    assert my_function(2) == 4
```

## Commands
| Command       | Purpose                                      |
|---------------|----------------------------------------------|
| /add-feature  | Start the workflow for adding a new feature  |
| /run-tests    | Run all tests in the repository              |
```
