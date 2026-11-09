# Roadmap

> Themes, not deadlines. `gflow-cli` drives a product it does not own: a single change
> upstream in Google Flow can rearrange the order of everything below. Each theme names
> its tracking issue; the issue, not this file, holds the current state.

The previous edition of this file described v0.9–v0.11 and is preserved in git history.
Everything it listed has shipped or been superseded; see [CHANGELOG.md](CHANGELOG.md).

## Generation lifecycle & automation

**Tracking: [#1191](https://github.com/ffroliva/gflow-cli/issues/1191)** ·
milestone "Generation lifecycle & automation"

Give gflow the lifecycle that hosted generative-media APIs and mature cloud CLIs share:
**submit → status → wait → download**. Capabilities are reported honestly, errors are
something a pipeline can act on, and a small exit-code core is stable.

- Machine-readable discovery: `gflow capabilities` + MCP `capabilities://self` (#1197),
  `--help --json` command tree (#1203), `models list` / `models show` (#1196).
- Actionable errors: `charged`, `next_action`, `operation_id` on every problem
  document (#1201), and a stable exit-code core with a deprecation cycle (#1202, breaking).
- Lifecycle verbs: `--no-wait`, `status`, `wait` (#1194); `download` and `generations`
  (#1195); `cancel`, local only and explicit that a submitted generation cannot be cancelled
  on the server (#1198); `reconcile` (#1199); `upload` (#1200); `image gen` / `video gen`
  bridge verbs (#1193).
- **Browser to submit, HTTP to observe.** The #1192 spike measured that `flow.google.com`'s
  own read calls (status, listing, balance) answer without a page, and that the signed clip
  URL downloads with no credential (results on #1192). Submitting stays in the
  browser because of reCAPTCHA.

## flow.google.com parity

**Tracking: [#639](https://github.com/ffroliva/gflow-cli/issues/639)**

Google is moving Flow accounts to `flow.google.com`, a rewritten frontend. gflow already
drives text-to-video, image-to-video, reference-to-video, text-to-image, image-to-image,
characters and a single-segment extend there. Still to port: local-file image-to-image on
the agent-only composer (#1190), `--ref <media uuid>` (#933), a 4th+ reference (#997),
custom voices (#826), and honest extend lengths for the Omni model (#1184).

## Reliability against a blackbox

gflow reverse-engineers Flow, so every claim is measured before it is encoded
(`/gflow:spike`). Ongoing work:

- Failures that keep Flow's own reason and say whether credits were spent: #1111, #1159,
  #1160, #1110.
- Visibility into queued and unknown outcomes: #741, #897.
- Drift detection on the surfaces with an outage history: #622, #730, and the nightly canary
  (#559).
- Auth edge cases: #796, #773, #768 (macOS), #480.

## MCP and agents

The CLI and the MCP server are two doors onto one product, and every CLI capability has an
MCP twin or a recorded reason it does not.

- Server-directed tasks and bounded waits: #1105, #481.
- Missing twins: video extend (#884), character create (#691).
- Distribution: Docker MCP Catalog (#822).

## Onboarding and evidence

- Walk install → auth → first command → failure as a stranger would (#601).
- User-facing evidence capture for reproducible bug reports (#707).
- Kubectl-style output flags (#1158, breaking; to be scheduled with #1202's deprecation cycle).

## Out of scope

Google's separately billed Gemini / Vertex AI generation APIs. gflow drives Flow, with the
user's own Google account.

---

*Themes, not deadlines. An independent CLI tracking a private API can have its plans broken
by a single change upstream, so fixed dates would be dishonest.*
