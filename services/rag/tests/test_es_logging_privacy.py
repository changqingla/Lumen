"""Logging privacy regressions for canonical Elasticsearch bulk errors."""

import logging
import sys
from importlib.util import find_spec
from types import ModuleType

import pytest

if find_spec("elasticsearch") is None:
    elasticsearch_module = ModuleType("elasticsearch")
    elasticsearch_module.AsyncElasticsearch = object
    sys.modules["elasticsearch"] = elasticsearch_module

from recall_lib.es_connection import (  # noqa: E402
    _log_bulk_failure,
    _summarize_bulk_failure,
    create_es_connection,
    normalize_es_endpoint,
)
from recall_lib._logging import (  # noqa: E402
    log_es_query_shape,
    log_operation_failure,
)


def test_bulk_error_log_omits_document_source_and_reason(caplog):
    private_source_marker = "private-document-body"
    private_reason_marker = "private-elasticsearch-reason"
    item = {
        "index": {
            "_id": "chunk-1",
            "status": 400,
            "error": {
                "type": "mapper_parsing_exception",
                "reason": private_reason_marker,
            },
            "data": {"_source": {"content": private_source_marker}},
        }
    }

    with caplog.at_level(logging.ERROR):
        _log_bulk_failure("Document indexing failed", item)

    assert private_source_marker not in caplog.text
    assert private_reason_marker not in caplog.text
    assert "chunk-1" not in caplog.text
    assert "mapper_parsing_exception" not in caplog.text
    assert "operation=index" in caplog.text
    assert "status=400" in caplog.text


def test_query_debug_log_omits_all_dsl_values(caplog):
    private_query_marker = "private-query-body"
    private_document_marker = "private-document-id"
    private_vector_marker = 987654.321
    query = {
        "query": {
            "bool": {
                "must": [{"query_string": {"query": private_query_marker}}],
                "filter": [{"terms": {"doc_id": [private_document_marker]}}],
            }
        },
        "knn": {"query_vector": [private_vector_marker]},
        "aggs": {"private-field": {"terms": {"field": "private-field"}}},
        "sort": [{"private-field": "asc"}],
    }

    logger = logging.getLogger("test.recall.query-privacy")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        log_es_query_shape(logger, query)

    assert private_query_marker not in caplog.text
    assert private_document_marker not in caplog.text
    assert str(private_vector_marker) not in caplog.text
    assert "private-field" not in caplog.text
    assert "has_knn=True" in caplog.text
    assert "must_clauses=1" in caplog.text
    assert "filter_clauses=1" in caplog.text


def test_bulk_error_result_omits_document_source_identifier_and_reason():
    marker = "private-bulk-marker"
    summary = _summarize_bulk_failure(
        {
            "index": {
                "_id": marker,
                "status": 429,
                "error": {"reason": marker},
                "data": {"_source": {"content": marker}},
            }
        }
    )

    assert summary == {"operation": "index", "status": 429}
    assert marker not in repr(summary)


def test_structural_failure_log_omits_exception_message(caplog):
    marker = "https://user:secret@example.invalid/private-response"
    logger = logging.getLogger("test.recall.failure-privacy")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_operation_failure(logger, "Elasticsearch health check", RuntimeError(marker))

    assert marker not in caplog.text
    assert "secret" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_es_connection_factory_preserves_options_without_mutating_input():
    config = {
        "hosts": "https://es.internal.example:9243",
        "timeout": 17,
        "username": "elastic-user",
        "password": "elastic-secret",
    }

    connection = create_es_connection(config)

    assert connection.hosts == config["hosts"]
    assert connection.kwargs == {
        "timeout": 17,
        "username": "elastic-user",
        "password": "elastic-secret",
    }
    assert config["hosts"] == "https://es.internal.example:9243"


def test_es_connection_factory_has_no_environment_specific_default():
    connection = create_es_connection()

    assert connection.hosts == "http://localhost:9200"
    assert connection.kwargs == {"timeout": 600}


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://elasticsearch.internal:9200",
        "http://user:password@elasticsearch.internal:9200",
        "http://elasticsearch.internal:9200/path",
        "http://elasticsearch.internal:9200?token=private",
        "http://elasticsearch.internal:9200#fragment",
        "http://elasticsearch.internal:99999",
    ],
)
def test_es_endpoint_rejects_credential_and_ambiguous_url_forms(endpoint):
    with pytest.raises(ValueError, match="Elasticsearch endpoint"):
        normalize_es_endpoint(endpoint)


@pytest.mark.asyncio
async def test_es_search_failure_is_not_reported_as_an_empty_result(caplog):
    marker = "private-elasticsearch-response"

    class FailingClient:
        async def search(self, **_kwargs):
            raise RuntimeError(marker)

    connection = create_es_connection({"hosts": "http://elasticsearch:9200"})
    connection.es = FailingClient()
    connection._connected = True

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match=marker):
            await connection.search("index", {"match_all": {}}, size=3)

    assert marker not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_https_es_connection_verifies_certificates_by_default(monkeypatch):
    captured = {}

    class FakeClient:
        cluster = None

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.cluster = self

        async def health(self):
            return {"status": "green"}

    monkeypatch.setattr(
        "recall_lib.es_connection.AsyncElasticsearch",
        FakeClient,
    )
    connection = create_es_connection({"hosts": "https://elasticsearch:9243"})

    await connection.connect()

    assert captured["verify_certs"] is True
