"""Input sanitization utilities for arXiv queries.

This module provides functions to sanitize and validate user input
before sending to arXiv MCP server tools.
"""
import re
from typing import Optional, Tuple


# arXiv ID patterns
# New format: YYMM.NNNNN (e.g., 2301.00001)
# Old format: category/YYMMNNN (e.g., hep-th/9901001)
ARXIV_NEW_ID_PATTERN = re.compile(r'^(\d{4}\.\d{4,5})(v\d+)?$')
ARXIV_OLD_ID_PATTERN = re.compile(r'^([a-z-]+/\d{7})(v\d+)?$', re.IGNORECASE)

# Characters that need escaping in arXiv search queries
SPECIAL_CHARS = ['\\', '"', "'", '(', ')', '[', ']', '{', '}', ':', ';', '&', '|', '!', '^', '~', '*', '?', '+', '-']


def sanitize_search_query(query: str) -> str:
    """Sanitize a search query for arXiv API.
    
    Removes or escapes special characters that could cause issues
    with the arXiv search API while preserving the search intent.
    
    Args:
        query: Raw search query from user
        
    Returns:
        Sanitized query safe for API calls
    """
    if not query:
        return ""
    
    # Strip leading/trailing whitespace
    query = query.strip()
    
    # Replace multiple spaces with single space
    query = re.sub(r'\s+', ' ', query)
    
    # Remove or escape special characters
    sanitized = []
    for char in query:
        if char in SPECIAL_CHARS:
            # Skip most special characters, but keep some with escaping
            if char in ['-', '+']:
                # Keep hyphen and plus for compound terms
                sanitized.append(char)
            elif char == '"':
                # Keep quotes for exact phrase matching
                sanitized.append(char)
            # Skip other special characters
        else:
            sanitized.append(char)
    
    result = ''.join(sanitized)
    
    # Ensure balanced quotes
    quote_count = result.count('"')
    if quote_count % 2 != 0:
        # Remove all quotes if unbalanced
        result = result.replace('"', '')
    
    # Final cleanup
    result = result.strip()
    
    return result


def validate_arxiv_id(arxiv_id: str) -> Tuple[bool, Optional[str]]:
    """Validate an arXiv paper ID.
    
    Checks if the provided string is a valid arXiv identifier
    in either the new format (YYMM.NNNNN) or old format (category/YYMMNNN).
    
    Args:
        arxiv_id: The arXiv ID to validate
        
    Returns:
        Tuple of (is_valid, cleaned_id or error_message)
        - If valid: (True, cleaned_id)
        - If invalid: (False, error_message)
    """
    if not arxiv_id:
        return False, "arXiv ID cannot be empty"
    
    # Clean up the ID
    arxiv_id = arxiv_id.strip()
    
    # Remove common prefixes
    prefixes_to_remove = ['arxiv:', 'arXiv:', 'ARXIV:', 'http://arxiv.org/abs/', 'https://arxiv.org/abs/',
                          'http://arxiv.org/pdf/', 'https://arxiv.org/pdf/']
    for prefix in prefixes_to_remove:
        if arxiv_id.startswith(prefix):
            arxiv_id = arxiv_id[len(prefix):]
            break
    
    # Remove .pdf suffix
    if arxiv_id.endswith('.pdf'):
        arxiv_id = arxiv_id[:-4]
    
    # Check new format (YYMM.NNNNN)
    match = ARXIV_NEW_ID_PATTERN.match(arxiv_id)
    if match:
        # Return ID without version suffix
        return True, match.group(1)
    
    # Check old format (category/YYMMNNN)
    match = ARXIV_OLD_ID_PATTERN.match(arxiv_id)
    if match:
        # Return ID without version suffix, lowercase category
        base_id = match.group(1).lower()
        return True, base_id
    
    return False, f"Invalid arXiv ID format: '{arxiv_id}'. Expected format: YYMM.NNNNN (e.g., 2301.00001) or category/YYMMNNN (e.g., hep-th/9901001)"


