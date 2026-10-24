# Legendary Dungeon desktop preview

## For players

1. Run `Legendary-Dungeon-Companion-Setup-<version>.exe`, or the portable `.exe`, which needs no installation.
   Nothing else is required: Python, the simulator and the adviser are included.
2. Sign in inside the game view as usual and open the Legendary Dungeon.
3. The **adviser** shows its recommendation at the top of the game: which door, Fight or Flee, which stone or
   blessing to take. Door choices also get an arrow. You can always click something else; the adviser follows.
4. **Autoplay** (header button) clicks the recommendation for you. It first needs to see each button once:
   when it says *"Autoplay needs to learn …"*, click that button yourself and it continues. It stops when
   - the game sends anything other than the recommended action, or does not respond;
   - a screen has no advice, the character must wait for HP, or an item needs picking up;
   - you click in the game, or press **F10** (works from any window).

Autoplay never spends mushrooms. **Automated play breaks the game's rules, and accounts that use it can be
suspended.** The app asks for confirmation once before autoplay first runs.

The adviser reads the client version `31.500.260929.1`. After a game update it shows *"No advice for this
screen"* until the app is updated. Practice uses plain graphics in installed copies; the game's own artwork
is not redistributed. The adviser's decisions and choices are logged privately in
`%LOCALAPPDATA%\sfld\desktop-adviser`.

Build the installers with `npm run dist` (assembles `out/desktop-runtime`, then writes
`out/desktop-dist`).

This Windows preview hosts the original S&F browser client in a desktop window.
Its artwork, animations, controls and sign-in remain those of the original game.
The separate Practice view runs our simulator with the current trained adviser.

The October 16 Practice update fills the window with the dungeon and moves
playback, undo and the run menu into the scene. Press **P** to play/pause or **N**
to take the recommended action. Start directly there with
`./desktop/Start-Companion.ps1 -Practice` from the repository root.
The deployed adviser now follows a survivable, guaranteed-escape arena route
through all five fights and collects the reward. See the
[arena and healing checks](../docs/ARENA_AND_DESKTOP_2026_10_16.md).

| Feature | Current status |
|---|---|
| Original game view, fullscreen and manual play | Client startup verified; account play needs a signed-in session |
| Separate saved login profiles | Implemented, with a separate browser session per profile |
| JSON profile import | Labels and world addresses only; sign in through the game afterward |
| Practice recommendations and Watch play | October 15 checkpoint; final stone choice uses fresh simulations of all three offers |
| Prominent mimic warning in practice | Verified before the first action |
| Mimic warning over the original game | Implemented for client `31.500.260929.1`; tested with synthetic responses, not a live dungeon encounter |
| Live recommendations over the original game | Implemented; tested with synthetic game responses, not yet in a live dungeon |
| Autoplay with learned buttons | Implemented and unit-tested; button positions are learned in the live game |
| Standalone installer | `npm run dist`: Windows installer and portable exe with a bundled Python (NumPy only) and engine |

## Start on this machine

Run [Start-Companion.ps1](Start-Companion.ps1) in PowerShell, or run this from the
repository root:

```powershell
& .\desktop\Start-Companion.ps1
```

The default view opens the original game. Use **Logins** to add or import login
profiles, then sign in inside the original client. A profile represents an S&F
login; the original client's character selector handles its worlds and characters.
This is not an import of existing MFbot credentials or browser cookies.
The generic `sfgame.net` address opens the official `play.sfgame.net` client,
skipping the marketing landing page as the site's own script specifies.

Use **Practice** for recommendations and **Watch play**. To check the new warning
immediately, open **Logins → Preview a mimic in practice**. The prepared encounter
starts before any interaction and has separate Fight and Leave actions.
**F11** or **Fullscreen** expands the application and hides both the companion
header and the practice toolbar. **F11** or **Escape** restores the controls.
Paid HP refills appear only on the recovery screen, before reentry.

At the final stone choice, Practice compares 2,048 continuations per offered
stone using the exact current state. Autoplay waits for the result; each card
shows its rank and estimated remaining time. This also applies to the four-agent
watch windows. Restart the companion to select the updated planning-bridge build.

## Watch four agents

Run `desktop/Start-Watch-Four.ps1` to open four practice windows in a 2 × 2 grid
on the primary monitor. Each runs the current trained adviser with an independent
seed, a seven-day event and a 1,000-mushroom budget. Events restart automatically
with new seeds. This operates entirely in the simulator.

