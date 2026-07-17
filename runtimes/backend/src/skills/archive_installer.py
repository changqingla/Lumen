"""安全验证并安装 `.skill` ZIP 归档。"""

import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

# `SKILL.md` 头部元数据允许出现的字段。
ALLOWED_FRONTMATTER_PROPERTIES = {"name", "description", "license", "allowed-tools", "metadata"}

# `.skill` 包的解压边界。总大小沿用嵌入式客户端原有的 100 MiB 限制；
# 单文件和成员数限制可防止大量小文件或单个资源耗尽磁盘与 inode。
SKILL_ARCHIVE_MAX_MEMBERS = 2048
SKILL_ARCHIVE_MAX_MEMBER_SIZE_BYTES = 50 * 1024 * 1024
SKILL_ARCHIVE_MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
SKILL_ARCHIVE_MAX_COMPRESSION_RATIO = 200
SKILL_ARCHIVE_RATIO_CHECK_MIN_BYTES = 1024 * 1024
SKILL_ARCHIVE_READ_CHUNK_SIZE_BYTES = 64 * 1024


class InvalidSkillArchiveError(ValueError):
    """表示 `.skill` ZIP 的结构或内容不满足安装边界。"""


class SkillAlreadyExistsError(FileExistsError):
    """表示自定义技能目标目录已经存在。"""

    def __init__(self, skill_name: str):
        self.skill_name = skill_name
        super().__init__(f"Skill '{skill_name}' already exists")


@dataclass(frozen=True)
class InstalledSkill:
    """一次成功安装的结果。"""

    name: str
    path: Path


@dataclass(frozen=True)
class _ArchiveMember:
    """已完成路径与文件类型校验的 ZIP 成员。"""

    info: zipfile.ZipInfo
    path_parts: tuple[str, ...]
    is_directory: bool


def _archive_member_path(info: zipfile.ZipInfo) -> tuple[str, ...]:
    """返回安全的 POSIX 相对路径段，并拒绝平台路径语义混淆。"""
    name = info.orig_filename
    if not name or "\x00" in name or name != info.filename:
        raise InvalidSkillArchiveError("Archive member has an empty or NUL-containing path")
    if "\\" in name:
        raise InvalidSkillArchiveError(f"Archive member uses a backslash in its path: {name}")

    windows_path = PureWindowsPath(name)
    if PurePosixPath(name).is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise InvalidSkillArchiveError(f"Archive member has an absolute path: {name}")

    canonical_name = name[:-1] if info.is_dir() else name
    parts = canonical_name.split("/")
    if not canonical_name or any(part in {"", ".", ".."} for part in parts):
        raise InvalidSkillArchiveError(f"Archive member has an unsafe path: {name}")
    return tuple(parts)


def _archive_member_type(info: zipfile.ZipInfo) -> bool:
    """校验成员类型，返回其是否为目录。"""
    is_directory = info.is_dir()
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)

    if file_type == stat.S_IFLNK:
        raise InvalidSkillArchiveError(f"Archive member is a symbolic link: {info.filename}")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise InvalidSkillArchiveError(f"Archive member is a special file: {info.filename}")
    if is_directory and file_type == stat.S_IFREG:
        raise InvalidSkillArchiveError(f"Archive member has conflicting file type metadata: {info.filename}")
    if not is_directory and file_type == stat.S_IFDIR:
        raise InvalidSkillArchiveError(f"Archive member has conflicting directory metadata: {info.filename}")
    if is_directory and info.file_size != 0:
        raise InvalidSkillArchiveError(f"Archive directory member contains data: {info.filename}")
    return is_directory


def _compression_ratio_exceeded(uncompressed_size: int, compressed_size: int) -> bool:
    """仅对达到阈值的数据应用压缩比限制，避免误伤很小的文本文件。"""
    if uncompressed_size < SKILL_ARCHIVE_RATIO_CHECK_MIN_BYTES:
        return False
    if compressed_size <= 0:
        return uncompressed_size > 0
    return uncompressed_size > compressed_size * SKILL_ARCHIVE_MAX_COMPRESSION_RATIO


