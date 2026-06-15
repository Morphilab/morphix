"""MCP — Model Context Protocol client + server.

Zero external dependencies. Implements JSON-RPC 2.0 over stdio
for tools/list and tools/call methods (MCP spec 2024-11-05).

- client.py: connect to external MCP servers, discover + register their tools
- server.py: expose Morphix tools so other clients (opencode, Claude) can use them
- protocol.py: JSON-RPC message types + framing over asyncio streams
- adapter.py: convert Morphix ToolDefinition <-> MCP tool schema
- config.py: load mcp_servers.json configuration
"""
