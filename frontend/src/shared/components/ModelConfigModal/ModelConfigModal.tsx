import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronRight,
  HeartPulse,
  Minus,
  PencilLine,
  Plus,
  Trash2,
  X,
} from 'lucide-react';

import ConfirmModal from '@/shared/components/ConfirmModal/ConfirmModal';
import ModelProviderIcon from '@/shared/components/ModelProviderIcon';
import { notifyChatModelsUpdated } from '@/features/chat/hooks/useChatModels';
import { useToast } from '@/shared/hooks/useToast';
import {
  api,
  type ModelConfigCatalogResponse,
  type ModelProviderCatalogItem,
  type ProviderRemoteModelsResponse,
  type UserConfiguredModel,
} from '@/shared/api/client';

import styles from './ModelConfigModal.module.css';

interface ModelConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface ProviderCard extends ModelProviderCatalogItem {
  models: UserConfiguredModel[];
}

interface ProviderConfigDeletionTarget {
  code: string;
  displayName: string;
  modelsCount: number;
}

interface EditProviderTarget {
  code: string;
  displayName: string;
  apiKeyLabel: string;
  baseUrl: string;
  isCustom: boolean;
  hasCredential: boolean;
  maskedApiKey?: string | null;
}

interface ToggleSwitchProps {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  onChange: () => void;
  ariaLabel: string;
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function getDisplayTitle(model: Pick<UserConfiguredModel, 'display_name' | 'provider_display_name' | 'provider_model_name'>) {
  const providerPrefix = `${model.provider_display_name} / `;
  if (model.display_name.startsWith(providerPrefix)) {
    return model.display_name.slice(providerPrefix.length);
  }
  return model.display_name || model.provider_model_name;
}

function getProviderToggleState(models: UserConfiguredModel[]) {
  if (!models.length) {
    return { checked: false, indeterminate: false };
  }
  const enabledCount = models.filter((model) => model.is_enabled).length;
  if (enabledCount === 0) {
    return { checked: false, indeterminate: false };
  }
  if (enabledCount === models.length) {
    return { checked: true, indeterminate: false };
  }
  return { checked: true, indeterminate: true };
}

function getDefaultProviderCode(providers: ModelProviderCatalogItem[]) {
  return providers.find((provider) => provider.code === 'dashscope')?.code || providers[0]?.code || '';
}

function getHealthStatusTitle(model: UserConfiguredModel) {
  if (model.health_status === 'healthy') {
    return model.last_health_latency_ms
      ? `检测成功 · ${model.last_health_latency_ms}ms`
      : '检测成功';
  }
  if (model.health_status === 'unhealthy') {
    return model.last_health_error || '检测失败';
  }
  return '';
}

function ToggleSwitch({ checked, indeterminate = false, disabled = false, onChange, ariaLabel }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      className={`${styles.switch} ${checked ? styles.switchChecked : ''} ${indeterminate ? styles.switchIndeterminate : ''}`}
      onClick={onChange}
    >
      <span className={styles.switchThumb} />
    </button>
  );
}

