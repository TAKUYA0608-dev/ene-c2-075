# ENE-C2-075 — Electricity-Retail Customer Switching Exception Evidence Agent

> **Category**: Cat 2 (domain workflow (a job to be done))
> **Industry**: Energy

## Overview

Triages stalled electricity-retail supplier-switching cases. Given a JSON payload with a batch scope and case records (contract, consent and meter-binding status, identity match, missing fields, source system), the agent reconciles contract, meter-point and consent data, classifies each case into a six-category taxonomy (consent, identity, meter-point, contract conflict, data gap, unclassified), retrieves the cited evidence-checklist and recovery-policy clauses and returns exception_briefs with missing evidence, non-decisional recovery suggestions and a priority queue, plus an exception_summary, citations and a draft disclaimer. Everything is deterministic; no LLM is used and free-text remarks are never interpreted. Customer identity fields are dropped and identifiers replaced by opaque tokens, a case whose source is not an authorised system of record loses its citation and causes the brief to be withheld for a person to check, and every brief is marked pending human approval — the agent never executes a switch. The taxonomy and policy clauses shipped here are a small seeded sample.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | 3.11 or later (`requires-python = ">=3.11"`) |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
