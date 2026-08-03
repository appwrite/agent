import { useEffect, useState } from "react"

import { Spinner } from "@/components/ui/spinner"
import {
  beginMcpOAuth,
  completeMcpOAuth,
  type McpServerRef,
} from "@/lib/mcp-oauth"

function Shell({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-svh items-center justify-center bg-background px-4">
      <div className="w-full max-w-md rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        <div className="mt-3 text-sm text-muted-foreground">{children}</div>
      </div>
    </div>
  )
}

function serverFromSearch(search: string): McpServerRef {
  const qs = new URLSearchParams(search)
  const id = qs.get("id") || ""
  const url = qs.get("url") || ""
  if (!id || !url) {
    throw new Error("Missing id or url query params")
  }
  return {
    id,
    name: qs.get("name") || id,
    url,
    description: qs.get("description") || "",
  }
}

export function McpOAuthStartPage() {
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const server = serverFromSearch(window.location.search)
        await beginMcpOAuth(server)
      } catch (err) {
        if (!cancelled) {
          setError(String((err as Error).message || err))
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <Shell title="Connection failed">
        <p className="text-destructive">{error}</p>
        <p className="mt-3">You can close this window and try again.</p>
      </Shell>
    )
  }

  return (
    <Shell title="Connecting…">
      <div className="flex items-center gap-2">
        <Spinner />
        <span>Redirecting to sign-in…</span>
      </div>
    </Shell>
  )
}

export function McpOAuthCallbackPage() {
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const credentials = await completeMcpOAuth(window.location.search)
        if (cancelled) return
        const payload = {
          type: "mcp-oauth",
          status: "ok",
          serverId: credentials.id,
          credentials,
        }
        try {
          window.opener?.postMessage(payload, window.location.origin)
        } catch {
          /* ignore */
        }
        // Also stash for same-tab fallback (no opener).
        try {
          localStorage.setItem(
            "agent_mcp_oauth_result",
            JSON.stringify(payload)
          )
        } catch {
          /* ignore */
        }
        setDone(true)
        window.setTimeout(() => window.close(), 800)
      } catch (err) {
        if (cancelled) return
        const message = String((err as Error).message || err)
        setError(message)
        try {
          window.opener?.postMessage(
            { type: "mcp-oauth", status: "error", message },
            window.location.origin
          )
        } catch {
          /* ignore */
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <Shell title="Connection failed">
        <p className="text-destructive">{error}</p>
        <p className="mt-3">You can close this window and try again.</p>
      </Shell>
    )
  }

  return (
    <Shell title={done ? "Connected" : "Finishing sign-in…"}>
      <div className="flex items-center gap-2">
        {!done ? <Spinner /> : null}
        <span>
          {done
            ? "You can close this window and return to the agent."
            : "Exchanging authorization code…"}
        </span>
      </div>
    </Shell>
  )
}
