# espol

Tu aula virtual de ESPOL (Canvas) en la terminal, y como herramientas para el agente que tú uses: Claude Code,
Codex, Cursor o cualquiera que hable MCP. Lee tus tareas, anuncios, notas, mensajes, calendario, foros y el
material de cada materia, baja los archivos y busca dentro de ellos.

Solo lee. No entrega, no publica y no cambia nada en el aula. Lo único que escribe es la renovación de tu propio
token (abajo explico por qué).

## Instalar

Necesitas [uv](https://docs.astral.sh/uv/). Funciona en Linux y macOS; en Windows, dentro de WSL.

```bash
uv tool install espol
```

## Conectar tu cuenta

1. En el aula virtual: **Cuenta → Configuraciones → + Nuevo token de acceso**. Ponle cualquier propósito y copia
   el token.
2. Pégalo aquí (no se ve mientras lo pegas):

   ```bash
   espol login
   ```

Los tokens del aula de ESPOL vencen a la hora. `espol` crea uno nuevo antes de que venza el anterior y borra
el viejo, y `login` deja programada esa renovación cada 10 minutos (un timer de systemd en Linux, un LaunchAgent en
macOS). Si tu computadora pasa más de una hora apagada o dormida, la cadena se corta: creas otro token y corres
`espol login` de nuevo. `espol estado` te dice cómo está.

Tu token vive en `~/.config/espol/token.env` (solo tú puedes leerlo) y nunca sale de tu computadora, salvo
hacia el aula. `espol logout` lo borra del aula y de tu máquina.

## Usarlo

```bash
espol tareas --dias 7          # lo que vence esta semana
espol anuncios --curso calculo
espol notas
espol archivos --curso fisica --nombre "semana 3"
espol archivos bajar 12345
espol buscar "regla de la cadena"
```

Cada comando acepta `--json` para agentes. `espol --help` lista todo.

## Conectarlo a tu agente

Como servidor MCP, por stdio:

```bash
claude mcp add espol -- espol mcp               # Claude Code
claude mcp add espol -- uvx espol mcp           # o sin instalar nada
codex mcp add espol -- espol mcp           # Codex
```

En Cursor u otro cliente, agrega un servidor MCP con el comando `espol` y el argumento `mcp`.

Si prefieres que tu agente use la CLI, hay una skill para Claude Code:

```bash
mkdir -p ~/.claude/skills/espol && espol skill > ~/.claude/skills/espol/SKILL.md
```

## Configuración

Todo tiene un valor por defecto. Para cambiar algo, crea `~/.config/espol/config.toml`:

```toml
[canvas]
url = "https://aulavirtual.espol.edu.ec"
cache_minutes = 10            # cuánto dura la copia local antes de volver a leer el aula

[material]
max_file_mb = 200             # no baja archivos más grandes

[storage]
data_dir = "~/.local/share/espol"   # la copia local y el material bajado
```

Las páginas escaneadas se leen con OCR si tienes `tesseract` con el idioma español instalado
(`espol ocr`).

## Licencia

MIT.
