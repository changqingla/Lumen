"""Elasticsearch utilities."""


def get_user_es_index(user_id: str) -> str:
    """
    Get Elasticsearch index name for a user.
    
    Format: {user_id}_reader
    All documents from all knowledge bases of this user use the same index.
    
    Args:
        user_id: User ID (UUID string)
    
    Returns:
        ES index name
    """
    # Remove hyphens from UUID and add _reader suffix
    clean_id = str(user_id).replace('-', '')
    return f"{clean_id}_reader"

