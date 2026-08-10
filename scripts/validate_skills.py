#!/usr/bin/env python3
"""Validate the repository's portable Agent Skills without third-party packages."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
SCRIPT_RE = re.compile(r"(?<![\w/])scripts/([A-Za-z0-9_.-]+\.py)")
FORBIDDEN = ("TODO", "[TODO:")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated YAML frontmatter")
    fields: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if not raw.strip():
            continue
        if ":" not in raw:
            raise ValueError(f"invalid frontmatter line: {raw}")
        key, value = raw.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, text[end + 5 :]


def main() -> int:
    errors: list[str] = []
    files = sorted(SKILLS.glob("*/SKILL.md"))
    if not files:
        errors.append("no skills/*/SKILL.md files found")

    names: set[str] = set()
    for path in files:
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        try:
            fields, body = parse_frontmatter(text)
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue

        if set(fields) != {"name", "description"}:
            errors.append(f"{rel}: frontmatter must contain only name and description")
        name = fields.get("name", "")
        if not NAME_RE.fullmatch(name):
            errors.append(f"{rel}: invalid name {name!r}")
        if name != path.parent.name:
            errors.append(f"{rel}: name must match directory")
        if name in names:
            errors.append(f"{rel}: duplicate skill name {name}")
        names.add(name)
        if len(fields.get("description", "")) < 40:
            errors.append(f"{rel}: description is too short")
        if any(marker in text for marker in FORBIDDEN):
            errors.append(f"{rel}: unresolved template marker")
        if not body.strip():
            errors.append(f"{rel}: empty instructions")

        for target in LINK_RE.findall(body):
            if "://" in target or target.startswith("#"):
                continue
            clean = target.split("#", 1)[0]
            if clean and not (path.parent / clean).resolve().exists():
                errors.append(f"{rel}: broken local link {target}")

        for script_name in sorted(set(SCRIPT_RE.findall(body))):
            target = path.parent / "scripts" / script_name
            if not target.is_file():
                errors.append(
                    f"{rel}: referenced script is not packaged: scripts/{script_name}"
                )

        agent_yaml = path.parent / "agents" / "openai.yaml"
        if not agent_yaml.exists():
            errors.append(f"{rel}: missing agents/openai.yaml")

    if errors:
        print("Skill validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Validated {len(files)} skills: {', '.join(sorted(names))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
