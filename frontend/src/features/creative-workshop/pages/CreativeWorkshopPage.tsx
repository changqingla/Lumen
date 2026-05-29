import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Check,
  CirclePlus,
  Download,
  FileText,
  ImagePlus,
  Loader2,
  Menu,
  Share,
  Upload,
  WandSparkles,
  X,
} from 'lucide-react';

import Sidebar from '@/app/components/Sidebar/Sidebar';
import textToImagePreview from '@/assets/creative-workshop/t2i.webp';
import translationPreview from '@/assets/creative-workshop/translation.webp';
import {
  api,
  type ChatModelOption,
  type CreativeImageOutputFormat,
  type CreativeImageQuality,
  type CreativeImageSize,
  type KnowledgeBaseListItem,
  type PaperTranslationStatus,
} from '@/shared/api/client';
import OptimizedMarkdown from '@/shared/components/OptimizedMarkdown';
import PDFViewer from '@/shared/components/PDFViewer/PDFViewer';
import { uploadKnowledgeDocuments } from '@/features/knowledge/utils/upload';
import { useGuestMode } from '@/shared/hooks/useGuestMode';
import { useToast } from '@/shared/hooks/useToast';
import { useChatSessions } from '@/features/chat/hooks/useChatSessions';
import { getErrorMessage } from '@/shared/utils/errorMessage';
import {
  safeLocalStorageGet,
  safeLocalStorageRemove,
  safeLocalStorageSet,
} from '@/shared/utils/localStorage';
import ChatModelSelector from '@/features/chat/components/ChatModelSelector/ChatModelSelector';
import { resolvePreferredModelName, useChatModels } from '@/features/chat/hooks/useChatModels';

import styles from './CreativeWorkshopPage.module.css';

type WorkshopView = 'home' | 'image2' | 'paper-translation';
type GenerationPhase = 'idle' | 'queued' | 'generating' | 'rendering' | 'done' | 'error';
type TranslationPhase = 'idle' | 'ready' | 'processing' | 'done' | 'error';
type ImageGenerationSnapshot = {
  prompt: string;
  size: CreativeImageSize;
  quality: CreativeImageQuality;
  outputFormat: CreativeImageOutputFormat;
  outputCompression: number;
  phase: GenerationPhase;
  imageDataUrl: string;
  errorMessage: string;
};

const TRANSLATION_PROCESSING_STATUSES: PaperTranslationStatus[] = ['queued', 'converting', 'translating'];
const PAPER_TRANSLATION_ACTIVE_TASK_KEY = 'creative-workshop:paper-translation:active-task';
const CREATIVE_WORKSHOP_MODEL_KEY = 'creative-workshop:default-model';
const DEFAULT_KNOWLEDGE_BASE_NAME = '我的知识库';
const imageGenerationListeners = new Set<(snapshot: ImageGenerationSnapshot) => void>();
let imageGenerationSnapshot: ImageGenerationSnapshot = {
  prompt: '',
  size: '1024x1024',
  quality: 'medium',
  outputFormat: 'jpeg',
  outputCompression: 80,
  phase: 'idle',
  imageDataUrl: '',
  errorMessage: '',
};
let imageGenerationRequestId = 0;
let imageGenerationPhaseTimer: number | null = null;

const SIZE_OPTIONS: Array<{ value: CreativeImageSize; label: string; hint: string }> = [
  { value: '1024x1024', label: '1:1', hint: '1024 x 1024' },
  { value: '1536x1024', label: '横图', hint: '1536 x 1024' },
  { value: '1024x1536', label: '竖图', hint: '1024 x 1536' },
  { value: '2048x2048', label: '高清方图', hint: '2048 x 2048' },
  { value: '3840x2160', label: '16:9', hint: '3840 x 2160' },
  { value: '2160x3840', label: '9:16', hint: '2160 x 3840' },
];

const QUALITY_OPTIONS: Array<{ value: CreativeImageQuality; label: string }> = [
  { value: 'low', label: '快速' },
  { value: 'medium', label: '均衡' },
  { value: 'high', label: '精细' },
];

const FORMAT_OPTIONS: Array<{ value: CreativeImageOutputFormat; label: string }> = [
  { value: 'jpeg', label: 'JPEG' },
  { value: 'png', label: 'PNG' },
  { value: 'webp', label: 'WebP' },
];

const EXAMPLE_PROMPTS = [
  'A minimal app icon for a note-taking product, clean vector-like style, white background',
  'A cinematic product photo of a transparent glass perfume bottle on black marble, soft studio lighting',
  'A calm reading room with warm morning light, modern Chinese interior design, realistic photography',
];

const PHASE_LABEL: Record<GenerationPhase, string> = {
  idle: '等待输入',
  queued: '请求已发送',
  generating: '模型生成中',
  rendering: '解码渲染中',
  done: '生成完成',
  error: '生成失败',
};

const PHASE_PROGRESS: Record<GenerationPhase, number> = {
  idle: 0,
  queued: 18,
  generating: 68,
  rendering: 92,
  done: 100,
  error: 100,
};

