import { useEffect, useState, type ReactNode } from "react"
import {
  CheckIcon,
  CopyIcon,
  RefreshCwIcon,
  Settings2Icon,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Spinner } from "@/components/ui/spinner"
import {
  ensureApiKey,
  fetchAgentSettings,
  type AgentSettings,
} from "@/lib/api"

function Flag({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Badge variant={ok ? "secondary" : "outline"}>
      {ok ? "configured" : "missing"} · {label}
    </Badge>
  )
}

function Row({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,11rem)_minmax(0,1fr)] items-start gap-3 text-sm">
      <dt className="min-w-0 break-words text-muted-foreground [overflow-wrap:anywhere]">
        {label}
      </dt>
      <dd className="min-w-0 overflow-hidden break-all font-medium [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  )
}

function EnvRow({ name, value }: { name: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 text-sm">
      <span className="font-mono text-xs break-all text-muted-foreground">
        {name}
      </span>
      <span className="min-w-0 break-all font-mono text-xs font-medium">
        {value || <span className="text-muted-foreground">unset</span>}
      </span>
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
    <section className="flex flex-col gap-3">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      <div className="flex min-w-0 flex-col gap-2.5 overflow-hidden rounded-xl border bg-muted/20 p-3">
        {children}
      </div>
    </section>
  )
}

