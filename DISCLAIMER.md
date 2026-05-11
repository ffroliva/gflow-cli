# Disclaimer

## Not affiliated with Google

`gflow-cli` is an **independent, unofficial** project. It is **not affiliated with, endorsed by, sponsored by, or otherwise connected to Google LLC, Alphabet Inc., DeepMind, or any of their subsidiaries.** All trademarks (Google, Flow, Veo, Gemini, Imagen, AI Ultra, AI Pro) are the property of their respective owners. The use of these names here is purely descriptive — to identify the service this tool integrates with — and does not imply endorsement.

## Reverse-engineered surface

This tool calls Google's private REST API at `aisandbox-pa.googleapis.com`. That surface is:

- **Undocumented.** Google does not publish a contract for it.
- **Unstable.** Endpoints, request shapes, and response shapes may change without notice. A version of `gflow-cli` that worked yesterday may break today.
- **Subject to access controls.** Google may rate-limit, throttle, restrict, or revoke access to this surface for any account at any time.
- **Not covered by any Google SLA.** When this surface goes down, you have no support recourse.

If you need a stable, supported, contractual API, use the [official Google Gen AI SDK](https://github.com/googleapis/python-genai) and the public Veo API on `generativelanguage.googleapis.com`. `gflow-cli` may support that path as a `GFLOW_CLI_PROVIDER=official` option in a future release (planned v0.5+); it is **not** part of v0.4.0a2.

## Account responsibility

To use `gflow-cli` you must:

1. **Own a valid Google account** with active access to [Google Flow](https://labs.google/fx/tools/flow) — typically an [AI Ultra or AI Pro](https://gemini.google/subscriptions/) subscription as of late 2025.
2. **Comply with Google's terms of service** for that account, including:
   - [Google Terms of Service](https://policies.google.com/terms)
   - [Generative AI Additional Terms](https://policies.google.com/terms/generative-ai)
   - Any subscription-specific terms (Ultra, Pro, etc.)
   - The [Google Labs Additional Terms](https://labs.google/terms) governing Flow

3. **Bear all costs.** Every generation made through `gflow-cli` consumes credits from your Google account, exactly as if you had clicked "Generate" in the web UI. Neither the maintainer nor any contributor will be liable for unexpected billing.

4. **Use only your own credentials.** Sharing accounts, automating mass-account creation, or otherwise circumventing Google's per-account quotas is prohibited by Google's terms and is **not a supported use case for `gflow-cli`**.

## Prohibited uses

The maintainer asks that you do **not** use `gflow-cli` to:

- Generate content that violates the [Generative AI Prohibited Use Policy](https://policies.google.com/terms/generative-ai/use-policy) (CSAM, harassment, IP infringement, etc.).
- Circumvent Google's billing, quota, or rate limits via account-rotation farms.
- Automate Flow access for users who do not have their own Ultra/Pro entitlement.
- Re-sell access to Veo via this tool as a "wrapped" commercial service.

These uses can get **your** Google account banned, can put the maintainer in a difficult position with Google, and are antithetical to why this project exists (which is to let legitimate Ultra/Pro subscribers use credits they already paid for, more efficiently).

## Takedown policy

If Google formally requests that `gflow-cli` cease or restrict any reverse-engineered surface, the maintainer will:

1. Acknowledge the request publicly within 7 days.
2. Comply by archiving / removing the affected code path.
3. Update the README and CHANGELOG to document the change.

If you are at Google and reading this: please open an issue at <https://github.com/ffroliva/gflow-cli/issues> or email `ffroliva@gmail.com`.

## No warranty

`gflow-cli` is provided **AS IS**, without warranty of any kind, express or implied. The maintainer:

- Makes no guarantee that any version will work, today or tomorrow.
- Makes no guarantee about output quality, completeness, or fitness for any particular use.
- Will not be liable for lost credits, banned accounts, lost time, or any other damages arising from use of this tool.

See [LICENSE](LICENSE) for the full legal text. By installing or using `gflow-cli`, you acknowledge that you have read and accept this disclaimer.

## Reporting issues

- **Security issues** (auth handling, secret leakage): please email `ffroliva@gmail.com` privately rather than opening a public issue.
- **Functional bugs** (something broke after a Flow update): open an issue at <https://github.com/ffroliva/gflow-cli/issues> with the error output and your Python/OS versions.
- **Legal / takedown / DMCA**: email `ffroliva@gmail.com`.

---

_Last updated: 2026-05-11 — refreshed for v0.4.0a2._
