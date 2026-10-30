"""Copy the package into another repository with a hash that catches accidental edits."""

import hashlib
import shutil
import subprocess
from pathlib import Path


class DirtyTreeError(RuntimeError):
    pass


def tree_hash(package_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*.py")):
        digest.update(path.relative_to(package_dir).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return done.stdout.strip()


def default_repo() -> Path:
    return Path(_git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel"))


def vendor(dest: Path, repo: Path | None = None) -> None:
    repo = repo or default_repo()
    if _git(repo, "status", "--porcelain"):
        raise DirtyTreeError(f"{repo}: the work tree is dirty; commit or stash before vendoring")
    commit = _git(repo, "rev-parse", "HEAD")
    dest.mkdir(parents=True, exist_ok=True)
    sources = sorted((repo / "src" / "pytest_speed").glob("*.py"))
    if (dest / "VERSION").exists():  # a re-vendor: drop modules the source no longer has
        for stale in {p.name for p in dest.glob("*.py")} - {s.name for s in sources}:
            (dest / stale).unlink()
    for source in sources:
        shutil.copyfile(source, dest / source.name)
    version = f"commit={commit}\nsha256={tree_hash(dest)}\n"
    (dest / "VERSION").write_bytes(version.encode("utf-8"))
