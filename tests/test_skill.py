"""The dmx-research skill (skills/dmx-research) must stay true to the servers it describes:
every tool call it shows exists on the matching world's server, with real parameters."""

import asyncio
import re
import zipfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "dmx-research"
WORLD_FILES = {"projects": "references/projects.md", "marketing": "references/marketing.md",
               "documentation": "references/documentation.md"}


@pytest.fixture(scope="module")
def schemas(tmp_path_factory):
    """{world: {tool: set(parameters)}} from the real MCP servers."""
    from mcp import Client

    from dmx_docs.config import Config
    from dmx_docs.server import build_server

    root = tmp_path_factory.mktemp("root")
    out = {}
    for world in WORLD_FILES:
        cfg = Config(roots=[str(root)], data_dir=tmp_path_factory.mktemp("data"), world=world,
                     world_title=world.capitalize(), profile=world, embedding_model="hash:256")

        async def tools():
            async with Client(build_server(cfg)) as client:
                return (await client.list_tools()).tools

        out[world] = {t.name: set((getattr(t, "input_schema", None) or t.inputSchema).get("properties", {}))
                      for t in asyncio.run(tools())}
    return out


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md starts with a --- frontmatter block"
    return dict(re.findall(r"^(\w+):\s*(.+)$", m.group(1), re.M))


def calls(text):
    """Code spans that look like tool calls or parameters: (first word, [parameters])."""
    for span in re.findall(r"`([^`\n]+)`", text):
        first = span.split()[0] if span.split() else ""
        yield span, first, re.findall(r"(?:^|\s)(\w+)=", span)


def check(text, tools: dict[str, set], where: str):
    """A span starting with a tool name: its parameters must be that tool's. A span starting with
    another snake_case word followed by arguments looks like a call to a tool that does not exist.
    A span 'name=...' must name a parameter of one of the tools."""
    every_param = set().union(*tools.values())
    problems = []
    for span, first, params in calls(text):
        if first in tools:
            bad = [p for p in params if p not in tools[first]]
            if bad:
                problems.append(f"{where}: `{span}` - {first} has no parameter {bad}")
        elif re.fullmatch(r"[a-z]+(?:_[a-z]+)+", first) and len(span.split()) > 1 and first not in every_param:
            problems.append(f"{where}: `{span}` - no tool {first}")
        elif re.match(r"^\w+=", span) and span.split("=")[0] not in every_param:
            problems.append(f"{where}: `{span}` - no such parameter")
    return problems


def test_skill_frontmatter_and_files():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = frontmatter(text)
    assert meta["name"] == "dmx-research"
    assert 100 < len(meta["description"]) <= 1024
    for name in ("dmx-projects", "dmx-marketing", "dmx-documentation", "dmx-docs"):
        assert name in text, name
    for ref in WORLD_FILES.values():
        assert (SKILL / ref).is_file() and ref in text, ref


def test_world_references_only_use_that_worlds_tools(schemas):
    problems = []
    for world, ref in WORLD_FILES.items():
        problems += check((SKILL / ref).read_text(encoding="utf-8"), schemas[world], ref)
    assert not problems, "\n".join(problems)


def test_core_only_uses_tools_that_exist_in_some_world(schemas):
    merged: dict[str, set] = {}
    for tools in schemas.values():
        for name, params in tools.items():
            merged.setdefault(name, set()).update(params)
    problems = check((SKILL / "SKILL.md").read_text(encoding="utf-8"), merged, "SKILL.md")
    assert not problems, "\n".join(problems)


def test_pictures_option_is_taught_where_every_search_has_it(schemas):
    assert all("pictures" in tools["search"] for tools in schemas.values())
    assert "pictures=true" in (SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_build_skill_zip_uses_forward_slashes(tmp_path):
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "build_skill.py"
    r = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(tmp_path / "dmx-research.zip") as z:
        names = z.namelist()
    assert "dmx-research/SKILL.md" in names and "dmx-research/references/projects.md" in names
    assert not any("\\" in n for n in names)
