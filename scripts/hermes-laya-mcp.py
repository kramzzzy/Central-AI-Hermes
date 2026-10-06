"""Native Hermes MCP integration, backed by the private Laya container."""
import contextlib
import os
import sys

sys.path.insert(0, os.environ.get('HERMES_REPO', '/opt/hermes'))
with contextlib.redirect_stdout(sys.stderr):
    import hermes_bootstrap
    from mcp.server.mcpserver import MCPServer
from hermes_laya import classify

server = MCPServer('laya')
server.tool(name='classify_request')(classify)
if __name__ == '__main__':
    server.run(transport='stdio')
