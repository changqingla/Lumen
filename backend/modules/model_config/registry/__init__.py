"""Static provider registry for the model configuration domain."""

from .model_provider_registry import (
    PROVIDER_REGISTRY,
    ProviderDefinition,
    ProviderModelDefinition,
    find_provider_for_runtime_model,
    get_provider_definition,
    list_provider_definitions,
)

__all__ = [
    "PROVIDER_REGISTRY",
    "ProviderDefinition",
    "ProviderModelDefinition",
    "find_provider_for_runtime_model",
    "get_provider_definition",
    "list_provider_definitions",
]
