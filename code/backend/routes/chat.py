from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import json
import shutil
from typing import List, Optional, AsyncGenerator
from datetime import datetime

router = APIRouter()

from backend.config import Config
config: Optional[Config] = None

def init_router(cfg: Config):
    global config
    config = cfg


class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = "phi3.5:latest"


def get_vault_context() -> str:
    """Bouw een rijke context string met live vault data."""
    lines = [f"Datum vandaag: {datetime.now().strftime('%d %B %Y, %H:%M')}"]

    if not config:
        return '\n'.join(lines)

    # Mappen
    if config.VAULT_ROOT.exists():
        try:
            excluded = {config.DUPLICATES_FOLDER.resolve(), config.CACHE_FOLDER.resolve()}
            folders = [
                e.name for e in sorted(config.VAULT_ROOT.iterdir())
                if e.is_dir() and not e.name.startswith('.') and e.resolve() not in excluded
            ]
            if folders:
                lines.append(f"Mappen in archief ({len(folders)}): {', '.join(folders[:25])}")
        except Exception:
            pass

        # Opslag
        try:
            total, used, free = shutil.disk_usage(config.VAULT_ROOT)
            lines.append(
                f"Opslag schijf: {round(used / 2**30, 1)} GB gebruikt, "
                f"{round(free / 2**30, 1)} GB vrij (totaal {round(total / 2**30, 1)} GB)"
            )
        except Exception:
            pass

    # Spotify top artiesten
    try:
        from backend.routes.spotify import parse_spotify_data
        data = parse_spotify_data(None, None)
        top_artists = data.get('top_artists', [])[:5]
        if top_artists:
            artist_list = ', '.join(a['name'] for a in top_artists)
            total_min = round(data.get('total_ms', 0) / 60000)
            lines.append(f"Spotify top artiesten: {artist_list}")
            if total_min > 0:
                lines.append(f"Totaal Spotify luistertijd: {total_min:,} minuten")
    except Exception:
        pass

    return '\n'.join(lines)


def build_system_prompt() -> str:
    vault_context = get_vault_context()
    return f"""Je bent Pasha, de persoonlijke AI assistent van DataVault (een lokaal media-archief).
Antwoord altijd kort en bondig in het Nederlands. Je bent vriendelijk en behulpzaam.

=== ACTUELE VAULT DATA ===
{vault_context}
=========================

Je kunt de app besturen met een <<ACTION>> tag op de LAATSTE REGEL van je antwoord.
Gebruik dit ALLEEN als de gebruiker er expliciet om vraagt of navigatie logisch is.

Beschikbare acties:
- Map openen in viewer:    <<ACTION: {{"action": "open_folder", "target": "mapnaam"}}>>
- Foto's op datum tonen:  <<ACTION: {{"action": "show_photos", "date": "YYYY-MM-DD"}}>>
- Specifiek bestand:       <<ACTION: {{"action": "open_file", "path": "/volledig/pad"}}>>
- Tab wisselen:            <<ACTION: {{"action": "open_tab", "target": "viewer"}}>> (ook: spotify, shows, home)
- Duplicaten scannen:      <<ACTION: {{"action": "scan_duplicates"}}>>

Gewone vragen beantwoord je zonder actie. Geef nooit een <<ACTION>> tag tenzij de gebruiker iets wil openen/navigeren."""


async def stream_ollama(ollama_messages: list, model: str) -> AsyncGenerator[str, None]:
    """Stream Ollama responses als SSE, filtert <think> blokken live."""
    payload = {
        "model": model,
        "messages": ollama_messages,
        "stream": True,
        "options": {"num_ctx": 4096}
    }

    buffer = ""
    in_think = False

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", "http://localhost:11434/api/chat",
                json=payload, timeout=120.0
            ) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        done = chunk.get("done", False)

                        if token:
                            buffer += token

                            # Filter <think> blokken live uit de stream
                            while True:
                                if in_think:
                                    end_idx = buffer.find("</think>")
                                    if end_idx != -1:
                                        buffer = buffer[end_idx + 8:]
                                        in_think = False
                                    else:
                                        # Nog in think blok, alles weggooien behalve staart
                                        buffer = buffer[-15:]
                                        break
                                else:
                                    start_idx = buffer.find("<think>")
                                    if start_idx != -1:
                                        # Alles voor <think> is veilig om te sturen
                                        safe = buffer[:start_idx]
                                        if safe:
                                            yield f"data: {json.dumps({'token': safe})}\n\n"
                                        buffer = buffer[start_idx + 7:]
                                        in_think = True
                                    else:
                                        # Geen think tag zichtbaar — stuur alles behalve laatste 15 chars
                                        # (om partiële tags niet te missen)
                                        if len(buffer) > 15:
                                            safe = buffer[:-15]
                                            yield f"data: {json.dumps({'token': safe})}\n\n"
                                            buffer = buffer[-15:]
                                        break

                        if done:
                            # Flush rest van buffer (als niet in think)
                            if buffer and not in_think:
                                yield f"data: {json.dumps({'token': buffer})}\n\n"
                            yield f"data: {json.dumps({'done': True})}\n\n"

                    except json.JSONDecodeError:
                        continue

    except httpx.RequestError as e:
        yield f"data: {json.dumps({'error': f'Kan Ollama niet bereiken: {e}'})}\n\n"


@router.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint — tokens worden direct verstuurd zodra ze beschikbaar zijn."""
    system_prompt = build_system_prompt()
    ollama_messages = [{"role": "system", "content": system_prompt}]
    for msg in request.messages:
        ollama_messages.append({"role": msg.role, "content": msg.content})

    return StreamingResponse(
        stream_ollama(ollama_messages, request.model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@router.post("/api/chat")
async def chat_with_ollama(request: ChatRequest):
    """Non-streaming fallback endpoint."""
    system_prompt = build_system_prompt()
    ollama_messages = [{"role": "system", "content": system_prompt}]
    for msg in request.messages:
        ollama_messages.append({"role": msg.role, "content": msg.content})

    payload = {
        "model": request.model,
        "messages": ollama_messages,
        "stream": False,
        "options": {"num_ctx": 4096}
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:11434/api/chat", json=payload, timeout=120.0
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Ollama fout: {response.text}"
                )
            data = response.json()
            raw = data.get("message", {}).get("content", "")
            # Strip think blokken
            import re
            clean = re.sub(r'<think>[\s\S]*?</think>', '', raw, flags=re.IGNORECASE).strip()
            return {"response": clean}

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Kan Ollama niet bereiken: {e}. Draait Ollama?"
        )