const WORKSHOP_TOOLS: Array<{
  view: WorkshopView;
  title: string;
  eyebrow: string;
  description: string;
  meta: string[];
  imageSrc: string;
  imageAlt: string;
  status: string;
  action: string;
  accent: 'image' | 'paper';
}> = [
  {
    view: 'image2',
    title: '文生图',
    eyebrow: 'Image2',
    description: '把 prompt 转换为可下载图片，适合灵感稿、封面、图标和视觉概念探索。',
    meta: ['多尺寸', '质量可控', '支持 PNG / JPEG / WebP'],
    imageSrc: textToImagePreview,
    imageAlt: '文生图功能示意图',
    status: '视觉生成',
    action: '开始生图',
    accent: 'image',
  },
  {
    view: 'paper-translation',
    title: '论文翻译',
    eyebrow: 'Paper Translation',
    description: '上传 PDF 后预览原文，生成中文 Markdown 译文，并支持下载译文或导出 PDF。',
    meta: ['PDF 原文预览', 'Markdown 译文', '导出 PDF'],
    imageSrc: translationPreview,
    imageAlt: '论文翻译功能示意图',
    status: '学术翻译',
    action: '开始翻译',
    accent: 'paper',
  },
];

const extensionForFormat = (format: CreativeImageOutputFormat) => (format === 'jpeg' ? 'jpg' : format);

const dataUrlToBlob = async (dataUrl: string) => {
  const response = await fetch(dataUrl);
  return response.blob();
};

const triggerBlobDownload = (blob: Blob, fileName: string) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const publishImageGenerationSnapshot = (updates: Partial<ImageGenerationSnapshot>) => {
  imageGenerationSnapshot = { ...imageGenerationSnapshot, ...updates };
  imageGenerationListeners.forEach((listener) => listener(imageGenerationSnapshot));
};

const subscribeToImageGeneration = (listener: (snapshot: ImageGenerationSnapshot) => void) => {
  imageGenerationListeners.add(listener);
  listener(imageGenerationSnapshot);
  return () => {
    imageGenerationListeners.delete(listener);
  };
};

const scheduleImageGenerationPhase = (requestId: number) => {
  if (imageGenerationPhaseTimer) {
    window.clearTimeout(imageGenerationPhaseTimer);
  }
  imageGenerationPhaseTimer = window.setTimeout(() => {
    if (imageGenerationRequestId === requestId && imageGenerationSnapshot.phase === 'queued') {
      publishImageGenerationSnapshot({ phase: 'generating' });
    }
  }, 700);
};

const runImageGeneration = async (input: {
  prompt: string;
  size: CreativeImageSize;
  quality: CreativeImageQuality;
  outputFormat: CreativeImageOutputFormat;
  outputCompression: number;
}) => {
  imageGenerationRequestId += 1;
  const requestId = imageGenerationRequestId;
  publishImageGenerationSnapshot({
    ...input,
    phase: 'queued',
    imageDataUrl: '',
    errorMessage: '',
  });
  scheduleImageGenerationPhase(requestId);

  try {
    const response = await api.generateImage({
      prompt: input.prompt,
      size: input.size,
      quality: input.quality,
      output_format: input.outputFormat,
      output_compression: input.outputFormat !== 'png' ? input.outputCompression : undefined,
    });
    if (imageGenerationRequestId !== requestId) return;
    const mimeType = response.mime_type || `image/${input.outputFormat}`;
    publishImageGenerationSnapshot({
      phase: 'rendering',
      imageDataUrl: `data:${mimeType};base64,${response.b64_json}`,
      errorMessage: '',
    });
    window.setTimeout(() => {
      if (imageGenerationRequestId === requestId) {
        publishImageGenerationSnapshot({ phase: 'done' });
      }
    }, 350);
  } catch (error: unknown) {
    if (imageGenerationRequestId !== requestId) return;
    publishImageGenerationSnapshot({
      phase: 'error',
      errorMessage: getErrorMessage(error, '图片生成失败'),
    });
  }
};

const persistActivePaperTranslationTask = (taskId: string, filename: string) => {
  safeLocalStorageSet(PAPER_TRANSLATION_ACTIVE_TASK_KEY, JSON.stringify({ taskId, filename }));
};

const readActivePaperTranslationTask = (): { taskId: string; filename: string } | null => {
  const raw = safeLocalStorageGet(PAPER_TRANSLATION_ACTIVE_TASK_KEY);
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw);
    const taskId = typeof payload?.taskId === 'string' ? payload.taskId.trim() : '';
    const filename = typeof payload?.filename === 'string' ? payload.filename.trim() : '';
    if (!taskId) return null;
    return { taskId, filename: filename || 'paper.pdf' };
  } catch {
    safeLocalStorageRemove(PAPER_TRANSLATION_ACTIVE_TASK_KEY);
    return null;
  }
};

const clearActivePaperTranslationTask = () => {
  safeLocalStorageRemove(PAPER_TRANSLATION_ACTIVE_TASK_KEY);
};

const getViewFromPath = (pathname: string): WorkshopView => {
  const normalizedPath = pathname.replace(/\/+$/, '');
  if (normalizedPath.endsWith('/image2')) return 'image2';
  if (normalizedPath.endsWith('/paper-translation')) return 'paper-translation';
  return 'home';
};

