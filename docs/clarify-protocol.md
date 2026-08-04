# Appwrite clarify protocol (`appwrite.clarify/v1`)

Shareable contract between the **agent engine** (this repo) and the **Appwrite Console**. The agent posts structured follow-ups via the built-in `clarify` tool; the Console renders prompts (choices, confirmations, text inputs) and sends the user's answers on the next turn.

This is **not** chat prose. Prefer `clarify` over guessing IDs, permissions, `unique()`, or proceeding with destructive MCP deletes when required details are missing.

---

## Transport

| Layer | Behavior |
|-------|----------|
| Tool name | `clarify` |
| Tool arguments | `prompts` — JSON **string** (array of prompts, or a single prompt object); optional `title` |
| Tool result | Canonical JSON **envelope** (string). On validation failure: `Error: invalid clarify prompts — …` |
| Delivery to Console | Turn timeline `tool_end` with `tool === "clarify"` and `output` = envelope JSON |

**Ignore** `clarify` when classifying MCP resource mutations (it is a UI meta-tool, like `console`).

### Envelope

```json
{
  "protocol": "appwrite.clarify/v1",
  "title": "Before deleting this bucket",
  "prompts": [ /* 1–5 ClarifyPrompt objects */ ]
}
```

- Unknown `protocol` values → ignore the call (forward-compat).
- Unknown `prompts[].kind` values → skip that prompt; do not fail the whole batch on the Console side (engine rejects unknown kinds up front).
- Engine validates known kinds before emitting; Console should still be defensive.

### Detecting a clarify tool result

```ts
function parseClarifyEnvelope(output: string): ClarifyEnvelope | null {
  const text = output.trim()
  if (text.startsWith('Error:')) return null
  try {
    const parsed = JSON.parse(text)
    if (parsed?.protocol !== 'appwrite.clarify/v1') return null
    if (!Array.isArray(parsed.prompts)) return null
    return parsed
  } catch {
    return null
  }
}
```

Render prompts in array order. Collect answers keyed by `prompt.id`.

---

## TypeScript types (copy into Console)

```ts
/** Wire protocol id — bump only on breaking changes. */
export type ClarifyProtocolId = 'appwrite.clarify/v1'

export type ClarifyEnvelope = {
  protocol: ClarifyProtocolId
  title?: string
  prompts: ClarifyPrompt[]
}

export type ClarifyOption = {
  id: string
  label: string
  description?: string
}

export type ClarifyPrompt =
  | {
      id: string
      kind: 'choice'
      question: string
      hint?: string
      required?: boolean
      options: ClarifyOption[]
      allowMultiple?: boolean
    }
  | {
      id: string
      kind: 'confirm'
      question: string
      hint?: string
      required?: boolean
      confirmLabel?: string
      cancelLabel?: string
      /** Destructive styling (deletes, irreversible ops) */
      danger?: boolean
    }
  | {
      id: string
      kind: 'text'
      question: string
      hint?: string
      required?: boolean
      placeholder?: string
      defaultValue?: string
      multiline?: boolean
    }

/** Suggested shape for the next user turn / structured reply. */
export type ClarifyAnswers = {
  protocol: ClarifyProtocolId
  answers: Array<
    | { id: string; kind: 'choice'; values: string[] }
    | { id: string; kind: 'confirm'; confirmed: boolean }
    | { id: string; kind: 'text'; value: string }
  >
}
```

---

## Prompt kinds

### `choice`

Pick one option (or several when `allowMultiple` is true).

| Field | Required | Notes |
|-------|----------|--------|
| `id` | yes | `^[a-z][a-z0-9_.-]{0,63}$` (1–64 chars) |
| `kind` | yes | `choice` |
| `question` | yes | Shown to the user |
| `options` | yes | 2–12 items with unique `id` + `label` |
| `allowMultiple` | no | Default `false` |
| `required` | no | Default `true` |
| `hint` | no | Secondary helper text |

### `confirm`

Yes / no gate — use before destructive MCP deletes or irreversible actions.

| Field | Required | Notes |
|-------|----------|--------|
| `id` | yes | Stable key |
| `kind` | yes | `confirm` |
| `question` | yes | State what will happen |
| `confirmLabel` | no | Default `Confirm` |
| `cancelLabel` | no | Default `Cancel` |
| `danger` | no | Default `false`; set `true` for deletes |
| `required` | no | Default `true` |
| `hint` | no | e.g. “This cannot be undone.” |

### `text`

Free-form value (resource name, email, custom id, …).

| Field | Required | Notes |
|-------|----------|--------|
| `id` | yes | Stable key |
| `kind` | yes | `text` |
| `question` | yes | |
| `placeholder` | no | |
| `defaultValue` | no | Pre-filled |
| `multiline` | no | Default `false` |
| `required` | no | Default `true` |
| `hint` | no | |

---

## Agent behavior

1. Call `clarify` when a required detail is missing or a destructive action needs confirmation — **do not guess**.
2. After a successful `clarify` call, stop mutating. Keep the spoken answer short (“Need one detail before I continue.”). Do not invent IDs or call delete MCP tools in the same turn.
3. Prefer one call with several prompts over many round-trips (max **5** prompts).
4. On the next turn, read the user's answers (plain text or structured `ClarifyAnswers`) and continue.

### Examples

**Missing resource id (choice):**

```json
{
  "protocol": "appwrite.clarify/v1",
  "title": "Which bucket?",
  "prompts": [
    {
      "id": "bucket",
      "kind": "choice",
      "question": "Which bucket should I delete?",
      "options": [
        { "id": "avatars", "label": "avatars", "description": "64 files" },
        { "id": "uploads", "label": "uploads", "description": "12 files" }
      ]
    }
  ]
}
```

**Destructive confirm:**

```json
{
  "protocol": "appwrite.clarify/v1",
  "prompts": [
    {
      "id": "confirm_delete",
      "kind": "confirm",
      "question": "Delete bucket avatars and all of its files?",
      "confirmLabel": "Delete",
      "cancelLabel": "Keep it",
      "danger": true,
      "hint": "This cannot be undone."
    }
  ]
}
```

**Name + region (text + choice):**

```json
{
  "protocol": "appwrite.clarify/v1",
  "title": "Create database",
  "prompts": [
    {
      "id": "name",
      "kind": "text",
      "question": "Database name",
      "placeholder": "main"
    },
    {
      "id": "id_mode",
      "kind": "choice",
      "question": "Database ID",
      "options": [
        { "id": "unique", "label": "Auto-generate (unique())" },
        { "id": "custom", "label": "I'll provide a custom ID" }
      ]
    }
  ]
}
```

---

## Console implementation checklist

1. On timeline / tool replay, when `tool === "clarify"` and output parses as `appwrite.clarify/v1`, render an inline form from `prompts`.
2. For `choice`, show selectable options; honor `allowMultiple`.
3. For `confirm` with `danger: true`, use destructive button styling.
4. For `text`, show an input (textarea when `multiline`).
5. On submit, send answers back as the next user message (structured JSON or a clear textual summary that includes each `id` → value).
6. Treat validation-error strings (`Error: invalid clarify prompts — …`) as failed tool calls; show nothing.
7. If the user cancels a `confirm`, do not invent a proceed signal — send `confirmed: false` (or equivalent text).

---

## Engine reference

| Piece | Location |
|-------|----------|
| Validator + envelope | `app/graph/clarify.py` |
| LangChain tool | `app/graph/tools.py` → `clarify` |
| Agents with the tool | researcher, planner, platform (`build_tools` / `build_appwrite_tools`) |
| Protocol id constant | `PROTOCOL = "appwrite.clarify/v1"` |
