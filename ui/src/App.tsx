import { ChatApp } from "@/components/chat-app"
import {
  McpOAuthCallbackPage,
  McpOAuthStartPage,
} from "@/components/mcp-oauth-pages"

export default function App() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/"

  if (path === "/oauth/mcp/start") {
    return <McpOAuthStartPage />
  }
  if (path === "/oauth/mcp/callback") {
    return <McpOAuthCallbackPage />
  }

  return <ChatApp />
}
