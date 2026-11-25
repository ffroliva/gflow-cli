# Clip editor references: `@` opens the asset browser (2026-11-25)

## Question

`gflow video edit MEDIA_ID --ref IMAGE` uploads the reference image into the project, opens
the clip editor (`/project/<pid>/edit/<media>`) and attaches the image through `@`. In the
live end-to-end run of #1467 the attach found no option for the uploaded image within the
90-second ingest budget (the timeout listed nothing offered), while the main composer's `@`
listed an uploaded clip. Does the clip editor take image references, and through which
widget?

## Method

In-process probe, `scripts/dev/spike_1467_clip_editor_refs.py`, through `FlowApiClient` and
the production clip-editor helpers (`_source_clip`, `_anchor`). Cost: none. A route aborted
every billed submit RPC (`jIps6`, `fZytfe`, `ogiZ0b` and the composer's video submits) for the
whole run and recorded none (`submits_aborted: []`). One run on one profile served
`flow.google.com`, against a finished clip and a reference image uploaded by the preceding
end-to-end run. The probe recorded the controls around the prompt box, the overlay before
and after typing `@` alone and `@` plus the image name's first six characters, the
add-media controls, and every `batchexecute` rpcid. The capture stays in
`scripts/dev/_spike_out/` (not committed).

## Observations

- The prompt box is `flow-edit-video-prompt-box`, inside `flow-scene-builder`.
- Typing `@` there does not open the composer's mention picker. It opens the asset browser,
  `flow-add-menu-popover-content`:
  - a side navigation `flow-add-menu-side-nav` with a project `mat-select` and category tabs
    by ligature (`dashboard`, `image`, `voice_selection`, `accessibility_new`,
    `drive_folder_upload`), an upload button, a search icon and a sort `mat-select`;
  - the list `flow-add-menu-asset-list cdk-virtual-scroll-viewport[role=listbox]`, whose items
    are `button[role=option].asset-item` (one carries `.asset-item-active`), each holding an
    `img`, with the visible text `<name>Image` (for example `ref-d56d9d65.pngImage`);
  - a detail pane `flow-add-menu-detail-pane` with `button.detail-add-to-prompt-btn`.
- `@` alone listed the project's images, the uploaded reference among them. `@` followed by
  the first six characters of its name filtered the list to that one item.
- The prompt box also holds `flow-add-menu` > `button[aria-haspopup=true]` with the `add`
  ligature. Clicking it timed out in the probe; what it opens was not recorded.
- 19 distinct `batchexecute` rpcids were requested, among them the clip editor's own project
  read `Zzl0ze` and the status poll `as29s`; none was a submit, and the change below names no
  new one. The full list is in the capture.

## Verdict

The clip editor takes image references through the asset browser that its `@` opens, not
through the composer's mention picker. The uploaded image was listed, so the upload and the
ingest wait were not at fault: the attach looked for options in the wrong widget, and typed
the full name with its extension, which the browser's filter is not shown to accept. The
pre-registered reading "images are listed under `@`" applies.

The clip-editor path now types `@` plus the name without its extension, waits within the
ingest budget for exactly one `flow-add-menu-asset-list button[role=option].asset-item`
naming the upload (the name, optionally followed by a media-type label), clicks it, clicks
`flow-add-menu-detail-pane button.detail-add-to-prompt-btn` once it is enabled, and verifies
the chip in the prompt box. Zero matches keep waiting; two are refused.

## Not measured

- Other account locales: the anchors are tags, classes and roles, and the matched text is the
  run-unique file name, but only one locale was observed.
- What the `add` button in `flow-add-menu` opens.
- Whether the detail pane's add button is ever disabled for a freshly processed image.
- Whether the browser's filter accepts the full name with its extension.
- A second run, a second profile, and the main composer's `@` (not probed here).
