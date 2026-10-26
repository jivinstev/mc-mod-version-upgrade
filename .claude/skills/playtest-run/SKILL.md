---
name: playtest-run
description: Run playtest scripts in the real Minecraft client of each installed version (the test-installs instances, one version at a time, no Gradle) through the Gate C carrier's playtest mode -- grade every step (commands, assertions, expected sounds, subtitles, chat, titles, boss bars, screens, unexpected errors), take key frames, optionally record the screen, and publish the report (per version, per step, with the frames) as a Claude Code artifact. Use when the user asks to run, replay or check a playtest, across versions, with video, or to show its results.
---

# Run a playtest

**Which fork:** the same for every fork. It runs in the instances that `tools/real-install.py install --with-carrier`
made (test-installs). It never touches the player's own mods folder or profiles, and changes nothing in any fork.

## 1. Before the first run

- **Instances:** one per version, made by `real-install.py plan` and then `install --with-carrier`. Without the
  carrier there is nothing to run the script. The carrier's `playtest` mode is in release `gatec-carrier-v2` and
  later; a CI-built carrier can be used before then with `--carrier-dir DIR` (a downloaded `gatec-carrier` artifact)
  or `--carrier-jar [MC=]JAR`. Either one replaces the carrier in the instance.
- **The screen (macOS):** the game opens a real window.
  - The user's session must be the one on the console, and the screen unlocked. The runner keeps the display awake,
    but it cannot unlock it.
  - Ask the user before a run that needs them away from the machine.
  - `--video` records with macOS's `screencapture`. macOS grants Screen Recording to the app the tool runs inside
    (Terminal, iTerm, the Claude desktop app, an IDE), never to `python3`, and refuses a command run inside it
    silently: no prompt appears. The runner checks first with one silent still. If it is refused, the runner prints
    which app to allow and carries on with key frames only. Tell the user, in these words:
    1. open System Settings > Privacy & Security > Screen & System Audio Recording;
    2. turn on <that app>, adding it with + if it is not listed;
    3. quit and reopen <that app>, because the change applies only to a fresh start.

    The session that asked does not get the permission. If that app is the one running this conversation (the
    Claude desktop app, a terminal), the run with video happens after the user restarts it, in a new session.
- **Lint first:** `python3 tools/playtest.py lint SCRIPT --jar MOD.jar`.

## 2. Run

```bash
python3 tools/playtest.py run SCRIPT [--plan PLAN] [--mc 1.21.1 --mc 26.3] [--video] [--timeout 900] [--report DIR]
```

- `--plan` is real-install's `plan.json`. The default is the only plan under `~/.mmvu/real-install/`.
- `--mc` (repeat it) narrows the run to those versions. The default is every version in the plan.
- `--video` records the screen.
- `--timeout` is seconds per version (default 900).
- `--report` sets the report folder. The default is `<plan folder>/playtests/<script>-<time>/`.

Each version runs alone. The carrier opens a new world, runs the script step by step, takes a frame at each step's
end, at each `@shot` and where a step fails, and prints `PLAYTEST: STEP n name: PASS|FAIL -- detail` and then
`PLAYTEST: PASS|FAIL -- k/n steps`. The runner reads the carrier's `playtest-result.json` for each step's lines,
observations and frames.

A version fails when:

- its playtest fails;
- the game crashes or exits before a verdict;
- no verdict arrives within the timeout.

Each case is named in the report. The exit status is 0 only when every version passed. A version whose display slept or locked mid-run (`glfwGetPrimaryMonitor failed`) is
reported as that, not as the mod's failure: keep the screen awake and run that version again.

## 3. The report

The report folder holds:

- `report.md`;
- `report.html`: self-contained, with frames linked by relative path. Failed steps start open;
- `playtest-report.json`;
- one folder per version: `stdout.log`, `stderr.log`, `latest.log`, `command.txt`, `frames/` with the result JSON and
  PNGs, and the `.mov` recording.

**Publish it as an artifact** when the user wants to see it or share it:

1. Load the `artifact-design` skill.
2. Shrink the frames, on macOS with `sips -Z 640 -s format jpeg IN.png --out OUT.jpg`, so the page stays well under
   the size limit.
3. Publish `report.html` with the frames as supporting `files` (their paths as the page names them).

A recording is too big for the page. Name its path in the reply instead.

On a FAIL, go to playtest-debug with the report folder.
