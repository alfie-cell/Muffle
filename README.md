# Muffle

A minimalist, native Safari ad blocker for macOS. Two Safari extensions inside one small host app:

| Part | Kind | What it does | Cost |
|---|---|---|---|
| **Blocker** | Safari Content Blocker | Blocks ad and tracker requests from EasyList + EasyPrivacy, plus domain-specific element hiding. | Rules are compiled by WebKit into bytecode. No JavaScript runs in pages. |
| **YouTube** | Safari Web Extension | Removes YouTube video ads (pre-roll, mid-roll, Shorts) by stripping ad metadata out of the player's data. | One small content script, only on youtube.com. No background process. |
| **Muffle.app** | SwiftUI host | Shows whether both extensions are on, opens Safari's extension settings, reloads rules. | Quit it after enabling. Safari runs the extensions on its own. |

Generic cosmetic rules (the `##.ad` kind with no domain) are deliberately left out. Safari would inject them into every page, which is the main performance cost of EasyList on Safari. Network blocking catches the requests anyway.

## Requirements

- macOS 12 Monterey or later, Safari 16.4 or later (Safari 17 recommended).
- **Xcode 14.2** on Monterey (the last version that runs there), or any newer Xcode on Ventura and up. Download from https://developer.apple.com/download/all (Apple ID needed, ~7 GB). Not on the Mac App Store for Monterey.
- **An Apple Development certificate.** Safari refuses extensions without an Apple-issued certificate chain: ad-hoc ("Sign to Run Locally") builds launch, but the system's integrity daemon logs `signature not valid: -67050` and Safari's rules loader times out after 120 s without ever calling the extension. A free Apple ID is enough: Xcode > Settings > Accounts > + > sign in, then Manage Certificates > + > Apple Development. If `security find-identity -v -p codesigning` still shows 0 identities afterwards, the keychain is missing Apple's WWDR G3 intermediate; import it with `curl -O https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer && security import AppleWWDRCAG3.cer -k ~/Library/Keychains/login.keychain-db`.
- Python 3 (ships with macOS Command Line Tools). No other dependencies.

## Build

```sh
# 0. One time, after installing Xcode:
sudo xcode-select -s /Applications/Xcode.app
sudo xcodebuild -runFirstLaunch

# 1. Generate the content blocker rules (downloads EasyList + EasyPrivacy, ~1 min)
make rules                   # lean profile (default); or PROFILE=minimal / PROFILE=full

# 2. Build, install to /Applications and open. The Makefile finds your Apple Development
#    certificate on its own; pass TEAM=ABCDE12345 to force a team.
make install
make status                  # is each extension enabled in Safari?
make reload                  # ask Safari to reload the rules (after make rules + make install)
```

Always install with `make install` rather than copying the app by hand: it unregisters the old copy first. Replacing the app in place leaves a stale record in the plugin registry that outranks the new copy, and Safari then reports "the extension is no longer valid".

### Rule profiles

| Profile | Rules | JSON | Compiled by WebKit | Compile time (2015 iMac) | What's in it |
|---|---|---|---|---|---|
| `minimal` | ~17k | 2 MB | 9 MB | 2 s | EasyList general + third-party rules, EasyPrivacy core, Peter Lowe's curated ad-server list |
| `lean` (default) | ~70k | 8 MB | 26 MB | 5 s | All EasyList/EasyPrivacy network blocking; no per-site cosmetics, no CNAME-cloaking long tail, no international-only sections |
| `full` | ~120k | 14 MB | 45 MB | 8 s | Everything the converter can express, including per-site cosmetics |

The compiled size is what every Safari process maps into memory, so it is the number that matters for lightness. Matching cost per request is roughly independent of rule count (WebKit compiles the rules to a state machine), so a smaller profile mostly saves memory and reload time. If ads slip through on `minimal`, move up to `lean`.

Before changing the bundle identifier, note that all three targets derive theirs from the `MUFFLE_BUNDLE_PREFIX` build setting (default `com.example`). Change it once in the project's build settings.

## Enable in Safari