def _validate_archive_members(archive: zipfile.ZipFile) -> list[_ArchiveMember]:
    """在落盘前验证 ZIP 的全部成员、路径关系与声明的资源用量。"""
    infos = archive.infolist()
    if not infos:
        raise InvalidSkillArchiveError("Skill archive is empty")
    if len(infos) > SKILL_ARCHIVE_MAX_MEMBERS:
        raise InvalidSkillArchiveError(f"Skill archive contains too many members (maximum {SKILL_ARCHIVE_MAX_MEMBERS})")

    members: list[_ArchiveMember] = []
    registered_paths: dict[tuple[str, ...], bool] = {}
    total_size = 0
    total_compressed_size = 0

    for info in infos:
        if info.flag_bits & 0x1:
            raise InvalidSkillArchiveError(f"Encrypted archive members are not supported: {info.filename}")
        if info.file_size < 0 or info.compress_size < 0:
            raise InvalidSkillArchiveError(f"Archive member has invalid size metadata: {info.filename}")

        path_parts = _archive_member_path(info)
        is_directory = _archive_member_type(info)

        if path_parts in registered_paths:
            raise InvalidSkillArchiveError(f"Archive contains a duplicate member path: {info.filename}")
        for index in range(1, len(path_parts)):
            if registered_paths.get(path_parts[:index]) is False:
                raise InvalidSkillArchiveError(f"Archive member is nested below a file: {info.filename}")
        if not is_directory and any(
            len(existing_path) > len(path_parts) and existing_path[: len(path_parts)] == path_parts
            for existing_path in registered_paths
        ):
            raise InvalidSkillArchiveError(f"Archive file conflicts with an existing directory path: {info.filename}")
        registered_paths[path_parts] = is_directory

        if info.file_size > SKILL_ARCHIVE_MAX_MEMBER_SIZE_BYTES:
            raise InvalidSkillArchiveError(
                f"Archive member exceeds the {SKILL_ARCHIVE_MAX_MEMBER_SIZE_BYTES}-byte size limit: {info.filename}"
            )
        if _compression_ratio_exceeded(info.file_size, info.compress_size):
            raise InvalidSkillArchiveError(f"Archive member exceeds the compression ratio limit: {info.filename}")

        total_size += info.file_size
        total_compressed_size += info.compress_size
        if total_size > SKILL_ARCHIVE_MAX_TOTAL_SIZE_BYTES:
            raise InvalidSkillArchiveError(f"Skill archive exceeds the {SKILL_ARCHIVE_MAX_TOTAL_SIZE_BYTES}-byte extracted size limit")

        members.append(_ArchiveMember(info=info, path_parts=path_parts, is_directory=is_directory))

    if _compression_ratio_exceeded(total_size, total_compressed_size):
        raise InvalidSkillArchiveError("Skill archive exceeds the aggregate compression ratio limit")
    return members


def _extract_archive_member(
    archive: zipfile.ZipFile,
    member: _ArchiveMember,
    destination_root: Path,
    extracted_size: int,
) -> int:
    """以固定大小块解压单个普通文件，并返回更新后的总写入字节数。"""
    destination = destination_root.joinpath(*member.path_parts)
    if member.is_directory:
        # ZIP 中的显式目录项可能排在其子项之后；全量预检已排除文件冲突。
        destination.mkdir(parents=True, exist_ok=True)
        return extracted_size

    destination.parent.mkdir(parents=True, exist_ok=True)
    member_size = 0
    with archive.open(member.info, "r") as source, destination.open("xb") as output:
        while chunk := source.read(SKILL_ARCHIVE_READ_CHUNK_SIZE_BYTES):
            member_size += len(chunk)
            extracted_size += len(chunk)
            if member_size > member.info.file_size or member_size > SKILL_ARCHIVE_MAX_MEMBER_SIZE_BYTES:
                raise InvalidSkillArchiveError(f"Archive member produced more data than declared: {member.info.filename}")
            if extracted_size > SKILL_ARCHIVE_MAX_TOTAL_SIZE_BYTES:
                raise InvalidSkillArchiveError(
                    f"Skill archive exceeds the {SKILL_ARCHIVE_MAX_TOTAL_SIZE_BYTES}-byte extracted size limit"
                )
            output.write(chunk)

    if member_size != member.info.file_size:
        raise InvalidSkillArchiveError(f"Archive member size does not match its metadata: {member.info.filename}")
    return extracted_size


def _safe_extract_skill_archive(archive_path: Path, destination_root: Path) -> None:
    """验证并流式解压 `.skill` 包，不使用 `extractall()`。"""
    destination_root.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = _validate_archive_members(archive)
            extracted_size = 0
            for member in members:
                extracted_size = _extract_archive_member(archive, member, destination_root, extracted_size)
    except InvalidSkillArchiveError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, NotImplementedError, RuntimeError) as exc:
        raise InvalidSkillArchiveError(f"Invalid or unsupported ZIP archive: {exc}") from exc


