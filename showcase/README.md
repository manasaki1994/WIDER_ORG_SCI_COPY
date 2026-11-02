# Showcase video and page

A static page (`showcase/site/index.html`) with a silent walk through the offline demo, in chapters, and a link to the
demo itself. Nothing here is part of the `wider` package. The video films the built demo below, so it shows exactly
what a visitor can try; no project, model or key is needed to film it.

1. **Build the demo** (step 3 of the next section) into `showcase/site/demo/`.

2. **Record** with headless Chrome; `scenes.py` is the script, one function per scene: the result first, one scene per
   chapter of the demo's tour (`showcase/demo/tour.yaml`) with the cursor doing each action step, then an end card. `--site` serves the built folder next to the stage on
   127.0.0.1 and refuses any path outside it. Needs a Python with `websockets`, Google Chrome and Swift (for
   `encode.swift`):

   ```bash
   python3 showcase/record.py ~/wider-showcase-video --site showcase/site
   ```

   Each scene is kept as its own take; the scenes are filmed in one run because the demo carries its state from one
   chapter to the next (`--only NAME` re-films a scene that does not depend on the others, such as `ending`). Next to
   `wider-showcase.mp4` the recorder writes `wider-showcase.chapters.vtt` and `wider-showcase.captions.vtt`, timed from
   the takes; the page draws the chapter buttons and the "Read the video as text" list from them.

3. **Put it on the page**: copy the video, the two `.vtt` files and a poster frame (`poster.jpg`, taken from the
   opening) into `showcase/site/` (all gitignored). Check frames with the `video-frame-check` skill, including that no
   personal name appears.

4. **Deploy** only when the owner asks: `npx vercel deploy --prod` from `showcase/site/`.

The recorder's other mode films a running WIDER UI through a path-cleaning proxy (`--port 8780`, with `--scenes`,
`--stage` and `--project` for another project's own script); `build_project.py` builds such a project.
`record_snapshot.py` uses that mode for the demo's snapshot.

## Interactive demo (`/demo`)

A browsable copy of the real web UI on an invented project (39 papers about employee voice, three database exports),
with a guided tour. It is a static folder: the pages answer from a recorded snapshot, the demo's own actions (labels,
duplicate pairs, search terms, Rank, Draw samples, screening runs) are simulated in the browser tab, and nothing is
sent anywhere. The behaviour is specified in
`openspec/specs/interactive-demo/spec.md`.

1. **Checkpoints** (projects at fixed moments of the review) live outside the repository, in
   `~/wider-showcase-data/demo/`:
   - `imported`, `ranked`, `sampled` are free: no model is called and no key is needed.

     ```bash
     PYTHONPATH=. .venv/bin/python showcase/demo/build_checkpoints.py
     ```

   - `screened` and `screened2` cost money (about 230 calls to gpt-4o-mini). The script asks for the key from the
     clipboard, empties the clipboard and never writes the key down; then sign the copies:

     ```bash
     PYTHON=.venv/bin/python zsh showcase/demo/build_demo_project.sh
     ```

     ```bash
     PYTHONPATH=. .venv/bin/python showcase/demo/build_checkpoints.py --paid
     ```

2. **Snapshot**: record what the real server answers at each checkpoint, cleaned of paths and names, into
   `showcase/demo/snapshot/` (committed; it holds only the invented project):

   ```bash
   python3 showcase/demo/record_snapshot.py
   ```

   It needs Google Chrome and `websockets` in the system `python3`, as `record.py` does; `--only screened screened2`
   records just those checkpoints.

3. **Build** the demo into `showcase/site/demo/` (gitignored). The build refuses a leak, an outside address or a call
   the snapshot cannot answer, and fills the tour (`showcase/demo/tour.yaml`) with the recorded numbers; a tour step
   whose checkpoint is not recorded is left out:

   ```bash
   PYTHONPATH=. .venv/bin/python showcase/demo/build_demo.py
   ```

   `--skip-coverage` builds even with unanswered calls, for a local look only; never deploy that build.

4. **Look** at it from a local static server, for example `python3 -m http.server 8821 -d showcase/site`, then
   open `http://localhost:8821/demo/`.

5. **Deploy** with the rest of the site (step 4 above), only when the owner asks.
