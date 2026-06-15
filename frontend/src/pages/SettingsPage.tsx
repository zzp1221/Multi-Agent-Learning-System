import { useCallback, useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  Check,
  ChevronDown,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  Plus,
  RotateCcw,
  Save,
  Settings2,
  SlidersHorizontal,
  TestTube2,
  Trash2,
  TriangleAlert,
  Wand2,
} from 'lucide-react';
import type { LayoutOutletContext } from '../components/Layout';
import {
  llmSettingsApi,
  type ProviderCapability,
} from '../api/settings';
import { getErrorMessage } from '../api/request';
import {
  MODEL_FIELDS,
  PROVIDERS,
  buildInitialProviders,
  buildPayload,
  emptySkillDraft,
  normalizeProviderKey,
  normalizeSkillDraft,
  parseSkillMarkdown,
  providerPreset,
  type ComponentDraft,
  type ProviderDraft,
  type ProviderKey,
  type SkillDraft,
} from '../utils/llmSettingsDraft';

interface ModelListState {
  loading: boolean;
  models: string[];
  error: string;
}

const ABILITY_GROUPS = [
  {
    key: 'rewrite_tutor',
    label: '改写与辅导',
    description: 'query rewrite、tutor、conversation summary',
    components: ['query_rewrite_llm', 'tutor_llm', 'conversation_summary_llm'],
    defaultModel: 'fast_model',
  },
  {
    key: 'generation',
    label: '资源生成',
    description: 'document、slides、mindmap、resource push',
    components: ['generation_llm', 'planning_llm', 'review_llm', 'resource_push_llm'],
    defaultModel: 'main_chat_model',
  },
  {
    key: 'assessment',
    label: '练习与评估',
    description: 'practice、judge、evaluation、profile',
    components: ['practice_llm', 'judge_llm', 'evaluation_llm', 'profile_llm'],
    defaultModel: 'main_chat_model',
  },
  {
    key: 'path',
    label: '路径规划',
    description: 'path planning、retrieval summary、safety',
    components: ['path_planning_llm', 'retrieval_llm', 'safety_llm'],
    defaultModel: 'reasoning_model',
  },
];

const ADVANCED_COMPONENTS = [
  { key: 'query_rewrite_llm', label: 'Query Rewrite' },
  { key: 'retrieval_llm', label: 'Retrieval Summary' },
  { key: 'generation_llm', label: 'Generation' },
  { key: 'practice_llm', label: 'Practice' },
  { key: 'judge_llm', label: 'Judge' },
  { key: 'profile_llm', label: 'Profile' },
  { key: 'tutor_llm', label: 'Tutor' },
  { key: 'conversation_summary_llm', label: 'Conversation Summary' },
  { key: 'planning_llm', label: 'Planning' },
  { key: 'review_llm', label: 'Review' },
  { key: 'safety_llm', label: 'Safety' },
  { key: 'evaluation_llm', label: 'Evaluation' },
  { key: 'path_planning_llm', label: 'Path Planning' },
  { key: 'resource_push_llm', label: 'Resource Push' },
];

function abilitySkillKey(groupKey: string) {
  return `ability:${groupKey}`;
}

