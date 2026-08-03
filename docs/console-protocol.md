# Appwrite Console protocol (`appwrite.console/v1`)

Shareable contract between the **assistant engine** (this repo) and the **Appwrite Console** (vibes). The agent posts UI metadata via the built-in `console` tool; the Console parses the tool result and turns it into components / side-effects.

This is **not** resource CRUD. Mutations still go through MCP (or other APIs). `console` only describes what the Console should show or do in the shell — including **structured resource lists** so the Console can render filters, validation, and deep links instead of parsing markdown.

---

## Transport

| Layer | Behavior |
|-------|----------|
| Tool name | `console` |
| Tool argument | `actions` — JSON **string** (array of actions, or a single action object) |
| Tool result | Canonical JSON **envelope** (string). On validation failure: `Error: invalid console actions — …` |
| Delivery to Console | Same path as other tools: turn timeline `tool_end` with `tool === "console"` and `output` = envelope JSON. Cloud may also persist `resultText` / `resultJson` on `AssistantTool`. |

**Ignore** `console` when classifying MCP resource mutations (it is a UI meta-tool, like `appwrite_search_tools`).

### Envelope

```json
{
  "protocol": "appwrite.console/v1",
  "actions": [ /* 1–20 ConsoleAction objects */ ]
}
```

- Unknown `protocol` values → ignore the call (forward-compat).
- Unknown `actions[].type` values → skip that action; do not fail the whole batch.
- Engine validates known types before emitting; Console should still be defensive.

### Detecting a console tool result

```ts
function parseConsoleEnvelope(output: string): ConsoleEnvelope | null {
  const text = output.trim()
  if (text.startsWith('Error:')) return null
  try {
    const parsed = JSON.parse(text)
    if (parsed?.protocol !== 'appwrite.console/v1') return null
    if (!Array.isArray(parsed.actions)) return null
    return parsed
  } catch {
    return null
  }
}
```

Apply actions in array order.

---

## TypeScript types (copy into Console)

```ts
/** Wire protocol id — bump only on breaking changes. */
export type ConsoleProtocolId = 'appwrite.console/v1'

export type ConsoleEnvelope = {
  protocol: ConsoleProtocolId
  actions: ConsoleAction[]
}

export type CreateResourceType =
  | 'database'
  | 'bucket'
  | 'user'
  | 'team'
  | 'function'
  | 'site'

export type ConsoleDialog =
  | 'invite_member'
  | 'create_project'
  | 'connect_mcp'
  | 'shortcuts'
  | 'docs_search'
  | 'feedback'
  | 'support'

export type ConsoleResourceItem = {
  resourceId: string
  title: string
  subtitle?: string
  /** Console-relative path, e.g. /project/{id}/databases/{db} */
  href?: string
  status?: string
  metadata?: Array<{ label: string; value: string }>
  /**
   * Typed attributes for Console filters / validation
   * (email, phone, status, enabled, region, …). Prefer this over parsing metadata.
   */
  fields?: Record<string, string | number | boolean | null>
}

export type ConsoleAction =
  | { type: 'set_theme'; theme: 'light' | 'dark' | 'system' }
  | { type: 'navigate'; path: string; hash?: string; replace?: boolean }
  | { type: 'open_create'; resource: CreateResourceType; projectId?: string }
  | { type: 'open_dialog'; dialog: ConsoleDialog; projectId?: string }
  | {
      type: 'toast'
      level: 'success' | 'error' | 'info' | 'warning'
      message: string
      description?: string
    }
  | { type: 'show_pane'; content: 'agent' | 'docs' | 'none' }
  | { type: 'toggle_terminal' }
  | { type: 'scroll_to_card'; cardId: string }
  | ({
      type: 'resource'
      mutation: 'create' | 'update' | 'delete'
      /** Appwrite resource kind, e.g. database, bucket, user, function, site, table, file, team */
      resourceType: string
    } & ConsoleResourceItem)
  | {
      type: 'resource_list'
      resourceType: string
      items: ConsoleResourceItem[]
      /** Heading shown above the list UI */
      title?: string
      description?: string
      /** Total matches (may be > items.length when truncated/paginated) */
      total?: number
      /** Deep link to the full Console list page */
      listHref?: string
      emptyMessage?: string
      projectId?: string
      /** Optional column hints for table layout; `key` should match `fields` keys */
      columns?: Array<{ key: string; label: string }>
    }
  | { type: 'refresh'; scopes: string[] }
```

---

## Actions

Aligned with Console Command Center handlers (`onSetTheme`, `onProjectCreate`, `navigate`, `#card-*`, terminal, MCP connect, …).

### `set_theme`

Switch Console appearance.

| Field | Required | Values |
|-------|----------|--------|
| `theme` | yes | `light` \| `dark` \| `system` |

```json
{ "type": "set_theme", "theme": "dark" }
```

**Console:** call the same path as Command Center / ThemeToggle (`onSetTheme`).

---

### `navigate`

Client-side route change.

