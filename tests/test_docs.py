"""Keeps the setup docs true to the code.

- Every environment setting the code reads is named in README.md, docker-compose.md or docs/.
- Links between the docs point at files that exist.
- The compose file pulls the published image instead of building on the server.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Read by the code but not set by whoever runs the toolkit: the test suite's own switches.
NOT_FOR_OPERATORS = {"REQUIRE_HYPERFRAMES", "TEST_WHISPER_MODEL"}

SETTING = re.compile(r"""os\.(?:environ\.get|getenv|environ\[)\(?\s*["']([A-Z][A-Z0-9_]+)["']""")


def settings_read_in(text):
    return set(SETTING.findall(text))


def code_files():
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"tests", "venv", ".venv", "node_modules"} or "__pycache__" in parts:
            continue
        yield path


def docs_text():
    files = [ROOT / "README.md", ROOT / "docker-compose.md", *sorted((ROOT / "docs").rglob("*.md"))]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_settings_reader_finds_each_form():
    text = 'os.environ.get("A_ONE")\nos.getenv(\'B_TWO\', "x")\nos.environ["C_THREE"]'
    assert settings_read_in(text) == {"A_ONE", "B_TWO", "C_THREE"}


def test_every_setting_the_code_reads_is_documented():
    wanted = set()
    for path in code_files():
        wanted |= settings_read_in(path.read_text(encoding="utf-8", errors="ignore"))
    docs = docs_text()
    missing = sorted(
        name for name in wanted - NOT_FOR_OPERATORS if not re.search(rf"\b{name}\b", docs)
    )
    assert missing == [], f"Document these in README.md or docs/: {', '.join(missing)}"


def test_links_between_the_docs_point_at_files_that_exist():
    docs = [ROOT / "README.md", ROOT / "docker-compose.md", ROOT / "AGENTS.md", ROOT / "CLAUDE.md"]
    broken = []
    for doc in docs:
        text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"(https?:|mailto:|#)", target):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                broken.append(f"{doc.name} -> {target}")
    assert broken == []


def test_readme_lists_every_fork_endpoint():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for endpoint in [
        "/v1/toolkit/health",
        "/v1/media/look",
        "/v1/video/reframe",
        "/v1/video/motion",
        "/v1/storage/",
    ]:
        assert endpoint in readme, endpoint


def test_compose_pulls_the_published_image():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "ghcr.io/hamchowderr/no-code-architects-toolkit" in compose
    assert not re.search(r"^\s+build:", compose, re.M)
    assert "redis:" in compose
