# Fixture App

A small chat server with a workspace indexer. The websocket server accepts
connections from the desktop client and streams assistant replies.

## Layout

- `src/server` transport layers (HTTP and WebSocket)
- `src/index` workspace indexing
- `src/chat` conversation sessions