| Field | Required | Notes |
|-------|----------|--------|
| `path` | yes | Must start with `/` |
| `hash` | no | With or without `#`; maps to `#card-*` scroll targets |
| `replace` | no | History replace vs push |

```json
{
  "type": "navigate",
  "path": "/project/64abc/databases",
  "hash": "card-api-endpoint"
}
```

---

### `open_create`

Open a project create dialog/drawer (Command Center `onProjectCreate`).

| Field | Required | Values |
|-------|----------|--------|
| `resource` | yes | `database` \| `bucket` \| `user` \| `team` \| `function` \| `site` |
| `projectId` | no | Defaults to current project context |

```json
{ "type": "open_create", "resource": "bucket", "projectId": "64abc" }
```

---

### `open_dialog`

Open a named Console dialog.

| Field | Required | Values |
|-------|----------|--------|
| `dialog` | yes | `invite_member` \| `create_project` \| `connect_mcp` \| `shortcuts` \| `docs_search` \| `feedback` \| `support` |
| `projectId` | no | When the dialog is project-scoped |

```json
{ "type": "open_dialog", "dialog": "connect_mcp" }
```

---

### `toast`

Transient notification (`sonner`).

| Field | Required | Values |
|-------|----------|--------|
| `level` | yes | `success` \| `error` \| `info` \| `warning` |
| `message` | yes | Short title |
| `description` | no | Supporting text |

```json
{
  "type": "toast",
  "level": "success",
  "message": "Database created",
  "description": "Main is ready to use"
}
```

---

### `show_pane`

Control the Console right pane.

| Field | Required | Values |
|-------|----------|--------|
| `content` | yes | `agent` \| `docs` \| `none` |

```json
{ "type": "show_pane", "content": "docs" }
```

---

### `toggle_terminal`

Toggle the project terminal panel (Command Center `onToggleTerminal`).

```json
{ "type": "toggle_terminal" }
```

---

### `scroll_to_card`

Scroll a settings/card section into view (`useScrollToCard` / `#card-{cardId}`).

| Field | Required | Notes |
|-------|----------|--------|
| `cardId` | yes | With or without `card-` prefix; engine strips a leading `card-` |

```json
{ "type": "scroll_to_card", "cardId": "api-endpoint" }
```

---

### `resource`

Render a **resource card** (or inline summary) for a mutation the agent already performed via MCP.

| Field | Required | Notes |
|-------|----------|--------|
| `mutation` | yes | `create` \| `update` \| `delete` |
| `resourceType` | yes | Free-form kind (`database`, `bucket`, `user`, `table`, …) |
| `resourceId` | yes | Appwrite `$id` |
| `title` | yes | Primary label (name) |
| `subtitle` | no | Secondary line |
| `href` | no | Deep link into Console |
| `status` | no | Status badge text |
| `metadata` | no | `{ label, value }[]` for card footer |
| `fields` | no | Typed map for Console filters / validation |

```json
{
  "type": "resource",
  "mutation": "create",
  "resourceType": "database",
  "resourceId": "main",
  "title": "Main",
  "subtitle": "TablesDB",
  "href": "/project/64abc/databases/main",
  "fields": { "type": "tablesdb" },
  "metadata": [
    { "label": "ID", "value": "main" },
    { "label": "Region", "value": "fra" }
  ]
}
```

**Console UI:** map onto `ResourceCard` (title / subtitle / resourceId / metadata) plus a mutation chip (`Created` / `Updated` / `Deleted`). Prefer this explicit payload over guessing from MCP tool names.

**Agent rule:** call MCP mutate first; only emit `resource` after a successful tool result (with the real `$id` / name from the response).

---

### `resource_list`

Render a **structured list** of resources in the agent chat (filters, validation, deep links) instead of a markdown bullet list or table.

Use this whenever the user asks to list/show/find resources (databases, users, buckets, functions, sites, teams, tables, files, …) and MCP (or another tool) returned rows.

| Field | Required | Notes |
|-------|----------|--------|
| `resourceType` | yes | Kind for the whole list (`database`, `user`, …) |
| `items` | yes | Array of rows (may be empty). Max **50** per call |
| `title` | no | List heading (“Databases”, “Users”) |
| `description` | no | Short context under the heading |
| `total` | no | Total matches; defaults to `items.length` when omitted |
| `listHref` | no | Link to the full Console list page |
| `emptyMessage` | no | Shown when `items` is empty |
| `projectId` | no | Scope hint for Console URL builders |
| `columns` | no | `{ key, label }[]` — `key` should match `items[].fields` keys |

Each **item** uses the shared `ConsoleResourceItem` shape:

| Field | Required | Notes |
|-------|----------|--------|
| `resourceId` | yes | Appwrite `$id` |
| `title` | yes | Primary label |
| `subtitle` | no | Secondary line |
| `href` | no | Deep link (prefer always when project context is known) |
| `status` | no | Badge text |
| `metadata` | no | Display-only `{ label, value }[]` |
| `fields` | no | Typed map for Console filters / validation |

