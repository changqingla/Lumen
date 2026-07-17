import asyncio
import stat
import warnings
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.gateway.routers import skills
from src.skills import archive_installer
from src.utils.thread_files import ResolvedThreadFile

SKILL_MD = b"---\nname: test-skill\ndescription: Test skill\n---\n\nInstructions\n"


def _write_archive(archive_path: Path, members: list[tuple[str | zipfile.ZipInfo, bytes]], *, compression: int = zipfile.ZIP_STORED) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=compression) as archive:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for name, content in members:
                archive.writestr(name, content)


def _install(archive_path: Path, skills_root: Path) -> skills.SkillInstallResponse:
    request = skills.SkillInstallRequest(thread_id="thread-1", path="/mnt/user-data/outputs/test.skill")
    resolved = ResolvedThreadFile(
        root=archive_path.parent.resolve(),
        parts=(archive_path.name,),
    )
    with (
        patch.object(skills, "get_paths", return_value=object()),
        patch.object(skills, "resolve_thread_file", return_value=resolved),
        patch.object(skills, "get_skills_root_path", return_value=skills_root),
    ):
        return asyncio.run(skills.install_skill(request))


def _assert_rejected(archive_path: Path, skills_root: Path, *, status_code: int = 400, detail: str | None = None) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        _install(archive_path, skills_root)

    exc = exc_info.value
    assert exc.status_code == status_code
    if detail is not None:
        assert detail.lower() in str(exc.detail).lower()

    custom_dir = skills_root / "custom"
    if custom_dir.exists():
        assert not list(custom_dir.glob(".skill-install-*"))
    return exc


@pytest.mark.parametrize("wrapped", [True, False])
def test_install_skill_supports_wrapped_and_root_archives_without_extractall(tmp_path, wrapped):
    archive_path = tmp_path / "test.skill"
    prefix = "test-skill/" if wrapped else ""
    _write_archive(
        archive_path,
        [
            (f"{prefix}SKILL.md", SKILL_MD),
            (f"{prefix}references/guide.txt", b"guide"),
            # An explicit directory entry may legally appear after a child.
            (f"{prefix}references/", b""),
        ],
    )
    skills_root = tmp_path / "skills"

    with patch.object(zipfile.ZipFile, "extractall", side_effect=AssertionError("extractall must not be used")):
        response = _install(archive_path, skills_root)

    target = skills_root / "custom" / "test-skill"
    assert response.success is True
    assert response.skill_name == "test-skill"
    assert (target / "SKILL.md").read_bytes() == SKILL_MD
    assert (target / "references" / "guide.txt").read_bytes() == b"guide"
    assert not list((skills_root / "custom").glob(".skill-install-*"))


@pytest.mark.parametrize(
    "unsafe_name, expected_detail",
    [
        ("../outside.txt", "unsafe path"),
        ("test-skill/../../outside.txt", "unsafe path"),
        ("/tmp/outside.txt", "absolute path"),
        ("C:/outside.txt", "absolute path"),
        (r"test-skill\..\outside.txt", "backslash"),
        ("test-skill/./outside.txt", "unsafe path"),
        ("test-skill//outside.txt", "unsafe path"),
    ],
)
def test_install_skill_rejects_unsafe_member_paths(tmp_path, unsafe_name, expected_detail):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), (unsafe_name, b"outside")])

    _assert_rejected(archive_path, tmp_path / "skills", detail=expected_detail)
    assert not (tmp_path / "outside.txt").exists()


def _typed_member(name: str, file_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (file_type | 0o644) << 16
    return info


@pytest.mark.parametrize(
    "file_type, expected_detail",
    [
        (stat.S_IFLNK, "symbolic link"),
        (stat.S_IFIFO, "special file"),
        (stat.S_IFCHR, "special file"),
        (stat.S_IFBLK, "special file"),
        (stat.S_IFSOCK, "special file"),
    ],
)
def test_install_skill_rejects_symlinks_and_special_files(tmp_path, file_type, expected_detail):
    archive_path = tmp_path / "test.skill"
    special = _typed_member("test-skill/resources/unsafe", file_type)
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), (special, b"target")])

    _assert_rejected(archive_path, tmp_path / "skills", detail=expected_detail)


@pytest.mark.parametrize(
    "conflicting_members, expected_detail",
    [
        (
            [("test-skill/assets/data.txt", b"one"), ("test-skill/assets/data.txt", b"two")],
            "duplicate member path",
        ),
        (
            [("test-skill/assets", b"file"), ("test-skill/assets/data.txt", b"child")],
            "nested below a file",
        ),
        (
            [("test-skill/assets/data.txt", b"child"), ("test-skill/assets", b"file")],
            "conflicts with an existing directory path",
        ),
        (
            [("test-skill/assets/", b""), ("test-skill/assets", b"file")],
            "duplicate member path",
        ),
    ],
)
def test_install_skill_rejects_duplicate_and_conflicting_members(tmp_path, conflicting_members, expected_detail):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), *conflicting_members])

    _assert_rejected(archive_path, tmp_path / "skills", detail=expected_detail)


def test_install_skill_enforces_member_count_limit(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), ("test-skill/asset.txt", b"asset")])

    with patch.object(archive_installer, "SKILL_ARCHIVE_MAX_MEMBERS", 1):
        _assert_rejected(archive_path, tmp_path / "skills", detail="too many members")


