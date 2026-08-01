import { useEffect, useId, useState } from "react"
import {
  BotIcon,
  BracesIcon,
  CheckIcon,
  CopyIcon,
  CornerDownLeftIcon,
  NewspaperIcon,
  PlusIcon,
  RouteIcon,
  SearchIcon,
  SparklesIcon,
  SquareFunctionIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react"
import { toast } from "sonner"

import {
  Attachment,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Bubble, BubbleContent } from "@/components/ui/bubble"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker"
import {
  Message,
  MessageAvatar,
  MessageContent,
  MessageFooter,
  MessageHeader,
} from "@/components/ui/message"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { Spinner } from "@/components/ui/spinner"
import { Toaster } from "@/components/ui/sonner"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  AgentSettingsButton,
  AgentSettingsSheet,
} from "@/components/agent-settings-sheet"
import { Markdown } from "@/components/markdown"
import { MessageMetadataSheet } from "@/components/message-metadata-sheet"
import {
  ensureApiKey,
  fetchReady,
  streamChat,
  type MessageMeta,
  type StreamEvent,
} from "@/lib/api"

type ToolActivity = {
  id: string
  tool: string
  agent: string
  input?: string
  output?: string
  state: "processing" | "done" | "error"
}

type ChatItem =
  | { id: string; role: "user"; content: string; meta: MessageMeta }
  | {
      id: string
      role: "assistant"
      content: string
      streaming: boolean
      statusLabel?: string
      route?: string
      tools: ToolActivity[]
      meta: MessageMeta
    }

function emptyMeta(
  messageId: string,
  role: "user" | "assistant",
  conversationId: string | null
): MessageMeta {
  return {
    messageId,
    role,
    conversationId,
    createdAt: new Date().toISOString(),
    contentChars: 0,
    tokenEvents: 0,
    tokenChars: 0,
    routes: [],
    tools: [],
    events: [],
  }
}

function stampEvent(event: StreamEvent): StreamEvent {
  return { ...event, at: new Date().toISOString() }
}

const SUGGESTIONS = [
  {
    label: "Appwrite TypeScript auth",
    prompt:
      "Using the Appwrite TypeScript skill, show how to create an email/password session and get the current user.",
    icon: SparklesIcon,
  },
  {
    label: "Appwrite CLI deploy",
    prompt:
      "Using the Appwrite CLI skill, explain how to init a project and deploy a function non-interactively.",
    icon: SearchIcon,
  },
  {
    label: "Latest BBC headlines",
    prompt: "What are the latest BBC news headlines? Open https://www.bbc.com/news in the browser.",
    icon: NewspaperIcon,
  },
  {
    label: "Quick calculation",
    prompt: "What is 17 × 19? Use the calculator tool.",
    icon: SquareFunctionIcon,
  },
] as const

