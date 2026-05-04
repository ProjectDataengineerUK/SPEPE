import json
from pydantic_settings import BaseSettings


class SPEPESettings(BaseSettings):
    anthropic_api_key: str = ""
    default_model: str = "claude-opus-4-6"
    max_budget_usd: float = 2.0
    max_turns: int = 50

    tier_model_map: str = "{}"

    gcp_project_id: str = ""
    gcs_bucket: str = ""
    bigquery_dataset_silver: str = "spepe_silver"
    bigquery_dataset_gold: str = "spepe_gold"
    bigquery_dataset_mlops: str = "spepe_mlops"
    vertex_location: str = "us-central1"
    # Claude via Vertex AI — uses ADC/WIF, no API key required
    use_vertex_claude: bool = False
    vertex_claude_location: str = "us-east5"
    firestore_collection: str = "spepe_sessions"

    log_level: str = "INFO"
    agent_permission_mode: str = "bypassPermissions"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    def get_tier_model_map(self) -> dict:
        try:
            return json.loads(self.tier_model_map)
        except Exception:
            return {}

    def model_for_tier(self, tier: str) -> str:
        return self.get_tier_model_map().get(tier, self.default_model)

    def startup_diagnostics(self) -> None:
        import logging

        logger = logging.getLogger("spepe.settings")
        logger.info(
            "SPEPE iniciando | project=%s | model=%s",
            self.gcp_project_id,
            self.default_model,
        )
        if self.use_vertex_claude:
            logger.info("Claude via Vertex AI (us-east5) — sem API key Anthropic")
        elif not self.anthropic_api_key:
            logger.warning("ANTHROPIC_API_KEY não configurada")


settings = SPEPESettings()
