import { fetchJson, postJson, putJson } from "./http"

// One entry in a model catalog. The optional metadata fields are only
// populated by backends that know them (e.g. xAI, OpenRouter).
export type ModelOption = {
  value: string
  label: string
  recommended?: boolean
  description?: string | null
  /** Context window in tokens. */
  context_window?: number | null
  /** USD per 1M input tokens. */
  input_cost_per_1m?: number | null
  /** USD per 1M output tokens. */
  output_cost_per_1m?: number | null
  reasoning?: boolean | null
}

export type ProviderValue = "default" | "xai" | "codex-cli"

// Providers that authenticate through a browser login (everything but "default").
export type LoginProviderValue = Exclude<ProviderValue, "default">

export type ProviderInfo = {
  value: ProviderValue
  label: string
  description: string
  requires_login: boolean
  logged_in: boolean
  /** False when the provider cannot be used on this server. */
  available: boolean
  /** Human-readable status or reason, e.g. "Codex CLI not found on the server". */
  detail: string | null
}

export type ProviderSettings = {
  active: ProviderValue
  providers: ProviderInfo[]
}

export type LoginState = {
  state: "idle" | "pending" | "complete" | "failed"
  user_code?: string
  verification_url?: string
  expires_in?: number
  error?: string
}

// Model selection, cost estimate, and admin review-config overrides.
export const settingsApi = {
  getModels: () =>
    fetchJson<{
      indexing_model: string
      review_model: string
      security_model: string
      backend: string
      indexing_source: "dashboard" | "config"
      review_source: "dashboard" | "config"
      security_source: "dashboard" | "config"
      config_indexing_model: string
      config_review_model: string
      config_security_model: string
      indexing_options: ModelOption[]
      review_options: ModelOption[]
      security_options: ModelOption[]
      review_thinking_mode: string
      thinking_options: {
        value: string
        label: string
        recommended?: boolean
      }[]
      api_style: string
      api_style_options: {
        value: string
        label: string
        recommended?: boolean
      }[]
    }>("/api/settings/models"),

  getProviders: () => fetchJson<ProviderSettings>("/api/settings/provider"),

  setProvider: (provider: ProviderValue) =>
    putJson<{ ok: boolean }>("/api/settings/provider", { provider }),

  startProviderLogin: (provider: LoginProviderValue) =>
    postJson<LoginState>(`/api/settings/provider/${provider}/login`, {}),

  getProviderLoginStatus: (provider: LoginProviderValue) =>
    fetchJson<LoginState>(`/api/settings/provider/${provider}/login/status`),

  providerLogout: (provider: LoginProviderValue) =>
    postJson<{ ok: boolean }>(`/api/settings/provider/${provider}/logout`, {}),

  saveModels: (
    indexing_model: string,
    review_model: string,
    security_model: string,
    review_thinking_mode: string = "off",
    api_style: string = "chat"
  ) =>
    putJson<{ ok: boolean }>("/api/settings/models", {
      indexing_model,
      review_model,
      security_model,
      review_thinking_mode,
      api_style,
    }),

  getCostEstimate: () =>
    fetchJson<{
      estimated_usd: number
      input_tokens: number
      output_tokens: number
      model: string
      file_count: number
    }>("/api/indexing/estimate"),

  getGlobalSettings: () =>
    fetchJson<{
      overrides: {
        filter?: Record<string, number | boolean | string>
        review?: Record<string, number | boolean | string>
      }
      effective: Record<string, unknown>
    }>("/api/admin/settings"),

  saveGlobalSettings: (
    overrides: Record<string, Record<string, number | boolean | string>>
  ) => putJson<{ ok: boolean }>("/api/admin/settings", { overrides }),
}