function newId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`
}

function copyText(text: string) {
  void navigator.clipboard.writeText(text).then(
    () => toast.success("Copied to clipboard"),
    () => toast.error("Could not copy")
  )
}

function ToolCard({ tool }: { tool: ToolActivity }) {
  return (
    <Collapsible className="w-full max-w-xl">
      <Attachment
        state={tool.state === "processing" ? "processing" : "done"}
        size="sm"
        className="w-full max-w-xl"
      >
        <AttachmentMedia>
          {tool.state === "processing" ? <Spinner /> : <WrenchIcon />}
        </AttachmentMedia>
        <AttachmentContent>
          <AttachmentTitle>
            {tool.agent} · {tool.tool}
          </AttachmentTitle>
          <AttachmentDescription>
            {tool.state === "processing"
              ? "Running…"
              : tool.input
                ? tool.input.slice(0, 80)
                : "Completed"}
          </AttachmentDescription>
        </AttachmentContent>
        {(tool.input || tool.output) && (
          <CollapsibleTrigger
            render={
              <Button
                variant="ghost"
                size="xs"
                className="relative z-20 mr-1"
              />
            }
          >
            Details
          </CollapsibleTrigger>
        )}
      </Attachment>
      <CollapsibleContent className="mt-2 rounded-lg border bg-muted/40 p-3 font-mono text-xs text-muted-foreground whitespace-pre-wrap">
        {tool.input ? `input:\n${tool.input}\n\n` : null}
        {tool.output ? `output:\n${tool.output}` : null}
      </CollapsibleContent>
    </Collapsible>
  )
}

export function ChatApp() {
  const listId = useId()
  const [items, setItems] = useState<ChatItem[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState<"checking" | "ok" | "bad">("checking")
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [metaOpen, setMetaOpen] = useState(false)
  const [inspectMeta, setInspectMeta] = useState<MessageMeta | null>(null)

  function openMessageMeta(meta: MessageMeta) {
    setInspectMeta(meta)
    setMetaOpen(true)
  }

  useEffect(() => {
    ensureApiKey()
    fetchReady()
      .then((r) => setReady(r.ready ? "ok" : "bad"))
      .catch(() => setReady("bad"))
    const t = window.setInterval(() => {
      fetchReady()
        .then((r) => setReady(r.ready ? "ok" : "bad"))
        .catch(() => setReady("bad"))
    }, 15000)
    return () => window.clearInterval(t)
  }, [])

  function resetChat() {
    setItems([])
    setConversationId(null)
    setInput("")
    toast.message("Started a new chat")
  }

  async function sendMessage(message: string) {
    if (!message || busy) return
    if (!ensureApiKey()) return

    const userId = newId("user")
    const assistantId = newId("assistant")
    const startedAt = Date.now()
    setInput("")
    setBusy(true)
    setItems((prev) => [
      ...prev,
      {
        id: userId,
        role: "user",
        content: message,
        meta: {
          ...emptyMeta(userId, "user", conversationId),
          contentChars: message.length,
          finishedAt: new Date().toISOString(),
          durationMs: 0,
        },
      },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        streaming: true,
        statusLabel: "Thinking…",
        tools: [],
        meta: emptyMeta(assistantId, "assistant", conversationId),
      },
    ])

    const patchAssistant = (
      fn: (
        item: Extract<ChatItem, { role: "assistant" }>
      ) => Extract<ChatItem, { role: "assistant" }>
    ) => {
      setItems((prev) =>
        prev.map((item) =>
          item.id === assistantId && item.role === "assistant" ? fn(item) : item
        )
      )
    }

    const pushMetaEvent = (
      item: Extract<ChatItem, { role: "assistant" }>,
      event: StreamEvent,
      extra?: Partial<MessageMeta>
    ): MessageMeta => {
      const stamped = stampEvent(event)
      const keepTimeline = event.type !== "token"
      return {
        ...item.meta,
        ...extra,
        conversationId: extra?.conversationId ?? item.meta.conversationId,
        events: keepTimeline
          ? [...item.meta.events, stamped]
          : item.meta.events,
        tools: extra?.tools ?? item.tools,
      }
    }

    try {
      await streamChat(conversationId, message, (event: StreamEvent) => {
        switch (event.type) {
          case "conversation":
            if (event.id) {
              setConversationId(event.id)
              patchAssistant((item) => ({
                ...item,
                meta: pushMetaEvent(item, event, {
                  conversationId: event.id,
                }),
              }))
              setItems((prev) =>
                prev.map((item) =>
                  item.id === userId && item.role === "user"
                    ? {
                        ...item,
                        meta: { ...item.meta, conversationId: event.id },
                      }
                    : item
                )
              )
            }
            break
          case "status":
            patchAssistant((item) => ({
              ...item,
              statusLabel: event.message || "Working…",
              meta: pushMetaEvent(item, event),
            }))
            break
          case "route":
            patchAssistant((item) => ({
              ...item,
              route: event.next,
              statusLabel:
                event.next === "FINISH"
                  ? "Composing answer…"
                  : `Routing to ${event.next}…`,
              meta: pushMetaEvent(item, event, {
                routes: event.next
                  ? [...item.meta.routes, event.next]
                  : item.meta.routes,
                finishReason:
                  event.next === "FINISH"
                    ? event.reason || "FINISH"
                    : item.meta.finishReason,
              }),
            }))
            break
          case "subagent_start":
            patchAssistant((item) => ({
              ...item,
              statusLabel:
                event.agent && event.agent !== "supervisor"
                  ? `${event.agent} is working…`
                  : item.statusLabel,
              meta: pushMetaEvent(item, event),
            }))
            break
          case "subagent_end":
          case "model_start":
            patchAssistant((item) => ({
              ...item,
              meta: pushMetaEvent(item, event),
            }))
            break
          case "tool_start": {
            const toolId = newId("tool")
            patchAssistant((item) => {
              const tools = [
                ...item.tools,
                {
                  id: toolId,
                  tool: event.tool || "tool",
                  agent: event.agent || "agent",
                  input: event.input,
                  state: "processing" as const,
                },
              ]
              return {
                ...item,
                statusLabel: `Running ${event.tool}…`,
                tools,
                meta: pushMetaEvent(item, event, { tools }),
              }
            })
            break
          }
          case "tool_end":
            patchAssistant((item) => {
              const tools = [...item.tools]
              for (let i = tools.length - 1; i >= 0; i--) {
                if (
                  tools[i].tool === event.tool &&
                  tools[i].state === "processing"
                ) {
                  tools[i] = {
                    ...tools[i],
                    output: event.output,
                    state: "done",
                  }
                  break
                }
              }
              return {
                ...item,
                tools,
                statusLabel: "Generating response…",
                meta: pushMetaEvent(item, event, { tools }),
              }
            })
            break
          case "answer_start":
            patchAssistant((item) => ({
              ...item,
              content: "",
              streaming: true,
              statusLabel: "Generating response…",
              meta: pushMetaEvent(item, event),
            }))
            break
          case "answer_reset":
            patchAssistant((item) => ({
              ...item,
              content: "",
              streaming: true,
              statusLabel: "Calling tools…",
              meta: pushMetaEvent(item, event),
            }))
            break
          case "token":
            patchAssistant((item) => {
              const chunk = event.content || ""
              const content = item.content + chunk
              return {
                ...item,
                content,
                streaming: true,
                meta: {
                  ...item.meta,
                  contentChars: content.length,
                  tokenEvents: item.meta.tokenEvents + 1,
                  tokenChars: item.meta.tokenChars + chunk.length,
                },
              }
            })
            break
          case "done": {
            const finishedAt = new Date().toISOString()
            patchAssistant((item) => {
              const content =
                event.answer || item.content || "(empty response)"
              return {
                ...item,
                content,
                streaming: false,
                statusLabel: undefined,
                meta: pushMetaEvent(item, event, {
                  contentChars: content.length,
                  finishedAt,
                  durationMs: Date.now() - startedAt,
                  finishReason: item.meta.finishReason || "done",
                  tools: item.tools,
                }),
              }
            })
            break
          }
          case "error":
            toast.error(event.detail || "Agent error")
            patchAssistant((item) => {
              const content = item.content || event.detail || "Error"
              const finishedAt = new Date().toISOString()
              return {
                ...item,
                content,
                streaming: false,
                statusLabel: undefined,
                meta: pushMetaEvent(item, event, {
                  contentChars: content.length,
                  finishedAt,
                  durationMs: Date.now() - startedAt,
                  finishReason: "error",
                }),
              }
            })
            break
          case "complete":
            patchAssistant((item) => ({
              ...item,
              streaming: false,
              statusLabel: undefined,
              meta: pushMetaEvent(item, event, {
                finishedAt: item.meta.finishedAt || new Date().toISOString(),
                durationMs: item.meta.durationMs ?? Date.now() - startedAt,
                contentChars: item.content.length,
                tools: item.tools,
              }),
            }))
            break
          default:
            patchAssistant((item) => ({
              ...item,
              meta: pushMetaEvent(item, event),
            }))
            break
        }
      })
    } catch (err) {
      const detail = String((err as Error).message || err)
      toast.error(detail)
      patchAssistant((item) => ({
        ...item,
        content: item.content || detail,
        streaming: false,
        statusLabel: undefined,
        meta: {
          ...item.meta,
          finishedAt: new Date().toISOString(),
          durationMs: Date.now() - startedAt,
          finishReason: "client_error",
          contentChars: (item.content || detail).length,
          events: [
            ...item.meta.events,
            stampEvent({ type: "error", detail }),
          ],
        },
      }))
    } finally {
      setBusy(false)
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    await sendMessage(input.trim())
  }

  return (
    <TooltipProvider>
      <Toaster position="top-center" />
      <SidebarProvider className="h-svh overflow-hidden">
        <Sidebar collapsible="icon" variant="inset">
          <SidebarHeader className="gap-3">
            <div className="flex items-center gap-2 px-1">
              <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <BotIcon />
              </div>
              <div className="flex min-w-0 flex-col group-data-[collapsible=icon]:hidden">
                <span className="truncate text-sm font-semibold">
                  Appwrite Assistant
                </span>
                <span className="truncate text-xs text-muted-foreground">
                  LangGraph engine
                </span>
              </div>
            </div>
            <Button className="w-full justify-start" onClick={resetChat}>
              <PlusIcon data-icon="inline-start" />
              New chat
            </Button>
          </SidebarHeader>

          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Try asking</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {SUGGESTIONS.map((s) => (
                    <SidebarMenuItem key={s.label}>
                      <SidebarMenuButton
                        tooltip={s.label}
                        disabled={busy}
                        onClick={() => void sendMessage(s.prompt)}
                      >
                        <s.icon />
                        <span>{s.label}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>

            <SidebarGroup>
              <SidebarGroupLabel>Session</SidebarGroupLabel>
              <SidebarGroupContent className="px-2">
                <div className="flex flex-col gap-2 rounded-lg border bg-sidebar-accent/40 p-3 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
                  <div className="flex items-center justify-between gap-2">
                    <span>Engine</span>
                    <Badge
                      variant={ready === "ok" ? "secondary" : "outline"}
                    >
                      {ready === "checking" ? (
                        <Spinner />
                      ) : ready === "ok" ? (
                        "Ready"
                      ) : (
                        "Offline"
                      )}
                    </Badge>
                  </div>
                  <Separator />
                  <p className="leading-relaxed">
                    No host shell access. Public https browsing only.
                  </p>
                </div>
                <SidebarMenu className="mt-2">
                  <SidebarMenuItem>
                    <AgentSettingsButton
                      variant="menu"
                      onClick={() => setSettingsOpen(true)}
                    />
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>

          <SidebarFooter className="group-data-[collapsible=icon]:hidden">
            <p className="px-2 text-xs text-muted-foreground">
              Built with{" "}
              <a
                className="underline underline-offset-3 hover:text-foreground"
                href="https://ui.shadcn.com/docs/changelog/2026-06-chat-components"
                target="_blank"
                rel="noreferrer"
              >
                shadcn chat
              </a>
            </p>
          </SidebarFooter>
        </Sidebar>

        <SidebarInset className="flex h-svh min-h-0 flex-col overflow-hidden md:h-[calc(100svh-1rem)]">
          <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b px-4">
            <div className="flex items-center gap-2">
              <SidebarTrigger />
              <Separator orientation="vertical" className="h-4" />
              <div className="flex flex-col">
                <span className="text-sm font-medium">
                  {conversationId ? "Conversation" : "New conversation"}
                </span>
                <span className="text-xs text-muted-foreground">
                  Supervisor · appwrite · researcher · worker
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <AgentSettingsButton onClick={() => setSettingsOpen(true)} />
              <Badge variant={ready === "ok" ? "secondary" : "outline"}>
                {ready === "ok" ? "Live" : ready === "bad" ? "Offline" : "…"}
              </Badge>
            </div>
          </header>

          <AgentSettingsSheet
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
          />
          <MessageMetadataSheet
            open={metaOpen}
            onOpenChange={setMetaOpen}
            meta={inspectMeta}
          />

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {items.length === 0 ? (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto flex w-full max-w-3xl items-center px-4 py-8">
                  <Empty className="border-0">
                    <EmptyHeader>
                      <EmptyMedia variant="icon">
                        <SparklesIcon />
                      </EmptyMedia>
                      <EmptyTitle>How can I help you today?</EmptyTitle>
                      <EmptyDescription>
                        Press send to start a new conversation. Watch routing,
                        tools, and streamed answers land in real time.
                      </EmptyDescription>
                    </EmptyHeader>
                    <EmptyContent>
                      <div className="flex w-full max-w-md flex-col gap-2">
                        {SUGGESTIONS.map((s) => (
                          <Bubble key={s.label} variant="muted">
                            <BubbleContent
                              render={
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => void sendMessage(s.prompt)}
                                />
                              }
                            >
                              {s.label}
                            </BubbleContent>
                          </Bubble>
                        ))}
                      </div>
                    </EmptyContent>
                  </Empty>
                </div>
              </div>
            ) : (
              <MessageScrollerProvider autoScroll defaultScrollPosition="end">
                <MessageScroller className="relative min-h-0 flex-1 overflow-hidden">
                  <MessageScrollerViewport className="absolute inset-0 size-auto">
                    <MessageScrollerContent className="mx-auto w-full max-w-3xl gap-6 px-4 py-8">
                      <MessageScrollerItem messageId={`${listId}-today`}>
                        <Marker variant="separator">
                          <MarkerContent>Today</MarkerContent>
                        </Marker>
                      </MessageScrollerItem>

                      {items.map((item) =>
                        item.role === "user" ? (
                          <MessageScrollerItem
                            key={item.id}
                            messageId={item.id}
                          >
                              <Message align="end">
                              <MessageAvatar>
                                <Avatar>
                                  <AvatarFallback>
                                    <UserIcon />
                                  </AvatarFallback>
                                </Avatar>
                              </MessageAvatar>
                              <MessageContent>
                                <MessageHeader>You</MessageHeader>
                                <Bubble variant="default" align="end">
                                  <BubbleContent>
                                    <Markdown>{item.content}</Markdown>
                                  </BubbleContent>
                                </Bubble>
                                <MessageFooter>
                                  <Tooltip>
                                    <TooltipTrigger
                                      render={
                                        <Button
                                          variant="ghost"
                                          size="icon-xs"
                                          aria-label="Message metadata"
                                          onClick={() =>
                                            openMessageMeta({
                                              ...item.meta,
                                              contentChars: item.content.length,
                                            })
                                          }
                                        />
                                      }
                                    >
                                      <BracesIcon />
                                    </TooltipTrigger>
                                    <TooltipContent>Metadata</TooltipContent>
                                  </Tooltip>
                                  <span className="text-muted-foreground">
                                    {item.content.length} chars
                                  </span>
                                </MessageFooter>
                              </MessageContent>
                            </Message>
                          </MessageScrollerItem>
                        ) : (
                          <MessageScrollerItem
                            key={item.id}
                            messageId={item.id}
                          >
                            <Message align="start">
                              <MessageAvatar>
                                <Avatar>
                                  <AvatarFallback>
                                    <BotIcon />
                                  </AvatarFallback>
                                </Avatar>
                              </MessageAvatar>
                              <MessageContent>
                                <MessageHeader>Assistant</MessageHeader>

                                <div className="flex w-full min-w-0 flex-col gap-3">
                                  {item.route && item.route !== "FINISH" ? (
                                    <Marker>
                                      <MarkerIcon>
                                        <RouteIcon />
                                      </MarkerIcon>
                                      <MarkerContent>
                                        Routed to {item.route}
                                      </MarkerContent>
                                    </Marker>
                                  ) : null}

                                  {item.tools.map((tool) => (
                                    <ToolCard key={tool.id} tool={tool} />
                                  ))}

                                  {item.streaming && !item.content ? (
                                    <Marker role="status">
                                      <MarkerIcon>
                                        <Spinner />
                                      </MarkerIcon>
                                      <MarkerContent className="shimmer">
                                        {item.statusLabel || "Thinking…"}
                                      </MarkerContent>
                                    </Marker>
                                  ) : null}

                                  {item.content ? (
                                    <Bubble variant="ghost" align="start">
                                      <BubbleContent>
                                        <Markdown>{item.content}</Markdown>
                                        {item.streaming ? (
                                          <span className="ml-0.5 inline-block h-4 w-1 translate-y-0.5 bg-foreground/70 animate-pulse" />
                                        ) : null}
                                      </BubbleContent>
                                    </Bubble>
                                  ) : null}
                                </div>

                                {!item.streaming && item.content ? (
                                  <MessageFooter>
                                    <Tooltip>
                                      <TooltipTrigger
                                        render={
                                          <Button
                                            variant="ghost"
                                            size="icon-xs"
                                            aria-label="Copy"
                                            onClick={() =>
                                              copyText(item.content)
                                            }
                                          />
                                        }
                                      >
                                        <CopyIcon />
                                      </TooltipTrigger>
                                      <TooltipContent>Copy</TooltipContent>
                                    </Tooltip>
                                    <Tooltip>
                                      <TooltipTrigger
                                        render={
                                          <Button
                                            variant="ghost"
                                            size="icon-xs"
                                            aria-label="Message metadata"
                                            onClick={() =>
                                              openMessageMeta({
                                                ...item.meta,
                                                contentChars:
                                                  item.content.length,
                                                tools: item.tools,
                                              })
                                            }
                                          />
                                        }
                                      >
                                        <BracesIcon />
                                      </TooltipTrigger>
                                      <TooltipContent>Metadata</TooltipContent>
                                    </Tooltip>
                                    <span className="text-muted-foreground">
                                      <CheckIcon className="mr-1 inline size-3" />
                                      Done
                                      {item.meta.durationMs != null
                                        ? ` · ${(item.meta.durationMs / 1000).toFixed(1)}s`
                                        : ""}
                                      {item.meta.events.length
                                        ? ` · ${item.meta.events.length} events`
                                        : ""}
                                    </span>
                                  </MessageFooter>
                                ) : null}
                              </MessageContent>
                            </Message>
                          </MessageScrollerItem>
                        )
                      )}
                    </MessageScrollerContent>
                  </MessageScrollerViewport>
                  <MessageScrollerButton />
                </MessageScroller>
              </MessageScrollerProvider>
            )}

            <div className="border-t bg-background/80 px-4 py-3 backdrop-blur supports-backdrop-filter:bg-background/60">
              <form
                className="mx-auto flex w-full max-w-3xl flex-col gap-2"
                onSubmit={onSubmit}
              >
                <InputGroup className="h-auto min-h-14 rounded-2xl">
                  <InputGroupTextarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Message Appwrite Assistant…"
                    disabled={busy}
                    rows={2}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault()
                        void onSubmit(e)
                      }
                    }}
                  />
                  <InputGroupAddon
                    align="block-end"
                    className="justify-between border-t"
                  >
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <KbdGroup>
                        <Kbd>↵</Kbd>
                      </KbdGroup>
                      <span>send</span>
                      <span className="text-border">·</span>
                      <KbdGroup>
                        <Kbd>⇧</Kbd>
                        <Kbd>↵</Kbd>
                      </KbdGroup>
                      <span>newline</span>
                    </div>
                    <InputGroupButton
                      type="submit"
                      variant="default"
                      size="sm"
                      disabled={busy || !input.trim()}
                    >
                      {busy ? (
                        <Spinner data-icon="inline-start" />
                      ) : (
                        <CornerDownLeftIcon data-icon="inline-start" />
                      )}
                      Send
                    </InputGroupButton>
                  </InputGroupAddon>
                </InputGroup>
              </form>
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