export default function CreativeWorkshopPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const { isGuestMode, promptLogin } = useGuestMode();
  const { chatSessions, refreshSessions } = useChatSessions();
  const {
    models: chatModels,
    defaultModelName,
    isLoading: isLoadingModels,
  } = useChatModels();

  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMainSidebarCollapsed, setIsMainSidebarCollapsed] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [selectedWorkshopModelName, setSelectedWorkshopModelName] = useState(
    () => safeLocalStorageGet(CREATIVE_WORKSHOP_MODEL_KEY) || '',
  );
  const currentView = useMemo(() => getViewFromPath(location.pathname), [location.pathname]);
  const resolvedWorkshopModelName = useMemo(
    () => resolvePreferredModelName(chatModels, selectedWorkshopModelName, { defaultModelName }),
    [chatModels, defaultModelName, selectedWorkshopModelName],
  );

  useEffect(() => {
    const check = () => {
      const isNarrowViewport = window.innerWidth <= 768;
      const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
      setIsMobile(isNarrowViewport && isTouchDevice);
    };
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  const handleNavigate = (view: WorkshopView) => {
    const path = view === 'home' ? '/creative-workshop' : `/creative-workshop/${view}`;
    navigate(path);
  };

  const handleNewChat = () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可新建对话',
        message: '游客模式下仅支持浏览页面和发送 3 条消息，新建对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }
    navigate('/');
  };

  const handleSelectChat = (chatId: string) => {
    navigate(`/?chatId=${chatId}`);
  };

  const handleDeleteChat = async (chatId: string) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理对话',
        message: '删除历史对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    try {
      await api.deleteChatSession(chatId);
      await refreshSessions();
      toast.success('对话已删除');
    } catch (error) {
      console.error('Failed to delete chat:', error);
      toast.error('删除对话失败');
    }
  };

  const renderContent = () => {
    if (currentView === 'image2') {
      return <ImageGenerationView />;
    }
    if (currentView === 'paper-translation') {
      return (
        <PaperTranslationView
          models={chatModels}
          modelName={resolvedWorkshopModelName}
          defaultModelName={defaultModelName}
          isModelSelectorDisabled={isGuestMode || isLoadingModels || chatModels.length === 0}
          onModelChange={handleWorkshopModelChange}
          onRequestSidebarCollapse={() => setIsMainSidebarCollapsed(true)}
        />
      );
    }
    return <WorkshopHome onSelectTool={handleNavigate} />;
  };

  const handleWorkshopModelChange = (modelName: string) => {
    setSelectedWorkshopModelName(modelName);
    safeLocalStorageSet(CREATIVE_WORKSHOP_MODEL_KEY, modelName);
  };

  return (
    <div className={styles.page}>
      {isMobile && isSidebarOpen && (
        <div className={styles.overlay} onClick={() => setIsSidebarOpen(false)} />
      )}

      <div className={`${styles.sidebarContainer} ${isMobile && isSidebarOpen ? styles.open : ''}`}>
        <Sidebar
          onNewChat={handleNewChat}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
          chats={chatSessions}
          collapsed={isMainSidebarCollapsed}
          onToggleCollapse={() => setIsMainSidebarCollapsed((collapsed) => !collapsed)}
        />
      </div>

      {isMobile && !isSidebarOpen && (
        <button
          className={styles.mobileMenuButton}
          onClick={() => setIsSidebarOpen(true)}
          aria-label="打开侧边栏"
        >
          <Menu size={20} />
        </button>
      )}

      <main className={styles.main}>
        {currentView === 'home' && (
          <div className={styles.workshopModelBar}>
            <ChatModelSelector
              models={chatModels}
              value={resolvedWorkshopModelName}
              onChange={handleWorkshopModelChange}
              defaultModelName={defaultModelName}
              disabled={isGuestMode || isLoadingModels || chatModels.length === 0}
              placement="bottom"
            />
          </div>
        )}
        {renderContent()}
      </main>
    </div>
  );
}

function WorkshopHome({ onSelectTool }: { onSelectTool: (view: WorkshopView) => void }) {
  return (
    <>
      <header className={`${styles.header} ${styles.homeHeader}`}>
        <div>
          <h1 className={styles.title}>创意工坊</h1>
        </div>
      </header>

      <div className={styles.homeScroll}>
        <section className={styles.toolGrid} aria-label="创意工坊功能">
          {WORKSHOP_TOOLS.map((tool) => {
            const Icon = tool.view === 'image2' ? ImagePlus : FileText;
            return (
              <button
                type="button"
                key={tool.view}
                className={`${styles.toolCard} ${styles[`${tool.accent}Tool`]}`}
                onClick={() => onSelectTool(tool.view)}
              >
                <span className={styles.toolImageFrame}>
                  <img src={tool.imageSrc} alt={tool.imageAlt} className={styles.toolImage} />
                </span>
                <span className={styles.toolBody}>
                  <span className={styles.toolTopline}>
                    <span className={styles.toolIcon}>
                      <Icon size={19} />
                    </span>
                    <span className={styles.toolStatus}>{tool.status}</span>
                  </span>
                  <span className={styles.toolEyebrow}>{tool.eyebrow}</span>
                  <span className={styles.toolTitle}>{tool.title}</span>
                  <span className={styles.toolDescription}>{tool.description}</span>
                  <span className={styles.toolMetaList}>
                    {tool.meta.map((item) => (
                      <span key={item}>{item}</span>
                    ))}
                  </span>
                  <span className={styles.toolFooter}>
                    {tool.action}
                    <ArrowRight size={17} />
                  </span>
                </span>
              </button>
            );
          })}
        </section>
      </div>
    </>
  );
}

