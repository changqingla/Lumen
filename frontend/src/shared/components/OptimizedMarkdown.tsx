import {
  type ComponentPropsWithoutRef,
  type ReactNode,
  isValidElement,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Check, Copy } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema, type Options as RehypeSanitizeOptions } from 'rehype-sanitize';
import 'katex/dist/katex.min.css'; // KaTeX样式
import '../../styles/markdown.css';

interface OptimizedMarkdownProps {
  children: string;
  className?: string;
  isStreaming?: boolean;
  showCaret?: boolean;
  preserveRaw?: boolean;
  deferImages?: boolean;
}

const STREAMING_CARET_HTML = '<span class="streaming-inline-caret" aria-hidden="true">▌</span>';
const STREAMING_CODE_CARET_TOKEN = '[[__STREAMING_CODE_CARET__]]';

const markdownSanitizeSchema: RehypeSanitizeOptions = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [
      ...(defaultSchema.attributes?.span || []),
      ['className', 'streaming-inline-caret'],
      ['className', 'streaming-code-caret'],
    ],
    a: [
      ...(defaultSchema.attributes?.a || []),
      ['rel', 'nofollow', 'noopener', 'noreferrer'],
      ['target', '_blank'],
    ],
    code: [
      ...(defaultSchema.attributes?.code || []),
      'dataCopyContent',
    ],
  },
};

function getTextContent(children: ReactNode): string {
  if (Array.isArray(children)) {
    return children.map((item) => getTextContent(item)).join('');
  }
  if (isValidElement<{ children?: ReactNode }>(children)) {
    return getTextContent(children.props.children);
  }
  if (typeof children === 'string' || typeof children === 'number') {
    return String(children);
  }
  return '';
}

function findCodeBlockData(children: ReactNode): { copyText: string } | null {
  if (Array.isArray(children)) {
    for (const child of children) {
      const result = findCodeBlockData(child);
      if (result) {
        return result;
      }
    }
    return null;
  }

  if (isValidElement<Record<string, unknown>>(children)) {
    const { children: nestedChildren } = children.props;
    const copyText = children.props['data-copy-content'];
    if (typeof copyText === 'string') {
      return { copyText };
    }
    return findCodeBlockData(nestedChildren as ReactNode);
  }

  return null;
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.setAttribute('readonly', 'true');
  textArea.style.position = 'fixed';
  textArea.style.left = '-999999px';
  textArea.style.top = '-999999px';
  textArea.style.opacity = '0';

  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();

  try {
    if (!document.execCommand('copy')) {
      throw new Error('document.execCommand("copy") returned false');
    }
  } finally {
    textArea.remove();
  }
}

interface CopyableCodeBlockProps extends ComponentPropsWithoutRef<'pre'> {
  children?: ReactNode;
  copyText: string;
}

function CopyableCodeBlock({
  children,
  copyText,
  ...props
}: CopyableCodeBlockProps) {
  const [isCopied, setIsCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);
  const isDisabled = copyText.length === 0;

  useEffect(() => () => {
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current);
    }
  }, []);

  async function handleCopy() {
    if (isDisabled) {
      return;
    }

    try {
      await copyTextToClipboard(copyText);
      setIsCopied(true);

      if (resetTimerRef.current !== null) {
        window.clearTimeout(resetTimerRef.current);
      }

      resetTimerRef.current = window.setTimeout(() => {
        setIsCopied(false);
        resetTimerRef.current = null;
      }, 2000);
    } catch (error) {
      console.error('Failed to copy code block:', error);
    }
  }

  return (
    <div className="code-block-shell">
      <button
        type="button"
        className={`code-block-copy-button ${isCopied ? 'is-copied' : ''}`}
        onClick={handleCopy}
        disabled={isDisabled}
        aria-label={isCopied ? '代码已复制' : '复制代码'}
        title={isCopied ? '已复制' : '复制代码'}
      >
        {isCopied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      </button>
      <pre {...props}>{children}</pre>
    </div>
  );
}

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

  // 代码块闭合 fence 后单独起一行，避免破坏 fence 语法。
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

