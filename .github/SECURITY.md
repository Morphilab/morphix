# Security Policy

## Reporting a Vulnerability

Report security vulnerabilities to lab@morphilab.com.
Do NOT open public issues for security bugs.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅        |

## API Keys

Morphix never logs or stores API keys. All keys are loaded from `.env` via pydantic-settings at startup and held in memory only. The `.env` file is gitignored and must never be committed.
