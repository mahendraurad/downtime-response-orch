"""Non-network tests for enabled LLM policy and endpoint normalization."""
from src.tools.config_loader import (
    load_learning_config, load_prescriptive_config, load_risk_config,
)
from src.tools.llm_client import _extract_foundry_base


def test_responses_endpoint_normalizes_to_foundry_resource_origin():
    endpoint = "https://example.services.ai.azure.com/openai/v1/responses"
    assert _extract_foundry_base(endpoint) == "https://example.services.ai.azure.com"


def test_llm_policies_are_enabled_for_runtime_agents():
    assert load_risk_config().llm_enabled is True
    assert load_prescriptive_config().llm_rationale_enabled is True
    assert load_learning_config().llm_narrative_enabled is True