export default function SettingsPage() {
  const { isAuthenticated, openAuthModal } = useOutletContext<LayoutOutletContext>();
  const [activeProvider, setActiveProvider] = useState<ProviderKey>('dashscope');
  const [fallbackProvider, setFallbackProvider] = useState('');
  const [providers, setProviders] = useState<Record<ProviderKey, ProviderDraft>>(() => buildInitialProviders());
  const [componentOverrides, setComponentOverrides] = useState<Record<string, ComponentDraft>>({});
  const [skillOverrides, setSkillOverrides] = useState<Record<string, SkillDraft>>({});
  const [providerCapabilities, setProviderCapabilities] = useState<ProviderCapability[]>([]);
  const [providerModels, setProviderModels] = useState<Record<string, ModelListState>>({});
  const [selectedProvider, setSelectedProvider] = useState<ProviderKey>('dashscope');
  const [showSecret, setShowSecret] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const providerKeys = useMemo(() => PROVIDERS.map((item) => item.key), []);
  const currentProvider = providers[selectedProvider];
  const selectedModelState = providerModels[selectedProvider] ?? { loading: false, models: [], error: '' };

  const loadSettings = useCallback(async () => {
    if (!isAuthenticated) {
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await llmSettingsApi.get();
      const drafts = buildInitialProviders(response);
      const active = normalizeProviderKey(response.activeProvider) || 'dashscope';
      setProviders(drafts);
      setProviderCapabilities(response.providerCapabilities);
      setActiveProvider(active);
      setFallbackProvider(normalizeProviderKey(response.fallbackProvider) || '');
      setSelectedProvider(active);
      setComponentOverrides(
        Object.fromEntries(
          Object.entries(response.componentOverrides ?? {}).map(([key, value]) => [
            key,
            {
              provider: normalizeProviderKey(value.provider) || value.provider,
              model: value.model,
            },
          ]),
        ),
      );
      setSkillOverrides(
        Object.fromEntries(
          Object.entries(response.skillOverrides ?? {}).map(([key, value]) => [key, normalizeSkillDraft(value)]),
        ),
      );
    } catch (loadError) {
      setError(getErrorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated) {
      openAuthModal('login', '登录后才能配置个人 LLM');
      return;
    }
    void loadSettings();
  }, [isAuthenticated, loadSettings, openAuthModal]);

  const updateProvider = (provider: ProviderKey, patch: Partial<ProviderDraft>) => {
    setProviders((current) => ({
      ...current,
      [provider]: {
        ...current[provider],
        ...patch,
      },
    }));
  };

  const updateProviderModel = (provider: ProviderKey, key: string, value: string) => {
    setProviders((current) => ({
      ...current,
      [provider]: {
        ...current[provider],
        modelOverrides: {
          ...current[provider].modelOverrides,
          [key]: value,
        },
      },
    }));
  };

  const modelOptionsForProvider = useCallback((provider: string) => {
    const providerKey = normalizeProviderKey(provider);
    if (!providerKey) {
      return [];
    }
    return providerModels[providerKey]?.models ?? [];
  }, [providerModels]);

  const fetchProviderModels = useCallback(async (provider: ProviderKey = selectedProvider) => {
    const draft = providers[provider];
    if (!draft) {
      return;
    }
    setProviderModels((current) => ({
      ...current,
      [provider]: {
        models: current[provider]?.models ?? [],
        error: '',
        loading: true,
      },
    }));
    try {
      const response = await llmSettingsApi.listModels({
        provider,
        baseUrl: draft.baseUrl.trim(),
        apiKey: draft.apiKey.trim() || undefined,
        apiSecret: draft.apiSecret.trim() || undefined,
        appId: draft.appId.trim() || undefined,
      });
      setProviderModels((current) => ({
        ...current,
        [provider]: {
          models: response.models,
          error: '',
          loading: false,
        },
      }));
      setProviders((current) => {
        const latest = current[provider];
        if (!latest) {
          return current;
        }
        const nextOverrides = { ...latest.modelOverrides };
        MODEL_FIELDS.forEach((field) => {
          if (!nextOverrides[field.key] && response.models[0]) {
            nextOverrides[field.key] = response.models[0];
          }
        });
        return {
          ...current,
          [provider]: {
            ...latest,
            baseUrl: response.baseUrl || latest.baseUrl,
            modelOverrides: nextOverrides,
          },
        };
      });
      setNotice(`已拉取 ${providerPreset(provider).label} 的 ${response.models.length} 个可用模型。`);
    } catch (modelsError) {
      setProviderModels((current) => ({
        ...current,
        [provider]: {
          models: current[provider]?.models ?? [],
          error: getErrorMessage(modelsError),
          loading: false,
        },
      }));
    }
  }, [providers, selectedProvider]);

  const applyGroup = (groupKey: string, provider: string, model: string) => {
    const group = ABILITY_GROUPS.find((item) => item.key === groupKey);
    if (!group) {
      return;
    }
    setComponentOverrides((current) => {
      const next = { ...current };
      group.components.forEach((component) => {
        next[component] = { provider, model };
      });
      return next;
    });
  };

  const clearGroup = (groupKey: string) => {
    const group = ABILITY_GROUPS.find((item) => item.key === groupKey);
    if (!group) {
      return;
    }
    setComponentOverrides((current) => {
      const next = { ...current };
      group.components.forEach((component) => {
        delete next[component];
      });
      return next;
    });
  };

  const updateSkill = (target: string, patch: Partial<SkillDraft>) => {
    setSkillOverrides((current) => ({
      ...current,
      [target]: {
        ...(current[target] ?? emptySkillDraft()),
        ...patch,
      },
    }));
  };

  const parseSkill = (target: string) => {
    setSkillOverrides((current) => {
      const currentDraft = current[target] ?? emptySkillDraft();
      const parsed = parseSkillMarkdown(currentDraft.body);
      return {
        ...current,
        [target]: {
          enabled: currentDraft.enabled || Boolean(parsed.body),
          name: parsed.name || currentDraft.name,
          description: parsed.description || currentDraft.description,
          body: parsed.body,
        },
      };
    });
  };

  const clearSkill = (target: string) => {
    setSkillOverrides((current) => {
      const next = { ...current };
      delete next[target];
      return next;
    });
  };

  const payload = () => buildPayload(activeProvider, fallbackProvider, providers, componentOverrides, skillOverrides);

  const save = async () => {
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const response = await llmSettingsApi.save(payload());
      setProviders(buildInitialProviders(response));
      setNotice('设置已保存，后续智能体请求会按当前用户配置路由。');
      const active = normalizeProviderKey(response.activeProvider) || selectedProvider;
      if (active && response.providers[active]?.hasApiKey) {
        await fetchProviderModels(active);
      }
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setError('');
    setNotice('');
    try {
      const response = await llmSettingsApi.test(payload());
      setNotice(response.message || (response.ok ? '配置可用' : '配置已保存，但当前厂商缺少 API Key'));
      await loadSettings();
      const active = normalizeProviderKey(response.activeProvider) || selectedProvider;
      if (active) {
        await fetchProviderModels(active);
      }
    } catch (testError) {
      setError(getErrorMessage(testError));
    } finally {
      setTesting(false);
    }
  };

  const reset = async () => {
    setResetting(true);
    setError('');
    setNotice('');
    try {
      await llmSettingsApi.delete();
      setActiveProvider('dashscope');
      setFallbackProvider('');
      setSelectedProvider('dashscope');
      setProviders(buildInitialProviders());
      setComponentOverrides({});
      setSkillOverrides({});
      setNotice('个人 LLM 配置已清空。正式用户任务需要重新保存自己的 API Key 后才能调用 Agent LLM。');
    } catch (resetError) {
      setError(getErrorMessage(resetError));
    } finally {
      setResetting(false);
    }
  };

  if (!isAuthenticated) {
    return (
      <div className="settings-page mx-auto flex min-h-[calc(100dvh-126px)] max-w-5xl items-center justify-center px-4">
        <div className="w-full rounded-[20px] bg-white/72 p-6 text-center dark:bg-slate-900/70">
          <KeyRound className="mx-auto h-8 w-8 text-primary-600 dark:text-primary-300" />
          <h1 className="mt-3 text-xl font-semibold text-slate-900 dark:text-white">个人 LLM 设置</h1>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">登录后可以保存自己的 API Key、厂商和模型路由。</p>
          <button
            type="button"
            onClick={() => openAuthModal('login', '登录后才能配置个人 LLM')}
            className="mt-5 inline-flex h-10 items-center justify-center rounded-lg bg-primary-600 px-4 text-sm font-medium text-white hover:bg-primary-700"
          >
            登录
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-page mx-auto grid w-full max-w-[1280px] gap-5 px-4 py-5 md:px-6 lg:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-normal text-primary-700 dark:text-primary-300">
            <Settings2 className="h-4 w-4" />
            LLM Runtime
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950 dark:text-white">个人模型设置</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void loadSettings()}
            disabled={loading}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-white/70 px-3 text-sm font-medium text-slate-600 hover:bg-white disabled:opacity-60 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-900"
          >
            {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
            刷新
          </button>
          <button
            type="button"
            onClick={() => void test()}
            disabled={testing || saving}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60 dark:bg-white dark:text-slate-900"
          >
            {testing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}
            保存并检查
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || testing}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60"
          >
            {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            保存
          </button>
        </div>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="flex items-start gap-2 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
          <Check className="mt-0.5 h-4 w-4 shrink-0" />
          {notice}
        </div>
      ) : null}

      <section className="grid gap-4 rounded-[20px] bg-white/70 p-4 dark:bg-slate-900/66">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">运行策略</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Agent LLM 只使用当前登录用户保存的 API Key，不会写入浏览器存储。</p>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="主厂商">
            <Select value={activeProvider} onChange={(value) => setActiveProvider(value as ProviderKey)}>
              {providerKeys.map((key) => (
                <option key={key} value={key}>{providerPreset(key).label}</option>
              ))}
            </Select>
          </Field>
          <Field label="备用厂商">
            <Select value={fallbackProvider} onChange={setFallbackProvider}>
              <option value="">不启用备用</option>
              {providerKeys.filter((key) => key !== activeProvider).map((key) => (
                <option key={key} value={key}>{providerPreset(key).label}</option>
              ))}
            </Select>
          </Field>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="rounded-[20px] bg-white/70 p-3 dark:bg-slate-900/66">
          <div className="mb-2 px-2 text-xs font-semibold text-slate-500 dark:text-slate-400">厂商</div>
          <div className="grid gap-2">
            {PROVIDERS.map((provider) => {
              const draft = providers[provider.key];
              const selected = selectedProvider === provider.key;
              return (
                <button
                  key={provider.key}
                  type="button"
                  onClick={() => setSelectedProvider(provider.key)}
                  className={`flex min-h-12 items-center justify-between gap-3 rounded-lg px-3 text-left transition-colors ${
                    selected
                      ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
                      : 'text-slate-600 hover:bg-white/80 dark:text-slate-300 dark:hover:bg-slate-800'
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold">{provider.label}</span>
                    <span className="block truncate text-xs opacity-65">{provider.key}</span>
                  </span>
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${draft.hasApiKey ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600'}`} />
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid gap-4 rounded-[20px] bg-white/70 p-4 dark:bg-slate-900/66">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">{providerPreset(selectedProvider).label}</h2>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                {providerCapabilities.find((item) => item.key === selectedProvider)?.description ?? 'OpenAI-compatible chat completions'}
              </p>
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
            <div className="grid gap-3">
              <Field label="Base URL">
                <Input
                  value={currentProvider.baseUrl}
                  onChange={(value) => updateProvider(selectedProvider, { baseUrl: value })}
                  placeholder="https://api.example.com/v1"
                />
              </Field>
              <div className="grid gap-3 md:grid-cols-2">
                {providerPreset(selectedProvider).secretFields.includes('apiKey') ? (
                  <Field label={currentProvider.hasApiKey ? 'API Key 已保存' : 'API Key'}>
                    <SecretInput
                      value={currentProvider.apiKey}
                      visible={showSecret}
                      onChange={(value) => updateProvider(selectedProvider, { apiKey: value })}
                      placeholder={currentProvider.hasApiKey ? '留空则保留已保存密钥' : '输入 API Key'}
                    />
                  </Field>
                ) : null}
                {providerPreset(selectedProvider).secretFields.includes('apiSecret') ? (
                  <Field label={currentProvider.hasApiSecret ? 'API Secret 已保存' : 'API Secret'}>
                    <SecretInput
                      value={currentProvider.apiSecret}
                      visible={showSecret}
                      onChange={(value) => updateProvider(selectedProvider, { apiSecret: value })}
                      placeholder={currentProvider.hasApiSecret ? '留空则保留已保存密钥' : '输入 API Secret'}
                    />
                  </Field>
                ) : null}
                {providerPreset(selectedProvider).secretFields.includes('appId') ? (
                  <Field label={currentProvider.hasAppId ? 'App ID 已保存' : 'App ID'}>
                    <SecretInput
                      value={currentProvider.appId}
                      visible={showSecret}
                      onChange={(value) => updateProvider(selectedProvider, { appId: value })}
                      placeholder={currentProvider.hasAppId ? '留空则保留已保存 App ID' : '输入 App ID'}
                    />
                  </Field>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => setShowSecret((current) => !current)}
                className="inline-flex h-9 w-fit items-center gap-2 rounded-lg bg-slate-100 px-3 text-xs font-medium text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              >
                {showSecret ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                {showSecret ? '隐藏输入' : '显示输入'}
              </button>
            </div>

            <div className="grid gap-3 rounded-lg bg-slate-50/70 p-3 dark:bg-slate-950/30">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">模型映射</div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void fetchProviderModels(selectedProvider)}
                    disabled={selectedModelState.loading}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-2.5 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-60"
                  >
                    {selectedModelState.loading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                    拉取模型
                  </button>
                  <button
                    type="button"
                    onClick={() => updateProvider(selectedProvider, { modelOverrides: { ...providerPreset(selectedProvider).models } })}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-2.5 text-xs font-medium text-slate-500 hover:text-primary-700 dark:bg-slate-900 dark:text-slate-300"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    默认
                  </button>
                </div>
              </div>
              {selectedModelState.error ? (
                <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                  {selectedModelState.error}
                </div>
              ) : null}
              {MODEL_FIELDS.map((field) => (
                <Field key={field.key} label={field.label}>
                  <ModelSelect
                    value={currentProvider.modelOverrides[field.key] ?? ''}
                    onChange={(value) => updateProviderModel(selectedProvider, field.key, value)}
                    models={selectedModelState.models}
                    fallbackValue={providerPreset(selectedProvider).models[field.key]}
                  />
                </Field>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 rounded-[20px] bg-white/70 p-4 dark:bg-slate-900/66">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">能力组</h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">把重写、辅导、评估等相近 Agent 统一到同一组模型。</p>
          </div>
          <Wand2 className="h-5 w-5 text-primary-600 dark:text-primary-300" />
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {ABILITY_GROUPS.map((group) => (
            <AbilityGroupRow
              key={group.key}
              label={group.label}
              description={group.description}
              provider={componentOverrides[group.components[0]]?.provider || activeProvider}
              model={componentOverrides[group.components[0]]?.model || group.defaultModel}
              providerOptions={providerKeys}
              getModels={modelOptionsForProvider}
              onApply={(provider, model) => applyGroup(group.key, provider, model)}
              onClear={() => clearGroup(group.key)}
            />
          ))}
        </div>
        <div className="grid gap-3 border-t border-slate-200/70 pt-4 dark:border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">自定义 Agent Skill</h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">只作为提示词偏好追加到对应 Agent，不会改变工具权限、输出协议或安全规则。</p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {ABILITY_GROUPS.map((group) => {
              const target = abilitySkillKey(group.key);
              return (
                <SkillEditor
                  key={target}
                  title={`${group.label} Skill`}
                  description={group.description}
                  value={skillOverrides[target] ?? emptySkillDraft()}
                  onChange={(patch) => updateSkill(target, patch)}
                  onParse={() => parseSkill(target)}
                  onClear={() => clearSkill(target)}
                />
              );
            })}
          </div>
        </div>
      </section>

      <section className="rounded-[20px] bg-white/70 p-4 dark:bg-slate-900/66">
        <button
          type="button"
          onClick={() => setAdvancedOpen((current) => !current)}
          className="flex w-full items-center justify-between gap-3 text-left"
        >
          <span>
            <span className="flex items-center gap-2 text-base font-semibold text-slate-900 dark:text-white">
              <SlidersHorizontal className="h-4 w-4" />
              高级 Agent 覆盖
            </span>
            <span className="mt-1 block text-sm text-slate-500 dark:text-slate-400">精确指定某个 Agent 的厂商和模型；留空会继承能力组或主厂商。</span>
          </span>
          <ChevronDown className={`h-5 w-5 text-slate-400 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
        </button>
        {advancedOpen ? (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {ADVANCED_COMPONENTS.map((component) => {
              const value = componentOverrides[component.key] ?? { provider: '', model: '' };
              return (
                <div key={component.key} className="grid gap-2 rounded-lg bg-slate-50/72 p-3 dark:bg-slate-950/30">
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">{component.label}</div>
                  <div className="grid gap-2 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_auto]">
                    <Select
                      value={value.provider}
                      onChange={(provider) => setComponentOverrides((current) => ({
                        ...current,
                        [component.key]: { ...value, provider },
                      }))}
                    >
                      <option value="">继承</option>
                      {providerKeys.map((key) => (
                        <option key={key} value={key}>{providerPreset(key).label}</option>
                      ))}
                    </Select>
                    <ModelSelect
                      value={value.model}
                      onChange={(model) => setComponentOverrides((current) => ({
                        ...current,
                        [component.key]: { ...value, model },
                      }))}
                      models={modelOptionsForProvider(value.provider || activeProvider)}
                      fallbackValue=""
                      inheritLabel="继承模型映射"
                    />
                    <button
                      type="button"
                      onClick={() => setComponentOverrides((current) => {
                        const next = { ...current };
                        delete next[component.key];
                        return next;
                      })}
                      className="inline-flex h-10 items-center justify-center rounded-lg bg-white px-3 text-slate-500 hover:text-rose-600 dark:bg-slate-900 dark:text-slate-300"
                      aria-label={`清空 ${component.label}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                  <SkillEditor
                    title={`${component.label} Skill`}
                    description="覆盖该 Agent 的组级 Skill"
                    compact
                    value={skillOverrides[component.key] ?? emptySkillDraft()}
                    onChange={(patch) => updateSkill(component.key, patch)}
                    onParse={() => parseSkill(component.key)}
                    onClear={() => clearSkill(component.key)}
                  />
                </div>
              );
            })}
          </div>
        ) : null}
      </section>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[20px] bg-white/70 p-4 dark:bg-slate-900/66">
        <div className="text-sm text-slate-500 dark:text-slate-400">保存后，新发起的智能体任务会读取这份用户级配置。</div>
        <button
          type="button"
          onClick={() => void reset()}
          disabled={resetting}
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-rose-50 px-3 text-sm font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-60 dark:bg-rose-500/10 dark:text-rose-300"
        >
          {resetting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
          清空个人配置
        </button>
      </div>
    </div>
  );
}

function AbilityGroupRow(props: {
  label: string;
  description: string;
  provider: string;
  model: string;
  providerOptions: ProviderKey[];
  getModels: (provider: string) => string[];
  onApply: (provider: string, model: string) => void;
  onClear: () => void;
}) {
  const [provider, setProvider] = useState(props.provider);
  const [model, setModel] = useState(props.model);

  useEffect(() => {
    setProvider(props.provider);
    setModel(props.model);
  }, [props.model, props.provider]);

  return (
    <div className="grid gap-3 rounded-lg bg-slate-50/72 p-3 dark:bg-slate-950/30">
      <div>
        <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">{props.label}</div>
        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{props.description}</div>
      </div>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,0.9fr)_minmax(0,1fr)]">
        <Select value={provider} onChange={setProvider}>
          {props.providerOptions.map((key) => (
            <option key={key} value={key}>{providerPreset(key).label}</option>
          ))}
        </Select>
        <ModelSelect
          value={model}
          onChange={setModel}
          models={props.getModels(provider)}
          fallbackValue=""
          inheritLabel="继承模型映射"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => props.onApply(provider, model)}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary-600 px-3 text-xs font-medium text-white hover:bg-primary-700"
        >
          <Plus className="h-3.5 w-3.5" />
          应用到组
        </button>
        <button
          type="button"
          onClick={props.onClear}
          className="inline-flex h-9 items-center gap-2 rounded-lg bg-white px-3 text-xs font-medium text-slate-500 hover:text-rose-600 dark:bg-slate-900 dark:text-slate-300"
        >
          <Trash2 className="h-3.5 w-3.5" />
          清空组
        </button>
      </div>
    </div>
  );
}

function SkillEditor(props: {
  title: string;
  description: string;
  value: SkillDraft;
  compact?: boolean;
  onChange: (patch: Partial<SkillDraft>) => void;
  onParse: () => void;
  onClear: () => void;
}) {
  const enabled = props.value.enabled;
  const bodyLength = props.value.body.length;

  return (
    <div className={props.compact
      ? 'mt-2 grid gap-3 border-t border-slate-200/70 pt-3 dark:border-slate-800'
      : 'grid gap-3 rounded-lg bg-slate-50/72 p-3 dark:bg-slate-950/30'}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{props.title}</div>
          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{props.description}</div>
        </div>
        <button
          type="button"
          onClick={() => props.onChange({ enabled: !enabled })}
          className={`inline-flex h-8 shrink-0 items-center rounded-lg px-2.5 text-xs font-medium ${
            enabled
              ? 'bg-primary-600 text-white hover:bg-primary-700'
              : 'bg-white text-slate-500 hover:text-primary-700 dark:bg-slate-900 dark:text-slate-300'
          }`}
        >
          {enabled ? '已启用' : '启用'}
        </button>
      </div>
      {enabled ? (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="名称">
              <Input
                value={props.value.name}
                onChange={(name) => props.onChange({ name })}
                placeholder="如：苏格拉底式辅导"
              />
            </Field>
            <Field label="描述">
              <Input
                value={props.value.description}
                onChange={(description) => props.onChange({ description })}
                placeholder="用于说明这段偏好"
              />
            </Field>
          </div>
          <Field label="Skill 正文">
            <Textarea
              value={props.value.body}
              onChange={(body) => props.onChange({ body })}
              placeholder="可以粘贴完整 SKILL.md；点击解析会提取 frontmatter，并只保留正文。"
            />
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className={`text-xs ${bodyLength > 8000 ? 'text-rose-600' : 'text-slate-400 dark:text-slate-500'}`}>
              {bodyLength}/8000
            </span>
            <span className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={props.onParse}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-2.5 text-xs font-medium text-slate-500 hover:text-primary-700 dark:bg-slate-900 dark:text-slate-300"
              >
                <Wand2 className="h-3.5 w-3.5" />
                解析 SKILL.md
              </button>
              <button
                type="button"
                onClick={props.onClear}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white px-2.5 text-xs font-medium text-slate-500 hover:text-rose-600 dark:bg-slate-900 dark:text-slate-300"
              >
                <Trash2 className="h-3.5 w-3.5" />
                清空
              </button>
            </span>
          </div>
        </>
      ) : null}
    </div>
  );
}

function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{props.label}</span>
      {props.children}
    </label>
  );
}

