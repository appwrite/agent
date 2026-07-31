import type { ReactNode } from "react"
import { CopyIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import type { MessageMeta, StreamEvent } from "@/lib/api"

function Row({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="grid min-w-0 grid-cols-[6.5rem_minmax(0,1fr)] items-start gap-3 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 overflow-hidden break-all font-medium [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="flex min-w-0 flex-col gap-3">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      <div className="flex min-w-0 flex-col gap-2.5 overflow-hidden rounded-xl border bg-muted/20 p-3">
        {children}
      </div>
    </section>
  )
}

function MonoBlock({ children }: { children: ReactNode }) {
  return (
    <pre className="mt-2 max-w-full min-w-0 overflow-x-hidden whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">
      {children}
    </pre>
  )
}

function eventSummary(event: StreamEvent): string {
  switch (event.type) {
    case "status":
      return event.message || ""
    case "route":
      return [event.next, event.reason].filter(Boolean).join(" · ")
    case "subagent_start":
    case "subagent_end":
    case "model_start":
    case "answer_start":
    case "answer_reset":
      return event.agent || ""
    case "tool_start":
      return `${event.agent || "agent"} · ${event.tool || "tool"} ${event.input || ""}`.trim()
    case "tool_end":
      return `${event.agent || "agent"} · ${event.tool || "tool"}`
    case "done":
      return `${(event.answer || "").length} chars`
    case "error":
      return event.detail || "error"
    case "complete":
      return event.id || "complete"
    case "conversation":
      return event.id || ""
    default:
      return ""
  }
}

export function MessageMetadataSheet({
  open,
  onOpenChange,
  meta,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  meta: MessageMeta | null
}) {
  function copyJson() {
    if (!meta) return
    void navigator.clipboard.writeText(JSON.stringify(meta, null, 2)).then(
      () => toast.success("Message metadata copied"),
      () => toast.error("Could not copy")
    )
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-full min-w-0 flex-col gap-0 overflow-hidden sm:max-w-lg"
      >
        <SheetHeader className="shrink-0 border-b pr-10">
          <SheetTitle>Message metadata</SheetTitle>
          <SheetDescription>
            Stream timeline and debug fields for this chat message.
          </SheetDescription>
          <div className="flex gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={copyJson}
              disabled={!meta}
            >
              <CopyIcon />
              Copy JSON
            </Button>
          </div>
        </SheetHeader>

        <ScrollArea className="min-h-0 min-w-0 flex-1 overflow-x-hidden">
          <div className="flex max-w-full min-w-0 flex-col gap-5 overflow-x-hidden p-4">
            {!meta ? (
              <p className="text-sm text-muted-foreground">
                No metadata for this message.
              </p>
            ) : (
              <>
                <Section title="Summary">
                  <Row label="Message ID">
                    <span className="font-mono text-xs font-normal">
                      {meta.messageId}
                    </span>
                  </Row>
                  <Row label="Role">{meta.role}</Row>
                  <Row label="Conversation">
                    <span className="font-mono text-xs font-normal">
                      {meta.conversationId || "—"}
                    </span>
                  </Row>
                  <Row label="Created">{meta.createdAt}</Row>
                  <Row label="Finished">{meta.finishedAt || "—"}</Row>
                  <Row label="Duration">
                    {meta.durationMs != null
                      ? `${(meta.durationMs / 1000).toFixed(2)}s`
                      : "—"}
                  </Row>
                  <Row label="Content">{meta.contentChars} chars</Row>
                  {meta.role === "assistant" ? (
                    <>
                      <Row label="Token events">{meta.tokenEvents}</Row>
                      <Row label="Token chars">{meta.tokenChars}</Row>
                      <Row label="Finish reason">
                        {meta.finishReason || "—"}
                      </Row>
                      <Row label="Routes">
                        {meta.routes.length ? (
                          <div className="flex min-w-0 flex-wrap gap-1">
                            {meta.routes.map((route, i) => (
                              <Badge
                                key={`${route}-${i}`}
                                variant="outline"
                                className="max-w-full break-all"
                              >
                                {route}
                              </Badge>
                            ))}
                          </div>
                        ) : (
                          "—"
                        )}
                      </Row>
                      <Row label="Tools">{meta.tools.length}</Row>
                    </>
                  ) : null}
                </Section>

                {meta.role === "assistant" && meta.tools.length > 0 ? (
                  <Section title="Tools">
                    {meta.tools.map((tool) => (
                      <div
                        key={tool.id}
                        className="min-w-0 overflow-hidden rounded-lg border bg-background/60 p-2 font-mono text-xs"
                      >
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="min-w-0 break-all">
                            {tool.agent} · {tool.tool}
                          </span>
                          <Badge variant="outline">{tool.state}</Badge>
                        </div>
                        {tool.input ? (
                          <MonoBlock>in: {tool.input}</MonoBlock>
                        ) : null}
                        {tool.output ? (
                          <MonoBlock>out: {tool.output}</MonoBlock>
                        ) : null}
                      </div>
                    ))}
                  </Section>
                ) : null}

                {meta.events.length > 0 ? (
                  <Section title="Event timeline">
                    <ol className="flex min-w-0 flex-col gap-2">
                      {meta.events.map((event, index) => (
                        <li
                          key={`${event.at}-${event.type}-${index}`}
                          className="min-w-0 overflow-hidden rounded-lg border bg-background/60 p-2"
                        >
                          <div className="flex min-w-0 flex-wrap items-center gap-2">
                            <Badge variant="secondary">{event.type}</Badge>
                            <span className="min-w-0 break-all font-mono text-[10px] text-muted-foreground">
                              {event.at}
                            </span>
                          </div>
                          {eventSummary(event) ? (
                            <p className="mt-1 max-w-full min-w-0 overflow-hidden whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">
                              {eventSummary(event)}
                            </p>
                          ) : null}
                        </li>
                      ))}
                    </ol>
                  </Section>
                ) : null}

                <Section title="Raw JSON">
                  <pre className="max-h-80 max-w-full min-w-0 overflow-x-hidden overflow-y-auto rounded-lg bg-background/80 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-muted-foreground [overflow-wrap:anywhere]">
                    {JSON.stringify(meta, null, 2)}
                  </pre>
                </Section>
              </>
            )}
          </div>
        </ScrollArea>
        <Separator />
        <p className="shrink-0 px-4 py-3 text-xs text-muted-foreground">
          Token stream payloads are summarized as counts to keep metadata
          readable.
        </p>
      </SheetContent>
    </Sheet>
  )
}