def sanitize_arxiv_id(arxiv_id: str) -> str:
    """Sanitize and normalize an arXiv ID.
    
    Cleans up the ID by removing prefixes, suffixes, and normalizing format.
    
    Args:
        arxiv_id: Raw arXiv ID string
        
    Returns:
        Cleaned arXiv ID, or empty string if invalid
    """
    is_valid, result = validate_arxiv_id(arxiv_id)
    if is_valid:
        return result
    return ""


def extract_arxiv_ids_from_text(text: str) -> list:
    """Extract arXiv IDs from a text string.
    
    Finds all arXiv IDs mentioned in the text, useful for parsing
    user queries that mention multiple papers.
    
    Args:
        text: Text that may contain arXiv IDs
        
    Returns:
        List of extracted arXiv IDs
    """
    if not text:
        return []
    
    ids = []
    
    # Find new format IDs
    new_format_matches = re.findall(r'\b(\d{4}\.\d{4,5})(?:v\d+)?\b', text)
    ids.extend(new_format_matches)
    
    # Find old format IDs
    old_format_matches = re.findall(r'\b([a-z-]+/\d{7})(?:v\d+)?\b', text, re.IGNORECASE)
    ids.extend([m.lower() for m in old_format_matches])
    
    # Remove duplicates while preserving order
    seen = set()
    unique_ids = []
    for id_ in ids:
        if id_ not in seen:
            seen.add(id_)
            unique_ids.append(id_)
    
    return unique_ids


def is_arxiv_search_query(query: str) -> bool:
    """Determine if a query is likely an arXiv search query.
    
    Heuristic check to determine if the user's query is intended
    for arXiv paper search.
    
    Args:
        query: User's query string
        
    Returns:
        True if the query appears to be an arXiv search
    """
    if not query:
        return False
    
    query_lower = query.lower()
    
    # Keywords that suggest arXiv search
    arxiv_keywords = [
        'arxiv', 'paper', 'papers', 'research', 'publication',
        'preprint', 'manuscript', 'article', 'study', 'studies',
        'find paper', 'search paper', 'look for paper',
        'academic', 'scientific', 'journal'
    ]
    
    for keyword in arxiv_keywords:
        if keyword in query_lower:
            return True
    
    # Check if query contains an arXiv ID
    if extract_arxiv_ids_from_text(query):
        return True
    
    return False


def prepare_arxiv_tool_input(tool_name: str, user_input: str) -> dict:
    """Prepare sanitized input for arXiv MCP tools.
    
    Sanitizes and formats user input appropriately for different
    arXiv MCP server tools.
    
    Args:
        tool_name: Name of the arXiv tool (e.g., 'search_papers', 'get_paper')
        user_input: Raw user input
        
    Returns:
        Dictionary of sanitized arguments for the tool
    """
    if tool_name in ['search_papers', 'arxiv_search_papers']:
        # Search tool - sanitize query
        return {"query": sanitize_search_query(user_input)}
    
    elif tool_name in ['get_paper', 'arxiv_get_paper']:
        # Get paper tool - validate and sanitize arXiv ID
        is_valid, result = validate_arxiv_id(user_input)
        if is_valid:
            return {"paper_id": result}
        else:
            # Try to extract ID from text
            ids = extract_arxiv_ids_from_text(user_input)
            if ids:
                return {"paper_id": ids[0]}
            # Return as-is and let the tool handle the error
            return {"paper_id": user_input.strip()}
    
    elif tool_name in ['download_paper', 'arxiv_download_paper']:
        # Download tool - validate arXiv ID
        is_valid, result = validate_arxiv_id(user_input)
        if is_valid:
            return {"paper_id": result}
        else:
            ids = extract_arxiv_ids_from_text(user_input)
            if ids:
                return {"paper_id": ids[0]}
            return {"paper_id": user_input.strip()}
    
    elif tool_name in ['list_papers', 'arxiv_list_papers']:
        # List papers tool - no input needed
        return {}
    
    else:
        # Unknown tool - return query as-is
        return {"query": sanitize_search_query(user_input)}
