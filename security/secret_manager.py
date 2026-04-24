"""Google Secret Manager helper with local .env fallback."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("spepe.security.secrets")

_cache: dict[str, str] = {}


def get_secret(secret_id: str, project_id: str | None = None) -> str:
    """Retrieve secret from Secret Manager (prod) or environment (dev)."""
    if secret_id in _cache:
        return _cache[secret_id]

    env_val = os.environ.get(secret_id, "")
    if env_val:
        _cache[secret_id] = env_val
        return env_val

    gcp_project = project_id or os.environ.get("GCP_PROJECT_ID", "")
    if not gcp_project:
        logger.debug(f"GCP_PROJECT_ID não configurado — usando env para {secret_id}")
        return ""

    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{gcp_project}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        value = response.payload.data.decode("utf-8").strip()
        _cache[secret_id] = value
        logger.debug(f"Secret carregado do Secret Manager: {secret_id}")
        return value
    except ImportError:
        logger.warning("google-cloud-secret-manager não instalado. Usando env.")
        return ""
    except Exception as e:
        logger.error(f"Falha ao ler Secret Manager ({secret_id}): {e}")
        return ""


def load_all_secrets() -> None:
    """Pre-load known secrets into environment at startup."""
    secrets_to_load = [
        "ANTHROPIC_API_KEY",
        "META_APP_TOKEN",
        "YOUTUBE_API_KEY",
    ]
    for secret_id in secrets_to_load:
        value = get_secret(secret_id)
        if value:
            os.environ[secret_id] = value
