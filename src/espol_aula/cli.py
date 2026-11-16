"""`espol`: your ESPOL aula virtual (Canvas) from the terminal, or as tools for any agent.

    espol login                    paste a Canvas token; it is kept alive from then on
    espol estado                   the token, its renewal, and the last read of the aula
    espol renovar [--instalar | --quitar]
    espol logout
    espol mcp                      MCP server over stdio (Claude Code, Codex, Cursor...)
    espol skill                    print the Claude Code skill

    espol cursos | tareas | anuncios | notas | personas | mensajes | calendario | foros
    espol archivos [listar | bajar <id>... | leer <id> --paginas 3-5]
    espol enlaces | buscar "pregunta" | sincronizar [--material] | ocr

Human-readable Spanish by default; `--json` for agents. Reads only: the one write is the renewal
of the student's own token.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from importlib import resources

from espol_aula import Aula, materials, ocr, queries, renewal, schedule, search, timefmt
from espol_aula.canvas import CanvasError
from espol_aula.config import ConfigError, load_config, now_utc, secrets_path
from espol_aula.store import connect


class UsageError(Exception):
    pass


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="salida JSON (para agentes)")
    p.add_argument("--sin-actualizar", dest="offline", action="store_true", default=argparse.SUPPRESS,
                   help="usar solo los datos locales, sin leer el aula virtual")
    p.add_argument("--actualizar", dest="refresh", action="store_true", default=argparse.SUPPRESS,
                   help="leer el aula virtual aunque los datos locales sean recientes (si no puedo, es un error)")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(prog="espol", parents=[common],
                                     description="Tu aula virtual de ESPOL desde la terminal o desde tu agente.")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="comando")

    p = sub.add_parser("login", parents=[common], help="guardar un token de Canvas y mantenerlo vivo")
    p.add_argument("--stdin", action="store_true", help="leer el token de la entrada estándar en vez de preguntarlo")
    p.add_argument("--sin-programar", dest="no_schedule", action="store_true",
                   help="no instalar la renovación automática cada 10 minutos")
    sub.add_parser("logout", parents=[common], help="borrar tus tokens del aula y de esta máquina")
    sub.add_parser("estado", parents=[common], help="cómo está el token y su renovación")
    p = sub.add_parser("renovar", parents=[common], help="renovar el token ahora (lo que corre cada 10 minutos)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--instalar", dest="install", action="store_true", help="programarla cada 10 minutos")
    group.add_argument("--quitar", dest="uninstall", action="store_true", help="dejar de programarla")
    sub.add_parser("mcp", help="servidor MCP por stdio, para conectar tu agente")
    sub.add_parser("skill", help="imprimir la skill para Claude Code")

    sub.add_parser("cursos", parents=[common], help="materias activas")

    p = sub.add_parser("tareas", parents=[common], help="tareas pendientes, por fecha de entrega")
    p.add_argument("--curso", dest="course", help="parte del nombre o código de la materia")
    p.add_argument("--dias", dest="days", type=int, help="solo las que vencen en los próximos N días")

    p = sub.add_parser("anuncios", parents=[common], help="anuncios recientes")
    p.add_argument("--curso", dest="course")
    p.add_argument("-n", dest="limit", type=int, default=10, help="cuántos (por defecto 10)")

    p = sub.add_parser("notas", parents=[common], help="calificaciones publicadas")
    p.add_argument("--curso", dest="course")

    p = sub.add_parser("personas", parents=[common], help="profesores, ayudantes y tus grupos")
    p.add_argument("--curso", dest="course")

    p = sub.add_parser("mensajes", parents=[common], help="mensajes de los profesores en el buzón del aula")
    p.add_argument("--curso", dest="course")
    p.add_argument("-n", dest="limit", type=int, default=10, help="cuántos (por defecto 10)")

    p = sub.add_parser("calendario", parents=[common], help="eventos del calendario y revisiones entre pares")
    p.add_argument("--curso", dest="course")
    p.add_argument("--dias", dest="days", type=int, default=30, help="los próximos N días (por defecto 30)")

    p = sub.add_parser("foros", parents=[common], help="foros de discusión de tus materias")
    p.add_argument("--curso", dest="course")

    p = sub.add_parser("archivos", parents=[common], help="catálogo del material: listar, bajar, leer")
    p.add_argument("action", nargs="?", choices=["listar", "bajar", "leer"], default="listar")
    p.add_argument("ids", nargs="*", type=int, help="ID de archivo (de `espol archivos`)")
    p.add_argument("--curso", dest="course")
    p.add_argument("--nombre", dest="name", help="parte del nombre, módulo, sección o carpeta (ej. 'semana 3')")
    p.add_argument("--paginas", dest="pages", help="para leer: rango de páginas, ej. 3-5")

    p = sub.add_parser("enlaces", parents=[common], help="enlaces de fuera del aula (Dropbox, SharePoint, videos)")
    p.add_argument("--curso", dest="course")
    p.add_argument("--nombre", dest="name", help="parte del título, módulo o dirección")

    p = sub.add_parser("buscar", parents=[common], help="buscar en el material descargado")
    p.add_argument("question", metavar="pregunta")
    p.add_argument("--curso", dest="course")
    p.add_argument("-n", dest="limit", type=int, default=5)

    p = sub.add_parser("sincronizar", parents=[common], help="leer el aula virtual ahora")
    p.add_argument("--material", action="store_true", help="también bajar e indexar el sílabo nuevo de cada curso")

    p = sub.add_parser("ocr", parents=[common], help="leer con OCR las páginas escaneadas que faltan (usa tesseract)")
    p.add_argument("--minutos", dest="minutes", type=float, help="parar después de N minutos (por defecto, hasta terminar)")
    return parser


def _out(data, as_json: bool, render) -> None:
    if as_json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        text = render(data)
        sys.stdout.write(text.rstrip() + "\n")


def _size(value) -> str:
    if not value:
        return ""
    return f"{value / 1024 / 1024:.1f} MB" if value >= 1024 * 1024 else f"{max(1, round(value / 1024))} KB"


def render_courses(rows) -> str:
    if not rows:
        return "No tienes materias activas."
    lines = []
    for c in rows:
        score = f" · nota actual {c['current_score']:g}" if c["current_score"] is not None else ""
        lines.append(f"• {c['name']} ({c['code'] or 's/c'}){score}\n  {c['url']}")
    return "\n".join(lines)


def render_tasks(rows, aula: Aula) -> str:
    if not rows:
        return "No tienes tareas pendientes. 🎉"
    now, tz = aula.now(), aula.cfg.tz
    lines = []
    for t in rows:
        if t["due"]:
            when = f"{timefmt.human(t['due'], tz)} ({timefmt.until(t['due'], now)})"
        else:
            when = "sin fecha de entrega"
        flags = []
        if t["late"]:
            flags.append("ATRASADA")
        if t["delivery"] != queries.CANVAS_SUBMISSION:
            flags.append(queries.DELIVERY_LABEL[t["delivery"]])
        flag = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"• {when} — {t['course']}: {t['assignment']}{flag}\n  {t['url']}")
    return "\n".join(lines)


def render_announcements(rows, aula: Aula) -> str:
    if not rows:
        return "No hay anuncios."
    blocks = []
    for a in rows:
        text = (a["text"] or "").strip()
        if len(text) > 400:
            text = text[:400].rstrip() + "…"
        extra = "".join(f"\n  📎 {f['file']} (archivo {f['file_id']})" for f in a["files"]) + "".join(
            f"\n  🔗 {link['title']} ({link['kind']}; {_link_state(link)})" for link in a["links"])
        blocks.append(f"• {timefmt.human(a['posted'], aula.cfg.tz)} — {a['course']}: {a['title']}"
                      f"{' (' + a['author'] + ')' if a['author'] else ''}\n  {text}{extra}\n  {a['url']}")
    return "\n\n".join(blocks)


def render_people(rows) -> str:
    role = {"teacher": "profesor", "ta": "ayudante"}
    blocks = []
    for c in rows:
        lines = [c["course"]]
        lines += [f"  • {r['name']} ({role.get(r['role'], r['role'])})" + (f" · {r['email']}" if r["email"] else "")
                  for r in c["staff"]] or ["  (sin profesores visibles)"]
        lines += [f"  👥 {g['name']}: {', '.join(g['members']) or 'solo tú'}" for g in c["groups"]]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "No tienes materias activas."


def render_inbox(rows, aula: Aula) -> str:
    if not rows:
        return "No hay mensajes de profesores."
    return "\n\n".join(
        f"• {timefmt.human(m['at'], aula.cfg.tz)} — {m['course']}: {m['subject'] or '(sin asunto)'} ({m['author']})\n"
        f"  {m['text']}" + "".join(f"\n  📎 {f['name']}" for f in m["files"]) for m in rows)


def render_calendar(data, aula: Aula) -> str:
    lines = [f"• {timefmt.human(e['start'], aula.cfg.tz)} — {e['course']}: {e['title']}"
             + (f" · {e['location']}" if e["location"] else "") for e in data["events"]]
    lines += [f"• 🔍 {timefmt.human(r['due'], aula.cfg.tz) if r['due'] else 'sin fecha'} — {r['course']}: "
              f"revisar a un compañero en {r['title']}" for r in data["peer_reviews"]]
    return "\n".join(lines) or "No hay eventos en el calendario."


def render_discussions(rows, aula: Aula) -> str:
    if not rows:
        return "No hay foros."
    out = []
    for d in rows:
        facts = [f"vence {timefmt.human(d['due'], aula.cfg.tz)}" if d["due"] else "",
                 "calificado" if d["graded"] else "",
                 {True: "participaste", False: "no has participado", None: ""}[d["participated"]],
                 f"{d['replies_to_you']} respuesta(s) a ti" if d["replies_to_you"] else ""]
        out.append(f"• {d['course']}: {d['title']}" + "".join(f" · {f}" for f in facts if f) + f"\n  {d['url']}")
    return "\n".join(out)


def _grade(value, grade, points) -> str:
    if value is None:
        return grade or "—"
    score = f"{value:g}" + (f"/{points:g}" if points else "")
    return score + (f" ({grade})" if grade and grade != f"{value:g}" else "")


def render_grades(rows) -> str:
    if not rows:
        return "No hay materias."
    blocks = []
    for c in rows:
        head = f"{c['course']}"
        if c["current_score"] is not None:
            head += f" — nota actual {c['current_score']:g}"
        lines = [head]
        if not c["assignments"]:
            lines.append("  (sin notas publicadas)")
        for t in c["assignments"]:
            late = f" (−{t['late_penalty']:g} por atraso)" if t.get("late_penalty") else ""
            lines.append(f"  • {t['assignment']}: {_grade(t['score'], t['grade'], t['points'])}{late}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_files(rows, max_mb: float) -> str:
    if not rows:
        return "No hay archivos."
    lines = []
    current = None
    for f in rows:
        if f["course"] != current:
            current = f["course"]
            lines.append(f"{current}:")
        where = " · ".join(v for v in (f["module"], f["section"], f["folder"]) if v)
        where = f" [{where}]" if where else ""
        copy = f", copia de {f['copy_of']}" if f["copy_of"] else ""
        lines.append(f"  {f['id']}  {f['file']}{where} {_size(f['size'])} ({queries.file_state(f, max_mb)}{copy})")
    return "\n".join(lines)


def _link_state(r) -> str:
    if r["access"] != "publico":
        return f"solo enlace: {r['reason']}"
    if r["reason"]:
        return f"no se abre: {r['reason']}"
    return f"abierto, archivo {r['file_id']}" if r["file_id"] is not None else "un bot lo intenta sin tu cuenta"


def render_links(rows) -> str:
    if not rows:
        return "No hay enlaces."
    return "\n".join(f"• {r['course']}: {r['title'] or r['url']} ({r['kind']}; {_link_state(r)})"
                     f"{' [' + r['module'] + ']' if r['module'] else ''}\n  {r['url']}" for r in rows)


def render_downloads(rows) -> str:
    return "\n".join(f"✓ {r['file']} → {r['local_path']}" if r.get("local_path") else f"✗ {r['file']}: {r['error']}"
                     for r in rows) or "Nada que bajar."


def render_read(data) -> str:
    lines = [f"{data['file']} — {data['course']}", data["url"], ""]
    if not data["content"]:
        lines.append("(sin texto indexado; bájalo primero con `espol archivos bajar "
                     f"{data['id']}`)")
    for page in data["content"]:
        lines.append(f"--- {data['unit']} {page['page']} ---")
        lines.append(page["text"])
    return "\n".join(lines)


def render_search(rows) -> str:
    if not rows:
        return "No encontré nada en el material indexado."
    return "\n\n".join(
        f"{i}. {r['file']} — {r['unit']} {r['page']} ({r['course']})\n   {r['snippet']}\n   {r['url']}"
        for i, r in enumerate(rows, start=1)
    )


def render_sync(report) -> str:
    text = (f"Listo: {report['courses']} materias leídas, {report['changes']} cambios nuevos, "
            f"{report['requests']} consultas al aula virtual.")
    for w in report["warnings"]:
        text += f"\n⚠ {w}"
    return text


def render_ocr(data) -> str:
    if data["engine"] is None:
        return ("No hay motor de OCR: instala tesseract con su modelo en español (por ejemplo "
                "`sudo apt install tesseract-ocr tesseract-ocr-spa` o `brew install tesseract-lang`). Las páginas escaneadas siguen "
                f"como imagen; {data['pending']} esperan su OCR.")
    if data["read"] is None:
        return "Otro OCR está corriendo; prueba en unos minutos."
    return (f"Leí {data['read']} página(s) escaneada(s) con OCR ({data['engine']})"
            + (f"; faltan {data['pending']}." if data["pending"] else "; no falta ninguna."))




def render_status(data) -> str:
    lines = []
    if not data["token_saved"]:
        return "No tienes un token guardado. Corre `espol login`."
    if data["chain_cut"]:
        lines.append("❌ El aula rechazó tu token: la cadena se cortó. Crea uno nuevo y corre `espol login`.")
    elif data["server_fixed"]:
        lines.append("✓ Token guardado. El aula ya no vence los tokens a la hora: no hace falta renovarlo.")
    elif data["renewed_at"]:
        lines.append(f"✓ Token renovado {timefmt.human(data['renewed_at'], data['tz'])}; se renueva a los 40 minutos.")
    else:
        lines.append("⚠ Token guardado, pero todavía no se ha renovado nunca: corre `espol renovar`.")
    lines.append(f"Renovación automática: {data['schedule'] or 'no instalada (`espol renovar --instalar`)'}")
    lines.append(f"Última lectura del aula: {timefmt.human(data['last_sync'], data['tz']) if data['last_sync'] else 'nunca'}")
    return "\n".join(lines)


def render_renewal(result: renewal.RenewalResult | None) -> str:
    if result is None:
        return "Otra renovación está corriendo ahora mismo."
    if result.chain_cut:
        return "❌ El aula rechazó tu token: la cadena se cortó. Crea uno nuevo y corre `espol login`."
    if result.server_fixed:
        return "✓ El aula ya no vence los tokens a la hora; la renovación se apagó sola."
    if result.renewal_error:
        return f"Token guardado, pero renovarlo falló esta vez ({result.renewal_error}). Se reintenta en la próxima pasada."
    return "✓ Token renovado." if result.renewed else "✓ El token sigue fresco; no hizo falta renovarlo."


def _token_saved() -> bool:
    try:
        return bool(renewal.load_secret_values().get("CANVAS_TOKEN"))
    except ConfigError:
        return False


def _keep_alive(cfg) -> None:
    """Every command renews the token when it is due, so a student who uses it often never loses the chain."""
    if not _token_saved():
        return
    try:
        renewal.maintain(cfg, now_utc(), wait=False)
    except renewal.RenewalError as exc:
        print(f"⚠ No pude renovar el token ({exc}).", file=sys.stderr)


def _refresh(aula: Aula, args) -> None:
    if getattr(args, "offline", False):
        return
    if getattr(args, "refresh", False):
        aula.sync()  # fresh data was asked for: the saved copy is no answer
        return
    try:
        aula.ensure_fresh()
    except (CanvasError, ConfigError) as exc:
        if queries.courses(aula.conn):
            print(f"⚠ No pude actualizar desde el aula virtual ({exc}); muestro los últimos datos guardados.",
                  file=sys.stderr)
        else:
            raise


def _parse_range(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    first, _, last = value.partition("-")
    return int(first), int(last or first)


def account(args) -> int:
    """login, logout, estado and renovar: the token and its renewal."""
    as_json = getattr(args, "json", False)
    cfg = load_config()
    if args.cmd == "login":
        token = sys.stdin.readline() if args.stdin else getpass.getpass(
            "Pega tu token de Canvas (aulavirtual → Cuenta → Configuraciones → + Nuevo token de acceso): ")
        result = renewal.reseed(cfg, token, now_utc())
        note = "" if args.no_schedule else "\n" + schedule.install()
        aula = Aula(cfg=cfg, explicit=True)
        try:
            courses = aula.sync().courses
        except CanvasError as exc:
            courses, note = None, note + f"\n⚠ No pude leer el aula todavía ({exc})."
        finally:
            aula.close()
        read = f"\n✓ Leí tus {courses} materias." if courses is not None else ""
        _out({"saved": True, "renewed": result.renewed, "renewal_error": result.renewal_error or None,
              "courses": courses}, as_json,
             lambda d: f"✓ Token verificado y guardado en {secrets_path()}.\n{render_renewal(result)}{read}{note}")
        return 0
    if args.cmd == "logout":
        deleted = renewal.revoke(cfg, now_utc()) if _token_saved() else 0
        secrets_path().unlink(missing_ok=True)
        removed = schedule.uninstall()
        _out({"revoked": deleted}, as_json,
             lambda d: f"Listo: borré {deleted} token(s) del aula y el token de esta máquina. {removed}")
        return 0
    if args.cmd == "renovar":
        if args.install:
            message = schedule.install()
            _out({"schedule": schedule.installed()}, as_json, lambda d: message)
            return 0
        if args.uninstall:
            message = schedule.uninstall()
            _out({"schedule": None}, as_json, lambda d: message)
            return 0
        result = renewal.maintain(cfg, now_utc())
        _out({"renewed": bool(result and result.renewed), "chain_cut": bool(result and result.chain_cut)},
             as_json, lambda d: render_renewal(result))
        return 1 if result and result.chain_cut else 0
    conn = connect(cfg.db_path)
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    saved = _token_saved()
    fingerprint = renewal.token_fingerprint(renewal.load_secret_values().get("CANVAS_TOKEN", "")) if saved else None
    data = {"token_saved": saved,
            "chain_cut": saved and meta.get(renewal.REFUSED_KEY) == fingerprint,
            "server_fixed": meta.get(renewal.DISABLED) == "server-fixed",
            "renewed_at": meta.get(renewal.CURRENT_CREATED) if meta.get(renewal.CURRENT_FINGERPRINT) == fingerprint
            else None,
            "schedule": schedule.installed(), "last_sync": meta.get("last_sync")}
    _out(data, as_json, lambda d: render_status({**d, "tz": cfg.tz}))
    return 0


def run(args, aula: Aula) -> int:
    as_json = getattr(args, "json", False)
    cmd = args.cmd

    if cmd == "sincronizar":
        report = aula.sync(materials=args.material)
        data = {"courses": report.courses, "changes": report.events, "requests": report.requests,
                "first_sync": report.first_sync, "warnings": report.warnings}
        _out(data, as_json, render_sync)
        return 0

    if cmd == "ocr":
        materials.backfill(aula.conn)  # what was indexed before OCR existed
        eng = ocr.engine(aula.cfg.data_dir)
        read = aula.read_scans(seconds=args.minutes * 60 if args.minutes else None) if eng else 0
        data = {"engine": f"tesseract, {eng.languages}" if eng else None, "read": read,
                "pending": queries.ocr_waiting(aula.conn)}
        _out(data, as_json, render_ocr)
        return 0

    if cmd != "buscar" and not (cmd == "archivos" and args.action == "leer"):
        _refresh(aula, args)
    conn = aula.conn
    ids = queries.course_ids(conn, getattr(args, "course", None))

    if cmd == "cursos":
        _out(queries.courses(conn), as_json, render_courses)
    elif cmd == "tareas":
        _out(queries.pending(conn, aula.now(), ids, days=args.days), as_json, lambda r: render_tasks(r, aula))
    elif cmd == "anuncios":
        _out(queries.announcements(conn, ids, limit=args.limit), as_json, lambda r: render_announcements(r, aula))
    elif cmd == "notas":
        _out(queries.grades(conn, ids), as_json, render_grades)
    elif cmd == "personas":
        _out(queries.people(conn, ids), as_json, render_people)
    elif cmd == "mensajes":
        _out(queries.inbox(conn, ids, limit=args.limit), as_json, lambda r: render_inbox(r, aula))
    elif cmd == "calendario":
        _out(queries.calendar(conn, aula.now(), ids, days=args.days), as_json, lambda r: render_calendar(r, aula))
    elif cmd == "foros":
        _out(queries.discussions(conn, ids), as_json, lambda r: render_discussions(r, aula))
    elif cmd == "enlaces":
        _out(queries.links(conn, ids, args.name), as_json, render_links)
    elif cmd == "buscar":
        _out(search.search(conn, args.question, course_ids=ids, limit=args.limit), as_json, render_search)
    elif cmd == "archivos":
        if args.action == "listar":
            _out(queries.files(conn, ids, args.name), as_json, lambda r: render_files(r, aula.cfg.max_file_mb))
        elif args.action == "leer":
            if len(args.ids) != 1:
                raise UsageError("Uso: espol archivos leer <id> [--paginas 3-5]")
            first, last = _parse_range(args.pages)
            _out(queries.read_pages(conn, args.ids[0], first, last), as_json, render_read)
        else:
            targets = [{"id": i} for i in args.ids] if args.ids else (
                queries.files(conn, ids, args.name) if (args.course or args.name) else [])
            if not targets:
                raise UsageError("Uso: espol archivos bajar <id>...  o  espol archivos bajar --curso X --nombre Y")
            results = download(aula, [f["id"] for f in targets])
            _out(results, as_json, render_downloads)
            return 0 if all(r.get("local_path") for r in results) else 1
    return 0


def download(aula: Aula, file_ids: list[int]) -> list[dict]:
    results = []
    for file_id in file_ids:
        try:
            f = queries.file_by_id(aula.conn, file_id)
        except queries.NotFound as exc:  # an unknown id does not stop the others
            results.append({"id": file_id, "file": str(file_id), "local_path": None, "error": str(exc)})
            continue
        try:
            path = aula.download(file_id)
            results.append({**queries.file_by_id(aula.conn, file_id), "local_path": str(path)})
        except CanvasError as exc:
            results.append({**f, "local_path": None, "error": str(exc)})
    return results


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # a retry or a hidden course is not the student's business; the MCP server keeps them on stderr
    logging.basicConfig(level=logging.WARNING if args.cmd == "mcp" else logging.ERROR, format="%(message)s")
    if args.cmd == "mcp":
        from espol_aula import tools
        return tools.serve()
    if args.cmd == "skill":
        sys.stdout.write(resources.files("espol_aula").joinpath("SKILL.md").read_text(encoding="utf-8"))
        return 0
    aula = None
    try:
        if args.cmd in ("login", "logout", "estado", "renovar"):
            return account(args)
        aula = Aula(explicit=True)
        _keep_alive(aula.cfg)
        return run(args, aula)
    except (ConfigError, CanvasError, queries.NotFound, UsageError, renewal.RenewalError) as exc:
        if getattr(args, "json", False):  # an agent parses stdout: the error comes as JSON too
            _out({"error": str(exc)}, True, None)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if aula is not None:
            aula.close()


if __name__ == "__main__":
    raise SystemExit(main())
