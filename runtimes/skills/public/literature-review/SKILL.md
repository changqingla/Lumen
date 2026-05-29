---
name: literature-review
description: Generate a high-quality literature review, survey, or related-work report from multiple papers. The output should cover research trajectories, method comparisons, controversies, and research gaps. Use when the user asks for a literature review, related work, survey, or review report. Prefer direct KB reading tools and do not depend on local reader_kb copy paths.
---

# Literature Review Skill

## Execution Rules

1. Clarify the review topic, time range, research object, and output language.
2. If there are 2 or more documents and the `task` tool is available in this turn, delegate parallel reading and structured evidence extraction to subagents.
3. If `task` is unavailable or there are fewer than 2 documents, the main agent should read directly.
4. Organize the review by themes or methodological threads. Do not pile up summaries in document order.

## Subagent Extraction Requirements (2+ Documents and `task` Available)

When calling `task`:
- Use `general-purpose` as `subagent_type`.
- In the `prompt`, clearly restrict the subagent to evidence extraction only. The subagent must not write the final review or publish files.

Each subagent `prompt` should require extraction of at least:
- Basic document information, including title, authors, year, and source. If metadata is missing, preserve at least the document name.
- Research questions and goals
- Theoretical framework / method paradigm
- Data and experimental design
- Key findings and evidence strength, preserving quantitative results, key numbers, and core comparative conclusions whenever possible
- Limitations and applicability boundaries
- Relationship to the current review topic, including which thematic branch it belongs to and what claims it supports or challenges
- Reusable citation information, such as author-year, document name, and key evidence sentences

If direct KB reading tools are available, preferably require subagents to use only:
- `kb_list_docs`
- `kb_read_doc`
- `kb_read_doc_lines`
- `kb_search_doc`

If temporary organization of extraction results is needed, the main agent should summarize them in its own reasoning or write a temporary file under `/mnt/user-data/workspace`.

## Output Structure (Required; Adapt to Source Document Type)

```markdown
# {Review Title}

## Abstract
## 1. Research Scope and Inclusion Criteria
## 2. Research Trajectory and Thematic Synthesis
## 3. Methodological Comparison and Evidence Assessment
## 4. Key Controversies and Inconsistent Conclusions
## 5. Research Gaps and Future Agenda
## References and Evidence Sources
```

## Quality Bar

1. Provide at least 3 thematic synthesis sections.
2. Every core conclusion must be tied to source evidence. Single-source claims must be clearly labeled.
3. Include controversies / disagreements and research gaps. Do not only provide positive summaries.
4. Use terms and evidence rather than vague phrasing.
5. If information is insufficient, disclose the gap first and then provide a conservative conclusion.
6. Do not use document IDs in the final report. Use document names instead when references are needed.

## Citation Rules

1. Use sequential numeric inline citations throughout the body, formatted as `[1]`, `[2]`, `[3]`. Do not use document-name-author-year strings as the main inline citation format.
2. Assign numbers by first appearance and reuse them throughout the entire document. The same source must always use the same number and must not be renumbered later.
3. When multiple sources support one conclusion, use parallel numbers such as `[1][2][3]`; consecutive numbers may be shortened as `[1-3]`.
4. Key judgments, data, method syntheses, controversial conclusions, and research gaps must include inline citations in the relevant sentence or paragraph. Do not list sources only at the end.
5. When paraphrasing a unique view, experimental result, or numerical finding from a single paper, provide that paper's unique corresponding number so a single-paper conclusion is not presented as field consensus.
6. The final "References and Evidence Sources" section must correspond one-to-one with body citation numbers, listed as `[1] ...`, `[2] ...`. Include at least authors, year, title, and source. If metadata is insufficient, preserve the document name and identifiable source.
7. If web search is used to supplement evidence, mark it separately as web supplementary sources and keep it distinct from the academic literature numbering system to avoid treating web content as paper evidence.
8. Never fabricate citations. Every body citation number must be traceable to evidence in read documents or retrieved sources.

## Final Delivery Requirements

1. Organize the final result as a complete Markdown document. Drafts may be written under `/mnt/user-data/workspace`, but the final deliverable must be written under `/mnt/user-data/outputs`, for example `/mnt/user-data/outputs/literature-review-{topic}.md`.
2. Use `write_file` to write the final Markdown file.
3. After generating the file, call `present_files` to publish it, passing the final Markdown file path. Do not stop after only calling `write_file`.
4. If self-checking is needed, use `read_file` or `ls` to inspect the file content and output directory.
5. The final response must include delivery information for the generated file and clearly tell the user that the Markdown file is available for download. Do not only say that it has been generated, and do not only paste the body text.
