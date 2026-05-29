---
name: paper-translation
description: Translate an academic paper or Markdown-converted paper into Chinese as a deliverable Markdown file. Use when the user asks to translate a paper, article, thesis, preprint, PDF-derived Markdown, or scholarly document. The workflow must create a new Markdown output file first, append translated content section by section, leave the references/bibliography section untranslated, and finally present the Markdown file as the artifact.
---

# Paper Translation Skill

## Goal

Translate the full academic paper into Chinese and deliver a Markdown file. Do not only answer with pasted translated text. The final artifact must be a `.md` file under `/mnt/user-data/outputs`.

## Inputs

- Prefer a Markdown source file under `/mnt/user-data/uploads`.
- If only a PDF is available and a Markdown version also exists, translate the Markdown version.
- If several files are available, choose the paper-like Markdown/PDF-derived file that matches the user's request. If there is no clear candidate, ask for clarification.

## Required Workflow

1. Identify the source paper file and inspect its structure.
   - Use `read_file` for Markdown input.
   - For large files, read by line ranges or logical sections instead of loading everything into the final response.
2. Create a new output Markdown file before translation starts.
   - Use a clear path such as `/mnt/user-data/outputs/{source-stem}.zh.md`.
   - Initialize it with an empty string or a short translated title/header if immediately known.
3. Translate section by section and append each translated section to the output file.
   - Use `write_file` with `append=true` after each section or batch.
   - Preserve Markdown hierarchy, formulas, tables, image links, code blocks, footnotes, citations, and numbering.
   - Preserve image Markdown exactly, such as `![](images/figure.jpg)` or `![caption](images/figure.jpg)`. Do not remove images, rewrite image paths, or translate image filenames.
   - If the source contains MinerU-generated Mermaid or `<details>` diagram transcriptions next to a real image, omit those diagram transcription blocks from the translated artifact and keep the real image Markdown plus the figure caption.
   - Translate titles, abstract, body text, figure/table captions, notes, acknowledgements, and appendices unless the user says otherwise.
   - Do not summarize or omit content.
4. Detect the references section.
   - Treat headings such as `References`, `Bibliography`, `Works Cited`, and equivalent bibliography headings in any language as the start of the references section.
   - From that heading to the end of the references list, keep the original text unchanged.
   - Do not translate author names, paper titles, journal names, DOI/URL fields, or citation entries.
5. After all sections are written, inspect the output file.
   - Confirm it is not empty.
   - Confirm the references section is present and untranslated when the source has references.
6. Publish the final Markdown file with `present_files`.

## Translation Rules

- Output language: Chinese.
- Keep academic tone precise and fluent.
- Preserve technical terms. When a term has no stable Chinese translation, keep the English term and optionally add a concise Chinese explanation on first occurrence.
- Preserve all citation markers exactly, such as `[1]`, `(Smith et al., 2024)`, `[@smith2024]`, and superscript-style markers.
- Preserve math exactly, including inline `$...$`, block `$$...$$`, LaTeX environments, and equation numbers.
- Preserve Markdown table structure. Translate cell text but not numeric values, formulas, URLs, or identifiers.
- Preserve code blocks exactly unless the surrounding prose comments are natural language and clearly should be translated.
- Do not add commentary about the translation process inside the output file.

## Section Append Pattern

Use this pattern throughout the job:

```text
read source section -> translate section -> append translated Markdown block to output file -> continue
```

When appending, separate sections with blank lines:

```markdown

## Translated Section Heading

Translated body text...
```

For the references section, append the original Markdown block unchanged:

```markdown

## References

[1] Original reference entry...
```

## Quality Checklist

Before presenting the file, verify:

- The output file exists under `/mnt/user-data/outputs`.
- Main sections from the source appear in the output in the same order.
- No translated content is only present in the chat response but missing from the file.
- References/bibliography entries remain in the original language.
- Markdown syntax is not broken by missing fences, table separators, or heading levels.

## Final Response

After calling `present_files`, reply briefly that the translated Markdown file has been generated and is available for download. Do not paste the full translation in the final chat response.

If the caller explicitly asks for machine-readable output, return only the requested structured value. In Creative Workshop paper translation requests, the caller expects machine-readable JSON, so after `present_files` reply with only:

```json
{"translated_markdown_path":"/mnt/user-data/outputs/{source-stem}.zh.md"}
```
