"""Repository exports with lazy loading."""

from importlib import import_module
from typing import Any

__all__ = [
    "UserRepository",
    "ActivationCodeRepository",
    "OrganizationRepository",
    "organization_repository",
    "OrganizationMemberRepository",
    "KnowledgeBaseRepository",
    "ChatRepository",
    "DocumentRepository",
]

_EXPORT_MAP = {
    "UserRepository": (".user_repository", "UserRepository"),
    "organization_repository": (
        "modules.organization.repositories.organization_repository",
        None,
    ),
    "ActivationCodeRepository": (
        "modules.admin.repositories.activation_code_repository",
        "ActivationCodeRepository",
    ),
    "OrganizationRepository": ("modules.organization.repositories.organization_repository", "OrganizationRepository"),
    "OrganizationMemberRepository": (
        "modules.organization.repositories.organization_member_repository",
        "OrganizationMemberRepository",
    ),
    "KnowledgeBaseRepository": (
        "modules.knowledge.repositories.kb_repository",
        "KnowledgeBaseRepository",
    ),
    "ChatRepository": ("modules.chat.repositories.chat_repository", "ChatRepository"),
    "DocumentRepository": (
        "modules.knowledge.repositories.document_repository",
        "DocumentRepository",
    ),
}


def __getattr__(name: str) -> Any:
    export = _EXPORT_MAP.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = export
    module = import_module(module_name, __name__)
    value = module if attribute_name is None else getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
