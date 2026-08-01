export type StreamEvent = {
  type: string
  id?: string
  message?: string
  next?: string
  reason?: string
  agent?: string
  tool?: string
  input?: string
  output?: string
  content?: string
  answer?: string
  detail?: string
  tool_calls?: number
  at?: string
}

export type ToolActivityMeta = {
  id: string
  tool: string
  agent: string
  input?: string
  output?: string
  state: "processing" | "done" | "error"
}

export type MessageMeta = {
  messageId: string
  role: "user" | "assistant"
  conversationId?: string | null
  createdAt: string
  finishedAt?: string
  durationMs?: number
  contentChars: number
  tokenEvents: number
  tokenChars: number
  routes: string[]
  finishReason?: string
  tools: ToolActivityMeta[]
  events: StreamEvent[]
}

export type AgentSettings = {
  llm: {
    model: string
    chat_model: string
    base_url: string | null
    api_key_configured: boolean
    temperature: number
  }
  auth: {
    session_api_key_configured: boolean
    header: string
  }
  server: {
    host: string
    port: number
  }
  browser_fetch: {
    enabled: boolean
    domain_limits?: string
    timeout_ms: number
    text_limit: number
    cache_ttl_seconds: number
    engine: string
  }
  web_search: {
    enabled: boolean
    engine: string
    api_key_required: boolean
  }
  appwrite_skills?: {
    count: number
    skills: Array<{ name: string; description: string }>
    loader_tool: string
  }
  mcp?: {
    oauth_redirect_base: string
    redirect_uri: string
    servers: McpServerStatus[]
  }
  runtime: {
    max_handoffs: number
    subagent_recursion_limit: number
    history_window: number
    graph: string
  }
  tools: Array<{ name: string; description: string }>
  appwrite_tools?: Array<{ name: string; description: string }>
  agents: Array<{ name: string; role: string; prompt: string }>
  env: Record<string, string>
}

declare global {
  interface Window {
    ASSISTANT_API_KEY?: string
  }
}

function apiKey(): string {
  return (
    window.ASSISTANT_API_KEY ||
    localStorage.getItem("assistant_api_key") ||
    ""
  )
}

export async function fetchReady(): Promise<{ ready: boolean }> {
  const headers: Record<string, string> = {}
  const key = apiKey()
  if (key) headers["X-Session-API-Key"] = key
  const res = await fetch("/ready", { headers })
  if (!res.ok) throw new Error("offline")
  return res.json()
}

export type McpServerStatus = {
  id: string
  name: string
  url: string
  description: string
  builtin: boolean
  status: "connected" | "connecting" | "disconnected" | string
  tools: string[]
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  const key = apiKey()
  if (key) headers["X-Session-API-Key"] = key
  return headers
}

async function readError(res: Response): Promise<string> {
  const text = await res.text()
  try {
    return JSON.parse(text).detail || text || res.statusText
  } catch {
    return text || res.statusText
  }
}

export async function fetchAgentSettings(): Promise<AgentSettings> {
  const res = await fetch("/api/settings", { headers: authHeaders() })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function fetchMcpServers(): Promise<McpServerStatus[]> {
  const res = await fetch("/api/mcp/servers", { headers: authHeaders() })
  if (!res.ok) throw new Error(await readError(res))
  const data = await res.json()
  return data.servers || []
}

export async function connectMcpServer(
  serverId: string
): Promise<{ authorization_url: string; status?: string }> {
  const res = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/connect`, {
    method: "POST",
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function disconnectMcpServer(serverId: string): Promise<void> {
  const res = await fetch(
    `/api/mcp/servers/${encodeURIComponent(serverId)}/disconnect`,
    { method: "POST", headers: authHeaders() }
  )
  if (!res.ok) throw new Error(await readError(res))
}

export async function addMcpServer(input: {
  id: string
  name: string
  url: string
  description?: string
}): Promise<McpServerStatus> {
  const res = await fetch("/api/mcp/servers", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(await readError(res))
  const data = await res.json()
  return data.server
}

export async function streamChat(
  conversationId: string | null,
  message: string,
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }
  const key = apiKey()
  if (key) headers["X-Session-API-Key"] = key

  const url = conversationId
    ? `/api/conversations/${conversationId}/messages/stream`
    : "/api/conversations/stream"

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ message }),
  })

  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      detail = JSON.parse(text).detail || text
    } catch {
      /* keep */
    }
    throw new Error(detail || res.statusText)
  }

  if (!res.body) throw new Error("No response body")

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split("\n\n")
    buffer = chunks.pop() || ""
    for (const chunk of chunks) {
      const line = chunk
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim())
        .join("")
      if (!line) continue
      try {
        onEvent(JSON.parse(line) as StreamEvent)
      } catch {
        /* skip */
      }
    }
  }
}

export function ensureApiKey(): boolean {
  if (apiKey()) return true
  const key = window.prompt(
    "Session API key (ASSISTANT_API_KEY)"
  )
  if (!key) return false
  localStorage.setItem("assistant_api_key", key)
  return true
}
