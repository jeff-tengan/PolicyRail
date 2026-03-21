# Changelog

All notable changes to this project will be documented in this file.

## 0.7.1 - 2026-03-21

- fixed CI workflow to separate test matrix from build job
- fixed `[release]` extra to include `pytest` dependency
- re-release of 0.7.0 due to PyPI upload conflict

## 0.7.0 - 2026-03-21

- hardened `LightweightNLPClassifier` with 8 new signals covering roleplay escaping, semantic evasion, context delimiter injection, backdoor attempts, credential extraction, and rule-forgetting attacks
- added `ContextSanitizer` for neutralizing prompt-injection patterns in untrusted context before prompt envelope construction
- added `AuditEvent` dataclass and `EventEmitter` protocol for pluggable observability sinks (SIEM, OpenTelemetry, webhooks)
- integrated optional `context_sanitizer` and `event_emitters` into `SecureGenAIPipeline`
- enhanced CI workflow with Python 3.10, 3.11, and 3.12 matrix and switched to pytest
- exported `ContextSanitizer`, `SanitizationResult`, `AuditEvent`, `EventEmitter`, and `InMemoryEventEmitter` from the public API

## 0.6.0 - 2026-03-21

- hardened audit logging with recursive redaction and non-blocking failure behavior
- added degraded-mode semantics for remote preflight judges and stricter verdict parsing
- added MCP input-schema validation before tool execution
- expanded MCP client support for roots, server-initiated requests, notifications, and GET-based HTTP streams
- added transport-level tests for stdio and streamable HTTP behavior
- elevated public package metadata and project documentation for community use

## 0.5.0 - 2026-03-20

- added a generic MCP layer for tool discovery, allowlisting, and execution
- added `StdioMCPTransport` and `StreamableHTTPMCPTransport`
- extended the secure pipeline with optional tool execution and structured tool results
- expanded audit logging to record MCP execution metadata
- refreshed bilingual documentation and public package metadata

## 0.4.0 - 2026-03-20

- added multi-provider preflight adapters for OpenAI, Azure OpenAI, Anthropic, Google Gen AI, and Bedrock
- added provider factory selection by environment variables
- improved package exports and documentation for provider-agnostic usage
