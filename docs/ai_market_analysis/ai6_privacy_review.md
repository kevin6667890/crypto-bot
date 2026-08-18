# AI-6 Privacy Review

Status: **APPROVED_FOR_NONE_AND_PAPER_INTERNAL_SHADOW** by the project owner. This is explicitly not approval for USER_DECLARED.

Approved data classes are public market facts, deterministic derivations, audited AI synthesis, frozen macro evidence, operational metadata, NONE and PAPER simulated positions. USER_DECLARED, real cost/quantity/stop/targets and real-position screenshots remain prohibited. Presentation and explicit position-detail reads use the admin bearer boundary. Tokens are held in memory, never URL/localStorage, console or analytics. Initial Presentation omits quantity, average cost, stop and targets. Provider text is rendered as text; macro URLs accept only HTTP(S) and use `noopener noreferrer`.

The API must not return full Context/Registry, unrelated Paper history, plan history, raw provider response, prompts, secrets, paths or environment variables. Errors contain stable sanitized codes. Fixtures and screenshots must be synthetic.

The B0 candidate uses an owner-only TLS endpoint bound to `127.0.0.1:8443` through an owner SSH tunnel. Nginx logs method plus `$uri`, never query strings or Authorization. CSP, frame denial, referrer policy, `nosniff` and `no-store` are enforced. Cross-instrument/mode tests fail closed. Hot retention is 30 days; days 31–365 use verified content-addressed request-closure archives whose indefinitely retained identity manifests preserve request/context/registry/report/audit IDs and hashes. Raw provider responses, prompts, Authorization and secrets are never logged or archived.

`AI_USER_POSITION_PLANS_ENABLED=false` remains mandatory. USER_DECLARED is **NOT_APPROVED**.
