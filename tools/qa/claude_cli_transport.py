"""TEST ONLY: the personal-subscription transport now lives in the product, for local single-user mode.

Kept so that `tools/qa/chat_server.py --claude-cli` keeps working; see
planning_factory/adapters/claude_subscription.py.
"""
from planning_factory.adapters.claude_subscription import claude_cli_transport  # noqa: F401
