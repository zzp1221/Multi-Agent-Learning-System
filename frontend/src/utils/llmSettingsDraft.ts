import type {
  UserLlmComponentOverrideRequest,
  UserLlmSettingsRequest,
  UserLlmSettingsResponse,
  UserLlmSkillOverrideRequest,
} from '../api/settings';

export type ProviderKey =
  | 'openai'
  | 'dashscope'
  | 'deepseek'
  | 'moonshot'
  | 'zhipu'
  | 'spark'
  | 'mimo'
  | 'custom_openai_compatible';

export interface ProviderDraft {
  provider: ProviderKey;
  baseUrl: string;
  apiKey: string;
  apiSecret: string;
  appId: string;
  hasApiKey: boolean;
  hasApiSecret: boolean;
  hasAppId: boolean;
  modelOverrides: Record<string, string>;
}

export interface ComponentDraft {
  provider: string;
  model: string;
}

export interface SkillDraft {
  enabled: boolean;
  name: string;
  description: string;
  body: string;
}

export const PROVIDERS: Array<{
  key: ProviderKey;
  label: string;
  baseUrl: string;
  models: Record<string, string>;
  secretFields: Array<'apiKey' | 'apiSecret' | 'appId'>;
}> = [
  {
    key: 'openai',
    label: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: 'gpt-4.1-mini',
      fast_model: 'gpt-4.1-mini',
      reasoning_model: 'o4-mini',
      code_model: 'gpt-4.1',
      safety_model: 'gpt-4.1-mini',
    },
  },
  {
    key: 'dashscope',
    label: 'DashScope',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: 'qwen-plus',
      fast_model: 'qwen-turbo',
      reasoning_model: 'qwen-max',
      code_model: 'qwen-coder-plus',
      safety_model: 'qwen-turbo',
    },
  },
  {
    key: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: 'deepseek-chat',
      fast_model: 'deepseek-chat',
      reasoning_model: 'deepseek-reasoner',
      code_model: 'deepseek-chat',
      safety_model: 'deepseek-chat',
    },
  },
  {
    key: 'moonshot',
    label: 'Moonshot',
    baseUrl: 'https://api.moonshot.cn/v1',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: 'moonshot-v1-32k',
      fast_model: 'moonshot-v1-8k',
      reasoning_model: 'moonshot-v1-32k',
      code_model: 'moonshot-v1-32k',
      safety_model: 'moonshot-v1-8k',
    },
  },
  {
    key: 'zhipu',
    label: '智谱 GLM',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: 'glm-4-plus',
      fast_model: 'glm-4-flash',
      reasoning_model: 'glm-4-plus',
      code_model: 'glm-4-plus',
      safety_model: 'glm-4-flash',
    },
  },
  {
    key: 'spark',
    label: '讯飞星火',
    baseUrl: 'https://spark-api-open.xf-yun.com/v1',
    secretFields: ['apiKey', 'apiSecret', 'appId'],
    models: {
      main_chat_model: '4.0Ultra',
      fast_model: 'generalv3.5',
      reasoning_model: 'Spark X2',
      code_model: 'Spark X2-Flash',
      safety_model: 'Spark X2-Flash',
    },
  },
  {
    key: 'mimo',
    label: 'MiMo',
    baseUrl: 'https://api.xiaomimimo.com/v1',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: 'mimo-v2-omni',
      fast_model: 'mimo-v2-flash',
      reasoning_model: 'mimo-v2-omni',
      code_model: 'mimo-v2-omni',
      safety_model: 'mimo-v2-flash',
    },
  },
  {
    key: 'custom_openai_compatible',
    label: '自定义兼容端点',
    baseUrl: '',
    secretFields: ['apiKey'],
    models: {
      main_chat_model: '',
      fast_model: '',
      reasoning_model: '',
      code_model: '',
      safety_model: '',
    },
  },
];

export const MODEL_FIELDS = [
  { key: 'main_chat_model', label: '主对话' },
  { key: 'fast_model', label: '轻量改写' },
  { key: 'reasoning_model', label: '推理规划' },
  { key: 'code_model', label: '代码生成' },
  { key: 'safety_model', label: '安全审核' },
];

export function createDefaultDraft(provider: ProviderKey): ProviderDraft {
  const preset = providerPreset(provider);
  return {
    provider,
    baseUrl: preset.baseUrl,
    apiKey: '',
    apiSecret: '',
    appId: '',
    hasApiKey: false,
    hasApiSecret: false,
    hasAppId: false,
    modelOverrides: { ...preset.models },
  };
}

export function providerPreset(provider: ProviderKey) {
  return PROVIDERS.find((item) => item.key === provider) ?? PROVIDERS[0];
}

