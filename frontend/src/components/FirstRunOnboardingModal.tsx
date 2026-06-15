import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, Eye, EyeOff, KeyRound, LoaderCircle, Sparkles, UserRoundSearch } from 'lucide-react';
import { llmSettingsApi } from '../api/settings';
import { getErrorMessage } from '../api/request';
import { smartEngineApi, type ProfileOnboardingPayload } from '../api/smartEngine';
import type { AuthUser } from '../api/auth';
import {
  MODEL_FIELDS,
  PROVIDERS,
  buildInitialProviders,
  buildPayload,
  normalizeProviderKey,
  providerPreset,
  type ProviderDraft,
  type ProviderKey,
} from '../utils/llmSettingsDraft';

export type FirstRunOnboardingStep = 'llm' | 'profile';

interface FirstRunOnboardingModalProps {
  open: boolean;
  step: FirstRunOnboardingStep;
  currentUser: AuthUser | null;
  onStepChange: (step: FirstRunOnboardingStep) => void;
  onLlmCompleted: () => void;
  onProfileCompleted: () => void;
}

const PROFILE_OPTIONS = {
  majorCode: [
    ['PROGRAMMING_LANGUAGES', '编程语言'],
    ['DATA_STRUCTURES_ALGORITHMS', '数据结构/算法'],
    ['OPERATING_SYSTEMS', '操作系统'],
    ['COMPUTER_NETWORKS', '计算机网络'],
    ['DATABASES', '数据库'],
    ['SOFTWARE_ENGINEERING', '软件工程'],
    ['COMPILERS', '编译原理'],
    ['COMPUTER_ARCHITECTURE', '计算机体系结构'],
    ['AI_ML', 'AI/ML'],
    ['SECURITY', '安全'],
    ['DISTRIBUTED_CLOUD', '分布式/云原生'],
    ['FRONTEND_WEB', '前端 Web'],
    ['BACKEND_SYSTEMS', '后端系统'],
    ['MATH_FOUNDATIONS', '数学基础'],
    ['DEV_TOOLS', '开发工具'],
  ],
  knowledgeBase: [
    '零基础，刚开始学习',
    '有基础，但知识不系统',
    '中等基础，需要查漏补缺',
    '基础较好，希望项目实战',
  ],
  learningPreference: [
    '先讲概念，再给例题',
    '项目实战驱动',
    '多做题巩固',
    '图文结合，步骤清晰',
  ],
  resourcePreference: [
    ['DOCUMENT', '文档教程'],
    ['VIDEO', '视频讲解'],
    ['QUIZ', '练习题'],
    ['CODE_CASE', '代码案例'],
  ],
};

