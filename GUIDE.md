# Building SnipLab: step-by-step guide

This guide takes you from an empty GitHub repository to a packaged Windows `.exe`. Each milestone ends with a commit, so your Git history tells the story of the build.

## 0. Why Python + PySide6?

| Option | Pros | Cons |
|---|---|---|
| **Python + PySide6 (Qt)** (used here) | Fastest to build, rich graphics/undo framework, one-file `.exe` via PyInstaller, works on Mac/Linux later | Bigger exe (~60-90 MB), not as snappy as native |
| C# + WPF/.NET | Native Windows feel, small installers | Windows only, more boilerplate for a canvas editor |
| Electron / Tauri | Web UI skills reuse | Screen capture and global hotkeys need native glue |

Snagit's core is a **capture layer** (grab pixels) plus an **editor** (a canvas of movable annotation objects over a bitmap). Qt's `QGraphicsScene` is purpose-built for the second half.

## 1. Install the tools

1. **Python 3.11 or newer**: https://www.python.org/downloads/ (tick "Add python.exe to PATH").
2. **Git**: https://git-scm.com/download/win
3. **VS Code**: https://code.visualstudio.com/ with the *Python* extension.
4. **GitHub account** and, optionally, the GitHub CLI: https://cli.github.com/

Check in a terminal:

```bash
python --version
git --version
```

Tell Git who you are (once per computer):

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## 2. Create the repository

### Option A: GitHub website
1. github.com -> **New repository**.
2. Name: `SnipLab`. Visibility: your choice. Tick **Add a README**, choose **Python** `.gitignore` and the **MIT** license. Click **Create**.
3. Click **Code -> HTTPS**, copy the URL, then:

```bash
git clone https://github.com/<your-username>/SnipLab.git
cd SnipLab
```

### Option B: GitHub CLI
```bash
mkdir SnipLab && cd SnipLab
git init -b main
gh auth login
gh repo create SnipLab --private --source . --remote origin
```

### Use the files in this project
Copy this project's contents into your repo folder (replacing the generated README/.gitignore/LICENSE), then:

```bash
git add .
git commit -m "Initial SnipLab project"
git push -u origin main
```

## 3. Set up the dev environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run_sniplab.py
```

A virtual environment (`.venv`) keeps SnipLab's libraries separate from the rest of your PC. It is already in `.gitignore`.

## 4. Project layout

```
SnipLab/
  run_sniplab.py          launcher (dev + PyInstaller)
  requirements.txt
  build.bat               builds dist\SnipLab.exe
  .github/workflows/      CI: tests + Windows build + release
  src/sniplab/
    app.py                main window, tray, capture flow, entry point
    capture.py            grab screen / active window
    overlay.py            drag-to-select region overlay
    editor.py             Canvas (QGraphicsView) + EditorWindow
    items.py              arrow, pixelate, step, text items
    hotkeys.py            global hotkeys (pynput)
    library.py            capture folder, naming (no Qt, unit tested)
  tests/test_library.py
