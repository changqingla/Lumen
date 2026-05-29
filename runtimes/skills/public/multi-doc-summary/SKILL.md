---
name: multi-doc-summary
description: Produce a thematic integrated summary across multiple documents. Use when the user asks to summarize several documents, synthesize multiple materials, or extract shared conclusions. Prefer direct KB reading tools and do not depend on local reader_kb copy paths.
---

# Multi-Document Summary Skill

## Execution Rules

1. Define the summary goal first, such as decision support, learning, or reporting, along with the desired granularity.
2. If there are 3 or more documents and the `task` tool is available in this turn, delegate grouped extraction to subagents.
3. If `task` is unavailable or there are fewer than 3 documents, the main agent should read directly.
4. The summary must aggregate by theme. Do not write a document-by-document running account.

## Subagent Extraction Template (3+ Documents and `task` Available)

When calling `task`:
- Use `general-purpose` as `subagent_type`.
- In the `prompt`, clearly restrict the subagent to grouped reading and evidence extraction only. The subagent must not draft the final report or publish files.

Each subagent `prompt` should include at least:
- Core proposition of the document
- Key methods or mechanisms
- Major facts or data points
- Conclusions and boundary conditions
- Relevance assessment to the topic
- Quotable evidence with document names and location information. If only document IDs are available, record the IDs first and replace them with document names in the final draft.

If direct KB reading tools are available, preferably require subagents to use only:
- `kb_list_docs`
- `kb_read_doc`
- `kb_read_doc_lines`
- `kb_search_doc`

Do not call `present_files` from subagents. Final file publishing is handled only by the main agent.

## Output Structure (Required)

```markdown
# {Integrated Summary Title}

## TL;DR (120-200 words)
## Thematic Summary (3-6 Themes)
## Key Consensus and Main Trends
## Major Disagreements and Causes
## Actionable Conclusions / Decision Recommendations
## Unresolved Questions and Follow-Up Information Needs
## Evidence Source List
```

## Quality Bar

1. Include at least 3 thematic sections, and each theme must integrate across documents.
2. Key points must cite document sources. Do not make unsourced statements.
3. Include a disagreement section. Do not only write consensus conclusions.
4. The output must not be too short or filled with templated abstractions. Include concrete facts, terms, or data.
5. If information is insufficient, clearly state the evidence gaps. Do not fill gaps with subjective guesses.
6. Do not use document IDs in the final report. Use document names instead when references are needed.

## Final Delivery Requirements

1. Organize the final result as a complete Markdown document. Drafts may be written under `/mnt/user-data/workspace`, but the final deliverable must be written under `/mnt/user-data/outputs`, for example `/mnt/user-data/outputs/multi-document-summary-{topic}.md`.
2. Use `write_file` to write the final Markdown file.
3. After generating the file, call `present_files` to publish it, passing the final Markdown file path. Do not stop after only calling `write_file`.
4. If self-checking is needed, use `read_file` or `ls` to inspect the file content and output directory.
5. The final response must include delivery information for the generated file and clearly tell the user that the Markdown file is available for download. Do not only say that it has been generated, and do not only paste the body text.
