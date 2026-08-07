# AI-6 Privacy Review

Status: **REQUIRES_PRIVACY_REVIEW**.

Data classes are public market facts, deterministic derivations, AI synthesis, frozen macro links, operational metadata, Paper simulated positions and USER_DECLARED positions. The latter two are sensitive. Presentation reads and explicit position detail reads use the existing admin bearer boundary. Tokens are held in memory, never URL/localStorage, console or analytics. Initial Presentation omits quantity, average cost, stop and targets. Provider text is rendered as text; macro URLs accept only HTTP(S) and use `noopener noreferrer`.

The API must not return full Context/Registry, unrelated Paper history, plan history, raw provider response, prompts, secrets, paths or environment variables. Errors contain stable sanitized codes. Fixtures and screenshots must be synthetic.

Open review items: confirm admin roles are user-scoped for shared environments; approve sensitive-field inventory and retention; verify reverse-proxy logs redact Authorization and query strings; verify CSP and analytics exclusions; penetration-test cross-instrument/mode access; approve screenshot policy; confirm incident deletion/retention obligations without violating immutable audit requirements. Until signed, USER_DECLARED canary is **NOT_READY**.
