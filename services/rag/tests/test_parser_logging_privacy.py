"""Privacy regressions for document parser fallback logging."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

RAG_ROOT = Path(__file__).resolve().parents[1]


def _load_module(
    module_name: str,
    relative_path: str,
    *,
    stubs: dict[str, ModuleType] | None = None,
):
    spec = importlib.util.spec_from_file_location(module_name, RAG_ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    stubs = stubs or {}
    previous = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


fake_core_nlp = ModuleType("core.nlp")
fake_core_nlp.rag_tokenizer = SimpleNamespace(
    tokenize=lambda text: text,
    fine_grained_tokenize=lambda text: text,
)


def _fake_tokenize(document, text, _english):
    document["content_with_weight"] = text


fake_core_nlp.tokenize = _fake_tokenize
fake_core_nlp.find_codec = lambda _data: "utf-8"
fake_pandas = ModuleType("pandas")
fake_pandas.read_excel = lambda _source: None
fake_pandas.read_csv = lambda _source: None
fake_openpyxl = ModuleType("openpyxl")
fake_openpyxl.Workbook = object
fake_openpyxl.load_workbook = lambda _source: None
fake_pptx = ModuleType("pptx")
fake_pptx.Presentation = lambda _source: None

ppt_md_parser = _load_module(
    "ppt_md_parser_privacy_test",
    "core/app/ppt_md_parser.py",
    stubs={"core.nlp": fake_core_nlp},
)
excel_parser = _load_module(
    "excel_parser_privacy_test",
    "deepdoc/parser/excel_parser.py",
    stubs={
        "core.nlp": fake_core_nlp,
        "openpyxl": fake_openpyxl,
        "pandas": fake_pandas,
    },
)
ppt_parser = _load_module(
    "ppt_parser_privacy_test",
    "deepdoc/parser/ppt_parser.py",
    stubs={"pptx": fake_pptx},
)


def test_ppt_shape_failure_does_not_log_provider_error_body(monkeypatch, caplog):
    marker = "private-ppt-provider-body"

    class BrokenShape:
        top = 0
        left = 0

        @property
        def shape_type(self):
            raise RuntimeError(marker)

    fake_presentation = SimpleNamespace(
        slides=[SimpleNamespace(shapes=[BrokenShape()])]
    )
    monkeypatch.setattr(ppt_parser, "Presentation", lambda _source: fake_presentation)

    with caplog.at_level(logging.WARNING):
        result = ppt_parser.DeepRAGPptParser()(b"input", 0, 1)

    assert result == [""]
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


def test_excel_fallback_hides_path_and_parser_error_bodies(monkeypatch, caplog):
    path_marker = "/private/user-secret/report.xlsx"
    openpyxl_marker = "private-openpyxl-body"
    pandas_marker = "private-pandas-body"

    def fail_openpyxl(_source):
        raise RuntimeError(openpyxl_marker)

    def fail_pandas(_source):
        raise RuntimeError(pandas_marker)

    monkeypatch.setattr(excel_parser, "load_workbook", fail_openpyxl)
    monkeypatch.setattr(excel_parser.pd, "read_excel", fail_pandas)

    with caplog.at_level(logging.INFO), pytest.raises(
        ValueError,
        match="Spreadsheet input could not be parsed",
    ) as exc_info:
        excel_parser.DeepRAGExcelParser._load_excel_to_workbook(path_marker)

    combined = caplog.text + str(exc_info.value)
    assert path_marker not in combined
    assert openpyxl_marker not in combined
    assert pandas_marker not in combined
    assert "RuntimeError" in caplog.text


def test_ppt_markdown_completion_log_omits_source_path(caplog):
    path_marker = "/private/user-secret/slides.md"

    with caplog.at_level(logging.INFO):
        chunks = ppt_md_parser.chunk(
            path_marker,
            binary=b"##PPT Page 1\nPublic content",
        )

    assert len(chunks) == 1
    assert path_marker not in caplog.text
    assert "chunk_count=1" in caplog.text