function Input(props: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <input
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      placeholder={props.placeholder}
      className="h-10 w-full rounded-lg bg-white/78 px-3 text-sm text-slate-800 outline-none transition focus:bg-white focus:ring-2 focus:ring-primary-500/20 dark:bg-slate-900/78 dark:text-slate-100 dark:focus:bg-slate-900"
    />
  );
}

function Textarea(props: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <textarea
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      placeholder={props.placeholder}
      rows={6}
      className="min-h-32 w-full resize-y rounded-lg bg-white/78 px-3 py-2 text-sm text-slate-800 outline-none transition focus:bg-white focus:ring-2 focus:ring-primary-500/20 dark:bg-slate-900/78 dark:text-slate-100 dark:focus:bg-slate-900"
    />
  );
}

function SecretInput(props: { value: string; visible: boolean; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <div className="relative">
      <input
        value={props.value}
        type={props.visible ? 'text' : 'password'}
        autoComplete="off"
        onChange={(event) => props.onChange(event.target.value)}
        placeholder={props.placeholder}
        className="h-10 w-full rounded-lg bg-white/78 px-3 pr-9 text-sm text-slate-800 outline-none transition focus:bg-white focus:ring-2 focus:ring-primary-500/20 dark:bg-slate-900/78 dark:text-slate-100 dark:focus:bg-slate-900"
      />
      <KeyRound className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
    </div>
  );
}

