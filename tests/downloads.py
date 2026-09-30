"""Download tracking: the delivery counts, and only the delivery.

Covers:

* a full download (OPDS and panel) increments the count and the last date;
* a bounded Range (``bytes=0-99``) is a partial, not a download, and does not
  count; an open-ended Range (``bytes=100-``) does;
* concurrent attempts and recovery of an attempt killed mid-flight;
* the states (available/downloaded/blocked) and the cleanup candidates.

Run with:  python tests/downloads.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_downloads_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "downloads-secret"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["REQUIRE_AUTH_PANEL"] = "false"
os.environ["BASE_URL"] = "http://testserver"

CONTENT = b"EPUB" + bytes(range(256)) * 20  # a few KB, enough for ranges

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def _make_book() -> str:
    from app.config import get_settings
    from app.database import session_scope
    from app.database.models import Book, ContentType

    folder = get_settings().library_dir / "testes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "livro.epub").write_bytes(CONTENT)
    with session_scope() as session:
        session.add(
            Book(
                id="bkdwn00000000000000000000000001",
                title="Livro de teste",
                format="epub",
                media_type="application/epub+zip",
                content_type=ContentType.EBOOK.value,
                file_path="testes/livro.epub",
                file_size=len(CONTENT),
            )
        )
    return "bkdwn00000000000000000000000001"


def main() -> int:
    from app.database import init_db, session_scope
    from app.database.models import (
        Book,
        DownloadEvent,
        DownloadStatus,
        FileState,
    )
    from app.downloads import store as downloads

    init_db()
    book_id = _make_book()

    print("\n[cliente e Range]")
    check("KOReader reconhecido", downloads.client_from_ua("KOReader/2024.1")[0] == "KOReader")
    check("CrossPoint reconhecido", downloads.client_from_ua("CrossPoint x")[0] == "CrossPoint")
    check("sem UA não inventa cliente", downloads.client_from_ua(None) == (None, None))
    check("hash do cliente é curto e não é o UA", downloads.client_from_ua("KOReader")[1] != "KOReader")
    check("range aberto conta", downloads.range_flags("bytes=100-") == (True, True, True))
    check("range fechado NÃO conta", downloads.range_flags("bytes=0-99") == (True, False, False))
    check("sem range conta", downloads.range_flags(None) == (False, False, True))
    check("multi-range não conta", downloads.range_flags("bytes=0-1,5-6")[2] is False)

    print("\n[entrega via HTTP conta o download]")
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.security import setup as setup_state

    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/setup",
            data={
                "username": "admin",
                "password": "test-password",
                "confirm_password": "test-password",
                "token": setup_state.token(),
            },
        )

        response = client.get(f"/opds/download/{book_id}")
        check(
            "OPDS entrega o arquivo",
            response.status_code == 200 and response.content == CONTENT,
            f"{response.status_code} {len(response.content)}",
        )
        with session_scope() as session:
            record = downloads.get_record(session, book_id)
            check("contador = 1 após entrega", record.download_count == 1, str(record.download_count))
            check("estado vira 'baixado anteriormente'", record.state == FileState.DOWNLOADED.value, record.state)
            check("nada em andamento", record.active_downloads == 0, str(record.active_downloads))
            check("último download gravado", record.last_download_at is not None)

        response = client.get(f"/library/{book_id}/file")
        check("painel também entrega", response.status_code == 200 and response.content == CONTENT)
        with session_scope() as session:
            check("contador = 2", downloads.get_record(session, book_id).download_count == 2)

        response = client.get(f"/opds/download/{book_id}", headers={"Range": "bytes=0-99"})
        check(
            "range fechado responde 206 com 100 bytes",
            response.status_code == 206 and len(response.content) == 100,
            f"{response.status_code} {len(response.content)}",
        )
        with session_scope() as session:
            check("range fechado não incrementa", downloads.get_record(session, book_id).download_count == 2)

        response = client.get(f"/opds/download/{book_id}", headers={"Range": "bytes=100-"})
        check(
            "range aberto responde o restante",
            response.status_code == 206 and len(response.content) == len(CONTENT) - 100,
            f"{response.status_code} {len(response.content)}",
        )
        with session_scope() as session:
            check("range aberto incrementa", downloads.get_record(session, book_id).download_count == 3)

        response = client.get("/api/downloads")
        check(
            "API lista o arquivo rastreado",
            response.status_code == 200
            and any(item.get("book_id") == book_id for item in response.json()["items"]),
            str(response.status_code),
        )

        before = client.get("/opds/all?per_page=100")
        check("antes de bloquear, aparece no OPDS", book_id in before.text)

        response = client.post(f"/api/downloads/book/{book_id}/block")
        check(
            "API bloqueia",
            response.status_code == 200 and response.json()["state"] == FileState.BLOCKED.value,
            f"{response.status_code} {response.text[:80]}",
        )
        with session_scope() as session:
            check("bloqueado não é oferecido", downloads.should_offer(session, book_id) is False)
        check("bloqueado some do catálogo OPDS 1.2", book_id not in client.get("/opds/all?per_page=100").text)
        check("bloqueado some do OPDS 2.0", book_id not in client.get("/opds/v2/all?per_page=100").text)
        check(
            "download bloqueado é recusado",
            client.get(f"/opds/download/{book_id}").status_code == 404,
        )
        check(
            "ficha OPDS 2.0 do bloqueado some",
            client.get(f"/opds/v2/books/{book_id}").status_code == 404,
        )
        check("o painel continua mostrando o livro", "Livro de teste" in client.get("/library").text)

        client.post(f"/api/downloads/book/{book_id}/unblock")
        with session_scope() as session:
            check("desbloqueado volta a ser oferecido", downloads.should_offer(session, book_id) is True)
        check("desbloqueado volta ao catálogo", book_id in client.get("/opds/all?per_page=100").text)
        check(
            "download volta a responder",
            client.get(f"/opds/download/{book_id}", headers={"Range": "bytes=0-0"}).status_code == 206,
        )
        check(
            "botão do painel bloqueia",
            client.post(
                f"/library/{book_id}/download-state", data={"action": "block"}
            ).status_code in (200, 303),
        )
        with session_scope() as session:
            check("painel: bloqueio gravado", downloads.get_record(session, book_id).state == FileState.BLOCKED.value)
        client.post(f"/library/{book_id}/download-state", data={"action": "unblock"})

    print("\n[downloads simultâneos]")
    with session_scope() as session:
        book = session.get(Book, book_id)
        first = downloads.start_download(session, book, user_agent="KOReader")
        second = downloads.start_download(session, book, user_agent="CrossPoint")
    with session_scope() as session:
        check("dois em andamento", downloads.get_record(session, book_id).active_downloads == 2)
    downloads.finish_download(first.id, status=DownloadStatus.COMPLETED.value, bytes_sent=10, bytes_total=10)
    with session_scope() as session:
        record = downloads.get_record(session, book_id)
        check("um terminou, o outro continua", record.active_downloads == 1, str(record.active_downloads))
        check("o concluído somou", record.download_count == 4, str(record.download_count))
    downloads.finish_download(second.id, status=DownloadStatus.INTERRUPTED.value, bytes_sent=2)
    with session_scope() as session:
        record = downloads.get_record(session, book_id)
        check("interrompido não soma", record.download_count == 4, str(record.download_count))
        check("nada em andamento", record.active_downloads == 0, str(record.active_downloads))

    print("\n[recuperação de download morto]")
    with session_scope() as session:
        book = session.get(Book, book_id)
        track = downloads.start_download(session, book, user_agent="KOReader")
    with session_scope() as session:
        check("iniciado fica ativo", downloads.get_record(session, book_id).active_downloads == 1)
        recovered = downloads.recover_interrupted(session, older_than_seconds=0)
    check("recuperação fechou o registro", recovered >= 1, str(recovered))
    with session_scope() as session:
        check("nada mais ativo após recuperar", downloads.get_record(session, book_id).active_downloads == 0)
        event = session.get(DownloadEvent, track.id)
        check("evento vira interrompido", event.status == DownloadStatus.INTERRUPTED.value, event.status)

    print("\n[estados e limpeza]")
    with session_scope() as session:
        downloads.mark_downloaded(session, book_id)
        candidates = downloads.cleanup_candidates(session)
        check("baixado vira candidato à limpeza", any(c.book_id == book_id for c in candidates), str(len(candidates)))
        check("histórico preservado", bool(session.query(DownloadEvent).count()))

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
