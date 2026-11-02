"""Home page routes: project summary, recent projects (switcher), open and create a project, backend check.

Opening or creating a project switches the folder every other route reads (``app.state.project``). Requests
carry JSON bodies, so a page from another origin cannot send them without a CORS preflight, which this app
never grants. Paths must be absolute folders on this computer; nothing is uploaded anywhere.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..core.project import PROJECT_FILE, Project
from ..core.recent import RecentProjects


class OpenBody(BaseModel):
    path: str


class CreateBody(BaseModel):
    path: str
    title: str = ""
    questions: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


def _summary_of(root: str | Path, current: Path, runner=None) -> dict:
    """The project summary, marked current or not; with ``runner`` (the app's JobRunner, for Home's cards) also the
    jobs still running in this server for that project, by label and stage only (a job whose server stopped is
    marked interrupted, as the Jobs panel shows it)."""
    from ..core.summary import project_summary

    p = Project.load(root)
    try:
        s = project_summary(p)
        if runner is not None:
            s["running"] = [{"label": j["label"], "stage": j["stage"]} for j in runner.list(p, active_only=True)
                            if j["status"] == "running"]
    finally:
        p.close()
    s["current"] = Path(s["root"]) == current
    return s


def _folder(raw: str) -> Path:
    path = Path((raw or "").strip()).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "give the full path of the folder, for example /Users/you/reviews/my-review")
    return path.resolve()


def register(app: FastAPI, P, recent: RecentProjects) -> None:
    """``P()`` returns a freshly loaded current Project; ``recent`` stores the switcher list."""
    app.state.recent = recent

    def current_root() -> Path:
        return Path(app.state.project.root).resolve()

    def switch(p: Project) -> None:
        app.state.project = p
        recent.add(p.root)

    @app.get("/api/project/summary")
    def get_summary():
        return _summary_of(current_root(), current_root())

    @app.get("/api/home")
    def get_home():
        """The current project first, then the other recent projects that still exist on disk. A folder that no
        longer holds a project, the open one included, is counted as missing, so Home still lists the others."""
        cur = current_root()
        missing = []
        runner = getattr(app.state, "jobs", None)
        projects = [_summary_of(cur, cur, runner)] if (cur / PROJECT_FILE).exists() else []
        if not projects:
            missing.append(str(cur))
        for item in recent.list():
            root = Path(item["path"])
            if root == cur:
                continue
            if not (root / PROJECT_FILE).exists():
                missing.append(str(root))
                continue
            try:
                s = _summary_of(root, cur, runner)
            except Exception as ex:  # an unreadable project.yaml must not break the home page
                s = {"root": str(root), "title": root.name, "error": str(ex)[:200], "current": False}
            s["opened_at"] = item.get("opened_at")
            projects.append(s)
        return {"projects": projects, "missing": missing, "recent_persisted": recent.path is not None}

    @app.post("/api/projects/open")
    def open_project(body: OpenBody):
        root = _folder(body.path)
        if not (root / PROJECT_FILE).exists():
            raise HTTPException(404, f"no {PROJECT_FILE} in {root}; create a project there instead")
        switch(Project.load(root))
        return _summary_of(root, root)

    @app.post("/api/projects/create")
    def create_project(body: CreateBody):
        root = _folder(body.path)
        if (root / PROJECT_FILE).exists():
            raise HTTPException(409, f"{root} already holds a project; open it instead")
        if not root.parent.exists():
            raise HTTPException(400, f"the parent folder {root.parent} does not exist")
        if root.exists() and any(root.iterdir()):
            raise HTTPException(400, f"{root} is not empty; choose a new or empty folder")
        try:
            p = Project.init(root, title=body.title.strip(),
                             questions=[q.strip() for q in body.questions if q.strip()],
                             platforms=[k.strip().lower() for k in body.platforms if k.strip()])
        except ValueError as ex:   # unknown platform key
            raise HTTPException(400, str(ex)) from ex
        except OSError as ex:
            raise HTTPException(400, f"could not create the project: {ex}") from ex
        switch(Project.load(p.root))
        return _summary_of(p.root, p.root)

    @app.post("/api/projects/forget")
    def forget_project(body: OpenBody):
        root = _folder(body.path)
        if root == current_root():
            raise HTTPException(400, "the open project cannot be removed from the list")
        recent.remove(root)  # the folder itself is never touched
        return {"ok": True}

    @app.post("/api/backends/check")
    async def post_backend_check():
        from ..core.backend_check import check_backends

        p = P()
        try:
            return await check_backends(p)
        finally:
            p.close()
