import { Check, Copy, ExternalLink, Loader2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "@/components/ui/sonner"
import { api } from "@/lib/api"
import { errorMessage } from "@/lib/api/http"
import type { LoginProviderValue, LoginState } from "@/lib/api/settings"

const POLL_INTERVAL_MS = 2000
// Give up after this many consecutive failed status requests.
const MAX_POLL_ERRORS = 5

function LoginFlow({
  provider,
  label,
  onClose,
  onComplete,
}: {
  provider: LoginProviderValue
  label: string
  onClose: () => void
  onComplete: () => void
}) {
  const [login, setLogin] = useState<LoginState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  // Bumping this restarts the whole flow ("Try again").
  const [attempt, setAttempt] = useState(0)

  // Keep the latest callbacks in refs so the polling effect only restarts on
  // provider/attempt changes, not on every parent render.
  const onCloseRef = useRef(onClose)
  const onCompleteRef = useRef(onComplete)
  useEffect(() => {
    onCloseRef.current = onClose
    onCompleteRef.current = onComplete
  })

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const finish = () => {
      toast.success(`Logged in to ${label}`)
      onCompleteRef.current()
      onCloseRef.current()
    }

    const handle = (state: LoginState) => {
      if (state.state === "complete") return finish()
      if (state.state === "failed") {
        setError(state.error || "Login failed")
        return
      }
      setLogin((prev) => ({ ...prev, ...state }))
    }

    const poll = async (errors: number) => {
      if (cancelled) return
      try {
        const state = await api.getProviderLoginStatus(provider)
        if (cancelled) return
        handle(state)
        if (state.state === "complete" || state.state === "failed") return
        errors = 0
      } catch (err) {
        if (cancelled) return
        errors += 1
        if (errors >= MAX_POLL_ERRORS) {
          setError(errorMessage(err))
          return
        }
      }
      timer = setTimeout(() => void poll(errors), POLL_INTERVAL_MS)
    }

    api
      .startProviderLogin(provider)
      .then((state) => {
        if (cancelled) return
        handle(state)
        if (state.state === "complete" || state.state === "failed") return
        timer = setTimeout(() => void poll(0), POLL_INTERVAL_MS)
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err))
      })

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [provider, label, attempt])

  const retry = () => {
    setLogin(null)
    setError(null)
    setCopied(false)
    setAttempt((a) => a + 1)
  }

  const copyCode = async () => {
    if (!login?.user_code) return
    try {
      await navigator.clipboard.writeText(login.user_code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("Couldn't copy — select the code and copy it manually")
    }
  }

  const url = login?.verification_url
  const safeUrl = url?.startsWith("https://") ? url : null

  return (
    <>
      <DialogHeader>
        <DialogTitle>Log in to {label}</DialogTitle>
        <DialogDescription>
          Open the sign-in page, approve access
          {login?.user_code ? " and enter the code below" : ""}. This window
          updates automatically.
        </DialogDescription>
      </DialogHeader>

      {error ? (
        <div className="space-y-3">
          <p role="alert" className="text-sm break-words text-destructive">
            {error}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              Close
            </Button>
            <Button size="sm" onClick={retry}>
              Try again
            </Button>
          </div>
        </div>
      ) : !login?.user_code && !safeUrl ? (
        <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Starting login…
        </div>
      ) : (
        <div className="space-y-4">
          {login?.user_code && (
            <div className="flex items-center gap-2 rounded-lg border bg-muted/40 p-3">
              <code
                aria-label="Login code"
                className="flex-1 text-center font-mono text-2xl font-semibold tracking-widest select-all"
              >
                {login.user_code}
              </code>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={copyCode}
                aria-label="Copy code"
              >
                {copied ? <Check /> : <Copy />}
              </Button>
            </div>
          )}

          {safeUrl && (
            <Button asChild className="w-full">
              <a href={safeUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink />
                Open sign-in page
              </a>
            </Button>
          )}

          <div
            role="status"
            className="flex items-center justify-center gap-2 text-sm text-muted-foreground"
          >
            <Loader2 className="size-4 animate-spin" />
            Waiting for approval…
          </div>
          {login?.expires_in ? (
            <p className="text-center text-xs text-muted-foreground">
              The code expires in about{" "}
              {Math.max(1, Math.round(login.expires_in / 60))} min.
            </p>
          ) : null}
        </div>
      )}
    </>
  )
}

// Device-code login for a subscription provider. Opening the dialog starts a
// login; closing it just stops polling (the server-side attempt is left alone).
export function ProviderLoginDialog({
  provider,
  label,
  open,
  onOpenChange,
  onComplete,
}: {
  provider: LoginProviderValue | null
  label: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onComplete: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {/* Mounted only while open so each open starts a fresh login. */}
        {provider && (
          <LoginFlow
            provider={provider}
            label={label}
            onClose={() => onOpenChange(false)}
            onComplete={onComplete}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
