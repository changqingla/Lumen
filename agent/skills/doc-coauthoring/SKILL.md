---
name: doc-coauthoring
description: Guide users through a structured workflow for co-authoring documentation. Use when user wants to write documentation, proposals, technical specs, decision docs, or similar structured content. This workflow helps users efficiently transfer context, refine content through iteration, and verify the doc works for readers. Trigger when user mentions writing docs, creating proposals, drafting specs, or similar documentation tasks.
---

# Doc Co-Authoring Workflow

This skill provides a structured workflow for guiding users through collaborative document creation. Act as an active guide, walking users through three stages: Context Gathering, Refinement & Structure, and Reader Testing.

## When to Offer This Workflow

**Trigger conditions:**
- User mentions writing documentation: "write a doc", "draft a proposal", "create a spec", "write up"
- User mentions specific doc types: "PRD", "design doc", "decision doc", "RFC"
- User seems to be starting a substantial writing task

**Initial offer:**
Offer the user a structured workflow for co-authoring the document. Explain the three stages:

1. **Context Gathering**: User provides all relevant context while you ask clarifying questions
2. **Refinement & Structure**: Iteratively build each section through brainstorming and editing
3. **Reader Testing**: Test the doc to catch blind spots before others read it

## Stage 1: Context Gathering

**Goal:** Close the gap between what the user knows and what you know.

### Initial Questions

1. What type of document is this? (e.g., technical spec, decision doc, proposal)
2. Who's the primary audience?
3. What's the desired impact when someone reads this?
4. Is there a template or specific format to follow?
5. Any other constraints or context to know?

### Info Dumping

Encourage the user to dump all context they have:
- Background on the project/problem
- Related discussions or documents
- Why alternative solutions aren't being used
- Timeline pressures or constraints
- Technical architecture or dependencies
- Stakeholder concerns

After the initial dump, ask 5-10 clarifying questions based on gaps.

**Exit condition:** Sufficient context gathered when you can ask about edge cases and trade-offs without needing basics explained.

## Stage 2: Refinement & Structure

**Goal:** Build the document section by section through brainstorming, curation, and iterative refinement.

For each section:
1. Ask clarifying questions about what to include
2. Brainstorm 5-20 options
3. User indicates what to keep/remove/combine
4. Draft the section
5. Refine through surgical edits

Start with whichever section has the most unknowns.

**Key instruction:** Instead of editing the doc directly, ask the user to indicate what to change. This helps learn their style for future sections.

### Near Completion

When 80%+ sections are done, re-read the entire document and check for:
- Flow and consistency across sections
- Redundancy or contradictions
- Generic filler content
- Whether every sentence carries weight

## Stage 3: Reader Testing

**Goal:** Test the document to verify it works for readers.

### Steps

1. **Predict Reader Questions**: Generate 5-10 questions readers would realistically ask
2. **Test**: For each question, evaluate if the document answers it clearly
3. **Additional Checks**: Check for ambiguity, false assumptions, contradictions
4. **Fix**: Loop back to refinement for problematic sections

**Exit condition:** Questions are consistently answered correctly with no new gaps.

## Final Review

1. Recommend a final read-through by the user
2. Suggest double-checking facts, links, and technical details
3. Verify it achieves the intended impact

## Tips

- Be direct and procedural
- If user wants to skip a stage, let them
- Don't let context gaps accumulate — address them as they come up
- Quality over speed
