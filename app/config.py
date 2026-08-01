from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    assistant_api_key: str = Field(default="", alias="ASSISTANT_API_KEY")

    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="openai/gpt-4o", alias="LLM_MODEL")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")

    # Headless browser web search (no Search API key). Default on.
    web_search_enabled: bool = Field(default=True, alias="WEB_SEARCH_ENABLED")

    # Public origin used for MCP OAuth redirect_uri (usually the UI host).
    mcp_oauth_redirect_base: str = Field(
        default="http://localhost:3001",
        alias="MCP_OAUTH_REDIRECT_BASE",
    )
    mcp_oauth_client_name: str = Field(
        default="Appwrite Assistant",
        alias="MCP_OAUTH_CLIENT_NAME",
    )
    mcp_data_dir: str = Field(default=".data/mcp", alias="MCP_DATA_DIR")

    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    @property
    def chat_model(self) -> str:
        # Accept litellm-style "openai/gpt-4o" or bare "gpt-4o".
        model = self.llm_model
        if "/" in model:
            _, model = model.split("/", 1)
        return model


@lru_cache
def get_settings() -> Settings:
    return Settings()
