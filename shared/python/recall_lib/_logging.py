"""Privacy-safe structural logging helpers for retrieval internals."""

from __future__ import annotations

import logging
from typing import Any


def _list_length(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def log_es_query_shape(logger: logging.Logger, query: dict[str, Any]) -> None:
    """Record query structure without serializing text, vectors, IDs, or fields."""
    query_clause = query.get("query")
    bool_clause = query_clause.get("bool") if isinstance(query_clause, dict) else None
    bool_clause = bool_clause if isinstance(bool_clause, dict) else {}
    aggregations = query.get("aggs")
    logger.debug(
        "Built Elasticsearch query: has_knn=%s must_clauses=%d "
        "filter_clauses=%d aggregations=%d sort_clauses=%d",
        isinstance(query.get("knn"), dict),
        _list_length(bool_clause.get("must")),
        _list_length(bool_clause.get("filter")),
        len(aggregations) if isinstance(aggregations, dict) else 0,
        _list_length(query.get("sort")),
    )


def log_operation_failure(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    *,
    level: int = logging.ERROR,
) -> None:
    """Log a fixed operation name and exception type without exception-derived text."""
    logger.log(
        level,
        "%s failed: error_type=%s",
        operation,
        type(error).__name__,
    )
