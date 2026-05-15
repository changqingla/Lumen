import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Download,
  ImagePlus,
  Loader2,
  Menu,
  WandSparkles,
} from 'lucide-react';

import Sidebar from '@/app/components/Sidebar/Sidebar';
import {
  api,
  type CreativeImageOutputFormat,
  type CreativeImageQuality,
  type CreativeImageSize,
} from '@/shared/api/client';
import { useGuestMode } from '@/shared/hooks/useGuestMode';
import { useToast } from '@/shared/hooks/useToast';
import { useChatSessions } from '@/features/chat/hooks/useChatSessions';
import { getErrorMessage } from '@/shared/utils/errorMessage';

import styles from './CreativeWorkshopPage.module.css';

type GenerationPhase = 'idle' | 'queued' | 'generating' | 'rendering' | 'done' | 'error';

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

const extensionForFormat = (format: CreativeImageOutputFormat) => (format === 'jpeg' ? 'jpg' : format);

const dataUrlToBlob = async (dataUrl: string) => {
  const response = await fetch(dataUrl);
  return response.blob();
};

export default function CreativeWorkshopPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { isGuestMode, promptLogin } = useGuestMode();
  const { chatSessions, refreshSessions } = useChatSessions();

  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [size, setSize] = useState<CreativeImageSize>('1024x1024');
  const [quality, setQuality] = useState<CreativeImageQuality>('medium');
  const [outputFormat, setOutputFormat] = useState<CreativeImageOutputFormat>('jpeg');
  const [outputCompression, setOutputCompression] = useState(80);
  const [phase, setPhase] = useState<GenerationPhase>('idle');
  const [imageDataUrl, setImageDataUrl] = useState('');
  const [lastPrompt, setLastPrompt] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  const downloadUrlRef = useRef<string | null>(null);
  const phaseTimerRef = useRef<number | null>(null);
  const isGenerating = phase === 'queued' || phase === 'generating' || phase === 'rendering';

  const progress = PHASE_PROGRESS[phase];
  const showCompression = outputFormat !== 'png';
  const canGenerate = prompt.trim().length > 0 && !isGenerating;

  const previewRatio = useMemo(() => {
    if (size === '1536x1024' || size === '3840x2160') return 'landscape';
    if (size === '1024x1536' || size === '2160x3840') return 'portrait';
    return 'square';
  }, [size]);

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

  useEffect(() => () => {
    if (downloadUrlRef.current) {
      URL.revokeObjectURL(downloadUrlRef.current);
    }
    if (phaseTimerRef.current) {
      window.clearTimeout(phaseTimerRef.current);
    }
  }, []);

  const moveToGeneratingSoon = () => {
    if (phaseTimerRef.current) {
      window.clearTimeout(phaseTimerRef.current);
    }
    phaseTimerRef.current = window.setTimeout(() => {
      setPhase((current) => (current === 'queued' ? 'generating' : current));
    }, 700);
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

    setErrorMessage('');
    setImageDataUrl('');
    setLastPrompt(normalizedPrompt);
    setPhase('queued');
    moveToGeneratingSoon();

    try {
      const response = await api.generateImage({
        prompt: normalizedPrompt,
        size,
        quality,
        output_format: outputFormat,
        output_compression: showCompression ? outputCompression : undefined,
      });
      setPhase('rendering');
      const mimeType = response.mime_type || `image/${outputFormat}`;
      setImageDataUrl(`data:${mimeType};base64,${response.b64_json}`);
      window.setTimeout(() => setPhase('done'), 350);
      toast.success('图片已生成');
    } catch (error: unknown) {
      const message = getErrorMessage(error, '图片生成失败');
      setErrorMessage(message);
      setPhase('error');
      toast.error(message);
    }
  };

  const handleDownload = async () => {
    if (!imageDataUrl) return;
    try {
      const blob = await dataUrlToBlob(imageDataUrl);
      if (downloadUrlRef.current) {
        URL.revokeObjectURL(downloadUrlRef.current);
      }
      const url = URL.createObjectURL(blob);
      downloadUrlRef.current = url;
      const link = document.createElement('a');
      link.href = url;
      link.download = `lumen-image2-${Date.now()}.${extensionForFormat(outputFormat)}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      console.error('Failed to download generated image:', error);
      toast.error('下载图片失败');
    }
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
        <header className={styles.header}>
          <div>
            <h1 className={styles.title}>创意工坊</h1>
            <p className={styles.subtitle}>Image2 文生图</p>
          </div>
        </header>

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
      </main>
    </div>
  );
}