export function buildInitialProviders(response?: UserLlmSettingsResponse): Record<ProviderKey, ProviderDraft> {
  const drafts = Object.fromEntries(PROVIDERS.map((item) => [item.key, createDefaultDraft(item.key)])) as Record<ProviderKey, ProviderDraft>;
  if (!response) {
    return drafts;
  }
  for (const [key, value] of Object.entries(response.providers)) {
    const providerKey = normalizeProviderKey(key);
    if (!providerKey) {
      continue;
    }
    drafts[providerKey] = {
      ...drafts[providerKey],
      baseUrl: value.baseUrl || drafts[providerKey].baseUrl,
      hasApiKey: value.hasApiKey,
      hasApiSecret: value.hasApiSecret,
      hasAppId: value.hasAppId,
      modelOverrides: {
        ...drafts[providerKey].modelOverrides,
        ...value.modelOverrides,
      },
    };
  }
  return drafts;
}

export function normalizeProviderKey(value: string): ProviderKey | '' {
  const normalized = value.trim().toLowerCase();
  if (normalized === 'bailian' || normalized === 'aliyun') {
    return 'dashscope';
  }
  if (normalized === 'openai_compatible' || normalized === 'custom') {
    return 'custom_openai_compatible';
  }
  return PROVIDERS.some((item) => item.key === normalized) ? normalized as ProviderKey : '';
}

export function emptySkillDraft(): SkillDraft {
  return {
    enabled: false,
    name: '',
    description: '',
    body: '',
  };
}

export function normalizeSkillDraft(value?: Partial<SkillDraft>): SkillDraft {
  return {
    enabled: Boolean(value?.enabled),
    name: value?.name?.trim() ?? '',
    description: value?.description?.trim() ?? '',
    body: (value?.body ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim(),
  };
}

export function parseSkillMarkdown(raw: string): SkillDraft {
  const body = raw.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  if (!body.startsWith('---')) {
    return { ...emptySkillDraft(), body };
  }

  const endIndex = body.indexOf('\n---', 3);
  if (endIndex < 0) {
    return { ...emptySkillDraft(), body };
  }

  const frontmatter = body.slice(3, endIndex).split('\n');
  const parsed = emptySkillDraft();
  for (const line of frontmatter) {
    const separator = line.indexOf(':');
    if (separator < 0) {
      continue;
    }
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim().replace(/^['"]|['"]$/g, '');
    if (key === 'name') {
      parsed.name = value;
    }
    if (key === 'description') {
      parsed.description = value;
    }
  }
  parsed.body = body.slice(endIndex + 4).trim();
  return parsed;
}

export function buildPayload(
  activeProvider: string,
  fallbackProvider: string,
  providers: Record<ProviderKey, ProviderDraft>,
  componentOverrides: Record<string, ComponentDraft> = {},
  skillOverrides: Record<string, SkillDraft> = {},
): UserLlmSettingsRequest {
  const providerPayload: UserLlmSettingsRequest['providers'] = {};
  const selectedProviders = new Set([activeProvider, fallbackProvider, ...Object.values(componentOverrides).map((item) => item.provider)]);
  for (const provider of Object.values(providers)) {
    if (!shouldPersistProvider(provider, selectedProviders)) {
      continue;
    }
    const item: UserLlmSettingsRequest['providers'][string] = {
      provider: provider.provider,
      baseUrl: provider.baseUrl.trim(),
      modelOverrides: Object.fromEntries(
        Object.entries(provider.modelOverrides).filter(([key, value]) => key.trim() && value.trim()),
      ),
    };
    if (provider.apiKey.trim()) {
      item.apiKey = provider.apiKey.trim();
    }
    if (provider.apiSecret.trim()) {
      item.apiSecret = provider.apiSecret.trim();
    }
    if (provider.appId.trim()) {
      item.appId = provider.appId.trim();
    }
    providerPayload[provider.provider] = item;
  }

  const overrides: Record<string, UserLlmComponentOverrideRequest> = {};
  for (const [component, override] of Object.entries(componentOverrides)) {
    const provider = override.provider.trim();
    const model = override.model.trim();
    if (provider || model) {
      overrides[component] = { provider, model };
    }
  }

  const skills: Record<string, UserLlmSkillOverrideRequest> = {};
  for (const [target, draft] of Object.entries(skillOverrides)) {
    const normalized = normalizeSkillDraft(draft);
    if (!normalized.enabled || !normalized.body) {
      continue;
    }
    skills[target] = normalized;
  }

  return {
    enabled: true,
    activeProvider,
    fallbackProvider,
    providers: providerPayload,
    componentOverrides: overrides,
    skillOverrides: skills,
  };
}

export function isLlmSettingsReady(response: UserLlmSettingsResponse): boolean {
  const active = normalizeProviderKey(response.activeProvider) || response.activeProvider.trim();
  return Boolean(active && response.providers[active]?.hasApiKey);
}

function shouldPersistProvider(provider: ProviderDraft, selectedProviders: Set<string>): boolean {
  if (selectedProviders.has(provider.provider)) {
    return true;
  }
  if (provider.apiKey.trim() || provider.apiSecret.trim() || provider.appId.trim()) {
    return true;
  }
  if (provider.hasApiKey || provider.hasApiSecret || provider.hasAppId) {
    return true;
  }
  const preset = providerPreset(provider.provider);
  if (provider.baseUrl.trim() !== preset.baseUrl.trim()) {
    return true;
  }
  return Object.entries(provider.modelOverrides).some(([key, value]) => value.trim() !== (preset.models[key] ?? '').trim());
}
