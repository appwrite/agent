/**
 * Browser-side MCP OAuth 2.1 (PKCE).
 * Discovers AS metadata, dynamically registers a public client, authorizes,
 * and returns tokens for localStorage — no engine involvement.
 */

import type { McpConnectionCredentials } from "@/lib/api"

const PENDING_KEY = "agent_mcp_oauth_pending"

export type McpServerRef = {
  id: string
  name: string
  url: string
  description?: string
}

type ProtectedResourceMetadata = {
  resource?: string
  authorization_servers?: string[]
  scopes_supported?: string[]
}

type AuthServerMetadata = {
  issuer?: string
  authorization_endpoint: string
  token_endpoint: string
  registration_endpoint?: string
  code_challenge_methods_supported?: string[]
}

type ClientInfo = {
  client_id: string
  client_secret?: string
  redirect_uris?: string[]
  [key: string]: unknown
}

type PendingOAuth = {
  server: McpServerRef
  state: string
  codeVerifier: string
  redirectUri: string
  clientInfo: ClientInfo
  tokenEndpoint: string
  resource?: string
}

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)
  let s = ""
  for (const b of arr) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

function randomString(len = 64): string {
  const bytes = crypto.getRandomValues(new Uint8Array(len))
  return b64url(bytes)
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier)
  )
  return b64url(digest)
}

function mcpOrigin(serverUrl: string): string {
  const u = new URL(serverUrl)
  return `${u.protocol}//${u.host}`
}

