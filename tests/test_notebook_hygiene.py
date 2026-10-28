import json
from pathlib import Path

from tools.check_no_pii import scan_text

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebook" / "smart_hr_recruitment.ipynb"


def _load():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def test_notebook_imports_src_instead_of_writing_copies():
    notebook = _load()
    code = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell.get("cell_type") == "code"
    )
    assert "%%writefile" not in code
    assert "FILES = {" not in code
    assert "notebook/src" not in code
    assert "notebook/data" not in code
    for module in ("src.security", "src.skills", "src.mcp_filesystem_server", "src.agents", "src.orchestrator"):
        assert module in code


def test_notebook_outputs_are_cleared():
    notebook = _load()
    for cell in notebook["cells"]:
        if cell.get("cell_type") == "code":
            assert cell.get("outputs") == []
            assert cell.get("execution_count") is None


def test_notebook_has_no_real_pii():
    notebook = _load()
    issues = list(scan_text(json.dumps(notebook), "notebook/smart_hr_recruitment.ipynb"))
    assert not issues, "\n".join(f"{i.path}:{i.line} {i.rule} {i.detail}" for i in issues)
