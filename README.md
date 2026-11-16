# espol-aula

Tu aula virtual de ESPOL (Canvas) en la terminal, y como herramientas para el agente que tú uses: Claude Code,
Codex, Cursor o cualquiera que hable MCP. Lee tus tareas, anuncios, notas, mensajes, calendario, foros y el
material de cada materia, baja los archivos y busca dentro de ellos.

Solo lee. No entrega, no publica y no cambia nada en el aula. Lo único que escribe es la renovación de tu propio
token (abajo explico por qué).

## Instalar

Necesitas [uv](https://docs.astral.sh/uv/). Funciona en Linux y macOS; en Windows, dentro de WSL.

```bash
uv tool install espol-aula
```

## Conectar tu cuenta

1. En el aula virtual: **Cuenta → Configuraciones → + Nuevo token de acceso**. Ponle cualquier propósito y copia
   el token.
2. Pégalo aquí (no se ve mientras lo pegas):

   ```bash
   espol-aula login
   ```

Los tokens del aula de ESPOL vencen a la hora. `espol-aula` crea uno nuevo antes de que venza el anterior y borra
el viejo, y `login` deja programada esa renovación cada 10 minutos (un timer de systemd en Linux, un LaunchAgent en
macOS). Si tu computadora pasa más de una hora apagada o dormida, la cadena se corta: creas otro token y corres
`espol-aula login` de nuevo. `espol-aula estado` te dice cómo está.

Tu token vive en `~/.config/espol-aula/token.env` (solo tú puedes leerlo) y nunca sale de tu computadora, salvo
hacia el aula. `espol-aula logout` lo borra del aula y de tu máquina.

## Usarlo

```bash
espol-aula tareas --dias 7          # lo que vence esta semana
espol-aula anuncios --curso calculo
espol-aula notas
espol-aula archivos --curso fisica --nombre "semana 3"
espol-aula archivos bajar 12345
espol-aula buscar "regla de la cadena"
```

Cada comando acepta `--json` para agentes. `espol-aula --help` lista todo.

## Conectarlo a tu agente

Como servidor MCP, por stdio:

```bash
claude mcp add espol-aula -- espol-aula mcp          # Claude Code
codex mcp add espol-aula -- espol-aula mcp           # Codex
```

En Cursor u otro cliente, agrega un servidor MCP con el comando `espol-aula` y el argumento `mcp`.

Si prefieres que tu agente use la CLI, hay una skill para Claude Code:

```bash
mkdir -p ~/.claude/skills/espol-aula && espol-aula skill > ~/.claude/skills/espol-aula/SKILL.md
```

## Configuración

Todo tiene un valor por defecto. Para cambiar algo, crea `~/.config/espol-aula/config.toml`:

```toml
[canvas]
url = "https://aulavirtual.espol.edu.ec"
cache_minutes = 10            # cuánto dura la copia local antes de volver a leer el aula

[material]
max_file_mb = 200             # no baja archivos más grandes

[storage]
data_dir = "~/.local/share/espol-aula"   # la copia local y el material bajado
```

Las páginas escaneadas se leen con OCR si tienes `tesseract` con el idioma español instalado
(`espol-aula ocr`).

## Licencia

MIT.
