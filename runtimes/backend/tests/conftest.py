"""

Sets up sys.path and pre-mocks modules that would cause circular import
issues when unit-testing lightweight config/registry code in isolation.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make 'src' importable from any working directory
sys.path.insert(0, str(Path(__file__).parent.parent))

_explicit_live_client_run = any(
    Path(arg).name == "test_client_live.py"
    or Path(arg).as_posix().endswith("tests/test_client_live.py")
    for arg in sys.argv[1:]
)

# Keep ordinary runtime unit tests isolated from local runtime config and
# credentials. Live client tests are explicit-only, so a full suite run skips
# them even when a local config file exists.
if not _explicit_live_client_run:

    runtime_test_config_dir = Path(tempfile.mkdtemp(prefix="lumen-runtime-tests-"))
    runtime_test_config = runtime_test_config_dir / "config.yaml"
    runtime_test_config.write_text(
        """
models:
  - name: test-model
    use: langchain_openai:ChatOpenAI
    model: test-model
sandbox:
  use: src.sandbox.local:LocalSandboxProvider
tool_groups: []
tools: []
checkpointer:
  type: memory
""".strip(),
        encoding="utf-8",
    )
    os.environ["LUMEN_CONFIG_PATH"] = str(runtime_test_config)
    os.environ["LUMEN_RUNTIME_SKIP_LIVE_TESTS"] = "true"
else:
    os.environ.pop("LUMEN_RUNTIME_SKIP_LIVE_TESTS", None)


@pytest.fixture(autouse=True)
def _reset_runtime_global_state():
    from src.agents.checkpointer import reset_checkpointer
    from src.config import app_config as app_config_module
    from src.config.checkpointer_config import set_checkpointer_config

    app_config_module._app_config = None
    set_checkpointer_config(None)
    reset_checkpointer()
    yield
    app_config_module._app_config = None
    set_checkpointer_config(None)
    reset_checkpointer()

# Break the circular import chain that exists in production code:
#   src.subagents.__init__
#     -> .executor (SubagentExecutor, SubagentResult)
#       -> src.agents.thread_state
#         -> src.agents.__init__
#           -> lead_agent.agent
#             -> subagent_limit_middleware
#               -> src.subagents.executor  <-- circular!
#
# By injecting a mock for src.subagents.executor *before* any test module
# triggers the import, __init__.py's "from .executor import ..." succeeds
# immediately without running the real executor module.
_executor_mock = MagicMock()
_executor_mock.SubagentExecutor = MagicMock
_executor_mock.SubagentResult = MagicMock
_executor_mock.SubagentStatus = MagicMock
_executor_mock.MAX_CONCURRENT_SUBAGENTS = 3
_executor_mock.get_background_task_result = MagicMock()

sys.modules["src.subagents.executor"] = _executor_mock
