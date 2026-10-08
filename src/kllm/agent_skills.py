"""Portable skill discovery and recoverable, owned-copy installation.

This module deliberately uses only the standard library: the standalone
installer imports it from the checkout before kllm has been bootstrapped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

AGENTS = ("codex", "claude", "cursor", "opencode", "hermes", "openclaw", "pi")
CATEGORIES = ("operations", "development", "all")
SKILL_SETS = (*CATEGORIES, "none")
MANIFEST = ".kllm-skills.json"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class SkillError(ValueError):
    """An invalid library, installation conflict, or failed installation."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    category: str
    path: Path


@dataclass(frozen=True)
class Change:
    name: str
    source: Path | None
    before: dict[str, str] | None
    after: dict[str, str] | None
    backup: bool = False


@dataclass
class Installation:
    root: Path
    changes: list[Change]
    manifest: dict
    previous_manifest: bytes | None


def validate_selection(category: str, *, session: bool = False) -> None:
    choices = SKILL_SETS if session else CATEGORIES
    if category not in choices:
        raise SkillError(f"Unknown skill category {category!r}; choose {', '.join(choices)}")


def _scalar(frontmatter: str, key: str) -> str:
    matches = re.findall(rf"^{key}:\s*(.+)$", frontmatter, re.MULTILINE)
    if len(matches) != 1:
        raise SkillError(f"SKILL.md needs one single-line {key} field")
    value = matches[0].strip()
    if value.startswith('"'):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise SkillError(f"Invalid quoted {key}: {error.msg}") from error
    elif value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    elif value.startswith(("|", ">", "[", "{")):
        raise SkillError(f"Use a single-line string for {key}")
    if not isinstance(value, str) or not value.strip():
        raise SkillError(f"SKILL.md {key} must be a non-empty string")
    return value


def fingerprint(path: Path) -> dict[str, str]:
    """Include extra files and empty directories; never follow skill symlinks."""
    if path.is_symlink() or not path.is_dir():
        raise SkillError(f"Expected a real skill directory: {path}")
    result = {}
    for child in sorted(path.rglob("*")):
        relative = child.relative_to(path).as_posix()
        if child.is_symlink():
            raise SkillError(f"Skill symlinks are not copied or followed: {child}")
        if child.is_dir():
            result[relative + "/"] = "directory"
        elif child.is_file():
            result[relative] = hashlib.sha256(child.read_bytes()).hexdigest()
        else:
            raise SkillError(f"Unsupported skill resource: {child}")
    return result


def discover_skills(library: Path, category: str = "all") -> list[Skill]:
    validate_selection(category)
    skills = []
    names = set()
    for group in CATEGORIES[:2]:
        directory = library / group
        if not directory.is_dir() or directory.is_symlink():
            raise SkillError(f"Missing or unsafe skill category: {directory}")
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir() or entry.is_symlink():
                raise SkillError(f"Expected a skill folder: {entry}")
            try:
                text = (entry / "SKILL.md").read_text(encoding="utf-8")
                parts = text.split("---", 2)
                if len(parts) != 3 or parts[0].strip() or not parts[2].strip():
                    raise SkillError("SKILL.md needs frontmatter and instructions")
                name = _scalar(parts[1], "name")
                description = _scalar(parts[1], "description")
                if not NAME.fullmatch(name) or len(name) > 64 or name != entry.name:
                    raise SkillError(
                        "Skill name must match its kebab-case folder (maximum 64 characters)"
                    )
                if len(description) > 1024 or name in names:
                    raise SkillError(
                        "Duplicate skill name or description longer than 1024 characters"
                    )
                fingerprint(entry)
            except (OSError, UnicodeError, SkillError) as error:
                raise SkillError(f"Invalid skill {entry}: {error}") from error
            names.add(name)
            if category in ("all", group):
                skills.append(Skill(name, description, group, entry))
    if not skills:
        raise SkillError(f"No skills found for {category} in {library}")
    return skills