function ImageGenerationView() {
  const toast = useToast();
  const { isGuestMode, promptLogin } = useGuestMode();

  const [prompt, setPrompt] = useState('');
  const [size, setSize] = useState<CreativeImageSize>('1024x1024');
  const [quality, setQuality] = useState<CreativeImageQuality>('medium');
  const [outputFormat, setOutputFormat] = useState<CreativeImageOutputFormat>('jpeg');
  const [outputCompression, setOutputCompression] = useState(80);
  const [phase, setPhase] = useState<GenerationPhase>('idle');
  const [imageDataUrl, setImageDataUrl] = useState('');
  const [lastPrompt, setLastPrompt] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  const previousPhaseRef = useRef<GenerationPhase>(imageGenerationSnapshot.phase);
  const isGenerating = phase === 'queued' || phase === 'generating' || phase === 'rendering';

  const progress = PHASE_PROGRESS[phase];
  const showCompression = outputFormat !== 'png';
  const canGenerate = prompt.trim().length > 0 && !isGenerating;

  const previewRatio = useMemo(() => {
    if (size === '1536x1024' || size === '3840x2160') return 'landscape';
    if (size === '1024x1536' || size === '2160x3840') return 'portrait';
    return 'square';
  }, [size]);

  useEffect(() => subscribeToImageGeneration((snapshot) => {
    const previousPhase = previousPhaseRef.current;
    setPrompt(snapshot.prompt);
    setSize(snapshot.size);
    setQuality(snapshot.quality);
    setOutputFormat(snapshot.outputFormat);
    setOutputCompression(snapshot.outputCompression);
    setPhase(snapshot.phase);
    setImageDataUrl(snapshot.imageDataUrl);
    setLastPrompt(snapshot.prompt);
    setErrorMessage(snapshot.errorMessage);
    if (previousPhase !== 'done' && snapshot.phase === 'done') {
      toast.success('图片已生成');
    }
    if (previousPhase !== 'error' && snapshot.phase === 'error' && snapshot.errorMessage) {
      toast.error(snapshot.errorMessage);
    }
    previousPhaseRef.current = snapshot.phase;
  }), []);

  const handleGenerate = async () => {
    const normalizedPrompt = prompt.trim();
    if (!normalizedPrompt) {
      toast.error('请输入 prompt');
      return;
    }
    if (isGuestMode) {
      promptLogin({
        title: '登录后可使用创意工坊',
        message: 'Image2 生图会消耗模型服务额度，请先登录后继续。',
        confirmText: '去登录',
      });
      return;
    }

    void runImageGeneration({
      prompt: normalizedPrompt,
      size,
      quality,
      outputFormat,
      outputCompression,
    });
  };

  const handleDownload = async () => {
    if (!imageDataUrl) return;
    try {
      const blob = await dataUrlToBlob(imageDataUrl);
      triggerBlobDownload(blob, `lumen-image2-${Date.now()}.${extensionForFormat(outputFormat)}`);
    } catch (error) {
      console.error('Failed to download generated image:', error);
      toast.error('下载图片失败');
    }
  };

  return (
    <>
      <div className={styles.workspace}>
        <section className={styles.controlPanel} aria-label="Image2 生图参数">
          <div className={styles.sectionHeader}>
            <WandSparkles size={18} />
            <h2>生成参数</h2>
          </div>

          <label className={styles.field}>
            <span>Prompt</span>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={7}
              maxLength={4000}
              placeholder="描述你想生成的图片"
            />
          </label>

          <div className={styles.promptChips} aria-label="示例 prompt">
            {EXAMPLE_PROMPTS.map((item) => (
              <button
                type="button"
                key={item}
                className={styles.promptChip}
                onClick={() => setPrompt(item)}
              >
                {item}
              </button>
            ))}
          </div>

          <div className={styles.fieldGroup}>
            <span className={styles.groupLabel}>分辨率</span>
            <div className={styles.sizeGrid}>
              {SIZE_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.value}
                  className={`${styles.optionButton} ${size === option.value ? styles.optionActive : ''}`}
                  onClick={() => setSize(option.value)}
                  aria-pressed={size === option.value}
                >
                  <span>{option.label}</span>
                  <small>{option.hint}</small>
                </button>
              ))}
            </div>
          </div>

          <div className={styles.inlineFields}>
            <label className={styles.field}>
              <span>质量</span>
              <select value={quality} onChange={(event) => setQuality(event.target.value as CreativeImageQuality)}>
                {QUALITY_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>

            <label className={styles.field}>
              <span>格式</span>
              <select value={outputFormat} onChange={(event) => setOutputFormat(event.target.value as CreativeImageOutputFormat)}>
                {FORMAT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          </div>

          {showCompression && (
            <label className={styles.sliderField}>
              <span>压缩质量</span>
              <div className={styles.sliderRow}>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={outputCompression}
                  onChange={(event) => setOutputCompression(Number(event.target.value))}
                />
                <strong>{outputCompression}</strong>
              </div>
            </label>
          )}

          <button
            type="button"
            className={styles.generateButton}
            onClick={handleGenerate}
            disabled={!canGenerate}
          >
            {isGenerating ? <Loader2 size={18} className={styles.spin} /> : <ImagePlus size={18} />}
            <span>{isGenerating ? '生成中' : '开始生成'}</span>
          </button>
        </section>

        <section className={styles.previewPanel} aria-label="生成结果预览">
          <div className={styles.previewHeader}>
            <div>
              <h2>预览</h2>
              <p>{lastPrompt || '生成后的图片会显示在这里'}</p>
            </div>
            <button
              type="button"
              className={styles.downloadButton}
              onClick={handleDownload}
              disabled={!imageDataUrl || isGenerating}
              title="下载图片"
              aria-label="下载图片"
            >
              <Download size={17} />
              <span>下载</span>
            </button>
          </div>

          <div className={styles.progressBlock}>
            <div className={styles.progressMeta}>
              <span>{PHASE_LABEL[phase]}</span>
              <strong>{progress}%</strong>
            </div>
            <div className={styles.progressTrack}>
              <div className={`${styles.progressFill} ${phase === 'error' ? styles.progressError : ''}`} style={{ width: `${progress}%` }} />
            </div>
          </div>

          <div className={`${styles.previewStage} ${styles[previewRatio]}`}>
            {imageDataUrl ? (
              <img src={imageDataUrl} alt={lastPrompt || 'Image2 生成图片'} className={styles.generatedImage} />
            ) : (
              <div className={styles.emptyPreview}>
                {isGenerating ? <Loader2 size={32} className={styles.spin} /> : <ImagePlus size={34} />}
                <span>{isGenerating ? 'Image2 正在生成图片' : '等待生成图片'}</span>
              </div>
            )}
          </div>

          {errorMessage && (
            <div className={styles.errorBox} role="alert">
              {errorMessage}
            </div>
          )}
        </section>
      </div>
    </>
  );
}