export default function ModelConfigModal({ isOpen, onClose }: ModelConfigModalProps) {
  const toast = useToast();
  const catalogRequestIdRef = useRef(0);
  const remoteModelsRequestIdRef = useRef(0);
  const providerMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const modelInputRef = useRef<HTMLInputElement>(null);

  const [catalog, setCatalog] = useState<ModelConfigCatalogResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({});

  const [togglingProviderCode, setTogglingProviderCode] = useState<string | null>(null);
  const [togglingBindingId, setTogglingBindingId] = useState<string | null>(null);
  const [healthCheckingBindingId, setHealthCheckingBindingId] = useState<string | null>(null);
  const [deletingBindingId, setDeletingBindingId] = useState<string | null>(null);
  const [deletingProviderCode, setDeletingProviderCode] = useState<string | null>(null);

  const [deletingProviderConfig, setDeletingProviderConfig] = useState<ProviderConfigDeletionTarget | null>(null);

  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<EditProviderTarget | null>(null);
  const [editBaseUrlInput, setEditBaseUrlInput] = useState('');
  const [editApiKeyInput, setEditApiKeyInput] = useState('');
  const [isSavingProviderConfig, setIsSavingProviderConfig] = useState(false);

  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [selectedProviderCode, setSelectedProviderCode] = useState('');
  const [isProviderMenuOpen, setIsProviderMenuOpen] = useState(false);
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false);
  const [modelSearchQuery, setModelSearchQuery] = useState('');
  const [baseUrlInput, setBaseUrlInput] = useState('');
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [remoteModelsState, setRemoteModelsState] = useState<ProviderRemoteModelsResponse | null>(null);
  const [remoteModelsError, setRemoteModelsError] = useState<string | null>(null);
  const [selectedRemoteModelName, setSelectedRemoteModelName] = useState('');
  const [isPreparingModels, setIsPreparingModels] = useState(false);
  const [isLoadingRemoteModels, setIsLoadingRemoteModels] = useState(false);
  const [addingModelName, setAddingModelName] = useState<string | null>(null);

  const providers = useMemo(() => catalog?.providers || [], [catalog]);
  const userModels = useMemo(() => catalog?.user_models || [], [catalog]);

  const loadCatalog = useCallback(async () => {
    const requestId = catalogRequestIdRef.current + 1;
    catalogRequestIdRef.current = requestId;
    setIsLoading(true);
    try {
      const response = await api.getModelConfigCatalog();
      if (requestId !== catalogRequestIdRef.current) {
        return response;
      }
      setCatalog(response);
      setLoadError(null);
      setSelectedProviderCode((prev) => {
        if (prev && response.providers.some((provider) => provider.code === prev)) {
          return prev;
        }
        return getDefaultProviderCode(response.providers);
      });
      setExpandedProviders((prev) => {
        const next = { ...prev };
        response.providers.forEach((provider) => {
          const providerModels = response.user_models.filter((model) => model.provider_code === provider.code);
          if (next[provider.code] === undefined) {
            next[provider.code] = providerModels.length > 0;
          }
        });
        return next;
      });
      return response;
    } catch (error) {
      if (requestId !== catalogRequestIdRef.current) {
        throw error;
      }
      const message = getErrorMessage(error, '加载模型配置失败');
      setLoadError(message);
      throw error;
    } finally {
      if (requestId === catalogRequestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  const providerCards = useMemo<ProviderCard[]>(() => {
    const providerOrder = new Map(providers.map((provider, index) => [provider.code, index]));
    return providers
      .filter((provider) => provider.credential_configured || userModels.some((model) => model.provider_code === provider.code))
      .map((provider) => ({
        ...provider,
        models: [...userModels]
          .filter((model) => model.provider_code === provider.code)
          .sort((left, right) => left.provider_model_name.localeCompare(right.provider_model_name)),
      }))
      .sort((left, right) => {
        const leftOrder = providerOrder.get(left.code) ?? Number.MAX_SAFE_INTEGER;
        const rightOrder = providerOrder.get(right.code) ?? Number.MAX_SAFE_INTEGER;
        return leftOrder - rightOrder;
      });
  }, [providers, userModels]);

  const selectedProvider = useMemo(() => {
    if (!providers.length) {
      return undefined;
    }
    return providers.find((provider) => provider.code === selectedProviderCode) || providers[0];
  }, [providers, selectedProviderCode]);

  const selectedRemoteModel = useMemo(
    () => remoteModelsState?.models.find((model) => model.name === selectedRemoteModelName),
    [remoteModelsState, selectedRemoteModelName],
  );

  const filteredRemoteModels = useMemo(() => {
    const models = remoteModelsState?.models || [];
    const normalizedQuery = modelSearchQuery.trim().toLowerCase();
    if (!normalizedQuery) {
      return models;
    }
    return models.filter((model) => (
      model.display_name.toLowerCase().includes(normalizedQuery)
      || model.name.toLowerCase().includes(normalizedQuery)
    ));
  }, [modelSearchQuery, remoteModelsState?.models]);

  const alreadyAdded = Boolean(
    selectedProvider
      && selectedRemoteModel
      && userModels.some(
        (model) => model.provider_code === selectedProvider.code && model.provider_model_name === selectedRemoteModel.name,
      ),
  );

  const providerCredentialHint = useMemo(() => {
    const hasDraftApiKey = apiKeyInput.trim().length > 0;
    if (hasDraftApiKey) {
      return '确认后会更新该供应商的唯一凭据，并影响该供应商下已添加模型。';
    }
    if (selectedProvider?.credential_configured && selectedProvider.api_key_masked) {
      return `当前已配置：${selectedProvider.api_key_masked}`;
    }
    return '此供应商下所有模型共用这套凭据。';
  }, [apiKeyInput, selectedProvider?.api_key_masked, selectedProvider?.credential_configured]);

  const modelInputValue = useMemo(() => {
    if (isModelMenuOpen) {
      return modelSearchQuery;
    }
    return selectedRemoteModel?.display_name || '';
  }, [isModelMenuOpen, modelSearchQuery, selectedRemoteModel?.display_name]);

  const applySingleModelUpdate = useCallback((updatedModel: UserConfiguredModel) => {
    setCatalog((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        user_models: prev.user_models.map((model) => (model.id === updatedModel.id ? updatedModel : model)),
      };
    });
  }, []);

  const applyProviderModelsUpdate = useCallback((providerCode: string, updatedModels: UserConfiguredModel[]) => {
    const byId = new Map(updatedModels.map((model) => [model.id, model]));
    setCatalog((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        user_models: prev.user_models.map((model) => byId.get(model.id) || model),
      };
    });
    setExpandedProviders((prev) => ({ ...prev, [providerCode]: true }));
  }, []);

  const resetAddModelState = useCallback(() => {
    remoteModelsRequestIdRef.current += 1;
    setRemoteModelsState(null);
    setRemoteModelsError(null);
    setSelectedRemoteModelName('');
    setIsPreparingModels(false);
    setIsLoadingRemoteModels(false);
    setAddingModelName(null);
    setIsModelMenuOpen(false);
    setModelSearchQuery('');
  }, []);

  const invalidateRemoteModels = useCallback(() => {
    remoteModelsRequestIdRef.current += 1;
    setRemoteModelsState(null);
    setRemoteModelsError(null);
    setSelectedRemoteModelName('');
    setIsModelMenuOpen(false);
    setModelSearchQuery('');
  }, []);

  const loadProviderModels = useCallback(async (
    providerCode: string,
    options?: { silent?: boolean; apiKey?: string; baseUrl?: string },
  ) => {
    const requestId = remoteModelsRequestIdRef.current + 1;
    remoteModelsRequestIdRef.current = requestId;
    setIsLoadingRemoteModels(true);
    try {
      const response = await api.previewProviderRemoteModels(providerCode, options?.apiKey, options?.baseUrl);
      if (requestId !== remoteModelsRequestIdRef.current) {
        return response;
      }
      setRemoteModelsState(response);
      setRemoteModelsError(null);
      setSelectedRemoteModelName((prev) => (
        prev && response.models.some((model) => model.name === prev) ? prev : ''
      ));
      return response;
    } catch (error) {
      if (requestId !== remoteModelsRequestIdRef.current) {
        throw error;
      }
      const message = getErrorMessage(error, '加载模型列表失败');
      setRemoteModelsState(null);
      setRemoteModelsError(message);
      setSelectedRemoteModelName('');
      if (!options?.silent) {
        toast.error(message);
      }
      throw error;
    } finally {
      if (requestId === remoteModelsRequestIdRef.current) {
        setIsLoadingRemoteModels(false);
      }
    }
  }, [toast]);

  const refreshCatalog = useCallback(async () => loadCatalog(), [loadCatalog]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    void loadCatalog().catch((error) => {
      toast.error(getErrorMessage(error, '加载模型配置失败'));
    });
  }, [isOpen, loadCatalog, toast]);

  useEffect(() => {
    if (!isOpen) {
      catalogRequestIdRef.current += 1;
      setCatalog(null);
      setLoadError(null);
      setExpandedProviders({});
      setDeletingBindingId(null);
      setDeletingProviderCode(null);
      setDeletingProviderConfig(null);
      setIsEditModalOpen(false);
      setEditingProvider(null);
      setEditBaseUrlInput('');
      setEditApiKeyInput('');
      setIsAddModalOpen(false);
      setIsProviderMenuOpen(false);
      resetAddModelState();
    }
  }, [isOpen, resetAddModelState]);

  useEffect(() => {
    if (!isAddModalOpen) {
      return;
    }
    setApiKeyInput('');
    setBaseUrlInput(selectedProvider?.base_url || '');
    resetAddModelState();
    setIsProviderMenuOpen(false);
  }, [selectedProvider?.base_url, selectedProviderCode, isAddModalOpen, resetAddModelState]);

  useEffect(() => {
    if (!isProviderMenuOpen && !isModelMenuOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      const clickedProviderMenu = providerMenuRef.current?.contains(target) ?? false;
      const clickedModelMenu = modelMenuRef.current?.contains(target) ?? false;
      if (clickedProviderMenu || clickedModelMenu) {
        return;
      }
      setIsProviderMenuOpen(false);
      setIsModelMenuOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [isModelMenuOpen, isProviderMenuOpen]);

  useEffect(() => {
    if (!isModelMenuOpen) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      modelInputRef.current?.focus();
    });
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [isModelMenuOpen]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      if (isAddModalOpen || isEditModalOpen) {
        setIsProviderMenuOpen(false);
        setIsModelMenuOpen(false);
        setIsAddModalOpen(false);
        setIsEditModalOpen(false);
        return;
      }
      onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isAddModalOpen, isEditModalOpen, isOpen, onClose]);

  const handleOpenAddModal = (providerCode?: string) => {
    if (!providers.length) {
      toast.error('当前没有可用的模型供应商');
      return;
    }
    if (providerCode) {
      setSelectedProviderCode(providerCode);
    } else {
      setSelectedProviderCode((prev) => {
        if (prev && providers.some((provider) => provider.code === prev)) {
          return prev;
        }
        return getDefaultProviderCode(providers);
      });
    }
    resetAddModelState();
    setIsModelMenuOpen(false);
    setIsAddModalOpen(true);
  };

  const handleCloseAddModal = () => {
    setIsAddModalOpen(false);
    setIsProviderMenuOpen(false);
    setIsModelMenuOpen(false);
    resetAddModelState();
  };

  const handlePrepareModels = async () => {
    if (!selectedProvider) {
      return;
    }
    const nextApiKey = apiKeyInput.trim();
    const nextBaseUrl = baseUrlInput.trim();
    if (selectedProvider.code === 'custom' && !nextBaseUrl) {
      toast.error('请输入 Base URL');
      return;
    }
    if (!nextApiKey && !selectedProvider.credential_configured) {
      toast.error(`请输入 ${selectedProvider.api_key_label}`);
      return;
    }
    try {
      setIsPreparingModels(true);
      setRemoteModelsError(null);
      await loadProviderModels(selectedProvider.code, {
        silent: true,
        apiKey: nextApiKey || undefined,
        baseUrl: selectedProvider.code === 'custom' ? nextBaseUrl : undefined,
      });
    } catch (error) {
      setRemoteModelsError(getErrorMessage(error, '获取模型列表失败'));
    } finally {
      setIsPreparingModels(false);
    }
  };

  const handleOpenModelField = () => {
    if (isPreparingModels || addingModelName !== null) {
      return;
    }
    if (!isModelMenuOpen) {
      setIsModelMenuOpen(true);
      setModelSearchQuery('');
      void handlePrepareModels();
    }
  };

  const handleAddSelectedModel = async () => {
    if (!selectedProvider || !selectedRemoteModel) {
      return;
    }
    try {
      setAddingModelName(selectedRemoteModel.name);
      const nextApiKey = apiKeyInput.trim();
      const nextBaseUrl = baseUrlInput.trim();
      await api.createUserModelBinding(
        selectedProvider.code,
        selectedRemoteModel.name,
        nextApiKey || undefined,
        selectedProvider.code === 'custom' ? nextBaseUrl : undefined,
      );
      await refreshCatalog();
      notifyChatModelsUpdated();
      toast.success(`已添加模型 ${selectedRemoteModel.display_name}`);
      handleCloseAddModal();
    } catch (error) {
      toast.error(getErrorMessage(error, '添加模型失败'));
    } finally {
      setAddingModelName(null);
    }
  };

  const handleOpenEditProvider = (provider: ModelProviderCatalogItem) => {
    setEditingProvider({
      code: provider.code,
      displayName: provider.display_name,
      apiKeyLabel: provider.api_key_label,
      baseUrl: provider.base_url,
      isCustom: provider.code === 'custom',
      hasCredential: provider.credential_configured,
      maskedApiKey: provider.api_key_masked,
    });
    setEditBaseUrlInput(provider.base_url);
    setEditApiKeyInput('');
    setIsEditModalOpen(true);
  };

  const handleSaveProviderConfig = async () => {
    if (!editingProvider) {
      return;
    }
    const nextApiKey = editApiKeyInput.trim();
    const nextBaseUrl = editBaseUrlInput.trim();
    if (editingProvider.isCustom && !nextBaseUrl) {
      toast.error('请输入 Base URL');
      return;
    }
    if (!nextApiKey && !editingProvider.hasCredential) {
      toast.error(`请输入 ${editingProvider.apiKeyLabel}`);
      return;
    }

    try {
      setIsSavingProviderConfig(true);
      await api.saveModelProviderCredential(
        editingProvider.code,
        nextApiKey || undefined,
        editingProvider.isCustom ? nextBaseUrl : undefined,
      );
      await refreshCatalog();
      toast.success(`已更新 ${editingProvider.displayName} 配置`);
      setIsEditModalOpen(false);
      setEditingProvider(null);
      setEditApiKeyInput('');
      setEditBaseUrlInput('');
    } catch (error) {
      toast.error(getErrorMessage(error, '保存供应商配置失败'));
    } finally {
      setIsSavingProviderConfig(false);
    }
  };

  const handleToggleProvider = async (provider: ProviderCard) => {
    if (!provider.models.length) {
      return;
    }
    const currentState = getProviderToggleState(provider.models);
    const nextEnabled = !currentState.checked;
    try {
      setTogglingProviderCode(provider.code);
      const updatedModels = await api.updateProviderBindingsEnabled(provider.code, nextEnabled);
      applyProviderModelsUpdate(provider.code, updatedModels);
      notifyChatModelsUpdated();
    } catch (error) {
      toast.error(getErrorMessage(error, '更新供应商状态失败'));
    } finally {
      setTogglingProviderCode(null);
    }
  };

  const handleToggleModel = async (model: UserConfiguredModel) => {
    try {
      setTogglingBindingId(model.id);
      const updated = await api.updateUserModelBindingEnabled(model.id, !model.is_enabled);
      applySingleModelUpdate(updated);
      notifyChatModelsUpdated();
    } catch (error) {
      toast.error(getErrorMessage(error, '更新模型状态失败'));
    } finally {
      setTogglingBindingId(null);
    }
  };

  const handleRunHealthCheck = async (model: UserConfiguredModel) => {
    try {
      setHealthCheckingBindingId(model.id);
      const updated = await api.runUserModelHealthCheck(model.id);
      applySingleModelUpdate(updated);
      if (updated.health_status === 'healthy') {
        toast.success(updated.last_health_latency_ms ? `检测成功 · ${updated.last_health_latency_ms}ms` : '检测成功');
      } else {
        toast.error(updated.last_health_error || '检测失败');
      }
    } catch (error) {
      toast.error(getErrorMessage(error, '检测模型状态失败'));
    } finally {
      setHealthCheckingBindingId(null);
    }
  };

  const handleDeleteModel = async (model: UserConfiguredModel) => {
    try {
      setDeletingBindingId(model.id);
      await api.deleteUserModelBinding(model.id);
      await refreshCatalog();
      notifyChatModelsUpdated();
      toast.success(`已移除模型 ${getDisplayTitle(model)}`);
    } catch (error) {
      toast.error(getErrorMessage(error, '移除模型失败'));
    } finally {
      setDeletingBindingId(null);
    }
  };

  const handleDeleteProviderConfig = async () => {
    if (!deletingProviderConfig) {
      return;
    }
    try {
      setDeletingProviderCode(deletingProviderConfig.code);
      await api.deleteModelProviderCredential(deletingProviderConfig.code);
      await refreshCatalog();
      notifyChatModelsUpdated();
      toast.success(`已清除 ${deletingProviderConfig.displayName} 配置`);
      setDeletingProviderConfig(null);
      setIsEditModalOpen(false);
      handleCloseAddModal();
    } catch (error) {
      toast.error(getErrorMessage(error, '清除供应商配置失败'));
    } finally {
      setDeletingProviderCode(null);
    }
  };

  if (!isOpen) {
    return null;
  }

  return (
    <>
      <div className={styles.overlay} onClick={onClose}>
        <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
          <div className={styles.pageHeader}>
            <div className={styles.headerTitle}>模型配置</div>

            <div className={styles.headerActions}>
              <button type="button" className={styles.headerPrimaryButton} onClick={() => handleOpenAddModal()}>
                <Plus size={16} />
                <span>添加模型</span>
              </button>
              <button type="button" className={styles.closeButton} onClick={onClose} aria-label="关闭模型配置">
                <X size={18} />
              </button>
            </div>
          </div>

          {isLoading && !catalog ? (
            <div className={styles.loadingWrap}>正在加载模型配置...</div>
          ) : loadError && !catalog ? (
            <div className={styles.errorWrap}>
              <div className={styles.errorTitle}>模型配置加载失败</div>
              <div className={styles.errorMessage}>{loadError}</div>
              <button
                type="button"
                className={styles.headerGhostButton}
                onClick={() => {
                  void loadCatalog().catch((error) => {
                    toast.error(getErrorMessage(error, '加载模型配置失败'));
                  });
                }}
              >
                重新加载
              </button>
            </div>
          ) : (
            <div className={styles.pageBody}>
              {!providerCards.length ? (
                <div className={styles.emptyState}>
                  <div className={styles.emptyTitle}>还没有已配置的供应商</div>
                  <div className={styles.emptyDescription}>点击右上角“添加模型”，先配置供应商凭据，再把模型加入你的可用列表。</div>
                </div>
              ) : (
                <div className={styles.providerList}>
                  {providerCards.map((provider) => {
                    const providerToggleState = getProviderToggleState(provider.models);
                    const isExpanded = expandedProviders[provider.code] ?? provider.models.length > 0;

                    return (
                      <section key={provider.code} className={styles.providerCard}>
                        <div
                          className={`${styles.providerCardHeader} ${isExpanded ? styles.providerCardHeaderExpanded : ''}`}
                          onClick={() => setExpandedProviders((prev) => ({ ...prev, [provider.code]: !isExpanded }))}
                        >
                          <div className={styles.providerCardTitleRow}>
                            {isExpanded ? (
                              <ChevronDown size={20} className={styles.providerChevron} />
                            ) : (
                              <ChevronRight size={20} className={styles.providerChevron} />
                            )}
                            <ModelProviderIcon iconKey={provider.icon_key} alt={provider.display_name} size={18} />
                            <div className={styles.providerCardTitle}>{provider.display_name}</div>
                          </div>

                          <div
                            className={styles.providerCardActions}
                            onClick={(event) => event.stopPropagation()}
                            onMouseDown={(event) => event.stopPropagation()}
                          >
                            <ToggleSwitch
                              checked={providerToggleState.checked}
                              indeterminate={providerToggleState.indeterminate}
                              disabled={!provider.models.length || togglingProviderCode === provider.code}
                              onChange={() => void handleToggleProvider(provider)}
                              ariaLabel={`${provider.display_name} 模型批量开关`}
                            />
                            <button
                              type="button"
                              className={styles.actionButton}
                              aria-label={`为 ${provider.display_name} 添加模型`}
                              onClick={() => handleOpenAddModal(provider.code)}
                            >
                              <Plus size={18} />
                            </button>
                            <button
                              type="button"
                              className={styles.actionButton}
                              aria-label={`清除 ${provider.display_name} 配置`}
                              disabled={deletingProviderCode === provider.code}
                              onClick={() => setDeletingProviderConfig({
                                code: provider.code,
                                displayName: provider.display_name,
                                modelsCount: provider.models.length,
                              })}
                            >
                              <Minus size={18} />
                            </button>
                            <button
                              type="button"
                              className={styles.actionButton}
                              aria-label={`编辑 ${provider.display_name} 配置`}
                              onClick={() => handleOpenEditProvider(provider)}
                            >
                              <PencilLine size={18} />
                            </button>
                          </div>
                        </div>

                        {isExpanded ? (
                          <div className={styles.providerCardContent}>
                            {!provider.models.length ? (
                              <div className={styles.providerEmpty}>当前供应商还没有添加模型。</div>
                            ) : (
                              provider.models.map((model, index) => (
                                <div key={model.id}>
                                  <div className={styles.modelRow}>
                                    <div className={styles.modelRowLeft}>
                                      {model.health_status === 'healthy' || model.health_status === 'unhealthy' ? (
                                        <span
                                          className={`${styles.healthDot} ${model.health_status === 'healthy' ? styles.healthDotHealthy : styles.healthDotUnhealthy}`}
                                          title={getHealthStatusTitle(model)}
                                        />
                                      ) : (
                                        <span className={styles.healthDotPlaceholder} />
                                      )}
                                      <div className={styles.modelName}>{model.provider_model_name}</div>
                                      <ToggleSwitch
                                        checked={model.is_enabled}
                                        disabled={togglingBindingId === model.id}
                                        onChange={() => void handleToggleModel(model)}
                                        ariaLabel={`${model.provider_model_name} 启用开关`}
                                      />
                                    </div>

                                    <div className={styles.modelRowActions}>
                                      <button
                                        type="button"
                                        className={styles.modelActionButton}
                                        aria-label={`检测 ${model.provider_model_name} 状态`}
                                        title={getHealthStatusTitle(model) || '检测模型状态'}
                                        onClick={() => void handleRunHealthCheck(model)}
                                      >
                                        <HeartPulse size={18} className={healthCheckingBindingId === model.id ? styles.spinning : ''} />
                                      </button>
                                      <button
                                        type="button"
                                        className={styles.modelActionButton}
                                        aria-label={`移除模型 ${model.provider_model_name}`}
                                        disabled={deletingBindingId === model.id}
                                        onClick={() => void handleDeleteModel(model)}
                                      >
                                        <Trash2 size={18} />
                                      </button>
                                    </div>
                                  </div>
                                  {index < provider.models.length - 1 ? <div className={styles.modelDivider} /> : null}
                                </div>
                              ))
                            )}
                          </div>
                        ) : null}
                      </section>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {isAddModalOpen && selectedProvider ? (
        <div className={styles.subOverlay} onClick={handleCloseAddModal}>
          <div className={styles.subModal} onClick={(event) => event.stopPropagation()}>
            <div className={styles.subHeader}>
              <div className={styles.subTitle}>添加模型</div>
              <button type="button" className={styles.closeButton} onClick={handleCloseAddModal} aria-label="关闭添加模型弹窗">
                <X size={18} />
              </button>
            </div>

            <div className={styles.subBody}>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>模型供应商</label>
                <div className={styles.providerSelect} ref={providerMenuRef}>
                  <button
                    type="button"
                    className={styles.providerTrigger}
                    onClick={() => setIsProviderMenuOpen((prev) => !prev)}
                    aria-haspopup="listbox"
                    aria-expanded={isProviderMenuOpen}
                    aria-label="选择模型供应商"
                  >
                    <div className={styles.providerTriggerContent}>
                      <ModelProviderIcon iconKey={selectedProvider.icon_key} alt={selectedProvider.display_name} size={18} />
                      <span className={styles.providerTriggerLabel}>{selectedProvider.display_name}</span>
                    </div>
                    <ChevronDown size={16} className={`${styles.providerTriggerIcon} ${isProviderMenuOpen ? styles.providerTriggerIconOpen : ''}`} />
                  </button>

                  {isProviderMenuOpen ? (
                    <div className={styles.providerMenu} role="listbox" aria-label="模型供应商列表">
                      {providers.map((provider) => {
                        const isSelected = provider.code === selectedProvider.code;
                        return (
                          <button
                            key={provider.code}
                            type="button"
                            role="option"
                            aria-selected={isSelected}
                            className={`${styles.providerOption} ${isSelected ? styles.providerOptionSelected : ''}`}
                            onClick={() => {
                              setSelectedProviderCode(provider.code);
                              setIsProviderMenuOpen(false);
                            }}
                          >
                            <ModelProviderIcon iconKey={provider.icon_key} alt={provider.display_name} size={18} />
                            <span className={styles.providerOptionLabel}>{provider.display_name}</span>
                            {isSelected ? <Check size={16} className={styles.providerOptionCheck} /> : null}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="provider-base-url">
                  {selectedProvider.code === 'custom' ? 'Base URL' : '默认 Base URL'}
                </label>
                <input
                  id="provider-base-url"
                  className={styles.input}
                  value={selectedProvider.code === 'custom' ? baseUrlInput : selectedProvider.base_url}
                  readOnly={selectedProvider.code !== 'custom'}
                  onChange={(event) => {
                    setBaseUrlInput(event.target.value);
                    invalidateRemoteModels();
                  }}
                  placeholder={selectedProvider.code === 'custom' ? 'https://your-provider.example.com/v1' : undefined}
                />
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="provider-api-key">
                  {selectedProvider.api_key_label}
                </label>
                <input
                  id="provider-api-key"
                  type="text"
                  className={styles.input}
                  value={apiKeyInput}
                  onChange={(event) => {
                    setApiKeyInput(event.target.value);
                    invalidateRemoteModels();
                  }}
                  placeholder={
                    selectedProvider.credential_configured
                      ? `如需覆盖，输入新的 ${selectedProvider.api_key_label}`
                      : `请输入 ${selectedProvider.api_key_label}`
                  }
                  autoComplete="off"
                />
                <div className={styles.fieldHelp}>{providerCredentialHint}</div>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel}>模型名称</label>
                <div className={styles.modelSelect} ref={modelMenuRef}>
                  <div
                    className={`${styles.modelTrigger} ${remoteModelsError ? styles.modelTriggerError : ''}`}
                    role="combobox"
                    aria-haspopup="listbox"
                    aria-expanded={isModelMenuOpen}
                    aria-label="选择模型"
                    onClick={handleOpenModelField}
                  >
                    <input
                      ref={modelInputRef}
                      type="text"
                      className={styles.modelInput}
                      value={modelInputValue}
                      onFocus={handleOpenModelField}
                      onChange={(event) => {
                        if (!isModelMenuOpen) {
                          handleOpenModelField();
                        }
                        setModelSearchQuery(event.target.value);
                      }}
                      placeholder={isPreparingModels || isLoadingRemoteModels ? '正在获取模型列表...' : '搜索并选择模型'}
                      readOnly={!isModelMenuOpen}
                      disabled={isPreparingModels || addingModelName !== null}
                      autoComplete="off"
                    />
                    <button
                      type="button"
                      className={styles.modelTriggerToggle}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (isModelMenuOpen) {
                          setIsModelMenuOpen(false);
                          return;
                        }
                        handleOpenModelField();
                      }}
                      aria-label={isModelMenuOpen ? '收起模型列表' : '展开模型列表'}
                      disabled={isPreparingModels || addingModelName !== null}
                    >
                      <ChevronDown
                        size={16}
                        className={`${styles.modelTriggerIcon} ${isModelMenuOpen ? styles.modelTriggerIconOpen : ''}`}
                      />
                    </button>
                  </div>

                  {isModelMenuOpen ? (
                    <div className={styles.modelMenu} role="listbox" aria-label="模型列表">
                      {isPreparingModels || isLoadingRemoteModels ? (
                        <div className={styles.modelMenuState}>正在获取模型列表...</div>
                      ) : filteredRemoteModels.length ? (
                        filteredRemoteModels.map((model) => {
                          const isSelected = model.name === selectedRemoteModelName;
                          return (
                            <button
                              key={model.name}
                              type="button"
                              role="option"
                              aria-selected={isSelected}
                              className={`${styles.modelOption} ${isSelected ? styles.modelOptionSelected : ''}`}
                              onClick={() => {
                                setSelectedRemoteModelName(model.name);
                                setIsModelMenuOpen(false);
                              }}
                            >
                              <div className={styles.modelOptionBody}>
                                <span className={styles.modelOptionTitle}>{model.display_name}</span>
                                <span className={styles.modelOptionMeta}>{model.name}</span>
                              </div>
                              {isSelected ? <Check size={16} className={styles.modelOptionCheck} /> : null}
                            </button>
                          );
                        })
                      ) : (
                        <div className={styles.modelMenuState}>
                          {remoteModelsError
                            ? '模型列表加载失败'
                            : remoteModelsState?.models.length
                              ? '没有匹配的模型'
                              : '当前没有可用模型'}
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>

                {remoteModelsError ? (
                  <div className={`${styles.fieldHelp} ${styles.fieldHelpError}`}>{remoteModelsError}</div>
                ) : alreadyAdded ? (
                  <div className={styles.fieldHelp}>这个模型已经添加过了。</div>
                ) : null}
              </div>
            </div>

            <div className={styles.subFooter}>
              <button type="button" className={styles.secondaryButton} onClick={handleCloseAddModal}>
                取消
              </button>
              <button
                type="button"
                className={styles.primaryButton}
                onClick={() => void handleAddSelectedModel()}
                disabled={!selectedRemoteModel || alreadyAdded || addingModelName === selectedRemoteModel.name}
              >
                {alreadyAdded ? '已添加' : addingModelName === selectedRemoteModel?.name ? '确认中...' : '确认'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {isEditModalOpen && editingProvider ? (
        <div className={styles.subOverlay} onClick={() => setIsEditModalOpen(false)}>
          <div className={styles.subModal} onClick={(event) => event.stopPropagation()}>
            <div className={styles.subHeader}>
              <div className={styles.subTitle}>编辑供应商配置</div>
              <button type="button" className={styles.closeButton} onClick={() => setIsEditModalOpen(false)} aria-label="关闭编辑供应商弹窗">
                <X size={18} />
              </button>
            </div>

            <div className={styles.subBody}>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>模型供应商</label>
                <div className={styles.readonlyProviderField}>
                  <ModelProviderIcon
                    iconKey={providers.find((item) => item.code === editingProvider.code)?.icon_key || editingProvider.code}
                    alt={editingProvider.displayName}
                    size={18}
                  />
                  <span>{editingProvider.displayName}</span>
                </div>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="edit-provider-base-url">
                  {editingProvider.isCustom ? 'Base URL' : '默认 Base URL'}
                </label>
                <input
                  id="edit-provider-base-url"
                  className={styles.input}
                  value={editingProvider.isCustom ? editBaseUrlInput : editingProvider.baseUrl}
                  readOnly={!editingProvider.isCustom}
                  onChange={(event) => setEditBaseUrlInput(event.target.value)}
                  placeholder={editingProvider.isCustom ? 'https://your-provider.example.com/v1' : undefined}
                />
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="edit-provider-api-key">
                  {editingProvider.apiKeyLabel}
                </label>
                <input
                  id="edit-provider-api-key"
                  type="text"
                  className={styles.input}
                  value={editApiKeyInput}
                  onChange={(event) => setEditApiKeyInput(event.target.value)}
                  placeholder={
                    editingProvider.hasCredential
                      ? `保持不变请留空，当前为 ${editingProvider.maskedApiKey || '已配置'}`
                      : `请输入 ${editingProvider.apiKeyLabel}`
                  }
                  autoComplete="off"
                />
              </div>
            </div>

            <div className={styles.subFooter}>
              <button type="button" className={styles.secondaryButton} onClick={() => setIsEditModalOpen(false)}>
                取消
              </button>
              <button
                type="button"
                className={styles.primaryButton}
                onClick={() => void handleSaveProviderConfig()}
                disabled={isSavingProviderConfig}
              >
                {isSavingProviderConfig ? '保存中...' : '确认'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <ConfirmModal
        isOpen={Boolean(deletingProviderConfig)}
        onCancel={() => setDeletingProviderConfig(null)}
        onConfirm={() => void handleDeleteProviderConfig()}
        loading={deletingProviderCode === deletingProviderConfig?.code}
        title="删除供应商下所有模型"
        message={
          deletingProviderConfig
            ? deletingProviderConfig.modelsCount > 0
              ? `确定删除 ${deletingProviderConfig.displayName} 下的全部 ${deletingProviderConfig.modelsCount} 个模型吗？删除后，如需继续使用该供应商，需要重新添加模型。`
              : `确定删除 ${deletingProviderConfig.displayName} 吗？这会清除该供应商配置。`
            : ''
        }
        confirmText="确认删除"
        cancelText="取消"
        type="danger"
      />
    </>
  );
}