- **P / Pause** pauses one window after its current animation; **Step** takes its next action.
- **F9 / All** pauses or resumes all four, including while another application has focus.
- **F8 / Flag** pauses all four and saves the focused window's replay, screenshot and position.
- **Pace** changes the animation speed for that window (default 0.9 seconds).
- Closing all four windows stops their simulation backend.

Completed events and flags are saved in `out/watch-four/<launch timestamp>/`.
Flag files include the window number, event, seed and capture timestamp; their
JSON recordings can be loaded in the regular practice viewer. A flag taken during
an action captures its settled result, with the preceding decisions in the replay.

For a different budget or pace:

```powershell
& .\desktop\Start-Watch-Four.ps1 -Budget 100 -Pace 2400
```

Close an existing four-window watch session before relaunching with new settings.
Running the launcher while it is already open retiles its existing windows.
`npm run test:watch` checks four independent native sessions, the deployed policy,
pause/step, saved flags, scene geometry and automatic event rollover.

## Setup on another Windows checkout

Install Node.js, a C++20 compiler with CMake, and Python. From the repository root:

```powershell
cmake -S . -B build-v0611
cmake --build build-v0611 --config Release --parallel
python -m venv out/rl-env
& out/rl-env/Scripts/python.exe -m pip install -r tools/requirements-rl.txt
cd desktop
npm ci
npm start
```

The current host expects `build-v0611/Release/sfld.exe`, the selected checkpoint
under `docs/interaction-training-2026-10-15/`, and the local assets used by the visual lab.
See [visual lab setup](../docs/VISUAL_LAB.md) for the optional private artwork
extraction. GPU training is not required for desktop recommendations. Original
game viewing needs only the desktop runtime and an Internet connection.

## Login profile format

Import a JSON file shaped like [example-profiles.json](example-profiles.json):

```json
{"accounts": [{"label": "Personal login", "world": "sfgame.net"}]}
```

The importer validates the whole file before saving it and accepts only `label`
and `world`. Credentials belong in the original game's own sign-in screen, not
this JSON. Each profile uses a persistent Chromium session; its game cookies and
site storage stay on this PC. The app's user-data directory holds those sessions
and `login-profiles.json`. Switching profiles closes the previous game view.

## Current live overlay

The observer checks the original site's `js/build.json` version and listens for
full dungeon response records from the official game host. It classifies only the
currently revealed encounter. A recognized mimic gets a large warning above the
game canvas before interaction. It does not classify future rooms or unrevealed
door contents. The warning clears when a new command starts, on navigation, or
when the version/record cannot be interpreted. Late responses from a prior page
cannot restore a stale warning.

Raw response bodies are not written to disk or sent to the toolbar. The observer
does not send game commands. Normal game rendering remains in an isolated,
sandboxed view without Node.js or the app's privileged IPC bridge.

The official [client manifest](https://sfgame.net/js/build.json) matched the
inspected version on October 14, 2026. A future client update requires a new
parser check; it must not silently inherit this version's field meanings.

## Remaining live integration

The existing [bounded live pilot](../docs/LIVE_STUDY.md) and application session
layer already have recommendation previews, explicit autoplay settings, event
budgets and action journals for supported states. They are not yet connected to
this embedded client's authenticated session.

The next implementation must map the client's current, visible state to the same
policy, display its recommendation, then route enabled actions through the same
session. Recovery, run restarts and unsupported special rooms still need full
coverage. An unknown state must pause automatic actions while leaving manual
play available. Background execution needs a process-owned scheduler, per-character
budgets and a visible pause control, rather than relying on a hidden renderer's
animation timer. Saved sessions must not automatically enable autoplay at startup.

## Verification

```powershell
cd desktop
npm test
npm run test:window
node node_modules/electron/cli.js . --verify-original
```

The window test starts the real C++/Python practice backend and new policy, checks
the mimic warning at zero actions, leaves the encounter, exercises the login
dialog, checks fullscreen view bounds/header visibility and Escape restoration,
and verifies that active encounters have no paid refills and that the practice
view lacks privileged IPC. The hidden-window test checks application layout;
it does not verify the OS's fullscreen animation. It writes a
screenshot and result to `out/desktop-smoke/` and closes its backend on exit.

The original-client check uses a separate anonymous profile, verifies Unity
startup and renderer isolation, and saves `out/desktop-original-check/`. It does
not enter an account. Successful startup does not validate live dungeon parsing.

The implementation uses Electron's
[WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view),
[persistent session partitions](https://www.electronjs.org/docs/latest/api/session)
and [remote-content isolation guidance](https://www.electronjs.org/docs/latest/tutorial/security/).
