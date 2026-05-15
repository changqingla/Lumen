export const STREAMING_CODE_CARET_TOKEN = '[[__STREAMING_CODE_CARET__]]';

const STREAMING_CARET_HTML = '<span class="streaming-inline-caret" aria-hidden="true">▌</span>';

function appendStreamingCaret(content: string): string {
  if (!content) {
    return STREAMING_CARET_HTML;
  }

  const trailingMatch = content.match(/(?:\n[\t ]*)+$/u);
  const trailing = trailingMatch?.[0] ?? '';
  const base = trailing ? content.slice(0, -trailing.length) : content;

  if (!base) {
    return `${STREAMING_CARET_HTML}${trailing}`;
  }

  if (/[`~]{3,}$/u.test(base.trimEnd())) {
    return `${base}\n${STREAMING_CARET_HTML}${trailing}`;
  }

  return `${base}${STREAMING_CARET_HTML}${trailing}`;
}

function formatDisplayMathBlock(body: string): string {
  const normalizedBody = body.trim();
  if (!normalizedBody) {
    return `\\[${body}\\]`;
  }

  return `\n$$\n${normalizedBody}\n$$\n`;
}

function normalizeLatexMathDelimitersInPlainText(content: string): string {
  return content
    .replace(/\\\[([\s\S]*?)\\\]/gu, (_match, body: string) => formatDisplayMathBlock(body))
    .replace(/\\\(((?:\\.|[^\\\n])+?)\\\)/gu, (_match, body: string) => `$${body}$`);
}

function normalizeLatexMathDelimitersInText(content: string): string {
  let result = '';
  let cursor = 0;

  while (cursor < content.length) {
    const backtickIndex = content.indexOf('`', cursor);
    if (backtickIndex === -1) {
      result += normalizeLatexMathDelimitersInPlainText(content.slice(cursor));
      break;
    }

    result += normalizeLatexMathDelimitersInPlainText(content.slice(cursor, backtickIndex));

    const delimiterLength = content.slice(backtickIndex).match(/^`+/u)?.[0].length ?? 1;
    const delimiter = '`'.repeat(delimiterLength);
    const closingIndex = content.indexOf(delimiter, backtickIndex + delimiterLength);

    if (closingIndex === -1) {
      result += content.slice(backtickIndex);
      break;
    }

    result += content.slice(backtickIndex, closingIndex + delimiterLength);
    cursor = closingIndex + delimiterLength;
  }

  return result;
}

export function normalizeLatexMathDelimiters(content: string): string {
  const normalized = content.replace(/\r\n?/gu, '\n');
  const lines = normalized.split('\n');
  const output: string[] = [];
  const textBuffer: string[] = [];
  let activeFence: { char: '`' | '~'; length: number } | null = null;
  let fenceBuffer: string[] | null = null;

  const flushTextBuffer = () => {
    if (textBuffer.length === 0) {
      return;
    }
    output.push(normalizeLatexMathDelimitersInText(textBuffer.join('\n')));
    textBuffer.length = 0;
  };

  for (const rawLine of lines) {
    const trimmedLine = rawLine.trimStart();
    const fenceMatch = trimmedLine.match(/^([`~]{3,})/u);

    if (activeFence) {
      fenceBuffer?.push(rawLine);

      if (fenceMatch) {
        const marker = fenceMatch[1];
        const fenceChar = marker[0] as '`' | '~';

        if (fenceChar === activeFence.char && marker.length >= activeFence.length) {
          output.push((fenceBuffer ?? []).join('\n'));
          activeFence = null;
          fenceBuffer = null;
        }
      }
      continue;
    }

    if (fenceMatch) {
      const marker = fenceMatch[1];
      flushTextBuffer();
      activeFence = { char: marker[0] as '`' | '~', length: marker.length };
      fenceBuffer = [rawLine];
      continue;
    }

    textBuffer.push(rawLine);
  }

  flushTextBuffer();
  if (fenceBuffer) {
    output.push(fenceBuffer.join('\n'));
  }

  return output.join('\n');
}

export function normalizePreservedMarkdown(content: string): string {
  const normalized = content.replace(/\r\n?/gu, '\n');
  const lines = normalized.split('\n');
  const output: string[] = [];

  let activeFence: { char: '`' | '~'; length: number } | null = null;
  let blankRun = 0;

  for (const rawLine of lines) {
    const trimmedLine = rawLine.trimStart();
    const fenceMatch = trimmedLine.match(/^([`~]{3,})/u);

    if (fenceMatch) {
      const marker = fenceMatch[1];
      const fenceChar = marker[0] as '`' | '~';

      if (!activeFence) {
        activeFence = { char: fenceChar, length: marker.length };
      } else if (activeFence.char === fenceChar && marker.length >= activeFence.length) {
        activeFence = null;
      }

      blankRun = 0;
      output.push(rawLine);
      continue;
    }

    if (activeFence) {
      output.push(rawLine);
      continue;
    }

    if (!rawLine.trim()) {
      blankRun += 1;
      if (blankRun <= 1) {
        output.push('');
      }
      continue;
    }

    blankRun = 0;
    output.push(rawLine);
  }

  return output.join('\n');
}

function canStartListFromPreviousLine(previousLine: string | null): boolean {
  if (previousLine === null) {
    return true;
  }

  const trimmedPreviousLine = previousLine.trim();
  if (!trimmedPreviousLine) {
    return true;
  }

  return (
    /[:：]$/u.test(trimmedPreviousLine)
    || /^>\s/u.test(trimmedPreviousLine)
    || /^([-*+])\s/u.test(trimmedPreviousLine)
    || /^\d+[.)]\s/u.test(trimmedPreviousLine)
  );
}

function normalizeLooseMarkdownLine(rawLine: string, previousLine: string | null): string {
  let line = rawLine;

  line = line.replace(/^(#{1,6})(\S.*)$/u, '$1 $2');
  line = line.replace(/^(\s{0,3}>)(\S.*)$/u, '$1 $2');

  if (!canStartListFromPreviousLine(previousLine)) {
    return line;
  }

  line = line.replace(/^(\s{0,3})([-*+])(\S.*)$/u, (match, indent: string, marker: string, content: string) => {
    const trimmed = match.trim();
    if (/^([-*+]){3,}$/u.test(trimmed)) {
      return match;
    }
    return `${indent}${marker} ${content}`;
  });

  line = line.replace(/^(\s{0,3})(\d{1,2}[.)])(\S.*)$/u, (match, indent: string, marker: string, content: string) => {
    if (/^\d/u.test(content)) {
      return match;
    }
    return `${indent}${marker} ${content}`;
  });

  line = line.replace(/^(\s{0,3}[-*+]\s+\[[ xX]\])(\S.*)$/u, '$1 $2');
  line = line.replace(/^(\s{0,3}\d{1,2}[.)]\s+\[[ xX]\])(\S.*)$/u, '$1 $2');

  return line;
}

function parseTableCells(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed || !trimmed.includes('|')) {
    return null;
  }

  if (
    trimmed.startsWith('>')
    || /^([-*+])\s/u.test(trimmed)
    || /^\d+[.)]\s/u.test(trimmed)
  ) {
    return null;
  }

  if (/^-/u.test(trimmed) && !trimmed.includes(' | ') && !trimmed.startsWith('|')) {
    return null;
  }

  const withoutOuterPipes = trimmed.replace(/^\|/u, '').replace(/\|$/u, '');
  const cells = withoutOuterPipes.split('|').map((cell) => cell.trim());
  if (cells.length < 2) {
    return null;
  }

  const nonEmptyCells = cells.filter((cell) => cell.length > 0);
  if (nonEmptyCells.length < 2) {
    return null;
  }

  return cells;
}

function isTableSeparatorRow(line: string): boolean {
  const cells = parseTableCells(line);
  return Boolean(
    cells
    && cells.length >= 2
    && cells.every((cell) => /^:?-{3,}:?$/u.test(cell)),
  );
}

function buildStreamingTableSeparatorRow(line: string, columnCount: number): string {
  const indent = line.match(/^\s*/u)?.[0] ?? '';
  const trimmed = line.trim();
  const hasLeadingPipe = trimmed.startsWith('|');
  const hasTrailingPipe = trimmed.endsWith('|');
  const body = Array.from({ length: columnCount }, () => ' --- ').join('|');

  return `${indent}${hasLeadingPipe ? '|' : ''}${body}${hasTrailingPipe ? '|' : ''}`;
}

function normalizeStreamingTableBlocks(lines: string[]): string[] {
  const result: string[] = [];

  for (let index = 0; index < lines.length;) {
    const headerCells = parseTableCells(lines[index]);
    if (!headerCells || isTableSeparatorRow(lines[index])) {
      result.push(lines[index]);
      index += 1;
      continue;
    }

    const block: string[] = [lines[index]];
    let cursor = index + 1;

    while (cursor < lines.length) {
      const currentCells = parseTableCells(lines[cursor]);
      if (!currentCells || currentCells.length !== headerCells.length) {
        break;
      }
      block.push(lines[cursor]);
      cursor += 1;
    }

    if (block.length >= 2 && !isTableSeparatorRow(block[1])) {
      result.push(block[0]);
      result.push(buildStreamingTableSeparatorRow(block[0], headerCells.length));
      result.push(...block.slice(1));
      index = cursor;
      continue;
    }

    result.push(lines[index]);
    index += 1;
  }

  return result;
}

export function normalizeMarkdownForDisplay(
  content: string,
  isStreaming = false,
  showCaret = false,
): string {
  const normalized = content.replace(/\r\n?/gu, '\n');
  const lines = normalized.split('\n');
  const output: string[] = [];

  let activeFence: { char: '`' | '~'; length: number } | null = null;
  let previousLine: string | null = null;

  for (const rawLine of lines) {
    const trimmedLine = rawLine.trimStart();
    const fenceMatch = trimmedLine.match(/^([`~]{3,})/u);

    if (fenceMatch) {
      const marker = fenceMatch[1];
      const fenceChar = marker[0] as '`' | '~';

      if (!activeFence) {
        activeFence = { char: fenceChar, length: marker.length };
      } else if (activeFence.char === fenceChar && marker.length >= activeFence.length) {
        activeFence = null;
      }

      output.push(rawLine);
      previousLine = rawLine;
      continue;
    }

    if (activeFence) {
      output.push(rawLine);
      previousLine = rawLine;
      continue;
    }

    const normalizedLine = normalizeLooseMarkdownLine(rawLine, previousLine);
    output.push(normalizedLine);
    previousLine = normalizedLine;
  }

  const normalizedLines = isStreaming
    ? normalizeStreamingTableBlocks(output)
    : output;

  const hasOpenCodeFence = Boolean(isStreaming && activeFence);

  if (hasOpenCodeFence && showCaret) {
    const lastIndex = normalizedLines.length - 1;
    const lastLine = normalizedLines[lastIndex] ?? '';
    const isFenceLine = /^([`~]{3,})/u.test(lastLine.trimStart());

    if (lastIndex >= 0 && !isFenceLine) {
      normalizedLines[lastIndex] = `${lastLine}${STREAMING_CODE_CARET_TOKEN}`;
    } else {
      normalizedLines.push(STREAMING_CODE_CARET_TOKEN);
    }
  }

  if (hasOpenCodeFence && activeFence) {
    normalizedLines.push(activeFence.char.repeat(activeFence.length));
  }

  const normalizedContent = normalizedLines.join('\n');
  if (showCaret && !hasOpenCodeFence) {
    return appendStreamingCaret(normalizedContent);
  }
  return normalizedContent;
}
