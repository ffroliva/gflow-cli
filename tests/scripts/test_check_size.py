"""Tests for the size ratchet gate (#1249)."""

from __future__ import annotations

from pathlib import Path

from scripts.ci import check_size as cs


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / "src" / "gflow_cli" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _cls(name: str, methods: int, extra_lines: int = 0) -> str:
    body = "".join(f"    def m{i}(self): ...\n" for i in range(methods))
    pad = "".join(f"    x{i} = 0\n" for i in range(extra_lines))  # comments don't extend a span
    return f"class {name}:\n{body}{pad}"


def _complex_func(name: str, branches: int) -> str:
    ifs = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(branches))
    return f"def {name}(x):\n{ifs}    return -1\n"


def _long_func(name: str, statements: int) -> str:
    return f"def {name}():\n" + "".join(f"    a{i} = {i}\n" for i in range(statements))


def _scan(root: Path) -> dict[str, cs.Measure]:
    return cs.measure_all(root / "src" / "gflow_cli", root)


def test_small_tree_is_clean(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", _cls("A", 3) + _complex_func("f", 3))
    assert cs.violations(_scan(tmp_path), {}) == []


def test_new_oversize_class_and_file_fail(tmp_path: Path) -> None:
    _write(tmp_path, "big.py", _cls("Big", 5, extra_lines=cs.LIMIT_FILE + 10))
    bad = cs.violations(_scan(tmp_path), {})
    assert any(v.startswith("file:src/gflow_cli/big.py:") for v in bad)
    assert any(v.startswith("class-lines:src/gflow_cli/big.py::Big:") for v in bad)


def test_new_too_complex_function_fails_with_ruff_c901(tmp_path: Path) -> None:
    _write(tmp_path, "cx.py", _complex_func("tangled", 12))
    bad = cs.violations(_scan(tmp_path), {})
    assert bad == ["C901:src/gflow_cli/cx.py::tangled: 13 > 10 (new; not in the baseline)"]


def test_new_too_long_method_fails_with_ruff_plr0915(tmp_path: Path) -> None:
    body = _long_func("run", 55).replace("\n    ", "\n        ").replace("def run", "    def run")
    _write(tmp_path, "ln.py", "class Job:\n" + body)
    bad = cs.violations(_scan(tmp_path), {})
    assert bad == ["PLR0915:src/gflow_cli/ln.py::Job.run: 55 > 50 (new; not in the baseline)"]


def test_too_many_public_methods_fail_with_ruff_plr0904_but_private_ones_do_not(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "pub.py", _cls("Pub", 21))
    _write(tmp_path, "priv.py", _cls("Priv", 25).replace("def m", "def _m"))
    bad = cs.violations(_scan(tmp_path), {})
    assert bad == ["PLR0904:src/gflow_cli/pub.py::Pub: 21 > 20 (new; not in the baseline)"]


def test_baselined_offender_may_shrink_or_stay_but_not_grow(tmp_path: Path) -> None:
    _write(tmp_path, "old.py", _complex_func("legacy", 14))
    key = "C901:src/gflow_cli/old.py::legacy"
    current = _scan(tmp_path)
    size = current[key].value
    assert cs.violations(current, {key: size}) == []
    assert cs.violations(current, {key: size + 3}) == []
    assert cs.violations(current, {key: size - 1}) == [
        f"{key}: grew {size - 1} -> {size} (limit 10; baseline may only shrink)"
    ]


def test_shrinking_an_offender_under_the_limit_passes_and_drops_it(tmp_path: Path) -> None:
    p = _write(tmp_path, "fix.py", _complex_func("legacy", 14))
    key = "C901:src/gflow_cli/fix.py::legacy"
    baseline = {key: _scan(tmp_path)[key].value}
    p.write_text(_complex_func("legacy", 4), encoding="utf-8")
    current = _scan(tmp_path)
    assert cs.violations(current, baseline) == []
    assert cs.ratcheted(current, baseline) == {}


def test_ratchet_lowers_and_drops_but_never_adds() -> None:
    lim = cs.LIMIT_FILE
    current = {
        "file:a.py": cs.Measure(lim + 50, lim),
        "file:b.py": cs.Measure(lim - 100, lim),
        "file:new.py": cs.Measure(lim + 900, lim),
    }
    baseline = {"file:a.py": lim + 200, "file:b.py": lim + 100}
    # a shrank (lowered); b fell under the limit (dropped); new.py is not added
    assert cs.ratcheted(current, baseline) == {"file:a.py": lim + 50}


def test_update_baseline_refuses_while_violations_exist(tmp_path: Path) -> None:
    _write(tmp_path, "cx.py", _complex_func("tangled", 12))
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text("{}", encoding="utf-8")
    code = cs.update_baseline(tmp_path / "src" / "gflow_cli", tmp_path, baseline_file)
    assert code == 1
    assert baseline_file.read_text(encoding="utf-8") == "{}"


def test_a_noqa_comment_does_not_hide_an_offender(tmp_path: Path) -> None:
    func = _complex_func("hidden", 12).replace("def hidden(x):", "def hidden(x):  # noqa: C901")
    _write(tmp_path, "nq.py", func)
    assert cs.violations(_scan(tmp_path), {}) == [
        "C901:src/gflow_cli/nq.py::hidden: 13 > 10 (new; not in the baseline)"
    ]


def test_a_file_that_does_not_parse_is_skipped_not_a_crash(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def f(:\n")
    assert cs.violations(_scan(tmp_path), {}) == []


def test_a_file_of_exactly_the_limit_passes(tmp_path: Path) -> None:
    _write(tmp_path, "edge.py", "x = 0\n" * cs.LIMIT_FILE)
    assert _scan(tmp_path)["file:src/gflow_cli/edge.py"].value == cs.LIMIT_FILE
    assert cs.violations(_scan(tmp_path), {}) == []


def test_nested_classes_with_the_same_name_get_separate_keys(tmp_path: Path) -> None:
    inner = _cls("Meta", 0, extra_lines=3).replace("\n", "\n    ")
    _write(tmp_path, "nest.py", f"class A:\n    {inner}\nclass B:\n    {inner}\n")
    keys = {k for k in _scan(tmp_path) if k.startswith("class-lines")}
    assert {"class-lines:src/gflow_cli/nest.py::A.Meta"} <= keys
    assert {"class-lines:src/gflow_cli/nest.py::B.Meta"} <= keys


def test_a_moved_offender_carries_its_baseline_entry(tmp_path: Path) -> None:
    _write(tmp_path, "new_home.py", _complex_func("legacy", 12))
    baseline = {"C901:src/gflow_cli/old_home.py::legacy": 13}
    current = _scan(tmp_path)
    assert cs.violations(current, baseline) == []
    assert cs.ratcheted(current, baseline) == {"C901:src/gflow_cli/new_home.py::legacy": 13}


def test_a_moved_offender_that_grew_fails(tmp_path: Path) -> None:
    _write(tmp_path, "new_home.py", _complex_func("legacy", 14))
    baseline = {"C901:src/gflow_cli/old_home.py::legacy": 13}
    assert cs.violations(_scan(tmp_path), baseline) == [
        "C901:src/gflow_cli/new_home.py::legacy: 15 > 10 (new; not in the baseline)"
    ]


def test_base_ref_check_rejects_a_hand_raised_or_added_entry_but_allows_a_move() -> None:
    base = {"C901:a.py::f": 12, "C901:a.py::g": 14}
    assert cs.escalations({"C901:a.py::f": 13, "C901:a.py::g": 14}, base) == [
        "C901:a.py::f: raised 12 -> 13 by hand"
    ]
    assert cs.escalations({**base, "C901:a.py::h": 11}, base) == ["C901:a.py::h: added by hand"]
    moved = {"C901:a.py::f": 12, "C901:b.py::g": 14}
    assert cs.escalations(moved, base) == []
    assert cs.escalations({"C901:a.py::f": 12, "C901:b.py::g": 15}, base) == [
        "C901:b.py::g: added by hand"
    ]