def test_install_skill_enforces_single_member_size_limit(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(
        archive_path,
        [("test-skill/SKILL.md", SKILL_MD), ("test-skill/asset.bin", b"x" * (len(SKILL_MD) + 1))],
    )

    with patch.object(archive_installer, "SKILL_ARCHIVE_MAX_MEMBER_SIZE_BYTES", len(SKILL_MD)):
        _assert_rejected(archive_path, tmp_path / "skills", detail="member exceeds")


def test_install_skill_enforces_total_extracted_size_limit(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), ("test-skill/asset.bin", b"abcd")])

    with patch.object(archive_installer, "SKILL_ARCHIVE_MAX_TOTAL_SIZE_BYTES", len(SKILL_MD) + 3):
        _assert_rejected(archive_path, tmp_path / "skills", detail="extracted size limit")


def test_install_skill_rejects_excessive_compression_ratio(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(
        archive_path,
        [("test-skill/SKILL.md", SKILL_MD), ("test-skill/highly-compressible.bin", b"0" * (2 * 1024 * 1024))],
        compression=zipfile.ZIP_DEFLATED,
    )

    with (
        patch.object(archive_installer, "SKILL_ARCHIVE_MAX_COMPRESSION_RATIO", 10),
        patch.object(archive_installer, "SKILL_ARCHIVE_RATIO_CHECK_MIN_BYTES", 1024),
    ):
        _assert_rejected(archive_path, tmp_path / "skills", detail="compression ratio limit")


class _OversizedMemberStream(BytesIO):
    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= archive_installer.SKILL_ARCHIVE_READ_CHUNK_SIZE_BYTES
        return super().read(size)


class _FakeArchive:
    def __init__(self, content: bytes):
        self.content = content

    def open(self, _info, _mode):
        return _OversizedMemberStream(self.content)


def test_stream_extraction_enforces_actual_member_size(tmp_path):
    info = zipfile.ZipInfo("asset.bin")
    info.file_size = 3
    member = archive_installer._ArchiveMember(info=info, path_parts=("asset.bin",), is_directory=False)

    with pytest.raises(archive_installer.InvalidSkillArchiveError, match="more data than declared"):
        archive_installer._extract_archive_member(_FakeArchive(b"four"), member, tmp_path, 0)


def test_install_skill_cleans_staging_directory_after_extraction_failure(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), ("test-skill/asset.txt", b"asset")])
    original_extract = archive_installer._extract_archive_member
    calls = 0

    def fail_after_first_write(archive, member, destination_root, extracted_size):
        nonlocal calls
        calls += 1
        result = original_extract(archive, member, destination_root, extracted_size)
        if calls == 1:
            raise OSError("simulated disk failure")
        return result

    with patch.object(archive_installer, "_extract_archive_member", side_effect=fail_after_first_write):
        _assert_rejected(archive_path, tmp_path / "skills", status_code=500, detail="安装技能失败")

    assert list((tmp_path / "skills" / "custom").iterdir()) == []


def test_install_skill_preserves_existing_target_and_returns_conflict(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD)])
    skills_root = tmp_path / "skills"
    existing_target = skills_root / "custom" / "test-skill"
    existing_target.mkdir(parents=True)
    marker = existing_target / "keep.txt"
    marker.write_text("original", encoding="utf-8")

    _assert_rejected(archive_path, skills_root, status_code=409, detail="already exists")

    assert marker.read_text(encoding="utf-8") == "original"
    assert not (existing_target / "SKILL.md").exists()


def test_install_skill_rejects_name_already_owned_by_public_skill(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD)])
    skills_root = tmp_path / "skills"
    public_target = skills_root / "public" / "nested" / "canonical"
    public_target.mkdir(parents=True)
    public_skill = public_target / "SKILL.md"
    public_skill.write_bytes(SKILL_MD)

    with pytest.raises(
        archive_installer.SkillAlreadyExistsError,
        match="already exists",
    ):
        archive_installer.install_skill_archive(archive_path, skills_root)

    assert public_skill.read_bytes() == SKILL_MD
    assert not (skills_root / "custom" / "test-skill").exists()


def test_install_skill_publishes_only_after_complete_staging(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), ("test-skill/asset.txt", b"complete")])
    skills_root = tmp_path / "skills"
    original_rename = Path.rename
    observed = False

    def checked_rename(source: Path, target: Path):
        nonlocal observed
        observed = True
        assert not target.exists()
        assert (source / "SKILL.md").read_bytes() == SKILL_MD
        assert (source / "asset.txt").read_bytes() == b"complete"
        return original_rename(source, target)

    with patch.object(Path, "rename", new=checked_rename):
        response = _install(archive_path, skills_root)

    assert observed is True
    assert response.success is True
    assert (skills_root / "custom" / "test-skill" / "asset.txt").read_bytes() == b"complete"


def test_install_skill_rolls_back_when_atomic_publish_fails(tmp_path):
    archive_path = tmp_path / "test.skill"
    _write_archive(archive_path, [("test-skill/SKILL.md", SKILL_MD), ("test-skill/asset.txt", b"complete")])
    skills_root = tmp_path / "skills"

    with patch.object(Path, "rename", side_effect=OSError("simulated publish failure")):
        _assert_rejected(archive_path, skills_root, status_code=500, detail="安装技能失败")

    assert list((skills_root / "custom").iterdir()) == []
