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
import {
  normalizeLatexMathDelimiters,
  normalizeMarkdownForDisplay,
  normalizePreservedMarkdown,
  STREAMING_CODE_CARET_TOKEN,
} from '@/shared/lib/markdownNormalize';
import { copyTextToClipboard } from '@/shared/utils/clipboard';
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

const markdownSanitizeSchema: RehypeSanitizeOptions = {
  ...defaultSchema,
  protocols: {
    ...defaultSchema.protocols,
    src: [
      ...(defaultSchema.protocols?.src || []),
      'data',
    ],
  },
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
  const [hasLoadError, setHasLoadError] = useState(false);

  if (!normalizedSrc) {
    return null;
  }

  const label = resolveImageLabel(normalizedSrc, alt);
  const canOpenImage = /^(?:https?:|data:image\/|\/)/iu.test(normalizedSrc);

  const imageActions = (
    <div className="markdown-image-actions">
      {hasLoadError ? null : (
        <button
          type="button"
          className="markdown-image-button"
          onClick={() => setIsPreviewEnabled(true)}
        >
          加载预览
        </button>
      )}
      {canOpenImage ? (
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
  );

  if (!isPreviewEnabled) {
    return (
      <div className="markdown-image-shell">
        <div className="markdown-image-card">
          <div className="markdown-image-meta">
            <div className="markdown-image-title">{label}</div>
            <div className="markdown-image-subtitle">图片预览默认按需加载，避免聊天页面卡顿</div>
          </div>
          {imageActions}
        </div>
      </div>
    );
  }

  if (hasLoadError) {
    return (
      <div className="markdown-image-shell">
        <div className="markdown-image-card markdown-image-card-error">
          <div className="markdown-image-meta">
            <div className="markdown-image-title">{label}</div>
            <div className="markdown-image-subtitle">图片资源不可用</div>
          </div>
          {canOpenImage ? (
            <div className="markdown-image-actions">
              <a
                href={normalizedSrc}
                target="_blank"
                rel="noopener noreferrer"
                className="markdown-image-link"
              >
                打开原图
              </a>
            </div>
          ) : null}
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
        onError={() => setHasLoadError(true)}
      />
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