export function AgentSettingsSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState<AgentSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    if (!ensureApiKey()) return
    setLoading(true)
    setError(null)
    try {
      setSettings(await fetchAgentSettings())
    } catch (err) {
      setError(String((err as Error).message || err))
      setSettings(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) void load()
  }, [open])

  function copyJson() {
    if (!settings) return
    void navigator.clipboard.writeText(JSON.stringify(settings, null, 2)).then(
      () => toast.success("Settings copied"),
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
          <SheetTitle>Agent settings</SheetTitle>
          <SheetDescription>
            Live runtime config from the assistant engine. Secrets stay masked.
          </SheetDescription>
          <div className="flex gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load()}
              disabled={loading}
            >
              {loading ? <Spinner /> : <RefreshCwIcon />}
              Refresh
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={copyJson}
              disabled={!settings}
            >
              <CopyIcon />
              Copy JSON
            </Button>
          </div>
        </SheetHeader>

        <ScrollArea className="min-h-0 min-w-0 flex-1 overflow-x-hidden">
          <div className="flex max-w-full min-w-0 flex-col gap-5 overflow-x-hidden p-4">
            {loading && !settings ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner /> Loading settings…
              </div>
            ) : null}

            {error ? (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                {error}
              </div>
            ) : null}

            {settings ? (
              <>
                <Section title="LLM">
                  <Row label="Model">{settings.llm.model}</Row>
                  <Row label="Chat model">{settings.llm.chat_model}</Row>
                  <Row label="Base URL">
                    {settings.llm.base_url || (
                      <span className="text-muted-foreground">default</span>
                    )}
                  </Row>
                  <Row label="Temperature">{settings.llm.temperature}</Row>
                  <Row label="API key">
                    <Flag
                      ok={settings.llm.api_key_configured}
                      label="LLM_API_KEY"
                    />
                  </Row>
                </Section>

                <Section title="Auth">
                  <Row label="Header">{settings.auth.header}</Row>
                  <Row label="Session key">
                    <Flag
                      ok={settings.auth.session_api_key_configured}
                      label="configured"
                    />
                  </Row>
                </Section>

                <Section title="Runtime">
                  <Row label="Graph">{settings.runtime.graph}</Row>
                  <Row label="Max handoffs">
                    {settings.runtime.max_handoffs}
                  </Row>
                  <Row label="Subagent steps">
                    {settings.runtime.subagent_recursion_limit}
                  </Row>
                  <Row label="History window">
                    {settings.runtime.history_window} turns
                  </Row>
                </Section>

                <Section title="Browser fetch">
                  <Row label="Status">
                    <Badge
                      variant={
                        settings.browser_fetch.enabled ? "secondary" : "outline"
                      }
                    >
                      {settings.browser_fetch.enabled ? "enabled" : "disabled"}
                    </Badge>
                  </Row>
                  <Row label="Engine">{settings.browser_fetch.engine}</Row>
                  <Row label="Timeout">
                    {settings.browser_fetch.timeout_ms} ms
                  </Row>
                  <Row label="Text limit">
                    {settings.browser_fetch.text_limit} chars
                  </Row>
                  <Row label="Cache TTL">
                    {settings.browser_fetch.cache_ttl_seconds}s
                  </Row>
                  <Row label="Domains">
                    {settings.browser_fetch.domain_limits ||
                      "public https only"}
                  </Row>
                </Section>

                <Section title="Web search">
                  <Row label="Status">
                    <Badge
                      variant={
                        settings.web_search?.enabled ? "secondary" : "outline"
                      }
                    >
                      {settings.web_search?.enabled ? "enabled" : "disabled"}
                    </Badge>
                  </Row>
                  <Row label="Engine">
                    {settings.web_search?.engine || "—"}
                  </Row>
                  <Row label="API key">
                    {settings.web_search?.api_key_required
                      ? "required"
                      : "not required"}
                  </Row>
                </Section>

                <Section title="Appwrite skills">
                  <Row label="Installed">
                    {settings.appwrite_skills?.count ?? 0}
                  </Row>
                  <Row label="Loader">
                    <span className="font-mono text-xs">
                      {settings.appwrite_skills?.loader_tool || "appwrite_skill"}
                    </span>
                  </Row>
                  {(settings.appwrite_skills?.skills || []).map((skill) => (
                    <div key={skill.name} className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <CheckIcon className="size-3.5 text-muted-foreground" />
                        <span className="font-mono text-sm">{skill.name}</span>
                      </div>
                      <p className="pl-5 text-xs leading-relaxed text-muted-foreground">
                        {skill.description}
                      </p>
                    </div>
                  ))}
                </Section>

                <Section title="Tools">
                  {settings.tools.map((tool) => (
                    <div key={tool.name} className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <CheckIcon className="size-3.5 text-muted-foreground" />
                        <span className="font-mono text-sm">{tool.name}</span>
                      </div>
                      <p className="pl-5 text-xs leading-relaxed text-muted-foreground">
                        {tool.description}
                      </p>
                    </div>
                  ))}
                </Section>

                <Section title="Agents">
                  {settings.agents.map((agent) => (
                    <Collapsible key={agent.name} className="rounded-lg border">
                      <CollapsibleTrigger
                        render={
                          <button
                            type="button"
                            className="flex w-full items-start justify-between gap-2 px-3 py-2 text-left hover:bg-muted/40"
                          />
                        }
                      >
                        <div>
                          <div className="font-mono text-sm">{agent.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {agent.role}
                          </div>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          Prompt
                        </span>
                      </CollapsibleTrigger>
                      <CollapsibleContent className="max-w-full min-w-0 overflow-x-hidden border-t bg-muted/30 px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap text-muted-foreground [overflow-wrap:anywhere]">
                        {agent.prompt}
                      </CollapsibleContent>
                    </Collapsible>
                  ))}
                </Section>

                <Section title="Environment">
                  {Object.entries(settings.env).map(([key, value]) => (
                    <EnvRow key={key} name={key} value={value} />
                  ))}
                </Section>

                <Section title="Server">
                  <Row label="Bind">
                    {settings.server.host}:{settings.server.port}
                  </Row>
                </Section>
              </>
            ) : null}
          </div>
        </ScrollArea>
        <Separator />
        <p className="px-4 py-3 text-xs text-muted-foreground">
          Read-only inspection. Change values via container env / `.env`, then
          rebuild.
        </p>
      </SheetContent>
    </Sheet>
  )
}

export function AgentSettingsButton({
  onClick,
  variant = "icon",
}: {
  onClick: () => void
  variant?: "icon" | "menu"
}) {
  if (variant === "menu") {
    return (
      <Button
        variant="ghost"
        className="h-8 w-full justify-start px-2"
        onClick={onClick}
      >
        <Settings2Icon data-icon="inline-start" />
        Agent settings
      </Button>
    )
  }

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label="Agent settings"
      onClick={onClick}
    >
      <Settings2Icon />
    </Button>
  )
}
