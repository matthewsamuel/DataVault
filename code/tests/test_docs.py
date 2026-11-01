"""
The install and start instructions must work as written (REPO-HYGIENE-3/-6/-7, A2-2..A2-4).

- The documented uvicorn command is the factory form; backend.main has no module-level app.
- The docs ask for Python 3.12, which the pinned requirements need.
- No doc tells you to kill everything on port 8000 (that also kills the browser tab).
- start.sh and stop_datavault.sh only stop a DataVault uvicorn, never another program.
"""
import re
from pathlib import Path

import backend.main

CODE = Path(__file__).resolve().parent.parent
REPO = CODE.parent
DOCS = [REPO / "README.md", REPO / "PORTFOLIO.md", CODE / "README.md",
        CODE / "APP_BUNDLE_README.md", CODE / "RESTART.md"]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_documented_start_command_matches_the_app_factory():
    assert callable(backend.main.create_app)
    assert not hasattr(backend.main, "app")
    for doc in (REPO / "README.md", CODE / "README.md"):
        text = _text(doc)
        assert "--factory backend.main:create_app" in text, doc
        assert "backend.main:app" not in text, doc
    for script in ("start.sh",):
        assert "--factory backend.main:create_app" in _text(CODE / script)


def test_docs_ask_for_python_3_12():
    assert "Python 3.12" in _text(CODE / "requirements.txt").splitlines()[0]
    for doc in DOCS:
        assert not re.search(r"Python 3\.(8|9|10|11)\+", _text(doc)), doc
    for doc in (REPO / "README.md", CODE / "README.md"):
        text = _text(doc)
        assert "python3.12 -m venv" in text and "python3 -m venv" not in text, doc


def test_install_steps_use_the_files_in_code():
    text = _text(REPO / "README.md")
    install = text[text.index("## Install"):text.index("## Running the tests")]
    assert "cd code" in install
    assert (CODE / "requirements.txt").exists() and (CODE / "config.example.json").exists()
    assert "cp config.example.json config.json" in install


def test_no_doc_kills_every_process_on_port_8000():
    for doc in DOCS:
        text = _text(doc)
        for line in text.splitlines():
            if re.search(r"lsof\s+-ti\s*:?\s*:?8000", line) and "kill" in line:
                # Only allowed as an explicit warning not to do it
                assert any(w in line for w in ("Gebruik geen", "zou die", "dat stopt ook")), (doc, line)
        assert not re.search(r"^\s*lsof -ti:?\s*:?8000 \| xargs kill", text, re.M), doc


def test_start_and_stop_scripts_only_stop_a_datavault_server():
    for script in ("start.sh", "stop_datavault.sh"):
        text = _text(CODE / script)
        assert "-sTCP:LISTEN" in text, script
        assert "ps -o command= -p" in text, script
        assert "*uvicorn*backend.main*" in text, script
