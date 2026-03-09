"""arXiv-specific result formatting utilities.

This module provides formatting functions for arXiv paper search results
and paper details to present them in a user-friendly format.
"""
import json
import re
from typing import Any, Dict, List, Optional, Union

from .models import ArxivPaper


def format_arxiv_search_results(results: Union[str, Dict, List]) -> str:
    """Format arXiv search results for display.
    
    Formats search results with title, authors, and abstract for each paper.
    
    Args:
        results: Raw search results (JSON string, dict, or list of papers)
        
    Returns:
        Formatted string with paper information
    """
    papers = _parse_results(results)
    
    if not papers:
        return "No papers found matching your search query."
    
    formatted_lines = [f"Found {len(papers)} paper(s):\n"]
    
    for i, paper in enumerate(papers, 1):
        formatted_lines.append(_format_single_paper(paper, index=i))
    
    return "\n".join(formatted_lines)


def format_paper_details(paper_data: Union[str, Dict]) -> str:
    """Format detailed paper information.
    
    Formats full paper metadata including title, authors, abstract,
    categories, and publication date.
    
    Args:
        paper_data: Raw paper data (JSON string or dict)
        
    Returns:
        Formatted string with full paper details
    """
    if isinstance(paper_data, str):
        try:
            paper_data = json.loads(paper_data)
        except json.JSONDecodeError:
            return paper_data  # Return as-is if not valid JSON
    
    if not paper_data:
        return "Paper details not available."
    
    paper = _normalize_paper_data(paper_data)
    
    lines = []
    lines.append(f"📄 **{paper.get('title', 'Unknown Title')}**\n")
    
    # Authors
    authors = paper.get('authors', [])
    if authors:
        if isinstance(authors, list):
            authors_str = ', '.join(authors)
        else:
            authors_str = str(authors)
        lines.append(f"👥 **Authors:** {authors_str}\n")
    
    # Abstract
    abstract = paper.get('abstract', '')
    if abstract:
        lines.append(f"📝 **Abstract:**\n{abstract}\n")
    
    # Categories
    categories = paper.get('categories', [])
    if categories:
        if isinstance(categories, list):
            categories_str = ', '.join(categories)
        else:
            categories_str = str(categories)
        lines.append(f"🏷️ **Categories:** {categories_str}\n")
    
    # Publication date
    published = paper.get('published', '')
    if published:
        lines.append(f"📅 **Published:** {published}\n")
    
    # arXiv ID and URLs
    arxiv_id = paper.get('arxiv_id', '') or paper.get('id', '')
    if arxiv_id:
        # Clean up arxiv_id if it contains version
        clean_id = _extract_arxiv_id(arxiv_id)
        pdf_url = construct_pdf_url(clean_id)
        abs_url = construct_abstract_url(clean_id)
        
        lines.append(f"🔗 **arXiv ID:** {clean_id}")
        lines.append(f"📎 **PDF:** {pdf_url}")
        lines.append(f"🌐 **Abstract Page:** {abs_url}")
    
    return "\n".join(lines)


def construct_pdf_url(arxiv_id: str) -> str:
    """Construct the arXiv PDF URL from an arXiv ID.
    
    Args:
        arxiv_id: The arXiv paper identifier (e.g., "2301.00001" or "2301.00001v1")
        
    Returns:
        The full PDF URL (e.g., "https://arxiv.org/pdf/2301.00001.pdf")
    """
    clean_id = _extract_arxiv_id(arxiv_id)
    return f"https://arxiv.org/pdf/{clean_id}.pdf"


def construct_abstract_url(arxiv_id: str) -> str:
    """Construct the arXiv abstract page URL from an arXiv ID.
    
    Args:
        arxiv_id: The arXiv paper identifier
        
    Returns:
        The full abstract page URL (e.g., "https://arxiv.org/abs/2301.00001")
    """
    clean_id = _extract_arxiv_id(arxiv_id)
    return f"https://arxiv.org/abs/{clean_id}"


def _extract_arxiv_id(arxiv_id: str) -> str:
    """Extract clean arXiv ID from various formats.
    
    Handles formats like:
    - "2301.00001"
    - "2301.00001v1"
    - "arxiv:2301.00001"
    - "http://arxiv.org/abs/2301.00001"
    
    Args:
        arxiv_id: Raw arXiv identifier string
        
    Returns:
        Clean arXiv ID without version suffix
    """
    if not arxiv_id:
        return ""
    
    # Remove common prefixes
    arxiv_id = arxiv_id.strip()
    
    # Handle URL format
    if 'arxiv.org' in arxiv_id:
        # Extract ID from URL
        match = re.search(r'arxiv\.org/(?:abs|pdf)/([^/\s]+)', arxiv_id)
        if match:
            arxiv_id = match.group(1)
    
    # Remove arxiv: prefix
    if arxiv_id.lower().startswith('arxiv:'):
        arxiv_id = arxiv_id[6:]
    
    # Remove .pdf suffix
    if arxiv_id.endswith('.pdf'):
        arxiv_id = arxiv_id[:-4]
    
    # Remove version suffix (e.g., v1, v2)
    arxiv_id = re.sub(r'v\d+$', '', arxiv_id)
    
    return arxiv_id.strip()