1. Open Muffle.app once. It shows both extensions as off.
2. Safari > Settings > Extensions. Tick **Muffle** and **Muffle for YouTube**.
3. If the extensions are missing from the list: Safari > Settings > Advanced > tick "Show features for web developers", then Develop > **Allow Unsigned Extensions**. This resets every time Safari quits, unless the app is signed with a Developer ID.
4. Open youtube.com. Safari asks whether Muffle for YouTube may read the page. Choose **Always Allow on youtube.com**.
5. Back in Muffle.app both rows turn green. Quit the app.

## Verify

- Ads and trackers: open an ad-heavy news site with Web Inspector > Network. Requests to doubleclick.net, googlesyndication.com and similar are marked as blocked by a content blocker.
- Rules compiled: press **Reload Rules** in the app. "Rules reloaded." means Safari accepted the list. On an error, open Console.app, filter on `Muffle`, and look for the rule index Safari rejected; add that pattern to the skip logic in `tools/build_rules.py`.
- YouTube: play a video that normally shows a pre-roll ad. Expect no pre-roll, no mid-roll, no "Ad" badge and no yellow markers on the progress bar. Scroll Shorts: no sponsored shorts. No "Ad blockers violate YouTube's Terms of Service" dialog after several videos.
- Main-world injection: in Web Inspector's console on youtube.com, `Object.getOwnPropertyDescriptor(window, "ytInitialPlayerResponse").set` is a function.
- Resource use: in Activity Monitor compare "Safari Web Content (youtube.com)" memory and CPU with the YouTube extension on and off. The "Muffle for YouTube" process should sit at 0% CPU when idle.
- No leakage: Web Inspector > Sources on any non-YouTube site shows no Muffle scripts.

## Keeping it updated

```sh
make update      # refresh lists, convert, hand the new rules to Safari. No rebuild, ~1 min.
make schedule    # do that every Monday at 10:00 via launchd (make unschedule to remove)
tail ~/Library/Logs/Muffle-update.log
```

`make update` writes the converted rules into the extension's own data folder (`~/Library/Containers/<bundle id>.blocker/Data/Library/Application Support/Muffle/`). The extension prefers that file over the bundled one, after parsing it to make sure a bad download can never take the blocker down. Rebuilding the app (`make rules && make install`) refreshes the bundled copy; whichever is newer wins.

### YouTube data is derived from uBlock Origin's list

`yt-main.js` removes ads by deleting known field names (`adPlacements`, `adSlots`, `playerAds`) from YouTube's player data and scrubbing them out of responses from known endpoints. Those names come from uBlock Origin's list and change a few times a year, so the weekly job keeps them current without a rebuild:

1. `tools/build_yt_config.py` reads the live uBlock list, picks the YouTube rules, and turns the ones it understands (`set`, `trusted-replace-*-response`, `json-prune`, `$replace=` rules) into `yt-config.json`: a list of field names and URL substrings, nothing executable. Plain network and cosmetic YouTube rules go to `tools/ubo-youtube-rules.txt` for the content blocker.
2. The file lands in the YouTube extension's container (`~/Library/Containers/<bundle id>.youtube/Data/Library/Application Support/Muffle/`).
3. At Safari launch the extension's background script asks the native handler for it (`sendNativeMessage`), validates it, and keeps it in extension storage. An open Safari re-asks once a day.
4. On each YouTube page `yt-guard.js` passes it to `yt-main.js` through a DOM attribute; the script merges it on top of its built-in defaults (union only, capped at 20 entries each).

Inspect what the page is using: Web Inspector console on youtube.com, `document.documentElement.getAttribute("data-muffle-config")`. Parser tests: `make test`.

The job posts a macOS notification only when uBlock adds a YouTube rule the parser does not understand (a new technique). `tools/ubo-youtube-snapshot.txt` is the baseline for that comparison; `python3 tools/build_yt_config.py --write-snapshot` refreshes it once the script has been reviewed.

Add personal rules in Adblock Plus syntax to `tools/extra_rules.txt`. Extra lists go in `tools/sources.txt`. The converter prints how many rules each source produced and why the rest were skipped (`--stats` for the full breakdown).