function PaperTranslationView({
  models,
  modelName,
  defaultModelName,
  isModelSelectorDisabled,
  onModelChange,
  onRequestSidebarCollapse,
}: {
  models: ChatModelOption[];
  modelName?: string;
  defaultModelName?: string;
  isModelSelectorDisabled: boolean;
  onModelChange: (modelName: string) => void;
  onRequestSidebarCollapse: () => void;
}) {
  const toast = useToast();
  const { isGuestMode, promptLogin } = useGuestMode();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const activeTaskIdRef = useRef('');
  const pollAbortRef = useRef<AbortController | null>(null);
  const createAbortRef = useRef<AbortController | null>(null);
  const restoreAbortRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);
  const knowledgeDialogRequestRef = useRef(0);
  const isMountedRef = useRef(true);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [restoredFilename, setRestoredFilename] = useState('');
  const [pdfPreviewUrl, setPdfPreviewUrl] = useState('');
  const [translationPhase, setTranslationPhase] = useState<TranslationPhase>('idle');
  const [translatedMarkdown, setTranslatedMarkdown] = useState('');
  const [translationTaskId, setTranslationTaskId] = useState('');
  const [translationError, setTranslationError] = useState('');
  const [isKnowledgeDialogOpen, setIsKnowledgeDialogOpen] = useState(false);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseListItem[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('');
  const [isLoadingKnowledgeBases, setIsLoadingKnowledgeBases] = useState(false);
  const [isAddingToKnowledgeBase, setIsAddingToKnowledgeBase] = useState(false);
  const [knowledgeUploadProgress, setKnowledgeUploadProgress] = useState(0);
  const [knowledgeDialogError, setKnowledgeDialogError] = useState('');

  const hasResult = translationPhase === 'done' && translatedMarkdown.trim().length > 0;
  const isProcessing = translationPhase === 'processing';
  const previewFileName = selectedFile?.name || restoredFilename;
  const translatedMarkdownFileName = `${previewFileName.replace(/\.pdf$/i, '') || 'paper-translation'}.zh.md`;

  useEffect(() => {
    const currentPreviewUrl = pdfPreviewUrl;
    return () => {
      if (currentPreviewUrl) {
        URL.revokeObjectURL(currentPreviewUrl);
      }
    };
  }, [pdfPreviewUrl]);

  useEffect(() => () => {
    isMountedRef.current = false;
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
    }
    pollAbortRef.current?.abort();
    createAbortRef.current?.abort();
    restoreAbortRef.current?.abort();
  }, []);

  const cancelActivePolling = () => {
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollAbortRef.current?.abort();
    pollAbortRef.current = null;
  };

  const resetActiveTaskFlow = () => {
    requestGenerationRef.current += 1;
    activeTaskIdRef.current = '';
    cancelActivePolling();
    createAbortRef.current?.abort();
    createAbortRef.current = null;
    restoreAbortRef.current?.abort();
    restoreAbortRef.current = null;
  };

  const updatePdfPreviewUrl = (nextUrl: string) => {
    setPdfPreviewUrl((currentUrl) => {
      if (currentUrl) {
        URL.revokeObjectURL(currentUrl);
      }
      return nextUrl;
    });
  };

  const restoreSourcePdfPreview = async (taskId: string, signal: AbortSignal) => {
    const { blob, fileName } = await api.getPaperTranslationSourcePdf(taskId, { signal });
    if (signal.aborted || !isMountedRef.current) return;
    updatePdfPreviewUrl(URL.createObjectURL(blob));
    setRestoredFilename(fileName || 'paper.pdf');
    setSelectedFile(null);
    onRequestSidebarCollapse();
  };

  const applyTaskState = async (
    task: Awaited<ReturnType<typeof api.getPaperTranslationTask>>,
    generation: number,
    signal: AbortSignal,
  ) => {
    if (!isMountedRef.current || requestGenerationRef.current !== generation) {
      return;
    }

    activeTaskIdRef.current = task.task_id;
    setTranslationTaskId(task.task_id);
    setRestoredFilename(task.filename);
    if (TRANSLATION_PROCESSING_STATUSES.includes(task.status)) {
      persistActivePaperTranslationTask(task.task_id, task.filename);
    } else {
      clearActivePaperTranslationTask();
    }

    if (task.status === 'completed') {
      const markdown = await api.getPaperTranslationResult(task.task_id, { signal });
      if (!isMountedRef.current || requestGenerationRef.current !== generation || signal.aborted) {
        return;
      }
      if (!markdown.trim()) {
        throw new Error('Markdown 译文为空');
      }
      setTranslatedMarkdown(markdown);
      setTranslationPhase('done');
      setTranslationError('');
      return;
    }

    if (task.status === 'failed') {
      const message = task.error || '论文翻译失败';
      setTranslationError(message);
      setTranslationPhase('error');
      setTranslatedMarkdown('');
      return;
    }

    if (TRANSLATION_PROCESSING_STATUSES.includes(task.status)) {
      setTranslationPhase('processing');
      setTranslatedMarkdown('');
      setTranslationError('');
      schedulePoll(task.task_id, generation);
    }
  };

  const schedulePoll = (taskId: string, generation: number) => {
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
    }

    pollTimerRef.current = window.setTimeout(async () => {
      const controller = new AbortController();
      pollAbortRef.current = controller;
      try {
        const task = await api.getPaperTranslationTask(taskId, { signal: controller.signal });
        if (!isMountedRef.current || activeTaskIdRef.current !== taskId || requestGenerationRef.current !== generation) {
          return;
        }
        if (task.status === 'completed') {
          await applyTaskState(task, generation, controller.signal);
          toast.success('翻译完成');
          return;
        }

        if (task.status === 'failed') {
          const message = task.error || '论文翻译失败';
          await applyTaskState(task, generation, controller.signal);
          toast.error(message);
          return;
        }

        if (TRANSLATION_PROCESSING_STATUSES.includes(task.status)) {
          await applyTaskState(task, generation, controller.signal);
        }
      } catch (error: unknown) {
        if (
          controller.signal.aborted
          || activeTaskIdRef.current !== taskId
          || requestGenerationRef.current !== generation
          || !isMountedRef.current
        ) {
          return;
        }
        const message = getErrorMessage(error, '获取翻译进度失败');
        setTranslationError(message);
        setTranslationPhase('error');
        toast.error(message);
      } finally {
        if (pollAbortRef.current === controller) {
          pollAbortRef.current = null;
        }
      }
    }, 2200);
  };

  useEffect(() => {
    if (isGuestMode) return;

    requestGenerationRef.current += 1;
    const generation = requestGenerationRef.current;
    const controller = new AbortController();
    restoreAbortRef.current = controller;

    const restoreTask = async () => {
      try {
        const cachedTask = readActivePaperTranslationTask();
        const task = cachedTask
          ? await api.getPaperTranslationTask(cachedTask.taskId, { signal: controller.signal })
          : await api.getLatestActivePaperTranslationTask({ signal: controller.signal });
        activeTaskIdRef.current = task.task_id;
        setTranslationTaskId(task.task_id);
        setRestoredFilename(task.filename);
        if (task.model_name) {
          onModelChange(task.model_name);
        }
        setTranslationPhase('processing');
        setTranslationError('');
        try {
          await restoreSourcePdfPreview(task.task_id, controller.signal);
        } catch (previewError) {
          if (controller.signal.aborted) return;
          console.warn('Failed to restore source PDF preview:', previewError);
        }
        await applyTaskState(task, generation, controller.signal);
      } catch (error: unknown) {
        if (controller.signal.aborted || !isMountedRef.current || requestGenerationRef.current !== generation) {
          return;
        }
        const message = getErrorMessage(error, '恢复翻译任务失败');
        if (message.includes('不存在') || message.includes('没有进行中') || message.includes('404')) {
          clearActivePaperTranslationTask();
          activeTaskIdRef.current = '';
          setTranslationTaskId('');
          setRestoredFilename('');
          updatePdfPreviewUrl('');
          setTranslationPhase('idle');
          return;
        }
        setTranslationError(message);
        setTranslationPhase('error');
      } finally {
        if (restoreAbortRef.current === controller) {
          restoreAbortRef.current = null;
        }
      }
    };

    void restoreTask();
  }, [isGuestMode]);

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;

    const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
    if (!isPdf) {
      toast.error('仅支持上传 PDF 文件');
      return;
    }

    resetActiveTaskFlow();
    clearActivePaperTranslationTask();
    setSelectedFile(file);
    setRestoredFilename('');
    updatePdfPreviewUrl(URL.createObjectURL(file));
    onRequestSidebarCollapse();
    setTranslatedMarkdown('');
    setTranslationTaskId('');
    setTranslationError('');
    setTranslationPhase('ready');
    toast.info('已选择文件');
  };

  const handleDrop = (event: React.DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    handleFiles(event.dataTransfer.files);
  };

  const handleStart = async () => {
    if (!selectedFile) {
      toast.error('请先上传 PDF 文件');
      return;
    }
    if (isGuestMode) {
      promptLogin({
        title: '登录后可使用论文翻译',
        message: '论文翻译需要上传文件并调用模型服务，请先登录后继续。',
        confirmText: '去登录',
      });
      return;
    }

    resetActiveTaskFlow();
    const generation = requestGenerationRef.current;
    const createController = new AbortController();
    createAbortRef.current = createController;
    setTranslatedMarkdown('');
    setTranslationTaskId('');
    setTranslationError('');
    setTranslationPhase('processing');

    try {
      const task = await api.createPaperTranslationTask(selectedFile, {
        signal: createController.signal,
        modelName,
      });
      if (!isMountedRef.current || requestGenerationRef.current !== generation) {
        return;
      }
      if (createAbortRef.current === createController) {
        createAbortRef.current = null;
      }
      activeTaskIdRef.current = task.task_id;
      setTranslationTaskId(task.task_id);
      setRestoredFilename(task.filename);
      if (TRANSLATION_PROCESSING_STATUSES.includes(task.status)) {
        persistActivePaperTranslationTask(task.task_id, task.filename);
      }

      if (task.status === 'completed') {
        await applyTaskState(task, generation, createController.signal);
        toast.success('翻译完成');
        return;
      }
      if (task.status === 'failed') {
        const message = task.error || '论文翻译失败';
        await applyTaskState(task, generation, createController.signal);
        toast.error(message);
        return;
      }

      await applyTaskState(task, generation, createController.signal);
    } catch (error: unknown) {
      if (createController.signal.aborted || !isMountedRef.current || requestGenerationRef.current !== generation) {
        return;
      }
      const message = getErrorMessage(error, '创建翻译任务失败');
      setTranslationError(message);
      setTranslationPhase('error');
      toast.error(message);
    } finally {
      if (createAbortRef.current === createController) {
        createAbortRef.current = null;
      }
    }
  };

  const handleClearFile = () => {
    resetActiveTaskFlow();
    clearActivePaperTranslationTask();
    setSelectedFile(null);
    setRestoredFilename('');
    updatePdfPreviewUrl('');
    setTranslatedMarkdown('');
    setTranslationTaskId('');
    setTranslationError('');
    setTranslationPhase('idle');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleDownloadMarkdown = async () => {
    if (!hasResult || !translationTaskId) return;
    try {
      const { blob, fileName } = await api.downloadPaperTranslationMarkdown(translationTaskId);
      triggerBlobDownload(blob, fileName || `${selectedFile?.name.replace(/\.pdf$/i, '') || 'paper-translation'}.zh.md`);
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '下载 Markdown 失败'));
    }
  };

  const handleDownloadPdf = async () => {
    if (!hasResult || !translationTaskId) return;
    try {
      const { blob, fileName } = await api.downloadPaperTranslationPdf(translationTaskId);
      triggerBlobDownload(blob, fileName || `${selectedFile?.name.replace(/\.pdf$/i, '') || 'paper-translation'}.zh.pdf`);
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '导出 PDF 失败'));
    }
  };

  const selectDefaultKnowledgeBase = (items: KnowledgeBaseListItem[]) => {
    const defaultKb = items.find((item) => item.name === DEFAULT_KNOWLEDGE_BASE_NAME);
    return defaultKb?.id || items[0]?.id || '';
  };

  const openKnowledgeDialog = async () => {
    if (!hasResult || !translationTaskId) return;
    if (isGuestMode) {
      promptLogin({
        title: '登录后可添加到知识库',
        message: '请先登录后继续。',
        confirmText: '去登录',
      });
      return;
    }

    setIsKnowledgeDialogOpen(true);
    setKnowledgeDialogError('');
    setKnowledgeUploadProgress(0);
    setIsLoadingKnowledgeBases(true);
    const requestId = knowledgeDialogRequestRef.current + 1;
    knowledgeDialogRequestRef.current = requestId;

    try {
      const response = await api.listKnowledgeBases(undefined, 1, 100);
      let items = response.items;
      if (items.length === 0) {
        const created = await api.createKnowledgeBase(DEFAULT_KNOWLEDGE_BASE_NAME, '', '其它');
        const refreshed = await api.listKnowledgeBases(undefined, 1, 100);
        items = refreshed.items.length > 0
          ? refreshed.items
          : [{ id: created.id, name: DEFAULT_KNOWLEDGE_BASE_NAME, description: '', category: '其它' }];
      }
      if (!isMountedRef.current || knowledgeDialogRequestRef.current !== requestId) return;
      setKnowledgeBases(items);
      setSelectedKnowledgeBaseId((current) => (
        current && items.some((item) => item.id === current)
          ? current
          : selectDefaultKnowledgeBase(items)
      ));
    } catch (error: unknown) {
      if (!isMountedRef.current || knowledgeDialogRequestRef.current !== requestId) return;
      setKnowledgeDialogError(getErrorMessage(error, '加载知识库失败'));
    } finally {
      if (isMountedRef.current && knowledgeDialogRequestRef.current === requestId) {
        setIsLoadingKnowledgeBases(false);
      }
    }
  };

  const closeKnowledgeDialog = () => {
    if (isAddingToKnowledgeBase) return;
    knowledgeDialogRequestRef.current += 1;
    setIsKnowledgeDialogOpen(false);
    setKnowledgeDialogError('');
    setKnowledgeUploadProgress(0);
    setIsLoadingKnowledgeBases(false);
  };

  const handleAddToKnowledgeBase = async () => {
    if (!hasResult || !translationTaskId || !selectedKnowledgeBaseId || isAddingToKnowledgeBase) return;

    setIsAddingToKnowledgeBase(true);
    setKnowledgeDialogError('');
    setKnowledgeUploadProgress(0);

    try {
      const { blob, fileName } = await api.downloadPaperTranslationMarkdownForKnowledgeBase(translationTaskId);
      const markdownFile = new File(
        [blob],
        fileName || translatedMarkdownFileName,
        { type: 'text/markdown;charset=utf-8' },
      );
      await uploadKnowledgeDocuments(selectedKnowledgeBaseId, [markdownFile], {
        onProgress: setKnowledgeUploadProgress,
      });
      toast.success('已添加到知识库');
      setIsKnowledgeDialogOpen(false);
    } catch (error: unknown) {
      setKnowledgeDialogError(getErrorMessage(error, '添加到知识库失败'));
    } finally {
      setIsAddingToKnowledgeBase(false);
    }
  };

  return (
    <>
      <div className={styles.translationWorkspace}>
        <section className={styles.translationPanel} aria-label="PDF 原文上传与预览">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            className={styles.hiddenInput}
            onChange={(event) => {
              handleFiles(event.target.files);
              event.target.value = '';
            }}
          />

          <div className={styles.translationSourceToolbar}>
            <ChatModelSelector
              models={models}
              value={modelName}
              onChange={onModelChange}
              defaultModelName={defaultModelName}
              disabled={isModelSelectorDisabled || isProcessing}
              placement="bottom"
            />
          </div>

          {!previewFileName ? (
            <button
              type="button"
              className={styles.uploadZone}
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload size={34} />
              <span>上传 PDF</span>
            </button>
          ) : (
            <div className={styles.pdfPreviewShell}>
              <div className={styles.pdfFrame}>
                <button
                  type="button"
                  className={styles.previewRemoveButton}
                  onClick={handleClearFile}
                  aria-label="移除 PDF"
                  title="移除 PDF"
                >
                  <X size={15} />
                </button>
                {pdfPreviewUrl ? (
                  <PDFViewer url={pdfPreviewUrl} fileName={previewFileName} hideToolbar />
                ) : (
                  <div className={styles.translationLoading}>
                    <Loader2 size={34} className={styles.spin} />
                  </div>
                )}
              </div>
            </div>
          )}

        </section>

        <section className={styles.translationResultPanel} aria-label="论文翻译结果">
          <div className={styles.translationResultToolbar}>
            {hasResult && (
              <button
                type="button"
                className={styles.translationIconButton}
                onClick={handleDownloadMarkdown}
                title="下载 Markdown 译文"
                aria-label="下载 Markdown 译文"
              >
                <Download size={17} />
              </button>
            )}
            {hasResult && (
              <button
                type="button"
                className={styles.translationIconButton}
                onClick={handleDownloadPdf}
                title="导出 PDF 下载"
                aria-label="导出 PDF 下载"
              >
                <Share size={17} />
              </button>
            )}
            {hasResult && (
              <button
                type="button"
                className={styles.translationIconButton}
                onClick={openKnowledgeDialog}
                title="添加到知识库"
                aria-label="添加到知识库"
              >
                <CirclePlus size={18} />
              </button>
            )}
          </div>

          <div className={styles.translationResultBody}>
            {isProcessing ? (
              <div className={styles.translationLoading}>
                <Loader2 size={34} className={styles.spin} />
                <h3>翻译中</h3>
                <p>可能需要等待5到10分钟</p>
              </div>
            ) : hasResult ? (
              <article className={styles.markdownResult}>
                <OptimizedMarkdown>{translatedMarkdown}</OptimizedMarkdown>
              </article>
            ) : translationPhase === 'ready' ? (
              <div className={styles.translationActionStage}>
                <button
                  type="button"
                  className={styles.translationPrimaryAction}
                  onClick={handleStart}
                  disabled={!selectedFile}
                >
                  <FileText size={18} />
                  <span>开始翻译</span>
                </button>
              </div>
            ) : translationPhase === 'error' ? (
              <div className={styles.translationEmpty}>
                <FileText size={34} />
                <h3>{translationError || '翻译失败'}</h3>
              </div>
            ) : (
              <div className={styles.translationEmpty}>
                <FileText size={38} />
                <h3>等待 PDF</h3>
              </div>
            )}
          </div>
        </section>
      </div>

      {isKnowledgeDialogOpen && (
        <div className={styles.knowledgeDialogOverlay} onClick={closeKnowledgeDialog}>
          <div
            className={styles.knowledgeDialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="paper-translation-kb-dialog-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className={styles.knowledgeDialogHeader}>
              <h3 id="paper-translation-kb-dialog-title">添加到知识库</h3>
              <button
                type="button"
                className={styles.knowledgeDialogClose}
                onClick={closeKnowledgeDialog}
                disabled={isAddingToKnowledgeBase}
                aria-label="关闭"
                title="关闭"
              >
                <X size={17} />
              </button>
            </div>

            <div className={styles.knowledgeDialogBody}>
              {isLoadingKnowledgeBases ? (
                <div className={styles.knowledgeDialogState}>
                  <Loader2 size={24} className={styles.spin} />
                </div>
              ) : knowledgeBases.length > 0 ? (
                <div className={styles.knowledgeList} role="radiogroup" aria-label="选择知识库">
                  {knowledgeBases.map((kb) => {
                    const isSelected = selectedKnowledgeBaseId === kb.id;
                    return (
                      <button
                        key={kb.id}
                        type="button"
                        className={`${styles.knowledgeOption} ${isSelected ? styles.knowledgeOptionSelected : ''}`}
                        onClick={() => setSelectedKnowledgeBaseId(kb.id)}
                        role="radio"
                        aria-checked={isSelected}
                      >
                        <span className={styles.knowledgeOptionName}>{kb.name}</span>
                        <span className={styles.knowledgeOptionMeta}>
                          {typeof kb.contents === 'number' ? `${kb.contents} 个文档` : kb.category || '知识库'}
                        </span>
                        {isSelected && <Check size={16} />}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className={styles.knowledgeDialogState}>暂无可用知识库</div>
              )}

              {knowledgeDialogError && (
                <div className={styles.knowledgeDialogError} role="alert">
                  {knowledgeDialogError}
                </div>
              )}

              {isAddingToKnowledgeBase && (
                <div className={styles.knowledgeUploadProgress}>
                  <div style={{ width: `${knowledgeUploadProgress}%` }} />
                </div>
              )}
            </div>

            <div className={styles.knowledgeDialogFooter}>
              <button
                type="button"
                className={styles.knowledgeDialogSecondary}
                onClick={closeKnowledgeDialog}
                disabled={isAddingToKnowledgeBase}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.knowledgeDialogPrimary}
                onClick={handleAddToKnowledgeBase}
                disabled={!selectedKnowledgeBaseId || isLoadingKnowledgeBases || isAddingToKnowledgeBase}
              >
                {isAddingToKnowledgeBase && <Loader2 size={16} className={styles.spin} />}
                <span>{isAddingToKnowledgeBase ? '添加中' : '确定'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
