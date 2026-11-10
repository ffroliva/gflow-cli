# Chrome close waits 30 s on CloudSyncedFolderChecker, and Chrome keeps only the last `--disable-features` (#1140)

**Date:** 2026-11-10 · **Machine:** the Windows workstation behind canary #559 and #1233
**Script:** [`scripts/dev/spike_disable_features_merge.py`](../../../scripts/dev/spike_disable_features_merge.py)
**Method:** bare Playwright 1.61.1, real Chrome (`channel="chrome"`), one throwaway profile
per arm. Each arm loads `http://example.com/`, reads `chrome://version`, stays open 3 s
(a window closed within about 2 s does not show the hang), then times `ctx.close()`.
**Cost:** $0. No Flow account is involved.

## Results (second run; the first agreed within 0.1 s on every hang)

| Arm | Switches passed by gflow | Final URL | `ctx.close()` |
|---|---|---|---|
| none | — | `http://` | **32.1 s** |
| lone (positive control) | `--disable-features=CloudSyncedFolderChecker` | **`https://`** | 4.7 s |
| merged | one switch: Playwright's list + `CloudSyncedFolderChecker` | `http://` | 4.3 s |
| headless | — (`headless=True`) | `http://` | **31.3 s** |
| headless + merged | merged | `http://` | 0.4 s |
| login (`login_launch_kwargs`) | merged | `http://`, `navigator.webdriver=false` | 0.4 s |

## Findings

1. **The hang is the feature.** Disabling `CloudSyncedFolderChecker` alone takes the close
   from about 31 s to under 5 s. This matches #1140's symbolized dump: shutdown waits on that
   feature's startup task, a Windows cloud-files COM call that never answers here.
2. **Chrome honours only the last `--disable-features`.** Playwright passes its own list,
   which includes `HttpsUpgrades`. A second, lone switch dropped that list: `http://` was
   upgraded to `https://`. The merged switch kept it. `chrome://version` shows both
   switches on the command line, and only the last one takes effect.
3. **Headless real Chrome hangs too.** #1140 expected headless to be unaffected. Measured,
   it is not, so every Playwright launch site gets the merged switch, not only headed ones.

## What this does not show

- Whether other machines hang. It depends on the Windows cloud-files provider.
  `CloudSyncedFolderChecker` reports metrics on OneDrive-synced folders, so it is suspected,
  not shown here.
- The close of the bare-subprocess passive login (`auth/real_chrome.py`). The user closes
  that window, so this probe cannot time it. It gets a lone switch, because no Playwright
  list exists there to drop.
