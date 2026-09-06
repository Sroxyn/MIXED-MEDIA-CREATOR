<p align="center">
  <b>If this app was useful to you, you can buy me a coffee.</b>
</p>

<p align="center">
  <a  href="https://www.buymeacoffee.com/mustafa.fbx" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" width="217" height="60" style="height: 60px !important;width: 217px !important;" ></a>
</p>

---

<p align="center">
  <img src="docs/gorseller/logo.png" width="120" alt="MixedMedia Round-Trip Studio">
</p>

<h1 align="center">MixedMedia Round-Trip Studio</h1>

<p align="center">
  <b>video → paper → video</b><br>
  Splits a video into frames and produces print-ready pages; after you have
  worked on them by hand, it scans the pages back in and turns the frames into
  video again — in the right order.
</p>

---

## Contents

- [What does it do?](#what-does-it-do)
- [Installation](#installation)
- [First launch](#first-launch)
- [Part 1 — Preparing for print (IMPORT)](#part-1--preparing-for-print-import)
- [Part 2 — Working on paper and scanning](#part-2--working-on-paper-and-scanning)
- [Part 3 — Turning it back into video (EXPORT)](#part-3--turning-it-back-into-video-export)
- [Special cases](#special-cases)
- [Troubleshooting](#troubleshooting)
- [Command line](#command-line)

---

## What does it do?

1. The video is split into individual frames at the frame rate you choose
   (6 fps, for example).
2. The frames are laid out on A4/A3 pages as a grid, and a print-ready PDF comes
   out.
3. You draw, paint, cut and glue on paper.
4. You scan the pages and hand them back to the app.
5. Every frame is found, corrected, cropped and turned into video **in the right
   order**.

The ordering is never left to file names or to the order you scanned in. Each
frame's millimetre coordinate on the page is stored in the project file, and
corner markers plus a QR code are printed onto the paper itself. You can scan
the pages in any order — even upside down — and the app will still put every
frame back where it belongs.

---

## Installation

### 1. Download the app

Download `MixedMedia-windows.zip` from this repository's **Releases** section and
extract it anywhere you like. There is no installer — just double-click
**`MixedMedia.exe`** in the folder.

The folder contains two programs:

| File | What it is for |
|---|---|
| `MixedMedia.exe` | The graphical app — this is the one you will normally use |
| `mm.exe` | Command-line tool — for batch work and automation |

> **Windows SmartScreen warning:** the program is not code-signed, so Windows may
> show an "Unknown publisher" warning the first time you run it.
> Click *More info → Run anyway* to get past it.

### 2. Install FFmpeg (required)

FFmpeg does the reading and writing of video, and it **does not ship inside the
app** (because of its licence). Without it the app still opens, but nothing
video-related works; you will see a red warning at the bottom of the main screen.

The easiest way — open PowerShell and run:

```powershell
winget install Gyan.FFmpeg
```

Restart the app afterwards and the warning disappears.

If you would rather not install it system-wide, download it from
[ffmpeg.org](https://ffmpeg.org/download.html) and drop the `ffmpeg` folder
**next to `MixedMedia.exe`** — the app looks there too.

### 3. (Optional) A font for non-ASCII characters

For the footer text on the PDFs, a Unicode-capable font is picked from your
system. Windows already has Segoe UI, so there is nothing for you to do.

---

## First launch

The first time you run the app it asks for a language. Your choice is remembered
and you will not be asked again.

<p align="center">
  <img src="docs/gorseller/02-dil-secimi.png" width="420" alt="Language selection screen">
</p>

To change it later, press the **🌐 English** button at the top right of the main
menu — the interface switches instantly, no restart needed.

### Main menu

<p align="center">
  <img src="docs/gorseller/01-ana-menu.png" width="640" alt="Main menu">
</p>

There are two things you can do from here:

- **New project** — pick a video and start the loop from the beginning.
  Dropping a video file onto the middle area does the same thing.
- **Open project** — carry on with work you started earlier.
  You can drop the project folder onto the app.

With a project open, the two large buttons at the bottom become active:

<p align="center">
  <img src="docs/gorseller/03-ana-menu-proje-acik.png" width="640" alt="Main menu with a project open">
</p>

- **Prepare for print (IMPORT)** — the video → paper direction
- **Turn into video (EXPORT)** — the paper → video direction

> Every setting is written to the project folder as you go. You can close the
> app, or even restart the computer, and the work carries on from where you left
> it.

---

## Part 1 — Preparing for print (IMPORT)

A four-step wizard. You can go back at any step.

### Step 1 — Video

<p align="center">
  <img src="docs/gorseller/04-import-1-video.png" width="720" alt="IMPORT step 1: video">
</p>

Drag and drop the video, then choose the **frame rate**.

**The frame rate is the most important decision.** It determines how many frames
you will be drawing:

| Frame rate | A 10-second video | Result |
|---|---|---|
| 4 fps | 40 frames | Choppy, "stop motion" feel — least work |
| 6 fps | 60 frames | Classic hand-drawn animation pace (recommended) |
| 12 fps | 120 frames | Smooth, but twice the work |
| 24 fps | 240 frames | Very smooth, a great deal of work |

The line underneath shows the result of your choice immediately:
`→ 48 frames · ~8 s · 12 pages of A4`. **This is where you see how many pages you
will be printing.**

> The **Shrink frames** field limits the width of the extracted frames. Leave it
> empty (`Original`) — that field only exists to save disk space, it does not
> improve print quality. Do not enter a value **larger** than your source video;
> that only bloats the images for nothing.

When you press **Next**, the frames are extracted. On long videos this takes a
while; you can follow the progress bar and cancel at any point.

### Step 2 — Look

<p align="center">
  <img src="docs/gorseller/05-import-2-gorunum.png" width="720" alt="IMPORT step 2: look">
</p>

This is where you set **how dark** the print will be. The preview on the right
updates instantly on a real frame.

The idea behind it: you are going to draw **on top of** what gets printed. The
lighter the print, the easier it is to draw over — but the harder it is to follow
the motion. Find the balance that suits how you work.

| Setting | What it does |
|---|---|
| **Processing mode** | Original (colour), Greyscale, Line art, Halftone |
| **Print density** | The most effective setting. Lower it and the print fades |
| **Contrast / Brightness / Gamma** | Fine tuning |

A good starting point: **Greyscale** + **40–55% density**. Print one test page
and adjust to your own printer.

### Step 3 — Page layout

<p align="center">
  <img src="docs/gorseller/06-import-3-duzen.png" width="720" alt="IMPORT step 3: page layout">
</p>

The preview on the right **is the page that will actually be printed** — there is
no separate drawing path, what you see is what comes out. Move between pages with
`‹` and `›`.

| Setting | Description |
|---|---|
| **Paper / Orientation** | A4, A3, Letter · Portrait or Landscape |
| **Grid → Automatic** | You say how many frames you want per page; the system picks the columns×rows arrangement and the orientation that give the most printed area |
| **Grid → Manual** | You enter the columns and rows yourself |
| **Margin / Gutter** | Leaves room for cutting |
| **Image** | *Fit* shows the whole frame, *Fill* fills the cell (cropping the edges) |
| **Cut marks** | Corner marks, a hairline frame, or none |

The summary at the bottom left tells you two things:

- **How many pages** will come out, and the cell dimensions.
- **Effective DPI** — the resolution the frames will be printed at. If this
  number turns amber ("this will look soft in print"), either choose fewer frames
  per page or move to a larger paper size.

#### Marks on the page

The four options in this box are what let the app find the frames again:

| Mark | What it is for |
|---|---|
| **Corner markers and page QR** | Identifies the page and corrects skew and perspective |
| **Micro-marker per cell** | Carries each frame's identity; survives even if you cut the frames apart |
| **Greyscale calibration strip** | Corrects scanner/printer colour shift |
| **Footer text** | Project name, fps, date, page number |

> ⚠️ **Do not turn these off.** You can, for aesthetic reasons, but then the app
> cannot align your scans automatically and you will have to mark the four
> corners of every page by hand. The app already shows an amber warning when you
> switch them off.

### Step 4 — Output

<p align="center">
  <img src="docs/gorseller/07-import-4-cikti.png" width="720" alt="IMPORT step 4: output">
</p>

Choose *PDF (single document)* as the **Format** — that is what you hand to the
printer or print shop. Some print shops want one file per page; in that case
choose *One PNG per page* and set the resolution.

Press **Generate**, and when it finishes use **Open folder** to get to the file.

#### When you print

This is the most critical point:

> 🖨️ **Scaling must be OFF in the printer settings.**
> Options like "Fit to page" or "Shrink oversized pages" must be **off**, and the
> scale must be **100% / Actual size**.

If the printer shrinks the page to 97%, the measurements on the paper no longer
match the ones in the project and the frames get cropped in the wrong places.
Printing one test page and checking it with a ruler is a good habit.

---

## Part 2 — Working on paper and scanning

### While you work

- **Do not paint over the corner markers or the QR code.** Do whatever you like
  on the frames themselves, but the black squares in the page corners and the QR
  code at the top right have to stay readable. The same goes for the grey strip
  along the bottom edge.
- If you want to cut the frames apart, that is fine — the small marker under each
  frame carries its identity. See
  [Working with cut-out cards](#working-with-cut-out-cards).

### While you scan

| Topic | Recommendation |
|---|---|
| **Resolution** | **300–600 DPI** is plenty. Higher does not improve quality, it only slows things down |
| **Colour** | Scan in colour (the app decides about greyscale itself) |
| **Framing** | **The whole page must be in frame.** If the edges get cropped, the corner markers are lost and alignment becomes impossible |
| **Auto-correction** | Turn **off** your scanner's "auto crop", "auto straighten" and "clean background" options — the app already does this, and does it better |
| **Format** | Prefer PNG or TIFF; JPEG works too. Multi-page PDFs are also accepted |
| **Order** | **Does not matter.** You can scan the pages in any order, even upside down |

---

## Part 3 — Turning it back into video (EXPORT)

Four steps again.

### Step 1 — Project

<p align="center">
  <img src="docs/gorseller/08-export-1-proje.png" width="720" alt="EXPORT step 1: project">
</p>

You tell the app **which project** the scans came from. If you arrived here with
a project already open from the main menu, this step is filled in for you.

This step is essential, because the app guesses nothing: it reads each frame's
position on the page and its place in the sequence from the project file.

### Step 2 — Scans

<p align="center">
  <img src="docs/gorseller/09-export-2-taramalar.png" width="720" alt="EXPORT step 2: scans">
</p>

Drag and drop your scanned files (individual images, a folder, or a multi-page
PDF), then press **Process scans**.

Each row in the table is one scan:

| Column | Meaning |
|---|---|
| **Status** | 🟢 *good* · 🟡 *fair* / *weak* · 🔴 *unreadable* |
| **Page** | Which page it is. If the QR could not be read, you can pick it here by hand |
| **Method** | `corners4` = all four corners were read (best), `cell_markers` = aligned using the cell markers only |
| **Markers** | How many corner + how many cell markers were found |
| **Note** | Warnings, such as the *"The page was scanned upside down; it was rotated automatically using the markers"* in the example |

At the bottom you see **how many frames were extracted**. If they were all found,
carry on.

> The **Bleed** slider adjusts where the cells get cropped. A negative value
> stays inside the paper edge (the default is −0.5 mm, which keeps the edge line
> out of the image). If you want drawing that spills outside the cell to be
> included as well, use a positive value.

### Step 3 — Frames

<p align="center">
  <img src="docs/gorseller/10-export-3-kareler.png" width="720" alt="EXPORT step 3: frames">
</p>

The strip at the top shows every frame with a colour code:
🟢 found · 🟡 low confidence · 🔴 missing.

The **Play** button on the right plays the animation at its real speed — you are
watching exactly what will be written to the file.

**Missing frames** — you choose what happens when a frame cannot be found:

| Option | Result |
|---|---|
| **Hold previous** | The previous frame stays on screen for two frames (the most natural, recommended) |
| **Skip** | The frame is not written at all — the video gets shorter and the timing changes |
| **Interpolate** | An in-between image is computed from the neighbouring frames |
| **Red frame** | The gap is printed in red — useful for seeing which frame is missing |

**Stabilisation** — removes the jitter caused by the paper sitting a few
millimetres differently on the scanner each time.

> In mixed media a slight jitter is usually a **wanted** look. Set it to **0** to
> keep the raw result. A low value is also safer when the source video moves a
> lot.

### Step 4 — Video

<p align="center">
  <img src="docs/gorseller/11-export-4-video.png" width="720" alt="EXPORT step 4: video">
</p>

| Setting | Description |
|---|---|
| **Frame rate** | The playback speed of the video |
| **Resolution** | Defaults to the size of the source video |
| **Codec** | *H.264* → for sharing · *ProRes 422 HQ* → if you are taking it back into an editor · *PNG sequence* → frame-by-frame files |
| **Re-attach the original audio** | Puts the source video's audio back |

> ⏱️ **Speed warning.** If you extracted frames at 6 fps and export the video at
> 12 fps, the motion runs twice as fast. When the two values differ, the app says
> so with an amber warning: "the motion will be **2×** faster". If you are doing
> it on purpose, there is no problem.

Press **Export**. When it finishes, use **Open folder** to get to the video.

🎉 The loop is complete.

---

## Special cases

### Working with cut-out cards

If you cut the frames apart and worked on them separately (collage, gluing them
onto different surfaces, and so on), you can lay the cards on the scanner in any
arrangement and scan them. The micro-marker under each card carries its identity;
when the app cannot find a page, it switches to card mode by itself.

The one thing to watch out for: **do not cut off the small square under the
cards.** Cutting outside the cut marks keeps you safe.

### Printing without markers and aligning by hand

If you turned the markers off for aesthetic reasons, automatic detection will not
work. In that case, select a row in the table on the **Scans** step and press
**Align selected page by hand…**. In the window that opens you drag to mark the
four corners of the page; the grid is overlaid using the project's measurements.

If the print sat off-centre on the paper, you can correct it millimetre by
millimetre with the **Shift grid horizontally/vertically** sliders underneath.

If **Apply this alignment to the following scans too** is ticked, the same
corners are applied to the following pages as well — the paper usually sits in
roughly the same place on the scanner, so this often works and saves you from
marking every page from scratch.

---

## Troubleshooting

<table>
<tr><th align="left">Symptom</th><th align="left">Cause and fix</th></tr>

<tr><td><b>"FFmpeg was not found"</b> — red warning on the main screen</td>
<td>FFmpeg is not installed. See the
<a href="#2-install-ffmpeg-required">installation section</a>.
Restart the app once you have installed it.</td></tr>

<tr><td><b>"No corner markers were found"</b></td>
<td>Most likely the scan cropped the edges of the page. Rescan with the whole
page in frame, and turn off your scanner's auto-crop setting. If you cut the
frames apart, card mode should take over instead.</td></tr>

<tr><td><b>"The page QR could not be read"</b></td>
<td>Usually harmless — the app infers the page number from the scan order. If it
matches the wrong page, pick the correct number by hand in the <b>Page</b> column
of the <b>Scans</b> table and process again.</td></tr>

<tr><td><b>"The page layout changed after these pages were printed"</b></td>
<td>You changed the grid settings after printing the PDF. The cells on the paper
no longer match the coordinates in the project. Either revert the layout to how
it was printed, or generate and print a new PDF with the current layout.</td></tr>

<tr><td><b>Frames are cropped in the wrong places</b></td>
<td>The printer may have scaled the page. Make sure the scale is <b>100% /
Actual size</b> when printing.</td></tr>

<tr><td><b>The video drifts or shakes at the edges</b></td>
<td>Lower <b>Stabilisation</b> on the <b>Frames</b> step, or set it to <b>0</b>.
On work with a very mobile source video, stabilisation can do more harm than
good.</td></tr>

<tr><td><b>The print is too faint or too dark</b></td>
<td>Adjust <b>Print density</b> on the <b>Look</b> step. Every printer is
different; it is worth printing one test page.</td></tr>

<tr><td><b>The frames extracted from the scans look poor</b></td>
<td>Scan at 300–600 DPI and turn off your scanner's auto-correction options. Make
sure you left the calibration strip printed — that is what corrects the colour
shift.</td></tr>

<tr><td><b>The interface is stuck in the wrong language</b></td>
<td>Change it with the 🌐 button at the top right of the main menu. The change is
applied instantly.</td></tr>
</table>

---

## Command line

Everything can also be done with `mm.exe` — handy for processing many projects at
once or for scripting.

If you want to try the app without preparing a video at all, you can set up an
example project and run the whole loop:

```powershell
mm example demo --frames 8
mm ingest demo/scans -p demo
mm video -p demo
```

After these three commands, `demo/out/` contains both the print PDF and the
video.

For the other commands:

```powershell
mm --help
```

> The command-line interface is currently Turkish only.

---

## Known limitations

- The graphical interface is available in **Turkish** and **English**; `mm.exe`
  is Turkish only.
- FFmpeg does not ship with the app and has to be installed separately.
- The program is not code-signed, so Windows shows a warning on first launch.
- Only a Windows build is provided. Running from source is supported on macOS and
  Linux as well.
