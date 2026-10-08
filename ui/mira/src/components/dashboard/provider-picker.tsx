import { Loader2 } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { ProviderLoginDialog } from "@/components/dashboard/provider-login-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/sonner"
import { api } from "@/lib/api"
import { errorMessage } from "@/lib/api/http"
import type {
  LoginProviderValue,
  ProviderInfo,
  ProviderSettings,
  ProviderValue,
} from "@/lib/api/settings"
import { cn } from "@/lib/utils"

function StatusBadge({ provider }: { provider: ProviderInfo }) {
  if (!provider.available)
    return <Badge variant="destructive">Unavailable</Badge>
  if (!provider.requires_login) return null
  return provider.logged_in ? (
    <Badge variant="secondary">Logged in</Badge>
  ) : (
    <Badge variant="outline">Not logged in</Badge>
  )
}

// Radio-style list of LLM providers with in-browser login for the
// subscription-based ones. `onChanged` fires after anything that alters the
// available model lists (switching provider, logging in or out) so the parent
// can refetch them. `readOnly` disables every action.
export function ProviderPicker({
  onChanged,
  readOnly = false,
}: {
  onChanged?: () => void
  readOnly?: boolean
}) {
  const [data, setData] = useState<ProviderSettings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<ProviderValue | null>(null)
  const [switched, setSwitched] = useState(false)
  const [loginFor, setLoginFor] = useState<ProviderInfo | null>(null)
  const [loginOpen, setLoginOpen] = useState(false)

  const refresh = useCallback(
    () =>
      api
        .getProviders()
        .then((d) => {
          setData(d)
          setLoadError(null)
        })
        .catch((err) => setLoadError(errorMessage(err))),
    []
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  const startLogin = (p: ProviderInfo) => {
    setLoginFor(p)
    setLoginOpen(true)
  }

  const select = async (p: ProviderInfo) => {
    if (readOnly || busy || !data || p.value === data.active) return
    if (!p.available) return
    if (p.requires_login && !p.logged_in) return startLogin(p)
    setBusy(p.value)
    try {
      await api.setProvider(p.value)
      setSwitched(true)
      await refresh()
      onChanged?.()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const logout = async (p: ProviderInfo) => {
    setBusy(p.value)
    try {
      await api.providerLogout(p.value as LoginProviderValue)
      toast.success(`Logged out of ${p.label}`)
      await refresh()
      onChanged?.()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  if (loadError && !data) {
    return (
      <p className="text-sm break-words text-destructive">
        Couldn't load providers: {loadError}
      </p>
    )
  }
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading providers…
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="LLM provider" className="space-y-2">
        {data.providers.map((p) => {
          const active = p.value === data.active
          const needsLogin = p.requires_login && !p.logged_in
          // Selecting a not-yet-logged-in provider opens the login dialog,
          // so only unavailable ones (or read-only) are truly disabled.
          const disabled = readOnly || !p.available || busy !== null
          const isBusy = busy === p.value
          return (
            <div
              key={p.value}
              className={cn(
                "flex items-center gap-3 rounded-lg border p-3",
                active && "border-primary bg-primary/5",
                !p.available && "opacity-70"
              )}
            >
              <button
                type="button"
                role="radio"
                aria-checked={active}
                disabled={disabled}
                onClick={() => void select(p)}
                className="flex min-w-0 flex-1 items-start gap-3 rounded-sm text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed"
              >
                <span
                  aria-hidden
                  className={cn(
                    "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                    active ? "border-primary" : "border-input"
                  )}
                >
                  {isBusy ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    active && (
                      <span className="size-2 rounded-full bg-primary" />
                    )
                  )}
                </span>
                <span className="min-w-0 space-y-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{p.label}</span>
                    <StatusBadge provider={p} />
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {p.description}
                  </span>
                  {p.detail && (
                    <span
                      className={cn(
                        "block text-xs break-words",
                        p.available
                          ? "text-muted-foreground"
                          : "text-destructive"
                      )}
                    >
                      {p.detail}
                    </span>
                  )}
                </span>
              </button>
              {p.requires_login && p.available && !readOnly && (
                <Button
                  size="sm"
                  variant={needsLogin ? "default" : "outline"}
                  disabled={busy !== null}
                  onClick={() => (needsLogin ? startLogin(p) : void logout(p))}
                >
                  {needsLogin ? "Log in" : "Log out"}
                </Button>
              )}
            </div>
          )
        })}
      </div>

      {switched && (
        <p role="status" className="text-xs text-muted-foreground">
          Model choices changed — re-pick your models below and Save.
        </p>
      )}

      <ProviderLoginDialog
        provider={loginFor ? (loginFor.value as LoginProviderValue) : null}
        label={loginFor?.label ?? ""}
        open={loginOpen}
        onOpenChange={setLoginOpen}
        onComplete={() => {
          void refresh()
          onChanged?.()
        }}
      />
    </div>
  )
}
