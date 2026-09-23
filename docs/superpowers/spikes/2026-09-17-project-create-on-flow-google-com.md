# Project creation: labs tRPC is retired, flow.google.com creates and renames by rpc

**Date:** 2026-09-17
**Accounts:** `ci-probe`, `denon82`, `flavio.oliva`, `promo-denon82` (`ffroliva` unmeasured — its
profile was held by another browser, and the lease was respected)
**Cost:** $0 — two empty projects created on `ci-probe`, no generation
**Harness:** `scripts/dev/spike_create_project_401.py`, `scripts/dev/spike_migrated_create_project.py`
**Issues:** [#864](https://github.com/ffroliva/gflow-cli/issues/864) (bundle) · #561 · #863 · #639

## Why this spike exists

Generating with no `--project` (CLI) or no `project` (MCP) starts with
`labs.google/fx/api/trpc/project.createProject`. Reports disagreed about it: #561 saw 401 on
a flow.google.com account on v0.66.0 and 200 on v0.67.0 a day later; the 2026-09-15
unentitled-account spike saw 401; #863 saw 401 over MCP. A fix keyed on "this host cannot
create projects" would have been an unmeasured capability claim, so the outcome
distribution came first.

## Finding 1 — labs `createProject` never succeeded, and Google says why

Three attempts per profile through `FlowApiClient.create_project` (the production path):

| profile | labs session token | host served | `createProject` |
|---|---|---|---|
| `ci-probe` | yes | flow.google.com `/` | **404** ×3 |
| `denon82` | yes | flow.google.com `/about` | **404** ×3 |
| `flavio.oliva` | no | flow.google.com `/` | **401** ×3 |
| `promo-denon82` | no | flow.google.com `/about` | **401** ×3 |

Stable per profile, 0 successes in 12. The 404 body is Google's own statement:

> `Flow RPCs have been deprecated and disabled. Flow has migrated to https://flow.google.com.`

The 401 is the same retired route reached by a session that carries no labs API token —
it is refused before the deprecation answer. Neither is an expired login, which is why
`gflow auth login` never helped anyone who followed that advice.

**What this does not show.** Every profile here is served flow.google.com, so nothing
here observes an account served labs.google. The labs arm stays; the fix keys on the
observed refusal, not on the host.

## Finding 2 — flow.google.com creates a project with one rpc, and takes the title

On `flow.google.com/`, the only creation control is the floating action button inside
`flow-projects-page` carrying the `add` ligature (`button.mat-mdc-fab:has(mat-icon)`).
Structural, locale-invariant. Clicking it fired, before navigation:

```
rpcids=jHPbke
f.req   [["jHPbke","[\"projects/*\",[null,[\"<title>\"]],[null,22]]",null,"generic"]]
reply   ["<project uuid>",["<title>"]]
```

and the page then landed on `flow.google.com/project/<uuid>`. The UI sends a timestamp
title (`Sep 17 - 17:02`); the rpc echoes it back with the id.

## Finding 3 — rename is `o8DA4`, through the header's `flow-editable-text`

On the project page, `flow-editable-text` holds one `input`. Filling it and pressing Enter
fired:

```
rpcids=o8DA4
f.req   [["o8DA4","[\"projects/<uuid>\",[\"<title>\"],[[\"project_title\"]],[null,22]]",null,"generic"]]
reply   ["<title>"]
```

After a reload the input carried the new title: the rename persisted.

## What this settles

- No-project generation, `gflow project create` and `gflow project rename` are broken on
  every profile we can measure, and the cause is the retired labs route — not auth.
- flow.google.com offers both operations through controls gflow can anchor structurally,
  and each has an rpc reply that states the outcome (id + title), so success can be read
  from the wire rather than inferred from a URL.

## Not measured

- An account served labs.google (none available).
- `ffroliva` (profile busy), and whether an `/about`-served account (`denon82`) can create
  at all on flow.google.com — its app never mounts, so creation there is expected to fail
  with the existing landing diagnosis.
- Localised UIs: anchors are structural, but no non-`en` profile was driven.

## Addendum — aspect radios on the same host (2026-09-17)

`scripts/dev/spike_migrated_aspect_radios.py --profile ffroliva`, settings pane only, nothing
submitted:

| Mode | Aspect radios (ligature) | Other groups |
|---|---|---|
| Image | `crop_16_9` 16:9 · `crop_landscape` 4:3 · `crop_square` 1:1 · **`crop_portrait` 3:4** · `crop_9_16` 9:16 | count x1–x4 |
| Video | `crop_16_9` 16:9 · `crop_9_16` 9:16 | Frames / Ingredients · 360p / 720p · 4s/6s/8s/10s · count x1–x4 |

The 2026-09-08 enumeration had four image radios, so gflow refused 3:4 there with exit 36;
the fifth (`crop_portrait`) is present now and is driven. Video offers exactly the two
aspects gflow already supports. The video resolution group (360p/720p) is what PR #787's
`--resolution` targets; it is not part of this bundle. `flavio.oliva` (no projects) could
not be enumerated — the spike needs a project to open — and is unmeasured.