def agent_destinations(
    agents: Sequence[str],
    scope: str,
    project: Path,
    *,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[Path, list[str]]:
    env = os.environ if env is None else env
    home = Path.home() if user_home is None else user_home
    if scope not in ("project", "user"):
        raise SkillError(f"Unknown installation scope: {scope}")
    requested = list(AGENTS) if "all" in agents else list(agents)
    invalid = set(agents) - set(AGENTS) - {"all"}
    if invalid:
        raise SkillError(f"Unknown agents: {', '.join(sorted(invalid))}")
    project_paths = {
        "codex": ".agents/skills",
        "claude": ".claude/skills",
        "cursor": ".cursor/skills",
        "opencode": ".opencode/skills",
        "hermes": ".hermes/skills",
        "openclaw": ".agents/skills",
        "pi": ".pi/skills",
    }

    def configured(key: str, fallback: Path) -> Path:
        return Path(env[key]).expanduser() if env.get(key) else fallback

    user_paths = {
        "codex": configured("CODEX_HOME", home / ".agents") / "skills",
        "claude": configured("CLAUDE_CONFIG_DIR", home / ".claude") / "skills",
        "cursor": home / ".cursor/skills",
        "opencode": configured(
            "OPENCODE_CONFIG_DIR", configured("XDG_CONFIG_HOME", home / ".config") / "opencode"
        )
        / "skills",
        "hermes": configured(
            "HERMES_HOME", home / (".hermes" + env.get("HERMES_DATA_DIR_SUFFIX", ""))
        )
        / "skills",
        "openclaw": configured("OPENCLAW_STATE_DIR", home / ".openclaw") / "skills",
        "pi": configured("PI_CODING_AGENT_DIR", home / ".pi/agent") / "skills",
    }
    result: dict[Path, list[str]] = {}
    for agent in dict.fromkeys(requested):
        root = (
            project / project_paths[agent] if scope == "project" else user_paths[agent]
        ).resolve()
        result.setdefault(root, []).append(agent)
    return result


def _manifest(root: Path) -> tuple[dict, bytes | None]:
    path = root / MANIFEST
    if path.is_symlink():
        raise SkillError(f"Refusing symlinked ownership manifest: {path}")
    if not path.exists():
        return {"version": 1, "skills": {}}, None
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        if data.get("version") != 1 or not isinstance(data.get("skills"), dict):
            raise ValueError("unsupported manifest structure")
        for name, entry in data["skills"].items():
            if (
                not NAME.fullmatch(name)
                or not isinstance(entry, dict)
                or not isinstance(entry.get("files"), dict)
            ):
                raise ValueError("invalid ownership entry")
            for relative, digest in entry["files"].items():
                parts = relative.rstrip("/").split("/")
                if (
                    not relative
                    or any(p in ("", ".", "..") for p in parts)
                    or "\\" in relative
                    or ":" in relative
                ):
                    raise ValueError("unsafe resource path")
                if not isinstance(digest, str) or not (
                    digest == "directory" or re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    raise ValueError("invalid resource digest")
    except (ValueError, AttributeError, TypeError) as error:
        raise SkillError(f"Invalid ownership manifest {path}: {error}") from error
    return data, raw


def prepare_install(
    skills: Sequence[Skill], root: Path, *, force: bool = False, prune: bool = False
) -> Installation:
    root = root.resolve()
    if root.exists() and not root.is_dir():
        raise SkillError(f"Skill root is not a directory: {root}")
    manifest, previous = _manifest(root)
    changes = []
    selected = {skill.name for skill in skills}
    if len(selected) != len(skills):
        raise SkillError("Duplicate selected skills")
    for skill in skills:
        if not NAME.fullmatch(skill.name):
            raise SkillError(f"Unsafe skill name: {skill.name}")
        target = root / skill.name
        after = fingerprint(skill.path)
        entry = manifest["skills"].get(skill.name)
        before = None
        unsafe = target.is_symlink() or (target.exists() and not target.is_dir())
        if target.exists() and not unsafe:
            try:
                before = fingerprint(target)
            except SkillError:
                unsafe = True
        exists = target.exists() or target.is_symlink()
        conflict = exists and (
            unsafe or (before != after and (not entry or before != entry["files"]))
        )
        if conflict and not force:
            raise SkillError(
                f"Conflicting or locally edited skill: {target}; back it up or rerun with --force"
            )
        if before != after or unsafe:
            changes.append(Change(skill.name, skill.path, before, after, conflict))
        manifest["skills"][skill.name] = {"source": str(skill.path.resolve()), "files": after}
    if prune:
        for name in list(manifest["skills"]):
            if name in selected:
                continue
            target = root / name
            if target.exists() or target.is_symlink():
                before = fingerprint(target)
                if before != manifest["skills"][name]["files"]:
                    raise SkillError(
                        f"Locally edited session skill: {target}; restore or move it before changing --kllm-skills"
                    )
                changes.append(Change(name, None, before, None))
            del manifest["skills"][name]
    return Installation(root, changes, manifest, previous)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def apply_install(installation: Installation) -> list[str]:
    """Swap whole folders, publishing ownership last; rollback failed swaps."""
    root = installation.root
    encoded = (json.dumps(installation.manifest, indent=2, sort_keys=True) + "\n").encode()
    if not installation.changes and encoded == installation.previous_manifest:
        return []
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".kllm-skills.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise SkillError(
            f"Installation lock exists: {lock}; check for another installer or an interrupted install"
        ) from error
    work = None
    applied: list[tuple[Change, Path]] = []
    committed = False
    rollback_failed = False
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        if _manifest(root)[1] != installation.previous_manifest:
            raise SkillError(f"Ownership changed during installation: {root}; retry")
        work = Path(tempfile.mkdtemp(prefix=".kllm-skills-", dir=root.parent))
        (work / "old").mkdir()
        (work / "new").mkdir()
        for change in installation.changes:
            if change.source:
                staged = work / "new" / change.name
                shutil.copytree(change.source, staged)
                if fingerprint(staged) != change.after:
                    raise SkillError(f"Skill source changed while copying: {change.source}; retry")
        for change in installation.changes:
            target = root / change.name
            old = work / "old" / change.name
            if not change.backup:
                current = fingerprint(target) if target.exists() or target.is_symlink() else None
                if current != change.before:
                    raise SkillError(f"Skill changed during installation: {target}; retry")
            if target.exists() or target.is_symlink():
                os.replace(target, old)
            applied.append((change, old))
            if change.source:
                os.replace(work / "new" / change.name, target)
        (work / "manifest.json").write_bytes(encoded)
        os.replace(work / "manifest.json", root / MANIFEST)
        committed = True
        messages = []
        for change, old in applied:
            if change.backup:
                messages.append(f"Backup: {old}")
            else:
                try:
                    _remove(old)
                except OSError:
                    messages.append(f"Installed successfully; old copy retained at {old}")
                    rollback_failed = True  # Retain the recovery folder outside discovery.
        return messages
    except BaseException as error:
        if committed:
            # Publishing ownership is the commit point. An interruption during
            # old-copy cleanup must not undo an already published installation.
            rollback_failed = True
            raise
        for change, old in reversed(applied):
            target = root / change.name
            try:
                if target.exists() or target.is_symlink():
                    if change.after is None or fingerprint(target) != change.after:
                        raise SkillError(f"Destination changed during rollback: {target}")
                    _remove(target)
                if old.exists() or old.is_symlink():
                    os.replace(old, target)
            except (OSError, SkillError):
                rollback_failed = True
        try:
            current_manifest = _manifest(root)[1]
            if current_manifest != installation.previous_manifest:
                if current_manifest != encoded:
                    raise SkillError("Ownership changed during rollback")
                if installation.previous_manifest is None:
                    (root / MANIFEST).unlink()
                else:
                    (work / "restore.json").write_bytes(installation.previous_manifest)
                    os.replace(work / "restore.json", root / MANIFEST)
        except (OSError, SkillError):
            rollback_failed = True
        if rollback_failed:
            raise SkillError(
                f"Installation failed and needs recovery; preserved backups at {work}"
            ) from error
        raise
    finally:
        if (
            work is not None
            and not rollback_failed
            and (not committed or not any(c.backup for c in installation.changes))
        ):
            shutil.rmtree(work, ignore_errors=True)
        lock.unlink(missing_ok=True)


def install_skills(skills: Sequence[Skill], root: Path, *, prune: bool = False) -> list[str]:
    return apply_install(prepare_install(skills, root, prune=prune))
