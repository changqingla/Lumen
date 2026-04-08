from __future__ import annotations

import importlib.util
from pathlib import Path

import defusedxml.minidom


def _load_apply_comments_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / ".."
        / "skills"
        / "custom"
        / "docx-commenting"
        / "scripts"
        / "apply_comments.py"
    ).resolve()
    spec = importlib.util.spec_from_file_location("docx_commenting_apply_comments", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


apply_comments_module = _load_apply_comments_module()


def _paragraph_xml(inner_xml: str):
    return defusedxml.minidom.parseString(
        f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{inner_xml}</w:p>'
    ).documentElement


def _child_elements(node):
    return [child for child in node.childNodes if child.nodeType == child.ELEMENT_NODE]


def _texts_from_runs(paragraph):
    texts = []
    for child in _child_elements(paragraph):
        if child.tagName == "w:r":
            texts.append(apply_comments_module._paragraph_text(child))
    return texts


def test_insert_comment_markers_for_selection_splits_single_run():
    paragraph = _paragraph_xml("<w:r><w:t>abcdef</w:t></w:r>")

    apply_comments_module._insert_comment_markers_for_selection(
        paragraph,
        comment_id=7,
        selection_text="cd",
        selection_occurrence=1,
    )

    children = _child_elements(paragraph)
    assert [child.tagName for child in children] == [
        "w:r",
        "w:commentRangeStart",
        "w:r",
        "w:commentRangeEnd",
        "w:r",
        "w:r",
    ]
    assert _texts_from_runs(paragraph) == ["ab", "cd", "", "ef"]
    assert children[1].getAttribute("w:id") == "7"
    assert children[3].getAttribute("w:id") == "7"


def test_insert_comment_markers_for_selection_spans_multiple_runs():
    paragraph = _paragraph_xml(
        "<w:r><w:t>ab</w:t></w:r>"
        "<w:r><w:t>cd</w:t></w:r>"
        "<w:r><w:t>ef</w:t></w:r>"
    )

    apply_comments_module._insert_comment_markers_for_selection(
        paragraph,
        comment_id=9,
        selection_text="bcde",
        selection_occurrence=1,
    )

    children = _child_elements(paragraph)
    assert [child.tagName for child in children] == [
        "w:r",
        "w:commentRangeStart",
        "w:r",
        "w:r",
        "w:r",
        "w:commentRangeEnd",
        "w:r",
        "w:r",
    ]
    assert _texts_from_runs(paragraph) == ["a", "b", "cd", "e", "", "f"]
    assert children[1].getAttribute("w:id") == "9"
    assert children[5].getAttribute("w:id") == "9"


def test_select_paragraph_can_use_selection_text_without_paragraph_index():
    paragraphs = [
        {"paragraph_index": 1, "text": "第一段内容", "node": None},
        {"paragraph_index": 2, "text": "这里包含关键术语统一战线工作", "node": None},
    ]

    result = apply_comments_module._select_paragraph(
        {"selection_text": "统一战线工作"},
        paragraphs,
    )

    assert result["paragraph_index"] == 2
