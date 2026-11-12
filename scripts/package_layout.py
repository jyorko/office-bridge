"""Stage only authored package files; keep runtime data outside action snapshots."""

import shutil
from pathlib import Path

from _bootstrap import ROOT

SOURCE_SUFFIXES = {".py", ".ps1", ".js", ".cs"}


def package_files(root: Path = ROOT) -> dict[Path, Path]:
    files = {Path("package.yaml"): root / "package.yaml"}
    source_root = root / "src"
    for source in source_root.rglob("*"):
        relative = source.relative_to(source_root)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if source.is_file() and source.suffix in SOURCE_SUFFIXES:
            if not source.resolve().is_relative_to(source_root.resolve()):
                raise RuntimeError("Package source resolves outside src.")
            files[source.relative_to(root)] = source
    return files


def stage_package(root: Path = ROOT) -> int:
    root = root.resolve()
    staged = root / ".package"
    if staged.is_symlink() or (staged.exists() and staged.resolve() != staged):
        raise RuntimeError("Refusing to stage through a redirected package directory.")
    staged.mkdir(exist_ok=True)
    files = package_files(root)
    for target in staged.rglob("*"):
        if target.is_file() and target.relative_to(staged) not in files:
            if not target.resolve().is_relative_to(staged):
                raise RuntimeError("Staged file resolves outside the package directory.")
            target.unlink()
    for directory in sorted(
        (path for path in staged.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not directory.resolve().is_relative_to(staged):
            raise RuntimeError("Staged directory resolves outside the package directory.")
        if not any(directory.iterdir()):
            directory.rmdir()
    for relative, source in files.items():
        target = staged / relative
        if target.exists() and target.read_bytes() == source.read_bytes():
            continue
        if not target.resolve().is_relative_to(staged):
            raise RuntimeError("Staged destination resolves outside the package directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return len(files)


def watch_sources():
    from watchfiles import watch

    for _ in watch(ROOT / "src", ROOT / "package.yaml"):
        stage_package()


if __name__ == "__main__":
    print(f"Staged {stage_package()} package files.")
