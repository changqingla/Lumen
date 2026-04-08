import { useMemo } from 'react';
import OptimizedMarkdown from '@/shared/components/OptimizedMarkdown';
import styles from './ThinkingTrace.module.css';

type TextBlock = {
  type: 'text';
  content: string;
};

type ToolEntry = {
  name: string;
  args?: string;
  result?: string;
  resultType: 'success' | 'error' | 'running';
};

type ToolBlock = {
  type: 'tool';
  entries: ToolEntry[];
};

type ParsedBlock = TextBlock | ToolBlock;

interface ThinkingTraceProps {
  content: string;
}

const TOOL_CALL_HEADER_RE = /^(?:🔧\s*)?工具调用[:：]$/;
const TOOL_RESULT_HEADER_RE = /^(?:📋\s*)?(.+?)\s*结果[:：]$/;
const EXECUTING_RE = /^(?:⏳\s*)?执行中.*$/;
const ITERATION_RE = /^(?:-{2,}\s*)?(?:🔄\s*)?迭代/i;
const TOOL_NAME_PATTERN = "[A-Za-z][A-Za-z0-9_.-]{0,63}";
const BULLET_CALL_RE = new RegExp(`^[-*]\\s+\`?(${TOOL_NAME_PATTERN})\`?\\s*$`);
const INLINE_ARGS_CALL_RE = new RegExp(`^(?:[-*]\\s+)?\`?(${TOOL_NAME_PATTERN})\`?\\s+参数[:：]\\s*(.*)$`);
const ARGS_PREFIX_RE = /^\s*参数[:：]\s*(.*)$/;

function normalizeLineForMatch(line: string): string {
  return line.replace(/\*\*/g, '').trim();
}

