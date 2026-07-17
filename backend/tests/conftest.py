"""Backend test-process environment isolation."""

import os


# Local deployment files must not change test collection or execution order.
os.environ["DEBUG"] = "false"
