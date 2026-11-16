---
name: poli
description: The user's ESPOL aula virtual (Canvas, aulavirtual.espol.edu.ec) through the `poli` CLI - pending assignments by due date, announcements, grades, professors' messages, calendar, forums, course files (list, download, read page by page) and full-text search over downloaded material. Use when the user asks about their ESPOL courses, deadlines, "qué tengo pendiente", announcements, grades, or wants to study from course material ("bájate el PDF de la semana 3 de Cálculo y explícame el ejercicio 4").
---

# poli: the user's ESPOL aula virtual

`poli` only reads Canvas; nothing can submit, post or change anything there. Reply in Spanish unless the
user writes in another language. Always pass `--json` and parse the output.

| Need | Command |
|---|---|
| Active courses | `poli cursos --json` |
| Pending work, by due date | `poli tareas --json` (`--dias 7`, `--curso calculo`) |
| Announcements | `poli anuncios --json` (`--curso fisica -n 5`) |
| Grades, with feedback, rubric and late penalty | `poli notas --json` |
| Professors, assistants and the user's groups | `poli personas --json` |
| Professors' messages in the aula inbox | `poli mensajes --json` |
| Calendar events and peer reviews to do | `poli calendario --json` (`--dias 30`) |
| Forums | `poli foros --json` |
| Course files (the catalog, downloaded or not) | `poli archivos --json` (`--curso ... --nombre "semana 3"`) |
| Download and index files | `poli archivos bajar <id> [<id>...] --json` → `local_path` |
| Read an indexed file | `poli archivos leer <id> --paginas 3-6 --json` |
| Links outside the aula | `poli enlaces --json` |
| Search downloaded material | `poli buscar "regla de la cadena" --json` (`--curso ... -n 8`) |
| Read the aula now | `poli sincronizar --json` |
| Token state | `poli estado --json` |

`--curso` matches any fragment of a course's name or code, ignoring accents and case. Data refreshes on its own
when older than 10 minutes, falling back to the saved copy when the aula can't be read.

## Workflows

- **"¿Qué me falta entregar esta semana?"**: `poli tareas --dias 7 --json`; order by `due`, weigh `points`,
  flag `late` and `offline_only`, and link each `url`.
- **"Bájate el PDF de la semana 3 de Cálculo y explícame el ejercicio 4"**:
  `poli archivos --curso calculo --nombre "semana 3" --json` → `poli archivos bajar <id> --json` → open
  the file at `local_path` directly, or `poli archivos leer <id> --json`.
- **Study questions**: `poli buscar "<keywords>" --json`, answer from each hit's `text`, and cite `file`,
  `unit` + `page` and `url`. Search covers downloaded files only, so `archivos bajar` the ones on the topic first.
  Much material is in English: search with English keywords too.

## Limits

- Never try to submit, post or message on Canvas. If asked, tell the user to do it at the item's `url`.
- Never read `~/.config/poli/token.env` or call the Canvas API with curl; use `poli`.
- When a command says the token expired or the chain was cut, tell the user to create a new token in the aula
  and run `poli login` themselves: it asks for the token in a hidden prompt.
