from __future__ import annotations

import importlib.util
from pathlib import Path

import defusedxml.minidom


def _load_docx_comment_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / ".."
        / "skills"
        / "public"
        / "docx"
        / "scripts"
        / "comment.py"
    ).resolve()
    spec = importlib.util.spec_from_file_location("docx_comment_script", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


comment_module = _load_docx_comment_module()


def _minimal_unpacked_docx(tmp_path: Path) -> Path:
    unpacked = tmp_path / "unpacked"
    word = unpacked / "word"
    rels = word / "_rels"
    rels.mkdir(parents=True)

    (rels / "document.xml.rels").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""",
        encoding="utf-8",
    )
    (unpacked / "[Content_Types].xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
</Types>
""",
        encoding="utf-8",
    )
    return unpacked


def _parse_xml(path: Path):
    return defusedxml.minidom.parseString(path.read_text(encoding="utf-8"))


def test_add_comment_creates_comment_parts_relationships_and_content_types(tmp_path):
    unpacked = _minimal_unpacked_docx(tmp_path)

    para_id, message = comment_module.add_comment(
        str(unpacked),
        comment_id=0,
        text="Comment text with &amp; entity",
        author="Reviewer",
        initials="R",
    )

    assert para_id
    assert message.startswith("Added comment 0")

    word = unpacked / "word"
    comments = _parse_xml(word / "comments.xml")
    comment = comments.getElementsByTagName("w:comment")[0]
    assert comment.getAttribute("w:id") == "0"
    assert comment.getAttribute("w:author") == "Reviewer"
    assert comment.getElementsByTagName("w:t")[0].firstChild.nodeValue == "Comment text with & entity"

    rels = (word / "_rels" / "document.xml.rels").read_text(encoding="utf-8")
    assert 'Target="comments.xml"' in rels
    assert 'Target="commentsExtended.xml"' in rels
    assert 'Target="commentsIds.xml"' in rels
    assert 'Target="commentsExtensible.xml"' in rels

    content_types = (unpacked / "[Content_Types].xml").read_text(encoding="utf-8")
    assert 'PartName="/word/comments.xml"' in content_types
    assert 'PartName="/word/commentsExtended.xml"' in content_types
    assert 'PartName="/word/commentsIds.xml"' in content_types
    assert 'PartName="/word/commentsExtensible.xml"' in content_types


def test_add_reply_records_parent_comment_reference(tmp_path):
    unpacked = _minimal_unpacked_docx(tmp_path)

    parent_para_id, parent_message = comment_module.add_comment(
        str(unpacked),
        comment_id=0,
        text="Parent comment",
    )
    reply_para_id, reply_message = comment_module.add_comment(
        str(unpacked),
        comment_id=1,
        text="Reply comment",
        parent_id=0,
    )

    assert parent_para_id
    assert reply_para_id
    assert parent_message.startswith("Added comment 0")
    assert reply_message.startswith("Added reply 1")

    extended = _parse_xml(unpacked / "word" / "commentsExtended.xml")
    entries = extended.getElementsByTagName("w15:commentEx")
    assert len(entries) == 2
    reply = entries[1]
    assert reply.getAttribute("w15:paraId") == reply_para_id
    assert reply.getAttribute("w15:paraIdParent") == parent_para_id
