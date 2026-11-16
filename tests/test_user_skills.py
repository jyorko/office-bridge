"""Verify user skill updates preserve independent skills and user modifications."""

import pytest

from office_bridge.runtime.skills import NAMES, install


def fixture_repo(tmp_path):
    repo = tmp_path / "repo"
    for name in NAMES:
        path = repo / ".agents" / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("original", encoding="utf-8")
    return repo


def test_install_update_and_unrelated_files(tmp_path):
    repo = fixture_repo(tmp_path)
    destination = tmp_path / "user" / "skills"
    unrelated = destination / "another-skill" / "SKILL.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")
    install(repo, destination)
    install(repo, destination)
    (repo / ".agents" / "skills" / NAMES[0] / "SKILL.md").write_text("updated")
    install(repo, destination)
    assert (destination / NAMES[0] / "SKILL.md").read_text() == "updated"
    assert unrelated.read_text() == "keep"
    assert (
        str(repo.resolve())
        in (destination / NAMES[0] / "references" / "installation.md").read_text()
    )


@pytest.mark.parametrize("managed", [False, True])
def test_preserve_unmanaged_or_edited_skill(tmp_path, managed):
    repo = fixture_repo(tmp_path)
    destination = tmp_path / "user" / "skills"
    if managed:
        install(repo, destination)
    path = destination / NAMES[0] / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("my edits")
    with pytest.raises(ValueError):
        install(repo, destination)
    assert path.read_text() == "my edits"
