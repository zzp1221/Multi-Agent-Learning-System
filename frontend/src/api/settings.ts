import { request } from './request';

export interface ProviderCapability {
  key: string;
  label: string;
  description: string;
}

export interface UserLlmProviderView {
  provider: string;
  baseUrl: string;
  hasApiKey: boolean;
  hasApiSecret: boolean;
  hasAppId: boolean;
  modelOverrides: Record<string, string>;
}

export interface UserLlmComponentView {
  provider: string;
  model: string;
}

export interface UserLlmSkillView {
  enabled: boolean;
  name: string;
  description: string;
  body: string;
}

export interface UserLlmSettingsResponse {
  enabled: boolean;
  activeProvider: string;
  fallbackProvider: string;
  providerCapabilities: ProviderCapability[];
  providers: Record<string, UserLlmProviderView>;
  componentOverrides: Record<string, UserLlmComponentView>;
  skillOverrides: Record<string, UserLlmSkillView>;
}

export interface UserLlmProviderConfigRequest {
  provider: string;
  baseUrl?: string;
  apiKey?: string;
  apiSecret?: string;
  appId?: string;
  modelOverrides: Record<string, string>;
}

export interface UserLlmComponentOverrideRequest {
  provider: string;
  model: string;
}

export interface UserLlmSkillOverrideRequest {
  enabled: boolean;
  name: string;
  description: string;
  body: string;
}

export interface UserLlmSettingsRequest {
  enabled: boolean;
  activeProvider: string;
  fallbackProvider: string;
  providers: Record<string, UserLlmProviderConfigRequest>;
  componentOverrides: Record<string, UserLlmComponentOverrideRequest>;
  skillOverrides: Record<string, UserLlmSkillOverrideRequest>;
}

export interface UserLlmTestResponse {
  ok: boolean;
  activeProvider: string;
  message: string;
}

export interface UserLlmModelListRequest {
  provider: string;
  baseUrl?: string;
  apiKey?: string;
  apiSecret?: string;
  appId?: string;
}

export interface UserLlmModelListResponse {
  provider: string;
  baseUrl: string;
  models: string[];
}

export const llmSettingsApi = {
  get(): Promise<UserLlmSettingsResponse> {
    return request.get<UserLlmSettingsResponse>('/api/settings/llm', { dedupe: false });
  },
  save(payload: UserLlmSettingsRequest): Promise<UserLlmSettingsResponse> {
    return request.put<UserLlmSettingsResponse>('/api/settings/llm', payload);
  },
  test(payload: UserLlmSettingsRequest): Promise<UserLlmTestResponse> {
    return request.post<UserLlmTestResponse>('/api/settings/llm/test', payload);
  },
  listModels(payload: UserLlmModelListRequest): Promise<UserLlmModelListResponse> {
    return request.post<UserLlmModelListResponse>('/api/settings/llm/models', payload);
  },
  delete(): Promise<{ status: string }> {
    return request.delete<{ status: string }>('/api/settings/llm');
  },
};