def _parse_results(results: Union[str, Dict, List]) -> List[Dict]:
    """Parse results into a list of paper dictionaries.
    
    Args:
        results: Raw results in various formats
        
    Returns:
        List of paper dictionaries
    """
    if isinstance(results, str):
        try:
            results = json.loads(results)
        except json.JSONDecodeError:
            # Not JSON, return empty list
            return []
    
    if isinstance(results, list):
        return results
    
    if isinstance(results, dict):
        # Check for common result wrapper keys
        if 'papers' in results:
            return results['papers']
        if 'results' in results:
            return results['results']
        if 'data' in results:
            data = results['data']
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and 'papers' in data:
                return data['papers']
        # Single paper result
        if 'title' in results or 'arxiv_id' in results:
            return [results]
    
    return []


def _normalize_paper_data(paper: Dict) -> Dict:
    """Normalize paper data to a consistent format.
    
    Args:
        paper: Raw paper dictionary
        
    Returns:
        Normalized paper dictionary
    """
    normalized = {}
    
    # Title
    normalized['title'] = paper.get('title', paper.get('name', ''))
    
    # Authors - handle various formats
    authors = paper.get('authors', paper.get('author', []))
    if isinstance(authors, str):
        # Split comma-separated authors
        authors = [a.strip() for a in authors.split(',')]
    elif isinstance(authors, list):
        # Handle list of dicts with 'name' key
        if authors and isinstance(authors[0], dict):
            authors = [a.get('name', str(a)) for a in authors]
    normalized['authors'] = authors
    
    # Abstract
    normalized['abstract'] = paper.get('abstract', paper.get('summary', ''))
    
    # Categories
    categories = paper.get('categories', paper.get('category', []))
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(',')]
    normalized['categories'] = categories
    
    # Dates
    normalized['published'] = paper.get('published', paper.get('published_date', ''))
    normalized['updated'] = paper.get('updated', paper.get('updated_date', ''))
    
    # IDs
    normalized['arxiv_id'] = paper.get('arxiv_id', paper.get('id', paper.get('entry_id', '')))
    
    return normalized


def _format_single_paper(paper: Dict, index: int = 1) -> str:
    """Format a single paper for display in search results.
    
    Args:
        paper: Paper dictionary
        index: Paper index in results
        
    Returns:
        Formatted paper string
    """
    normalized = _normalize_paper_data(paper)
    
    lines = []
    lines.append(f"---\n**{index}. {normalized.get('title', 'Unknown Title')}**")
    
    # Authors (truncated if too many)
    authors = normalized.get('authors', [])
    if authors:
        if len(authors) > 5:
            authors_str = ', '.join(authors[:5]) + f' et al. ({len(authors)} authors)'
        else:
            authors_str = ', '.join(authors)
        lines.append(f"   Authors: {authors_str}")
    
    # Abstract preview (first 300 chars)
    abstract = normalized.get('abstract', '')
    if abstract:
        preview = abstract[:300]
        if len(abstract) > 300:
            preview += '...'
        lines.append(f"   Abstract: {preview}")
    
    # arXiv ID and PDF URL
    arxiv_id = normalized.get('arxiv_id', '')
    if arxiv_id:
        clean_id = _extract_arxiv_id(arxiv_id)
        pdf_url = construct_pdf_url(clean_id)
        lines.append(f"   arXiv: {clean_id} | PDF: {pdf_url}")
    
    lines.append("")  # Empty line between papers
    
    return "\n".join(lines)


def create_arxiv_paper_from_dict(data: Dict) -> ArxivPaper:
    """Create an ArxivPaper instance from a dictionary.
    
    Args:
        data: Paper data dictionary
        
    Returns:
        ArxivPaper instance
    """
    normalized = _normalize_paper_data(data)
    arxiv_id = _extract_arxiv_id(normalized.get('arxiv_id', ''))
    
    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=normalized.get('title', ''),
        authors=normalized.get('authors', []),
        abstract=normalized.get('abstract', ''),
        categories=normalized.get('categories', []),
        published=normalized.get('published', ''),
        updated=normalized.get('updated'),
        pdf_url=construct_pdf_url(arxiv_id) if arxiv_id else None,
    )