function ModelSelect(props: {
  value: string;
  onChange: (value: string) => void;
  models: string[];
  fallbackValue?: string;
  inheritLabel?: string;
}) {
  const options = useMemo(() => {
    const values = new Set<string>();
    props.models.forEach((model) => {
      const trimmed = model.trim();
      if (trimmed) {
        values.add(trimmed);
      }
    });
    if (props.fallbackValue?.trim()) {
      values.add(props.fallbackValue.trim());
    }
    if (props.value.trim()) {
      values.add(props.value.trim());
    }
    return Array.from(values);
  }, [props.fallbackValue, props.models, props.value]);

  return (
    <select
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      className="h-10 w-full rounded-lg bg-white/78 px-3 text-sm text-slate-800 outline-none transition focus:bg-white focus:ring-2 focus:ring-primary-500/20 dark:bg-slate-900/78 dark:text-slate-100 dark:focus:bg-slate-900"
    >
      <option value="">{props.inheritLabel ?? '请选择模型'}</option>
      {options.map((model) => (
        <option key={model} value={model}>{model}</option>
      ))}
    </select>
  );
}

function Select(props: { value: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return (
    <select
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      className="h-10 w-full rounded-lg bg-white/78 px-3 text-sm text-slate-800 outline-none transition focus:bg-white focus:ring-2 focus:ring-primary-500/20 dark:bg-slate-900/78 dark:text-slate-100 dark:focus:bg-slate-900"
    >
      {props.children}
    </select>
  );
}
