---
name: doc-comparison
description: Perform high-quality structured comparative analysis across multiple documents. Use when the user asks to compare, contrast, analyze similarities and differences, or horizontally evaluate multiple documents or papers. Prefer direct KB reading tools and do not depend on local reader_kb copy paths.
---

# Multi-Document Comparison Skill

## Execution Rules

1. Prefer direct KB reading tools enabled in the current runtime to confirm the document scope first. If `kb_list_docs`, `kb_read_doc`, `kb_read_doc_lines`, or `kb_search_doc` are available in this turn, use them first.
2. Define a unified set of comparison dimensions before reading, so the criteria do not shift mid-analysis.
3. If there are 3 or more documents and the `task` tool is available in this turn, delegate parallel reading to subagents, with each subagent responsible for 1-2 documents.
4. If `task` is unavailable or there are fewer than 3 documents, the main agent should read directly.
5. The output must include evidence mapping. Do not make unsupported conclusions.

## Subagent Delegation Requirements (3+ Documents and `task` Available)

When calling `task`:
- Use `general-purpose` as `subagent_type`.
- In the `prompt`, clearly restrict the subagent to document reading and evidence extraction only. The subagent must not draft the final report or publish files.

Each subagent `prompt` should require extraction of at least:
- Research goals / problem definition
- Methods and technical route
- Data sources and experimental setup
- Key results and metrics
- Limitations and applicability boundaries
- Quotable evidence sentences with document names and location information. If only document IDs are available, record the IDs first and replace them with document names in the final draft.

If direct KB reading tools are available, preferably require subagents to use only:
- `kb_list_docs`
- `kb_read_doc`
- `kb_read_doc_lines`
- `kb_search_doc`

Do not call `present_files` from subagents. Final file publishing is handled only by the main agent.

## Baseline Comparison Dimensions

Cover at least the following dimensions when applicable. Expand or adjust them based on document type and user needs:
- Problem definition
- Method paradigm
- Data and samples
- Metrics and evaluation approach
- Key conclusions
- Novel contributions
- Limitations
- Applicable scenarios

## Output Structure (Required)

```markdown
# {Comparison Report Title}

## One-Sentence Conclusions (3-5 Items)
## Comparison Matrix (Table)
## Consensus Points
## Key Differences and Conflicting Evidence
## Strengths, Weaknesses, and Applicability Conditions
## Selection Recommendations by Scenario
## Evidence Source List
```

## Quality Bar

1. The comparison matrix must cover at least 6 dimensions.
2. Every key conclusion must be tied to source documents. Cross-document conclusions should preferably be supported by at least 2 sources.
3. Explicitly identify conflicting conclusions and possible causes, such as data differences, metric-definition differences, or experimental-condition differences.
4. Do not produce a comparison that is merely a stitched sequence of per-document summaries.
5. If information is insufficient, state the gap first and then provide a tentative judgment. Do not invent missing information.
6. Do not use document IDs in the final report. Use document names instead when references are needed.

## Final Delivery Requirements

1. Organize the final result as a complete Markdown document. Drafts may be written under `/mnt/user-data/workspace`, but the final deliverable must be written under `/mnt/user-data/outputs`, for example `/mnt/user-data/outputs/document-comparison-report-{topic}.md`.
2. Use `write_file` to write the final Markdown file.
3. After generating the file, call `present_files` to publish it, passing the final Markdown file path. Do not stop after only calling `write_file`.
4. If self-checking is needed, use `read_file` or `ls` to inspect the file content and output directory.
5. The final response must include delivery information for the generated file and clearly tell the user that the Markdown file is available for download. Do not only say that it has been generated, and do not only paste the body text.
