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
  credentials?: McpConnectionCredentials[]
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

export type SuggestedMcpServer = {
  id: string
  name: string
  url: string
  description: string
  builtin: boolean
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
    note?: string
    suggested_servers?: SuggestedMcpServer[]
    /** @deprecated */
    servers?: McpServerStatus[]
  }
  runtime: {
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
  status: "connected" | "disconnected" | string
  tools: string[]
}

export type McpConnectionCredentials = {
  id: string
  name?: string
  url?: string
  description?: string
  tokens?: Record<string, unknown>
  client_info?: Record<string, unknown>
}

const MCP_CONNECTIONS_KEY = "assistant_mcp_connections"
const MCP_CUSTOM_SERVERS_KEY = "assistant_mcp_custom_servers"

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

export function loadCustomMcpServers(): SuggestedMcpServer[] {
  try {
    const raw = localStorage.getItem(MCP_CUSTOM_SERVERS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function loadMcpConnections(): McpConnectionCredentials[] {
  try {
    const raw = localStorage.getItem(MCP_CONNECTIONS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const custom = loadCustomMcpServers()
    return parsed.map((c: McpConnectionCredentials) => {
      const meta = custom.find((s) => s.id === c.id)
      return {
        ...c,
        url: c.url || meta?.url,
        name: c.name || meta?.name,
        description: c.description || meta?.description || "",
      }
    })
  } catch {
    return []
  }
}

export function saveMcpConnection(conn: McpConnectionCredentials): void {
  const next = loadMcpConnections().filter((c) => c.id !== conn.id)
  next.push(conn)
  localStorage.setItem(MCP_CONNECTIONS_KEY, JSON.stringify(next))
}

export function removeMcpConnection(serverId: string): void {
  const next = loadMcpConnections().filter((c) => c.id !== serverId)
  localStorage.setItem(MCP_CONNECTIONS_KEY, JSON.stringify(next))
}

export function saveCustomMcpServer(server: {
  id: string
  name: string
  url: string
  description?: string
}): SuggestedMcpServer {
  const row: SuggestedMcpServer = {
    id: server.id,
    name: server.name,
    url: server.url,
    description: server.description || "",
    builtin: false,
  }
  const next = loadCustomMcpServers().filter((s) => s.id !== row.id)
  next.push(row)
  localStorage.setItem(MCP_CUSTOM_SERVERS_KEY, JSON.stringify(next))
  return row
}

export function listLocalMcpServers(
  suggested: SuggestedMcpServer[] = []
): McpServerStatus[] {
  const creds = new Set(
    loadMcpConnections()
      .filter(
        (c) => c.tokens && (c.tokens as { access_token?: string }).access_token
      )
      .map((c) => c.id)
  )
  const byId = new Map<string, SuggestedMcpServer>()
  for (const s of suggested) byId.set(s.id, s)
  for (const s of loadCustomMcpServers()) {
    if (!byId.has(s.id)) byId.set(s.id, s)
  }
  return Array.from(byId.values()).map((s) => ({
    ...s,
    status: creds.has(s.id) ? "connected" : "disconnected",
    tools: [],
  }))
}

export async function fetchMeta(): Promise<AgentSettings> {
  const res = await fetch("/api/meta", { headers: authHeaders() })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** @deprecated use fetchMeta */
export async function fetchAgentSettings(): Promise<AgentSettings> {
  return fetchMeta()
}

export function disconnectMcpServer(serverId: string): void {
  removeMcpConnection(serverId)
}

export function addMcpServer(input: {
  id: string
  name: string
  url: string
  description?: string
}): SuggestedMcpServer {
  return saveCustomMcpServer(input)
}

export type ChatAttachment = {
  id: string
  name: string
  mime: string
  size: number
  kind: "image" | "text" | "file" | string
  previewUrl?: string
  content_base64?: string
}

export type HistoryMessage = {
  role: "user" | "assistant"
  content: string
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || "")
      const comma = result.indexOf(",")
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () =>
      reject(reader.error || new Error("Failed to read file"))
    reader.readAsDataURL(file)
  })
}

export async function encodeAttachment(file: File): Promise<ChatAttachment> {
  const content_base64 = await fileToBase64(file)
  const mime = file.type || "application/octet-stream"
  const kind = mime.startsWith("image/")
    ? "image"
    : mime.startsWith("text/") ||
        /\.(txt|md|json|ya?ml|csv|xml|html?|css|js|ts|tsx|jsx|py)$/i.test(
          file.name
        )
      ? "text"
      : "file"
  return {
    id: `${file.name}-${file.size}-${file.lastModified}`,
    name: file.name,
    mime,
    size: file.size,
    kind,
    content_base64,
  }
}

export async function streamChat(
  message: string,
  history: HistoryMessage[],
  onEvent: (event: StreamEvent) => void,
  attachments: ChatAttachment[] = []
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }
  const key = apiKey()
  if (key) headers["X-Session-API-Key"] = key

  const res = await fetch("/api/turn", {
    method: "POST",
    headers,
    body: JSON.stringify({
      message,
      history,
      attachments: attachments.map(
        ({ id, name, mime, size, kind, content_base64 }) => ({
          id,
          name,
          mime,
          size,
          kind,
          content_base64,
        })
      ),
      mcp_connections: loadMcpConnections(),
    }),
  })

  if (!res.ok) throw new Error(await readError(res))
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
        const event = JSON.parse(line) as StreamEvent
        if (
          event.type === "mcp_credentials" &&
          Array.isArray(event.credentials)
        ) {
          for (const cred of event.credentials) {
            if (cred?.id) saveMcpConnection(cred)
          }
        }
        onEvent(event)
      } catch {
        /* skip */
      }
    }
  }
}

export function ensureApiKey(): boolean {
  if (apiKey()) return true
  const key = window.prompt("Session API key (ASSISTANT_API_KEY)")
  if (!key) return false
  localStorage.setItem("assistant_api_key", key)
  return true
}
