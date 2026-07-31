import type { Components } from "react-markdown"
import ReactMarkdown from "react-markdown"
import rehypeSanitize, { defaultSchema } from "rehype-sanitize"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"

/** Sanitize schema: GFM tags only, no raw HTML/scripts/event handlers. */
const schema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames || []),
    "input", // GFM task list checkboxes (disabled below)
  ],
  attributes: {
    ...defaultSchema.attributes,
    a: [...(defaultSchema.attributes?.a || []), "target", "rel"],
    code: [...(defaultSchema.attributes?.code || []), "className"],
    input: ["type", "checked", "disabled"],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: ["http", "https", "mailto"],
  },
  clobber: [...(defaultSchema.clobber || []), "name", "id"],
}

function safeHref(href?: string | null): string | undefined {
  if (!href) return undefined
  const trimmed = href.trim()
  const lower = trimmed.toLowerCase()
  if (
    lower.startsWith("http://") ||
    lower.startsWith("https://") ||
    lower.startsWith("mailto:")
  ) {
    return trimmed
  }
  // Block javascript:, data:, vbscript:, relative tricks that sanitize missed.
  return undefined
}

const components: Components = {
  a({ href, children, ...props }) {
    const safe = safeHref(href)
    if (!safe) {
      return <span {...props}>{children}</span>
    }
    return (
      <a
        {...props}
        href={safe}
        target="_blank"
        rel="noopener noreferrer nofollow"
      >
        {children}
      </a>
    )
  },
  img() {
    // Images from model/user content are a common XSS/tracking vector — skip.
    return null
  },
  input({ type, checked, disabled, ...props }) {
    if (type !== "checkbox") return null
    return (
      <input
        {...props}
        type="checkbox"
        checked={Boolean(checked)}
        disabled
        readOnly
      />
    )
  },
  pre({ children, className, ...props }) {
    return (
      <pre
        {...props}
        className={cn(
          "my-3 overflow-x-auto rounded-lg border bg-muted/60 p-3 font-mono text-xs leading-relaxed",
          className
        )}
      >
        {children}
      </pre>
    )
  },
  code({ className, children, ...props }) {
    const isBlock = Boolean(className?.includes("language-"))
    if (isBlock) {
      return (
        <code {...props} className={cn("font-mono text-xs", className)}>
          {children}
        </code>
      )
    }
    return (
      <code
        {...props}
        className={cn(
          "rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]",
          className
        )}
      >
        {children}
      </code>
    )
  },
  ul({ children, ...props }) {
    return (
      <ul {...props} className="my-2 list-disc space-y-1 ps-5">
        {children}
      </ul>
    )
  },
  ol({ children, ...props }) {
    return (
      <ol {...props} className="my-2 list-decimal space-y-1 ps-5">
        {children}
      </ol>
    )
  },
  li({ children, ...props }) {
    return (
      <li {...props} className="leading-relaxed">
        {children}
      </li>
    )
  },
  p({ children, ...props }) {
    return (
      <p {...props} className="my-2 leading-relaxed first:mt-0 last:mb-0">
        {children}
      </p>
    )
  },
  h1({ children, ...props }) {
    return (
      <h3 {...props} className="mt-3 mb-2 text-base font-semibold tracking-tight">
        {children}
      </h3>
    )
  },
  h2({ children, ...props }) {
    return (
      <h3 {...props} className="mt-3 mb-2 text-base font-semibold tracking-tight">
        {children}
      </h3>
    )
  },
  h3({ children, ...props }) {
    return (
      <h4 {...props} className="mt-3 mb-1.5 text-sm font-semibold tracking-tight">
        {children}
      </h4>
    )
  },
  blockquote({ children, ...props }) {
    return (
      <blockquote
        {...props}
        className="my-2 border-s-2 border-border ps-3 text-muted-foreground"
      >
        {children}
      </blockquote>
    )
  },
  table({ children, ...props }) {
    return (
      <div className="my-3 overflow-x-auto rounded-lg border">
        <table {...props} className="w-full text-left text-sm">
          {children}
        </table>
      </div>
    )
  },
  th({ children, ...props }) {
    return (
      <th
        {...props}
        className="border-b bg-muted/50 px-3 py-1.5 font-medium"
      >
        {children}
      </th>
    )
  },
  td({ children, ...props }) {
    return (
      <td {...props} className="border-b px-3 py-1.5 align-top">
        {children}
      </td>
    )
  },
  hr() {
    return <hr className="my-4 border-border" />
  },
}

type MarkdownProps = {
  children: string
  className?: string
}

/**
 * Render model/user markdown safely:
 * - no rehype-raw (raw HTML never parsed)
 * - rehype-sanitize strips dangerous tags/attrs
 * - links limited to http(s)/mailto + noopener
 * - remote images blocked
 */
export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div
      className={cn(
        "max-w-none text-sm wrap-break-word [overflow-wrap:anywhere]",
        className
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, schema]]}
        components={components}
        skipHtml
        urlTransform={(url) => safeHref(url) ?? ""}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