function compactText(value: string): string {
  return value
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

function compactCode(value: string): string {
  return value
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
}

function normalizeToolOutput(value: string): string {
  const raw = compactCode(value)
    .replace(/^```[a-zA-Z]*\n?/, '')
    .replace(/\n?```$/, '')
    .trim();

  if (!raw) return '';

  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function extractToolNameFromResultHeader(line: string): string | null {
  const normalized = normalizeLineForMatch(line);
  const match = normalized.match(TOOL_RESULT_HEADER_RE);
  return match ? match[1].trim().replace(/^`|`$/g, '') : null;
}

function detectResultType(result?: string): ToolEntry['resultType'] {
  if (!result) return 'running';
  if (result.includes('❌') || result.includes('"success": false')) return 'error';
  if (result.includes('"success": true')) return 'success';
  return 'success';
}

function isToolHeaderLine(line: string): boolean {
  return TOOL_CALL_HEADER_RE.test(normalizeLineForMatch(line));
}

function isBoundaryLine(line: string): boolean {
  const normalized = normalizeLineForMatch(line);
  return isToolHeaderLine(line)
    || EXECUTING_RE.test(normalized)
    || ITERATION_RE.test(normalized)
    || extractToolNameFromResultHeader(line) !== null;
}

function isToolCallLine(line: string): boolean {
  const normalized = normalizeLineForMatch(line);
  return BULLET_CALL_RE.test(normalized) || INLINE_ARGS_CALL_RE.test(normalized);
}

function isToolSyntaxStartLine(line: string): boolean {
  const normalized = normalizeLineForMatch(line);
  return isToolHeaderLine(line)
    || extractToolNameFromResultHeader(line) !== null
    || isToolCallLine(line)
    || EXECUTING_RE.test(normalized);
}

function readResultContent(lines: string[], startIndex: number): { content: string; nextIndex: number } {
  let i = startIndex;
  while (i < lines.length && lines[i].trim() === '') i += 1;

  if (i >= lines.length) {
    return { content: '', nextIndex: i };
  }

  if (lines[i].trim().startsWith('```')) {
    i += 1;
    const codeLines: string[] = [];

    while (i < lines.length) {
      const line = lines[i];
      if (line.trim().startsWith('```')) {
        i += 1;
        break;
      }
      codeLines.push(line);
      i += 1;
    }

    while (i < lines.length && lines[i].trim() === '') i += 1;

    return {
      content: normalizeToolOutput(codeLines.join('\n')),
      nextIndex: i,
    };
  }

  const block: string[] = [];
  while (i < lines.length) {
    const line = lines[i];

    if (isBoundaryLine(line) || isToolCallLine(line)) break;

    if (line.trim() === '' && block.length > 0) {
      const next = lines[i + 1];
      if (!next || isBoundaryLine(next) || isToolCallLine(next)) {
        i += 1;
        break;
      }
    }

    block.push(line);
    i += 1;
  }

  while (i < lines.length && lines[i].trim() === '') i += 1;

  return {
    content: normalizeToolOutput(block.join('\n')),
    nextIndex: i,
  };
}

function readArgsContent(lines: string[], startIndex: number, firstSegment = ''): { args: string; nextIndex: number } {
  let i = startIndex;
  const segments: string[] = [];

  if (firstSegment.trim()) {
    segments.push(firstSegment);
  }

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (isBoundaryLine(line) || isToolCallLine(line)) break;

    if (trimmed.startsWith('```')) {
      i += 1;
      while (i < lines.length) {
        const codeLine = lines[i];
        if (codeLine.trim().startsWith('```')) {
          i += 1;
          break;
        }
        segments.push(codeLine);
        i += 1;
      }
      continue;
    }

    if (!trimmed) {
      if (segments.length > 0) segments.push('');
      i += 1;
      continue;
    }

    if (segments.length === 0) {
      const prefixed = line.match(ARGS_PREFIX_RE);
      if (prefixed) {
        if (prefixed[1].trim()) {
          segments.push(prefixed[1]);
        }
        i += 1;
        continue;
      }
    }

    const looksLikePayload = /^\s/.test(line)
      || trimmed.startsWith('{')
      || trimmed.startsWith('[')
      || trimmed.startsWith('"')
      || trimmed.startsWith('}')
      || trimmed.startsWith(']');

    if (!looksLikePayload) break;

    segments.push(line);
    i += 1;
  }

  return {
    args: normalizeToolOutput(segments.join('\n')),
    nextIndex: i,
  };
}

function parseToolBlock(lines: string[], startIndex: number): { block: ToolBlock; nextIndex: number } | null {
  let i = startIndex;
  if (isToolHeaderLine(lines[i])) {
    i += 1;
  }
  const entries: ToolEntry[] = [];
  const results: Array<{ name: string; content: string }> = [];
  let sawToolSyntax = false;

  while (i < lines.length) {
    const line = lines[i];

    const normalizedLine = normalizeLineForMatch(line);

    if (i !== startIndex && isToolHeaderLine(line)) break;
    if (ITERATION_RE.test(normalizedLine)) break;

    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (EXECUTING_RE.test(normalizedLine)) {
      sawToolSyntax = true;
      i += 1;
      continue;
    }

    const resultToolName = extractToolNameFromResultHeader(line);
    if (resultToolName) {
      sawToolSyntax = true;
      const { content, nextIndex } = readResultContent(lines, i + 1);
      results.push({ name: resultToolName, content });
      i = nextIndex;
      continue;
    }

    const bulletCall = normalizedLine.match(BULLET_CALL_RE);
    if (bulletCall) {
      sawToolSyntax = true;
      const name = bulletCall[1].trim();
      let nextIndex = i + 1;
      let args = '';

      const maybeArgsLine = lines[nextIndex] || '';
      const prefixed = normalizeLineForMatch(maybeArgsLine).match(ARGS_PREFIX_RE);
      if (prefixed) {
        const parsedArgs = readArgsContent(lines, nextIndex + 1, prefixed[1] || '');
        args = parsedArgs.args;
        nextIndex = parsedArgs.nextIndex;
      }

      entries.push({ name, args, resultType: 'running' });
      i = nextIndex;
      continue;
    }

    const inlineCall = normalizedLine.match(INLINE_ARGS_CALL_RE);
    if (inlineCall) {
      sawToolSyntax = true;
      const name = inlineCall[1].trim();
      const parsedArgs = readArgsContent(lines, i + 1, inlineCall[2] || '');
      entries.push({ name, args: parsedArgs.args, resultType: 'running' });
      i = parsedArgs.nextIndex;
      continue;
    }

    break;
  }

  if (!sawToolSyntax) return null;

  for (const result of results) {
    const target = entries.find((entry) => entry.name === result.name && !entry.result);
    if (target) {
      target.result = result.content;
      target.resultType = detectResultType(result.content);
      continue;
    }

    entries.push({
      name: result.name,
      result: result.content,
      resultType: detectResultType(result.content),
    });
  }

  if (entries.length === 0) return null;

  return {
    block: { type: 'tool', entries },
    nextIndex: i,
  };
}

function parseThinking(rawContent: string): ParsedBlock[] {
  const content = rawContent.replace(/\r\n/g, '\n');
  const lines = content.split('\n');
  const blocks: ParsedBlock[] = [];
  const textBuffer: string[] = [];

  const flushText = () => {
    const text = compactText(textBuffer.join('\n'))
      .replace(/^\s*(?:-{2,}\s*)?(?:🔄\s*)?迭代\s*\d*\s*(?:-{2,})?\s*$/gim, '')
      .replace(/^\s*-{2,}\s*$/gm, '')
      .trim();

    textBuffer.length = 0;
    if (text) {
      blocks.push({ type: 'text', content: text });
    }
  };

  let i = 0;
  while (i < lines.length) {
    if (isToolSyntaxStartLine(lines[i])) {
      const parsed = parseToolBlock(lines, i);
      if (parsed) {
        flushText();
        blocks.push(parsed.block);
        i = parsed.nextIndex;
        continue;
      }
    }

    textBuffer.push(lines[i]);
    i += 1;
  }

  flushText();
  return blocks;
}

function getStatusLabel(entry: ToolEntry): string {
  if (entry.resultType === 'error') return '失败';
  if (entry.result) return '完成';
  return '执行中';
}

export default function ThinkingTrace({ content }: ThinkingTraceProps) {
  const blocks = useMemo(() => parseThinking(content), [content]);

  return (
    <div className={styles.trace}>
      {blocks.map((block, index) => {
        if (block.type === 'text') {
          return (
            <div key={`text-${index}`} className={styles.textBlock}>
              <OptimizedMarkdown className={styles.textMarkdown}>{block.content}</OptimizedMarkdown>
            </div>
          );
        }

        return (
          <details key={`tool-${index}`} className={styles.toolGroup} open>
            <summary className={styles.toolGroupSummary}>
              <span className={styles.toolGroupTitle}>工具调用轨迹</span>
              <span className={styles.toolGroupMeta}>{block.entries.length} 步</span>
            </summary>

            <div className={styles.toolList}>
              {block.entries.map((entry, entryIndex) => (
                <details key={`${entry.name}-${entryIndex}`} className={styles.toolItem}>
                  <summary className={styles.toolItemHeader}>
                    <div className={styles.toolTitle}>
                      <span className={styles.toolName}>{entry.name}</span>
                    </div>
                    <span className={`${styles.toolState} ${styles[`state_${entry.resultType}`]}`}>{getStatusLabel(entry)}</span>
                  </summary>

                  <div className={styles.toolBody}>
                    <details className={styles.foldPanel}>
                      <summary className={styles.foldSummary}>参数</summary>
                      <pre className={styles.codeLike}>{entry.args || '(无参数)'}</pre>
                    </details>

                    <details className={styles.foldPanel}>
                      <summary className={styles.foldSummary}>结果</summary>
                      <pre className={styles.codeLike}>{entry.result || '(等待执行结果)'}</pre>
                    </details>
                  </div>
                </details>
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}