```json
{
  "type": "resource_list",
  "resourceType": "user",
  "title": "Users",
  "total": 2,
  "listHref": "/project/64abc/auth/users",
  "projectId": "64abc",
  "columns": [
    { "key": "email", "label": "Email" },
    { "key": "status", "label": "Status" }
  ],
  "items": [
    {
      "resourceId": "user_1",
      "title": "Ada Lovelace",
      "subtitle": "ada@example.com",
      "href": "/project/64abc/auth/user/user_1",
      "status": "verified",
      "fields": {
        "email": "ada@example.com",
        "status": "verified",
        "emailVerification": true
      },
      "metadata": [
        { "label": "ID", "value": "user_1" }
      ]
    },
    {
      "resourceId": "user_2",
      "title": "Alan Turing",
      "subtitle": "alan@example.com",
      "href": "/project/64abc/auth/user/user_2",
      "fields": {
        "email": "alan@example.com",
        "status": "unverified",
        "emailVerification": false
      }
    }
  ]
}
```

**Console UI:** render a filterable list/table (reuse resource list patterns / `ResourceCard` grid). Use `fields` + `columns` for sorting/filtering; use `href` / `listHref` for navigation. Validate `resourceType` against known Console resource schemas when available.

**Agent rules:**
1. MCP list first → then `console` with `resource_list` (include real ids/names from the tool result).
2. Spoken/text answer should be brief (“Here are 2 users.”) — **do not** duplicate the rows as markdown.
3. If truncated, set `total` to the full count and prefer linking via `listHref`.
4. Empty results: still emit `resource_list` with `items: []` and an `emptyMessage`.

---

### `refresh`


Invalidate Console React Query (or equivalent) caches after mutations.

| Field | Required | Notes |
|-------|----------|--------|
| `scopes` | yes | Non-empty string array, lowercased by the engine |

Suggested scope tokens (Console may alias):

`databases`, `tables`, `buckets`, `files`, `users`, `teams`, `functions`, `sites`, `providers`, `topics`, `messages`, `project`, `organization`

```json
{
  "type": "refresh",
  "scopes": ["databases", "tables"]
}
```

---

## Examples

### Theme change

User: “Switch the console to dark mode.”

```json
{
  "protocol": "appwrite.console/v1",
  "actions": [{ "type": "set_theme", "theme": "dark" }]
}
```

### Created a database via MCP

1. MCP `databases_create` (or equivalent) succeeds.
2. Agent calls `console`:

```json
{
  "protocol": "appwrite.console/v1",
  "actions": [
    {
      "type": "resource",
      "mutation": "create",
      "resourceType": "database",
      "resourceId": "main",
      "title": "Main",
      "href": "/project/64abc/databases/main"
    },
    { "type": "refresh", "scopes": ["databases"] },
    {
      "type": "toast",
      "level": "success",
      "message": "Created database Main"
    }
  ]
}
```

### Batch in one tool call

`actions` may list up to **20** items. Prefer one `console` call with several actions over many round-trips. A single `resource_list` may contain up to **50** rows.

### Listed databases (prefer this over markdown)

User: “List my databases.”

1. MCP list succeeds.
2. Agent calls `console`:

```json
{
  "protocol": "appwrite.console/v1",
  "actions": [
    {
      "type": "resource_list",
      "resourceType": "database",
      "title": "Databases",
      "listHref": "/project/64abc/databases",
      "projectId": "64abc",
      "items": [
        {
          "resourceId": "main",
          "title": "Main",
          "href": "/project/64abc/databases/main",
          "fields": { "type": "tablesdb" },
          "metadata": [{ "label": "ID", "value": "main" }]
        },
        {
          "resourceId": "analytics",
          "title": "Analytics",
          "href": "/project/64abc/databases/analytics",
          "fields": { "type": "tablesdb" }
        }
      ]
    }
  ]
}
```

3. Text answer: “You have 2 databases.” (no markdown table).

---

## Console implementation checklist

1. On timeline / tool replay, when `tool === "console"` and output parses as `appwrite.console/v1`, dispatch `actions` in order.
2. Reuse Command Center handlers where they already exist (`onSetTheme`, `onProjectCreate`, `onToggleTerminal`, `onOpenConnectMcp`, `navigate`).
3. For `resource`, render a card (or feed `ConversationResourceSummary`) from the payload — do not re-classify from the tool name.
4. For `resource_list`, render an inline filterable list/table with per-row links; do not fall back to parsing assistant markdown.
5. For `refresh`, invalidate the listed scopes / query keys.
6. Treat validation-error strings (`Error: invalid console actions — …`) as failed tool calls; show nothing in the shell.
7. Keep turn timeline UI (`status`, `route`, `tool_*`, …) separate from these shell side-effects.

---

## Engine reference

| Piece | Location |
|-------|----------|
| Validator + envelope | `app/graph/console.py` |
| LangChain tool | `app/graph/tools.py` → `console` |
| Agents with the tool | researcher, worker, appwrite (`build_tools` / `build_appwrite_tools`) |
| Protocol id constant | `PROTOCOL = "appwrite.console/v1"` |