```

**Design rule:** keep logic that doesn't need Qt (like `library.py`) in plain modules so it is testable in CI without a screen.

## 5. Build it in milestones

Work on a branch per milestone, open a pull request, and merge. For solo work, committing straight to `main` is fine too.

```bash
git switch -c feature/region-capture
# ...code...
git add -A && git commit -m "Add region capture overlay"
git push -u origin feature/region-capture
gh pr create --fill        # or open the PR on github.com
```

### M1. App skeleton (`app.py`)
A `QApplication`, a `QMainWindow`, and a generated icon. Goal: a window opens. Commit: *"Add main window"*.

### M2. Capture the screen (`capture.py`)
`QScreen.grabWindow(0)` returns a `QPixmap` of the whole screen. Save it with `pixmap.save(path)`. Add the **Full screen** button. Hide the main window first (a 250 ms timer) so SnipLab isn't in its own screenshot. Commit: *"Full-screen capture"*.

### M3. Region selection (`overlay.py`)
1. Grab the whole screen first (a frozen picture).
2. Show a frameless, always-on-top widget the size of the screen that paints that picture, dimmed.
3. Mouse press/drag/release defines a `QRect`; paint the un-dimmed picture inside it.
4. Convert logical coordinates to device pixels with `devicePixelRatio()` (essential on 125%/150% scaling) and crop with `pixmap.copy()`.

Commit: *"Region capture overlay"*.

### M4. Capture library (`library.py` + tests)
Timestamped unique file names, listing newest first. Write the unit tests **before** the code if you like TDD. Show thumbnails in a `QListWidget` in icon mode. Commit: *"Capture library"*.

### M5. Editor canvas (`editor.py`)
- `QGraphicsScene` holds the screenshot as a background pixmap (z = -1000) and every annotation as an item above it.
- `QGraphicsView` shows it, handles zoom (Ctrl + wheel) and mouse input.
- Tool state machine: on mouse **press** create an item, on **move** update its geometry, on **release** commit it (or discard if tiny).

Commit: *"Editor canvas with rectangle tool"*.

### M6. More tools (`items.py`)
| Tool | How |
|---|---|
| Rectangle / Ellipse / Line | built-in `QGraphicsRectItem`, `QGraphicsEllipseItem`, `QGraphicsLineItem` |
| Arrow | subclass `QGraphicsLineItem`, override `paint()` to add a triangle head |
| Highlight | rectangle with semi-transparent fill, no outline |
| Blur | custom item that shrinks then enlarges the screenshot region (pixelation) |
| Step | circle + number, auto-incrementing |
| Text | `QGraphicsTextItem` with `TextEditorInteraction` |

Commit per tool.

### M7. Undo/redo and crop
Use `QUndoStack` with `QUndoCommand` subclasses (`AddCommand`, `DeleteCommand`, `CropCommand`). Qt gives you ready-made Undo/Redo actions via `createUndoAction()`. Crop only changes the scene rectangle, so it is fully undoable and non-destructive until you save.

### M8. Export
`scene.render(painter, target, source)` paints the scene (without selection handles) into a `QImage`. Then `image.save(path)` and `QClipboard.setImage(image)` give Save and Copy.

### M9. Tray and global hotkeys (`hotkeys.py`)
- `QSystemTrayIcon` keeps SnipLab alive when its window closes.
- `pynput.keyboard.GlobalHotKeys` listens in another thread. Emit **Qt signals** from its callbacks. Never touch widgets directly from that thread.

### M10. Package
```bash
build.bat
```
PyInstaller creates `dist\SnipLab.exe`. Antivirus programs sometimes flag unsigned PyInstaller one-file apps; if that happens during testing, use `--onedir` instead of `--onefile`, or code-sign later.

### M11. Continuous integration and releases
`.github/workflows/build.yml` runs the unit tests on every push and builds the Windows exe. To publish a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions then attaches `SnipLab.exe` to a GitHub Release.

## 6. Known limits of v0.1

- Capture uses the monitor under the mouse cursor, not the whole multi-monitor desktop.
- Active-window capture is Windows only and may be slightly off on mixed-DPI multi-monitor setups.
- Saving overwrites the capture file with a flattened image; there is no editable project format yet.
- Global hotkeys can conflict with other apps; edit `DEFAULT_KEYS` in `hotkeys.py`.
- The GUI was written without being run in the authoring sandbox. Expect to fix a few small bugs on first run; that is normal. Run `python run_sniplab.py` from a terminal so any traceback is visible.

## 7. Roadmap: the Snagit features still to build

1. **Editable project file** (`.sniplab` = zip of base PNG + JSON list of annotations) so annotations stay editable after saving.
2. **Scrolling capture**: scroll a window with simulated input and stitch frames (`pywin32`/`pyautogui` + image stitching).
3. **Screen recording and GIF**: capture frames in a loop (`mss`) and encode with `imageio`/`ffmpeg`.
4. **Text capture (OCR)**: `pytesseract` or Windows OCR.
5. **Templates/stamps/callouts**: speech-bubble and stamp items, shadow and border effects.
6. **Settings dialog**: hotkeys, default format, auto-copy, capture delay.
7. **Multi-monitor and "all-in-one" capture**, then an installer with Inno Setup.

Each is a new item type or a new capture mode on top of the architecture you already have.

## 8. Day-to-day Git cheat sheet

```bash
git status                       # what changed
git add -A                       # stage everything
git commit -m "Message"          # save a snapshot
git push                         # upload
git pull                         # download
git switch -c feature/name       # new branch
git log --oneline --graph        # history
```