function resourceFromServer(serverUrl: string): string {
  const u = new URL(serverUrl)
  // Canonical resource URL (trailing slash if path is empty/root).
  if (!u.pathname || u.pathname === "/") return `${u.origin}/`
  return u.href.endsWith("/") ? u.href : `${u.href}`
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`)
  }
  return res.json() as Promise<T>
}

async function discoverProtectedResource(
  serverUrl: string
): Promise<ProtectedResourceMetadata> {
  const origin = mcpOrigin(serverUrl)
  const candidates = [
    `${origin}/.well-known/oauth-protected-resource`,
    // Some servers nest PRM under the resource path.
    `${origin}/.well-known/oauth-protected-resource${new URL(serverUrl).pathname.replace(/\/$/, "")}`,
  ]
  // Prefer probing the MCP endpoint for WWW-Authenticate resource_metadata.
  try {
    const probe = await fetch(serverUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocolVersion: "2025-03-26",
          capabilities: {},
          clientInfo: { name: "appwrite-agent-ui", version: "0.1.0" },
        },
      }),
    })
    const www = probe.headers.get("WWW-Authenticate") || ""
    const match = www.match(/resource_metadata=(?:"([^"]+)"|([^\s,]+))/)
    const metaUrl = match?.[1] || match?.[2]
    if (metaUrl) {
      return fetchJson<ProtectedResourceMetadata>(metaUrl)
    }
  } catch {
    /* fall through to well-known */
  }

  let lastErr: unknown
  for (const url of candidates) {
    try {
      return await fetchJson<ProtectedResourceMetadata>(url)
    } catch (err) {
      lastErr = err
    }
  }
  throw lastErr || new Error("Could not discover protected resource metadata")
}

async function discoverAuthServer(
  authServerUrl: string
): Promise<AuthServerMetadata> {
  const parsed = new URL(authServerUrl)
  const origin = parsed.origin
  const path = parsed.pathname.replace(/\/$/, "")
  const urls = [
    path ? `${origin}/.well-known/oauth-authorization-server${path}` : "",
    `${origin}/.well-known/oauth-authorization-server`,
    path ? `${origin}/.well-known/openid-configuration${path}` : "",
    `${authServerUrl.replace(/\/$/, "")}/.well-known/openid-configuration`,
  ].filter(Boolean)

  let lastErr: unknown
  for (const url of urls) {
    try {
      return await fetchJson<AuthServerMetadata>(url)
    } catch (err) {
      lastErr = err
    }
  }
  throw lastErr || new Error("Could not discover authorization server metadata")
}

async function registerClient(
  registrationEndpoint: string,
  redirectUri: string,
  clientName: string
): Promise<ClientInfo> {
  return fetchJson<ClientInfo>(registrationEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: clientName,
      redirect_uris: [redirectUri],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
      application_type: "web",
    }),
  })
}

function savePending(pending: PendingOAuth): void {
  sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending))
}

function loadPending(): PendingOAuth | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY)
    if (!raw) return null
    return JSON.parse(raw) as PendingOAuth
  } catch {
    return null
  }
}

function clearPending(): void {
  sessionStorage.removeItem(PENDING_KEY)
}

export function oauthCallbackPath(): string {
  return "/oauth/mcp/callback"
}

export function oauthRedirectUri(): string {
  return `${window.location.origin}${oauthCallbackPath()}`
}

/**
 * Start OAuth in the current window (intended for a popup).
 * Redirects to the authorization server.
 */
export async function beginMcpOAuth(
  server: McpServerRef,
  opts?: { existingClientInfo?: ClientInfo | null; clientName?: string }
): Promise<void> {
  const redirectUri = oauthRedirectUri()
  const prm = await discoverProtectedResource(server.url)
  const asUrl = prm.authorization_servers?.[0]
  if (!asUrl) {
    throw new Error("No authorization_servers in protected resource metadata")
  }
  const asMeta = await discoverAuthServer(asUrl)
  if (!asMeta.authorization_endpoint || !asMeta.token_endpoint) {
    throw new Error("Authorization server metadata incomplete")
  }

  let clientInfo = opts?.existingClientInfo || null
  if (!clientInfo?.client_id) {
    if (!asMeta.registration_endpoint) {
      throw new Error(
        "Authorization server has no registration_endpoint and no client_info was provided"
      )
    }
    clientInfo = await registerClient(
      asMeta.registration_endpoint,
      redirectUri,
      opts?.clientName || "Appwrite Agent"
    )
  }

  const codeVerifier = randomString(64)
  const codeChallenge = await pkceChallenge(codeVerifier)
  const state = randomString(32)
  const resource = prm.resource || resourceFromServer(server.url)

  savePending({
    server,
    state,
    codeVerifier,
    redirectUri,
    clientInfo,
    tokenEndpoint: asMeta.token_endpoint,
    resource,
  })

  const params = new URLSearchParams({
    response_type: "code",
    client_id: clientInfo.client_id,
    redirect_uri: redirectUri,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
    resource,
  })

  // Console OAuth2 only materializes project:/organization: API scopes when
  // authorization_details binds them to resources (or `*`).
  const scopes =
    prm.scopes_supported && prm.scopes_supported.length > 0
      ? prm.scopes_supported.join(" ")
      : "openid profile email project:all organization:all"
  params.set("scope", scopes)
  params.set(
    "authorization_details",
    JSON.stringify([
      { type: "project", identifiers: ["*"] },
      { type: "organization", identifiers: ["*"] },
    ])
  )

  window.location.assign(
    `${asMeta.authorization_endpoint}?${params.toString()}`
  )
}

/**
 * Finish OAuth on the callback page. Returns credentials for the opener to store.
 */
export async function completeMcpOAuth(
  search: string
): Promise<McpConnectionCredentials> {
  const qs = new URLSearchParams(search.startsWith("?") ? search : `?${search}`)
  const error = qs.get("error")
  if (error) {
    throw new Error(qs.get("error_description") || error)
  }
  const code = qs.get("code")
  const state = qs.get("state")
  if (!code || !state) {
    throw new Error("Missing authorization code or state")
  }

  const pending = loadPending()
  if (!pending) {
    throw new Error("OAuth session expired. Close this window and try Connect again.")
  }
  if (pending.state !== state) {
    clearPending()
    throw new Error("OAuth state mismatch")
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: pending.redirectUri,
    client_id: pending.clientInfo.client_id,
    code_verifier: pending.codeVerifier,
  })
  if (pending.resource) body.set("resource", pending.resource)
  if (pending.clientInfo.client_secret) {
    body.set("client_secret", String(pending.clientInfo.client_secret))
  }

  const tokenRes = await fetch(pending.tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  })
  if (!tokenRes.ok) {
    const text = await tokenRes.text().catch(() => "")
    clearPending()
    throw new Error(`Token exchange failed: ${tokenRes.status} ${text}`)
  }
  const tokens = await tokenRes.json()
  clearPending()

  return {
    id: pending.server.id,
    name: pending.server.name,
    url: pending.server.url,
    description: pending.server.description || "",
    tokens,
    client_info: pending.clientInfo,
  }
}

/**
 * Open a popup that runs the full OAuth flow and resolves with credentials.
 */
export function connectMcpInPopup(
  server: McpServerRef
): Promise<McpConnectionCredentials> {
  const params = new URLSearchParams({
    id: server.id,
    name: server.name,
    url: server.url,
    description: server.description || "",
  })
  const popup = window.open(
    `/oauth/mcp/start?${params.toString()}`,
    "mcp-oauth",
    "popup,width=520,height=720"
  )
  if (!popup) {
    // Fallback: same-tab flow
    window.location.href = `/oauth/mcp/start?${params.toString()}`
    return new Promise(() => {
      /* navigation away */
    })
  }

  const win = popup
  return new Promise((resolve, reject) => {
    const timer = window.setInterval(() => {
      if (win.closed) {
        window.clearInterval(timer)
        window.removeEventListener("message", onMessage)
        reject(new Error("OAuth popup closed before completing"))
      }
    }, 500)

    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return
      const data = event.data
      if (!data || data.type !== "mcp-oauth") return
      window.clearInterval(timer)
      window.removeEventListener("message", onMessage)
      try {
        win.close()
      } catch {
        /* ignore */
      }
      if (data.status === "ok" && data.credentials) {
        resolve(data.credentials as McpConnectionCredentials)
      } else {
        reject(new Error(data.message || "MCP connection failed"))
      }
    }
    window.addEventListener("message", onMessage)
  })
}