function normalizeLatexMathDelimiters(content: string): string {
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

function normalizePreservedMarkdown(content: string): string {
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

  // 容忍 LLM 输出 `##标题` / `###5.1小节` 这类少空格的标题写法。
  line = line.replace(/^(#{1,6})(\S.*)$/u, '$1 $2');

  // 容忍 `>引用` 这种 blockquote 少空格写法。
  line = line.replace(/^(\s{0,3}>)(\S.*)$/u, '$1 $2');

  if (!canStartListFromPreviousLine(previousLine)) {
    return line;
  }

  // 容忍 `-列表项` / `*列表项` 这类少空格的无序列表写法。
  line = line.replace(/^(\s{0,3})([-*+])(\S.*)$/u, (match, indent: string, marker: string, content: string) => {
    const trimmed = match.trim();
    if (/^([-*+]){3,}$/u.test(trimmed)) {
      return match;
    }
    return `${indent}${marker} ${content}`;
  });

  // 容忍 `1.列表项` / `2)列表项` 这类少空格的有序列表写法。
  line = line.replace(/^(\s{0,3})(\d{1,2}[.)])(\S.*)$/u, (match, indent: string, marker: string, content: string) => {
    if (/^\d/u.test(content)) {
      return match;
    }
    return `${indent}${marker} ${content}`;
  });

  // 容忍 task list 勾选框后漏空格。
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

function normalizeMarkdownForDisplay(
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
    // 流式阶段如果代码块尚未闭合，临时补一个闭合 fence，避免后续内容整段被吞成 code block。
    normalizedLines.push(activeFence.char.repeat(activeFence.length));
  }

  const normalizedContent = normalizedLines.join('\n');
  if (showCaret && !hasOpenCodeFence) {
    return appendStreamingCaret(normalizedContent);
  }
  return normalizedContent;
}

interface MarkdownImageProps extends ComponentPropsWithoutRef<'img'> {
  deferImages?: boolean;
}

function resolveImageLabel(src?: string, alt?: string): string {
  const normalizedAlt = String(alt || '').trim();
  if (normalizedAlt) {
    return normalizedAlt;
  }
  const normalizedSrc = String(src || '').trim();
  if (!normalizedSrc) {
    return '图片预览';
  }
  if (normalizedSrc.startsWith('data:')) {
    return '内联图片';
  }
  try {
    const parsed = new URL(normalizedSrc);
    const filename = parsed.pathname.split('/').filter(Boolean).pop();
    if (filename) {
      return decodeURIComponent(filename);
    }
    return parsed.hostname || '远程图片';
  } catch {
    const filename = normalizedSrc.split('/').filter(Boolean).pop();
    return filename || '图片预览';
  }
}

function MarkdownImage({ src, alt, deferImages = false, ...props }: MarkdownImageProps) {
  const normalizedSrc = String(src || '').trim();
  const [isPreviewEnabled, setIsPreviewEnabled] = useState(!deferImages);

  if (!normalizedSrc) {
    return null;
  }

  const label = resolveImageLabel(normalizedSrc, alt);
  const isExternal = /^https?:\/\//iu.test(normalizedSrc);

  if (!isPreviewEnabled) {
    return (
      <div className="markdown-image-shell">
        <div className="markdown-image-card">
          <div className="markdown-image-meta">
            <div className="markdown-image-title">{label}</div>
            <div className="markdown-image-subtitle">图片预览默认按需加载，避免聊天页面卡顿</div>
          </div>
          <div className="markdown-image-actions">
            <button
              type="button"
              className="markdown-image-button"
              onClick={() => setIsPreviewEnabled(true)}
            >
              加载预览
            </button>
            {isExternal ? (
              <a
                href={normalizedSrc}
                target="_blank"
                rel="noopener noreferrer"
                className="markdown-image-link"
              >
                打开原图
              </a>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="markdown-image-shell">
      <img
        {...props}
        src={normalizedSrc}
        alt={alt || label}
        loading="lazy"
        decoding="async"
        className="markdown-inline-image"
      />
      {isExternal ? (
        <div className="markdown-image-caption">
          <a
            href={normalizedSrc}
            target="_blank"
            rel="noopener noreferrer"
            className="markdown-image-link"
          >
            在新窗口打开原图
          </a>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 紧凑型Markdown渲染组件
 *
 * 设计理念：
 * 1. 做极少量的语法容错，提升 LLM 输出渲染稳定性
 * 2. 通过自定义组件强制所有元素 margin:0
 * 3. 用CSS选择器精确控制相邻元素间距
 */
export default function OptimizedMarkdown({
  children,
  className,
  isStreaming = false,
  showCaret = false,
  preserveRaw = false,
  deferImages = false,
}: OptimizedMarkdownProps) {
  const normalizedContent = useMemo(() => {
    const normalized = preserveRaw
      ? normalizePreservedMarkdown(children)
      : isStreaming
      ? normalizeMarkdownForDisplay(children, isStreaming, showCaret)
      : children;

    // LLM 经常输出 \(...\) / \[...\]，先转换成 remark-math 可识别的定界符。
    return normalizeLatexMathDelimiters(normalized);
  }, [children, isStreaming, showCaret, preserveRaw]);

  return (
    <div className={`markdown-content ${className || ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, markdownSanitizeSchema], rehypeKatex]}
        components={{
          pre: ({ children, ...props }) => {
            const blockData = findCodeBlockData(children);
            return (
              <CopyableCodeBlock
                {...props}
                copyText={blockData?.copyText ?? getTextContent(children)}
              >
                {children}
              </CopyableCodeBlock>
            );
          },
          // 表格：增加水平滚动容器，防止撑破布局
          table: ({ children, ...props }) => (
            <div className="table-wrapper">
              <table {...props}>{children}</table>
            </div>
          ),
          code: ({ children, className, ...props }) => {
            const textContent = getTextContent(children);
            const hasStreamingCodeCaret = textContent.includes(STREAMING_CODE_CARET_TOKEN);
            const normalizedCode = textContent.replace(STREAMING_CODE_CARET_TOKEN, '');

            if (!hasStreamingCodeCaret) {
              return (
                <code
                  {...props}
                  className={className}
                  data-copy-content={normalizedCode}
                >
                  {children}
                </code>
              );
            }

            return (
              <code
                {...props}
                className={className}
                data-copy-content={normalizedCode}
              >
                {normalizedCode}
                <span className="streaming-code-caret" aria-hidden="true">▌</span>
              </code>
            );
          },
          img: ({ src, alt, ...props }) => (
            <MarkdownImage
              {...props}
              src={src}
              alt={alt}
              deferImages={deferImages}
            />
          ),
        }}
      >
        {normalizedContent}
      </ReactMarkdown>
    </div>
  );
}
