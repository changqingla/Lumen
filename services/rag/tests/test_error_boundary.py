"""Privacy and stability regressions for the RAG error boundary."""

import logging

from error_boundary import log_rag_failure, public_error_message


def test_failure_log_and_public_message_omit_remote_details(caplog):
    private_exception_marker = "private-provider-body"
    private_task_marker = "private-task-identifier"
    logger = logging.getLogger("test.rag.error-boundary")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_rag_failure(
            logger,
            stage="embedding",
            error=RuntimeError(private_exception_marker),
            task_id=private_task_marker,
        )

    assert private_exception_marker not in caplog.text
    assert private_task_marker not in caplog.text
    assert "stage=embedding" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert public_error_message("embedding") == "文档向量化失败"