def _validate_skill_frontmatter(skill_dir: Path) -> tuple[bool, str, str | None]:
    """校验技能目录中的 `SKILL.md` 头部元数据。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return False, "未找到 SKILL.md", None

    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"无法以 UTF-8 读取 SKILL.md：{exc}", None
    if not content.startswith("---"):
        return False, "未找到 YAML 头部元数据", None

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "头部元数据格式无效", None

    try:
        frontmatter = yaml.safe_load(match.group(1))
        if not isinstance(frontmatter, dict):
            return False, "头部元数据必须是 YAML 字典", None
    except yaml.YAMLError as exc:
        return False, f"头部元数据中的 YAML 无效：{exc}", None

    unexpected_keys = set(frontmatter) - ALLOWED_FRONTMATTER_PROPERTIES
    if unexpected_keys:
        return False, f"SKILL.md 头部元数据中存在不允许的字段：{', '.join(sorted(unexpected_keys))}", None
    if "name" not in frontmatter:
        return False, "头部元数据缺少 'name'", None
    if "description" not in frontmatter:
        return False, "头部元数据缺少 'description'", None

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"name 必须是字符串，当前为 {type(name).__name__}", None
    name = name.strip()
    if not name:
        return False, "name 不能为空", None
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return False, f"name '{name}' 必须使用短横线命名，只能包含小写字母、数字和连字符", None
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"name '{name}' 不能以连字符开头/结尾，也不能包含连续连字符", None
    if len(name) > 64:
        return False, f"name 过长（{len(name)} 个字符），最大长度为 64", None

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"description 必须是字符串，当前为 {type(description).__name__}", None
    description = description.strip()
    if description:
        if "<" in description or ">" in description:
            return False, "description 不能包含尖括号（< 或 >）", None
        if len(description) > 1024:
            return False, f"description 过长（{len(description)} 个字符），最大长度为 1024", None

    return True, "技能校验通过", name


def _skill_name_exists(skills_root: Path, skill_name: str) -> bool:
    """Return whether a visible public or custom skill already owns the name."""
    for category in ("public", "custom"):
        category_root = skills_root / category
        if not category_root.is_dir():
            continue
        for skill_file in category_root.rglob("SKILL.md"):
            relative_parts = skill_file.relative_to(category_root).parts
            if any(part.startswith(".") for part in relative_parts):
                continue
            is_valid, _message, existing_name = _validate_skill_frontmatter(
                skill_file.parent
            )
            if is_valid and existing_name == skill_name:
                return True
    return False


def install_skill_archive(archive_path: Path, skills_root: Path) -> InstalledSkill:
    """安全安装 `.skill` 归档，并在全部校验通过后原子发布。"""
    if not zipfile.is_zipfile(archive_path):
        raise InvalidSkillArchiveError("File is not a valid ZIP archive")

    custom_skills_dir = skills_root / "custom"
    custom_skills_dir.mkdir(parents=True, exist_ok=True)

    # 暂存目录与目标目录位于同一文件系统，确保最终 rename 是原子操作。
    with tempfile.TemporaryDirectory(prefix=".skill-install-", dir=custom_skills_dir) as temp_dir:
        extracted_root = Path(temp_dir) / "extracted"
        _safe_extract_skill_archive(archive_path, extracted_root)

        extracted_items = list(extracted_root.iterdir())
        if not extracted_items:
            raise InvalidSkillArchiveError("Skill archive is empty")
        skill_dir = extracted_items[0] if len(extracted_items) == 1 and extracted_items[0].is_dir() else extracted_root

        is_valid, message, skill_name = _validate_skill_frontmatter(skill_dir)
        if not is_valid:
            raise InvalidSkillArchiveError(f"Invalid skill: {message}")
        if not skill_name:
            raise InvalidSkillArchiveError("Could not determine skill name")

        target_dir = custom_skills_dir / skill_name
        if (
            _skill_name_exists(skills_root, skill_name)
            or target_dir.exists()
            or target_dir.is_symlink()
        ):
            raise SkillAlreadyExistsError(skill_name)

        try:
            skill_dir.rename(target_dir)
        except OSError as exc:
            if target_dir.exists() or target_dir.is_symlink():
                raise SkillAlreadyExistsError(skill_name) from exc
            raise

    return InstalledSkill(name=skill_name, path=target_dir)
