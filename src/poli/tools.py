"""The MCP tools: the same reads as the CLI, as structured JSON for any agent."""

from __future__ import annotations

import sys

from poli import Aula, __version__, queries, renewal, search
from poli.canvas import CanvasError
from poli.config import ConfigError, now_utc
from poli.mcp import Server, Tool, ToolError

INSTRUCTIONS = """Read-only access to the user's ESPOL aula virtual (Canvas). Answer the student in Spanish unless they
write in another language, and link each item's `url`. `course` filters accept any fragment of a course's name or
code, ignoring accents and case. Search only covers downloaded files: find a document with `list_files`, then
`download_files` it before `search_material` or `read_file`. Nothing here can submit or post on Canvas: when asked,
tell the student to do it themselves at the item's `url`."""

COURSE = {"type": "string", "description": "Fragment of the course name or code, e.g. 'calculo'"}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 50}


def build(aula: Aula) -> Server:
    def ids(args: dict) -> list[int] | None:
        return queries.course_ids(aula.conn, args.get("course"))

    def fresh() -> None:
        try:
            aula.ensure_fresh()
        except (CanvasError, ConfigError) as exc:
            if not queries.courses(aula.conn):
                raise ToolError(str(exc)) from exc  # nothing saved to fall back on

    def reading(handler, refresh: bool = True):
        def run(args: dict):
            if refresh:
                fresh()
            try:
                return handler(args)
            except (queries.NotFound, CanvasError, ConfigError) as exc:
                raise ToolError(str(exc)) from exc
        return run

    def download(args: dict):
        from poli.cli import download as fetch
        return fetch(aula, [int(i) for i in args["file_ids"]])

    def sync(args: dict):
        try:
            report = aula.sync(materials=bool(args.get("syllabi")))
        except (CanvasError, ConfigError) as exc:
            raise ToolError(str(exc)) from exc
        return {"courses": report.courses, "changes": report.events, "requests": report.requests,
                "warnings": report.warnings}

    tools = [
        Tool("list_courses", "The student's active courses, with current score.",
             reading(lambda a: queries.courses(aula.conn))),
        Tool("pending_assignments", "Assignments not handed in yet, ordered by due date (recently overdue first). "
             "`delivery` says whether it is handed in on the aula, elsewhere, or is a scheduled event (an exam).",
             reading(lambda a: queries.pending(aula.conn, aula.now(), ids(a), days=a.get("days"))),
             {"course": COURSE, "days": {"type": "integer", "minimum": 1, "description": "Only due within N days"}}),
        Tool("find_assignments", "Any assignment of the semester (handed in or past included) whose title or "
             "instructions carry these words, e.g. 'taller 2'.",
             reading(lambda a: queries.find_assignments(aula.conn, aula.now(), a["words"], ids(a))),
             {"words": {"type": "string"}, "course": COURSE}, ["words"]),
        Tool("get_assignment", "One assignment with the professor's instructions, rubric, and the files and links "
             "it points to.", reading(lambda a: queries.assignment(aula.conn, aula.now(), int(a["assignment_id"]))),
             {"assignment_id": {"type": "integer"}}, ["assignment_id"]),
        Tool("announcements", "Recent announcements, newest first, with their attached files and links.",
             reading(lambda a: queries.announcements(aula.conn, ids(a), limit=a.get("limit", 10))),
             {"course": COURSE, "limit": LIMIT}),
        Tool("grades", "Published grades per course, with the professor's feedback, rubric and late penalty.",
             reading(lambda a: queries.grades(aula.conn, ids(a))), {"course": COURSE}),
        Tool("people", "Professors and assistants of each course (email when visible) and the student's groups.",
             reading(lambda a: queries.people(aula.conn, ids(a))), {"course": COURSE}),
        Tool("inbox", "Messages professors and assistants sent to the student's aula inbox, newest first.",
             reading(lambda a: queries.inbox(aula.conn, ids(a), limit=a.get("limit", 10))),
             {"course": COURSE, "limit": LIMIT}),
        Tool("calendar", "Upcoming calendar events (exams, live classes) and peer reviews still to do.",
             reading(lambda a: queries.calendar(aula.conn, aula.now(), ids(a), days=a.get("days", 30))),
             {"course": COURSE, "days": {"type": "integer", "minimum": 1}}),
        Tool("discussions", "Course forums: due date, whether the student participated, replies to them.",
             reading(lambda a: queries.discussions(aula.conn, ids(a))), {"course": COURSE}),
        Tool("list_files", "The course material catalog: every document the aula shows, downloaded or not.",
             reading(lambda a: queries.files(aula.conn, ids(a), a.get("name"))),
             {"course": COURSE, "name": {"type": "string", "description": "Fragment of name, module, section or "
                                                                          "folder, e.g. 'semana 3'"}}),
        Tool("modules", "The course Modules in order, as the student sees them.",
             reading(lambda a: queries.module_items(aula.conn, ids(a), a.get("name"))),
             {"course": COURSE, "name": {"type": "string"}}),
        Tool("links", "Links outside the aula (Dropbox, SharePoint, videos) and whether they open without a login.",
             reading(lambda a: queries.links(aula.conn, ids(a), a.get("name"))),
             {"course": COURSE, "name": {"type": "string"}}),
        Tool("download_files", "Download and index files by id; returns each `local_path`, which you may open "
             "directly.", reading(download), {"file_ids": {"type": "array", "items": {"type": "integer"},
                                                            "minItems": 1}}, ["file_ids"]),
        Tool("read_file", "Text of a downloaded file, page by page.",
             reading(lambda a: queries.read_pages(aula.conn, int(a["file_id"]), a.get("first_page"),
                                                  a.get("last_page")), refresh=False),
             {"file_id": {"type": "integer"}, "first_page": {"type": "integer"},
              "last_page": {"type": "integer"}}, ["file_id"]),
        Tool("search_material", "Full-text search over downloaded course material. Much material is in English: "
             "try English keywords too. Cite `file`, `unit` + `page` and `url`.",
             reading(lambda a: search.search(aula.conn, a["query"], course_ids=ids(a), limit=a.get("limit", 5)),
                     refresh=False),
             {"query": {"type": "string"}, "course": COURSE, "limit": LIMIT}, ["query"]),
        Tool("sync", "Read the aula now instead of the saved copy (it refreshes on its own after 10 minutes).",
             sync, {"syllabi": {"type": "boolean", "description": "Also download each course's new syllabus"}}),
    ]
    for tool in tools:
        tool.read_only = tool.name not in ("download_files", "sync")
    return Server("poli", __version__, tools, INSTRUCTIONS, before_call=lambda: keep_alive(aula))


def keep_alive(aula: Aula) -> None:
    try:
        renewal.maintain(aula.cfg, now_utc(), wait=False)
    except (renewal.RenewalError, ConfigError) as exc:
        print(f"poli: no pude renovar el token ({exc})", file=sys.stderr)


def serve() -> int:
    aula = Aula(explicit=True)
    try:
        return build(aula).serve()
    finally:
        aula.close()