The Apple Development certificate used for signing expires after a year. Installed builds keep working; the next `make install` after expiry just needs Xcode (with the Apple ID still signed in) to issue a new one, which it does on its own.

## How the YouTube part works

YouTube serves video ads from the same servers as the video itself, so no network rule can block them. Instead the player is told there are no ads. `YouTube/Resources/yt-main.js` runs in the page's own JavaScript world before any YouTube code and:

1. wraps `window.ytInitialPlayerResponse` so `adPlacements`, `playerAds` and `adSlots` are deleted on assignment;
2. wraps `fetch()` for `/youtubei/v1/player` and friends and rewrites those keys in the response body;
3. does the same for `XMLHttpRequest`;
4. wraps `JSON.parse` to prune the same keys, and the Shorts `isAd` flag.

This is the approach used by the uBlock Origin filter list; the relevant lines are kept in `tools/ubo-youtube-snapshot.txt` so they can be diffed against the live list when YouTube changes something. `yt-guard.js` is a safety net that mutes and skips an ad if one still starts, and injects `yt-main.js` itself if Safari did not run it as a main-world script. `yt.css` hides ad slots that need `:has()`.

## Layout

```
Muffle/           SwiftUI host app
Blocker/          Content blocker extension; blockerList.json and rules-meta.json are generated
YouTube/          Web extension; Resources/ holds manifest.json and the scripts
tools/            build_rules.py (converter), build_yt_config.py (uBlock → YouTube data), update.sh,
                  sources.txt, extra_rules.txt, ubo-youtube-snapshot.txt, test_yt_config.py
Makefile          make rules | install | status | reload | update | schedule | test | clean
```

## Gotchas found the hard way

- A macOS app extension written in Swift must link **AppKit** (the template links Cocoa). With only Foundation linked, PlugInKit logs "misconfigured plugin; external subsystem not present; possible missing linkage", the extension process starts but never answers the host, and Safari's loader gives up after 120 s with `SFErrorDomain Code=3` / `NSCocoaErrorDomain Code=4097`. Both extension targets carry `-framework AppKit` in `OTHER_LDFLAGS` and `import AppKit` for this reason.
- Ad-hoc signing does not work for Safari extensions. See Requirements.
- Watch what Safari is doing with: `log stream --predicate 'subsystem == "com.example.muffle" OR (process == "Safari" AND eventMessage CONTAINS[c] "content blocker")'`. Use `/usr/bin/log` in zsh, which has a builtin named `log`.

## If the project file will not open

`Muffle.xcodeproj` was written by hand. If Xcode refuses it, recreate it with the templates and drop the same files in:

1. File > New > Project > macOS > App: product name `Muffle`, SwiftUI, Swift. Replace the generated Swift files with `Muffle/*.swift`. Set the Info.plist file and entitlements to the ones in `Muffle/`.
2. File > New > Target > macOS > Content Blocker Extension: name `Blocker`. Replace its handler and `blockerList.json` with the files in `Blocker/`, add `rules-meta.json` to its resources.
3. File > New > Target > macOS > Safari Extension: name `YouTube`, type Safari Web Extension, Manifest v3. Delete the template's popup, options, images and `_locales`; copy the five files from `YouTube/Resources/` into the target's Resources folder.
4. Bundle identifiers must be `<prefix>.muffle`, `<prefix>.muffle.blocker` and `<prefix>.muffle.youtube`; the app derives the extension identifiers from its own.
5. Deployment target macOS 12.0 for all three targets.

## Known limits

- YouTube changes its player data now and then. When ads reappear, diff `tools/ubo-youtube-snapshot.txt` against the live uBlock list and update `yt-main.js`.
- Server-side ad insertion, which YouTube has tested, cannot be removed by pruning; the skipper in `yt-guard.js` is the only defence against it.
- Cosmetic exceptions (`#@#`) and extended selectors (`:has-text`, `:-abp-has`) are not converted. Safari content blockers cannot express them.