export default function FirstRunOnboardingModal(props: FirstRunOnboardingModalProps) {
  const [activeProvider, setActiveProvider] = useState<ProviderKey>('dashscope');
  const [providers, setProviders] = useState<Record<ProviderKey, ProviderDraft>>(() => buildInitialProviders());
  const [showSecret, setShowSecret] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [profileSaving, setProfileSaving] = useState(false);
  const [error, setError] = useState('');
  const [majorCode, setMajorCode] = useState('');
  const [knowledgeBase, setKnowledgeBase] = useState('');
  const [learningGoal, setLearningGoal] = useState('');
  const [learningPreference, setLearningPreference] = useState('');
  const [resourcePreference, setResourcePreference] = useState('');

  useEffect(() => {
    if (!props.open) {
      return;
    }
    setError('');
    setMajorCode(props.currentUser?.majorCode ?? '');
    void loadLlmSettings();
  }, [props.currentUser?.majorCode, props.open]);

  useEffect(() => {
    if (!props.open) {
      return;
    }
    setError('');
  }, [props.open, props.step]);

  const selectedProvider = providers[activeProvider];
  const providerInfo = useMemo(() => providerPreset(activeProvider), [activeProvider]);

  const updateProvider = (provider: ProviderKey, patch: Partial<ProviderDraft>) => {
    setProviders((current) => ({
      ...current,
      [provider]: {
        ...current[provider],
        ...patch,
      },
    }));
  };

  const updateModel = (key: string, value: string) => {
    setProviders((current) => ({
      ...current,
      [activeProvider]: {
        ...current[activeProvider],
        modelOverrides: {
          ...current[activeProvider].modelOverrides,
          [key]: value,
        },
      },
    }));
  };

  const loadLlmSettings = async () => {
    try {
      const response = await llmSettingsApi.get();
      const drafts = buildInitialProviders(response);
      const active = normalizeProviderKey(response.activeProvider) || 'dashscope';
      setProviders(drafts);
      setActiveProvider(active);
      setAdvancedOpen(active === 'custom_openai_compatible');
    } catch (loadError) {
      setError(getErrorMessage(loadError));
    }
  };

  const saveLlm = async () => {
    const current = providers[activeProvider];
    if (!current.apiKey.trim() && !current.hasApiKey) {
      setError('请先填写并保存 API Key');
      return;
    }
    const mainModel = current.modelOverrides.main_chat_model?.trim();
    if (!mainModel) {
      setError('请先填写主对话模型');
      return;
    }
    if (activeProvider === 'custom_openai_compatible' && !current.baseUrl.trim()) {
      setError('自定义兼容端点需要填写 Base URL');
      return;
    }

    setLlmSaving(true);
    setError('');
    try {
      const response = await llmSettingsApi.save(buildPayload(activeProvider, '', providers));
      const active = normalizeProviderKey(response.activeProvider) || activeProvider;
      if (!response.providers[active]?.hasApiKey) {
        setError('模型配置已保存，但当前厂商仍缺少 API Key');
        return;
      }
      setProviders(buildInitialProviders(response));
      props.onLlmCompleted();
      props.onStepChange('profile');
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setLlmSaving(false);
    }
  };

  const saveProfile = async () => {
    const payload: ProfileOnboardingPayload = {
      majorCode: majorCode.trim(),
      knowledgeBase: knowledgeBase.trim(),
      learningGoal: learningGoal.trim(),
      learningPreference: learningPreference.trim(),
      resourcePreference: resourcePreference.trim(),
    };
    if (Object.values(payload).some((value) => !value)) {
      setError('请先完成所有学习画像字段');
      return;
    }

    setProfileSaving(true);
    setError('');
    try {
      await smartEngineApi.completeProfileOnboarding(payload);
      window.dispatchEvent(new Event('app:profile-updated'));
      props.onProfileCompleted();
    } catch (saveError) {
      setError(getErrorMessage(saveError));
    } finally {
      setProfileSaving(false);
    }
  };

  if (!props.open) {
    return null;
  }

  const direction = props.step === 'llm' ? -1 : 1;
  const inputClass = 'w-full rounded-xl bg-slate-50/86 px-3.5 py-2.5 text-sm outline-none transition-all focus:bg-white focus:shadow-[0_10px_24px_rgba(59,130,246,0.14)] dark:bg-slate-950/70 dark:text-slate-200 dark:focus:bg-slate-950/90';
  const labelClass = 'mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400';

  return (
    <div className="fixed inset-0 z-[130] flex items-center justify-center px-4 py-6">
      <div className="absolute inset-0 bg-slate-950/56 backdrop-blur-sm" />
      <div className="relative max-h-[92dvh] w-full max-w-[620px] overflow-hidden rounded-[24px] bg-white/94 shadow-2xl shadow-slate-950/12 backdrop-blur dark:bg-slate-900/94 dark:shadow-slate-950/40">
        <div className="flex items-center justify-between gap-4 px-5 pb-3 pt-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-600 text-white">
              {props.step === 'llm' ? <KeyRound className="h-4 w-4" /> : <UserRoundSearch className="h-4 w-4" />}
            </div>
            <div>
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">
                {props.step === 'llm' ? '配置模型和 API Key' : '完成学习画像'}
              </h3>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                {props.step === 'llm' ? '智能对话和路径刷新将使用你的个人模型配置。' : '系统将据此生成首版个性化学习路径。'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <span className={`h-1.5 w-7 rounded-full ${props.step === 'llm' ? 'bg-primary-600' : 'bg-slate-200 dark:bg-slate-700'}`} />
            <span className={`h-1.5 w-7 rounded-full ${props.step === 'profile' ? 'bg-primary-600' : 'bg-slate-200 dark:bg-slate-700'}`} />
          </div>
        </div>

        <div className="relative max-h-[76dvh] overflow-y-auto px-5 pb-5 pt-2">
          <AnimatePresence mode="wait" initial={false}>
            {props.step === 'llm' ? (
              <motion.div
                key="llm"
                initial={{ opacity: 0, x: direction * 36 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -36 }}
                transition={{ duration: 0.32, ease: [0.32, 0.72, 0, 1] }}
                className="space-y-3.5"
              >
                <label className="block">
                  <div className={labelClass}>厂商</div>
                  <select
                    value={activeProvider}
                    onChange={(event) => {
                      const next = normalizeProviderKey(event.target.value) || 'dashscope';
                      setActiveProvider(next);
                      setAdvancedOpen(next === 'custom_openai_compatible');
                    }}
                    className={inputClass}
                  >
                    {PROVIDERS.map((provider) => (
                      <option key={provider.key} value={provider.key}>{provider.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <div className={labelClass}>API Key</div>
                  <div className="relative">
                    <input
                      type={showSecret ? 'text' : 'password'}
                      value={selectedProvider.apiKey}
                      onChange={(event) => updateProvider(activeProvider, { apiKey: event.target.value })}
                      className={`${inputClass} pr-11`}
                      placeholder={selectedProvider.hasApiKey ? '已保存 API Key，留空则继续使用' : '粘贴你的 API Key'}
                      autoComplete="off"
                    />
                    <button
                      type="button"
                      onClick={() => setShowSecret((value) => !value)}
                      className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 hover:bg-white hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                    >
                      {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </label>
                <label className="block">
                  <div className={labelClass}>模型</div>
                  <input
                    value={selectedProvider.modelOverrides.main_chat_model ?? ''}
                    onChange={(event) => updateModel('main_chat_model', event.target.value)}
                    className={inputClass}
                    placeholder={providerInfo.models.main_chat_model || '例如 gpt-4.1-mini'}
                  />
                </label>

                <div className="rounded-xl bg-slate-50/70 p-3 dark:bg-slate-950/42">
                  <button
                    type="button"
                    onClick={() => setAdvancedOpen((value) => !value)}
                    className="flex w-full items-center justify-between text-left text-xs font-medium text-slate-600 dark:text-slate-300"
                  >
                    <span>高级项</span>
                    <ChevronDown className={`h-4 w-4 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
                  </button>
                  {advancedOpen ? (
                    <div className="mt-3 space-y-3">
                      <label className="block">
                        <div className={labelClass}>Base URL</div>
                        <input
                          value={selectedProvider.baseUrl}
                          onChange={(event) => updateProvider(activeProvider, { baseUrl: event.target.value })}
                          className={inputClass}
                          placeholder={providerInfo.baseUrl || 'https://api.example.com/v1'}
                        />
                      </label>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {MODEL_FIELDS.filter((field) => field.key !== 'main_chat_model').map((field) => (
                          <label key={field.key} className="block">
                            <div className={labelClass}>{field.label}</div>
                            <input
                              value={selectedProvider.modelOverrides[field.key] ?? ''}
                              onChange={(event) => updateModel(field.key, event.target.value)}
                              className={inputClass}
                              placeholder={providerInfo.models[field.key] || field.label}
                            />
                          </label>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>

                {error ? <ErrorBox message={error} /> : null}
                <button
                  type="button"
                  onClick={() => void saveLlm()}
                  disabled={llmSaving}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-70"
                >
                  {llmSaving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  保存模型配置
                </button>
              </motion.div>
            ) : (
              <motion.div
                key="profile"
                initial={{ opacity: 0, x: 36 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -direction * 36 }}
                transition={{ duration: 0.32, ease: [0.32, 0.72, 0, 1] }}
                className="space-y-3.5"
              >
                <SelectField label="专业方向" value={majorCode} onChange={setMajorCode} options={PROFILE_OPTIONS.majorCode} placeholder="请选择专业方向" className={inputClass} />
                <SelectField label="当前基础" value={knowledgeBase} onChange={setKnowledgeBase} options={PROFILE_OPTIONS.knowledgeBase.map((item) => [item, item])} placeholder="请选择当前基础" className={inputClass} />
                <label className="block">
                  <div className={labelClass}>学习目标</div>
                  <input
                    value={learningGoal}
                    onChange={(event) => setLearningGoal(event.target.value)}
                    className={inputClass}
                    placeholder="例如：两个月内掌握数据库索引并完成项目实战"
                  />
                </label>
                <SelectField label="学习偏好" value={learningPreference} onChange={setLearningPreference} options={PROFILE_OPTIONS.learningPreference.map((item) => [item, item])} placeholder="请选择学习偏好" className={inputClass} />
                <SelectField label="资源偏好" value={resourcePreference} onChange={setResourcePreference} options={PROFILE_OPTIONS.resourcePreference} placeholder="请选择资源偏好" className={inputClass} />

                {error ? <ErrorBox message={error} /> : null}
                <button
                  type="button"
                  onClick={() => void saveProfile()}
                  disabled={profileSaving}
                  className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-70"
                >
                  {profileSaving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  保存画像并生成学习路径
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function SelectField(props: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[][];
  placeholder: string;
  className: string;
}) {
  return (
    <label className="block">
      <div className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">{props.label}</div>
      <select value={props.value} onChange={(event) => props.onChange(event.target.value)} className={props.className}>
        <option value="">{props.placeholder}</option>
        {props.options.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
    </label>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:bg-rose-500/10 dark:text-rose-400">
      {message}
    </div>
  );
}
