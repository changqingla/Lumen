"""JSON parsing utilities with error handling."""
import json
import re
from typing import Any, Dict, Optional

from .logger import get_logger

logger = get_logger(__name__)


def extract_json_from_text(text: str) -> Optional[str]:
    """
    Extract JSON content from text that may contain markdown code blocks.
    
    Args:
        text: Text that may contain JSON
        
    Returns:
        Extracted JSON string or None
    """
    # Try to find JSON in markdown code blocks
    json_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    matches = re.findall(json_pattern, text, re.DOTALL)
    
    if matches:
        return matches[0].strip()
    
    # Try to find JSON object directly - use non-greedy matching first
    # Look for complete JSON objects (balanced braces)
    try:
        # Find the first { and try to match balanced braces
        start = text.find('{')
        if start != -1:
            brace_count = 0
            in_string = False
            escape_next = False
            
            for i in range(start, len(text)):
                char = text[i]
                
                if escape_next:
                    escape_next = False
                    continue
                
                if char == '\\':
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # Found complete JSON
                            return text[start:i+1]
            
            # If we didn't find balanced braces, return what we have
            return text[start:]
    except Exception:
        pass
    
    # Fallback: try greedy pattern
    json_obj_pattern = r'\{.*\}'
    matches = re.findall(json_obj_pattern, text, re.DOTALL)
    
    if matches:
        # Return the longest match (likely the most complete JSON)
        return max(matches, key=len)
    
    return None


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    """
    Safely parse JSON with multiple fallback strategies.
    
    Args:
        text: Text to parse as JSON
        
    Returns:
        Parsed JSON dict or None if parsing fails
    """
    if not text:
        return None
    
    # First attempt: direct parsing
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Second attempt: extract from markdown/text
    extracted = extract_json_from_text(text)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as e:
            # Try to fix common issues
            # 1. Incomplete string at the end
            if "Unterminated string" in str(e) or "Expecting" in str(e):
                # Try to close the JSON by adding missing closing braces/quotes
                try:
                    # Count open braces
                    open_braces = extracted.count('{') - extracted.count('}')
                    fixed = extracted + ('}' * open_braces)
                    return json.loads(fixed)
                except:
                    pass
    
    # Third attempt: strip whitespace and try again
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    
    # Fourth attempt: try to extract and fix incomplete JSON
    try:
        extracted = extract_json_from_text(text)
        if extracted:
            # Try to fix by adding closing braces
            open_braces = extracted.count('{') - extracted.count('}')
            if open_braces > 0:
                fixed = extracted.rstrip(',').rstrip() + ('}' * open_braces)
                try:
                    return json.loads(fixed)
                except:
                    pass
    except:
        pass
    
    logger.warning(f"Failed to parse JSON from text: {text[:200]}...")
    return None


def parse_json_response(
    response: str,
    expected_fields: Optional[list] = None
) -> Optional[Dict[str, Any]]:
    """
    Parse JSON response and validate expected fields.
    
    Args:
        response: Response text to parse
        expected_fields: Optional list of required field names
        
    Returns:
        Parsed and validated JSON dict or None
    """
    parsed = safe_json_loads(response)
    
    if parsed is None:
        return None
    
    # Validate expected fields if provided
    if expected_fields:
        missing_fields = [field for field in expected_fields if field not in parsed]
        if missing_fields:
            logger.warning(f"Missing expected fields in JSON: {missing_fields}")
            return None
    
    return parsed

