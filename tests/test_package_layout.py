from package_layout import stage_package


def test_stage_contains_only_runtime_source_not_private_data(tmp_path):
    (tmp_path / "package.yaml").write_text("spec-version: v2\n", encoding="utf-8")
    for name in (
        "src/example_actions.py",
        "src/office_bridge/message_view.html",
        "src/office_bridge/native/example.ps1",
        ".state/key",
        ".runtime/library.py",
        "scripts/helper.py",
        "tests/test_example.py",
        "docs/note.md",
        "src/__pycache__/example.pyc",
        "src/.state/private.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    assert stage_package(tmp_path) == 4
    staged = tmp_path / ".package"
    assert {p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()} == {
        "package.yaml",
        "src/example_actions.py",
        "src/office_bridge/message_view.html",
        "src/office_bridge/native/example.ps1",
    }
    assert (tmp_path / ".state/key").read_text() == "fixture"


def test_stage_removes_retired_sources_and_does_not_recopy_unchanged_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "package.yaml").write_text("spec-version: v2\n", encoding="utf-8")
    source = tmp_path / "src/example_actions.py"
    source.write_text("value = 1\n", encoding="utf-8")
    stage_package(tmp_path)
    target = tmp_path / ".package/src/example_actions.py"
    timestamp = target.stat().st_mtime_ns
    stage_package(tmp_path)
    assert target.stat().st_mtime_ns == timestamp
    source.unlink()
    stage_package(tmp_path)
    assert not target.exists()


def test_html_only_edit_signals_runtime_reload(tmp_path):
    import os

    (tmp_path / "package.yaml").write_text("spec-version: v2\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    html = tmp_path / "src/view.html"
    html.write_text("<main>First</main>", encoding="utf-8")
    stage_package(tmp_path)
    manifest = tmp_path / ".package/package.yaml"
    os.utime(manifest, (100, 100))
    stage_package(tmp_path)
    assert manifest.stat().st_mtime == 100
    html.write_text("<main>Updated</main>", encoding="utf-8")
    stage_package(tmp_path)
    assert manifest.stat().st_mtime > 100
    assert (tmp_path / ".package/src/view.html").read_bytes() == html.read_bytes()
