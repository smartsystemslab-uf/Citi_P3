from google.adk.models.lite_llm import LiteLlm
from .settings import settings

def build_model() -> LiteLlm:
    return LiteLlm(
        model=f"openai/{settings.model_name}",
        api_base=settings.api_base,
        api_key=settings.api_key or "missing-api-key",
    )
