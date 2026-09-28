"""Cooperative cancellation of running conversions.

The invariant under test: once a job is cancelled it must **never** publish an
output book -- whether the cancellation lands before it starts, in the middle of
the conversion, or after the file was already staged.

Run with:  python tests/cancellation.py
Uses a throwaway DATA_DIR so it never touches the real library.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_cancel_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["RSS_WORKER_ENABLED"] = "false"
os.environ["REQUIRE_AUTH_PANEL"] = "false"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


# --- fixtures -------------------------------------------------------------
def make_cbz(name: str, count: int = 24, size=(1200, 1800)) -> Path:
    from PIL import Image

    path = WORKDIR / name
    with zipfile.ZipFile(path, "w") as zf:
        for index in range(count):
            img = Image.new("RGB", size, (30 + index, 90, 140))
            page = WORKDIR / f"src_{index:03d}.png"
            img.save(page)
            zf.write(page, f"{index + 1:03d}.png")
            page.unlink()
    return path


def new_book(path: Path, book_id: str) -> str:
    """Register a Book row pointing at a copy inside the library dir."""
    from app.config import get_settings
    from app.database import session_scope
    from app.database.models import Book

    library = get_settings().library_dir
    library.mkdir(parents=True, exist_ok=True)
    target = library / f"{book_id}{path.suffix}"
    shutil.copy2(path, target)

    with session_scope() as session:
        session.add(
            Book(
                id=book_id,
                title="Teste de cancelamento",
                format=path.suffix.lstrip("."),
                content_type="comic",
                media_type="application/vnd.comicbook+zip",
                file_path=target.name,
                file_size=target.stat().st_size,
                source="upload",
            )
        )
        session.commit()
    return book_id


def make_running_job(book_id: str, *, target_format: str = "epub") -> str:
    """Enqueue a job and claim it, exactly as the worker loop would."""
    from app.database import session_scope
    from app.database.models import Book
    from app.workers import queue

    with session_scope() as session:
        book = session.get(Book, book_id)
        job = queue.enqueue(
            session,
            book,
            target_format=target_format,
            device_profile="xteink_x4_pro",
            keep_original=True,
        )
        job_id = job.id

    with session_scope() as session:
        claimed = queue.claim_next(session)
        assert claimed is not None and claimed.id == job_id, "outro job foi reivindicado"
    return job_id


def job_state(job_id: str) -> dict:
    from app.database import session_scope
    from app.database.models import ConversionJob

    with session_scope() as session:
        job = session.get(ConversionJob, job_id)
        return {
            "status": job.status,
            "message": job.message or "",
            "output_book_id": job.output_book_id,
        }


def is_cancelled(job_id: str) -> bool:
    from app.database import session_scope
    from app.workers import queue

    with session_scope() as session:
        return queue.is_cancelled(session, job_id)


def book_count() -> int:
    from sqlalchemy import func, select

    from app.database import session_scope
    from app.database.models import Book

    with session_scope() as session:
        return int(session.scalar(select(func.count(Book.id))) or 0)


class _FastReporterMixin:
    """Reports (and therefore checks for cancellation) on every tick."""

    def __init__(self, job_id, **kwargs):
        kwargs.setdefault("cancel_interval", 0.0)
        kwargs.setdefault("min_interval", 0.0)
        super().__init__(job_id, **kwargs)


# --- checks ---------------------------------------------------------------
def check_queue_semantics() -> str:
    from app.database import session_scope
    from app.database.models import Book, JobStatus
    from app.workers import queue

    book_id = new_book(make_cbz("small.cbz", 2, (120, 180)), "book-cancel-1")

    print("[fila: cancelar um job pendente]")
    with session_scope() as session:
        book = session.get(Book, book_id)
        job = queue.enqueue(session, book, target_format="epub", device_profile="eink_generic")
        pending_id = job.id
    with session_scope() as session:
        check("cancelar pendente devolve True", queue.cancel(session, pending_id))
    state = job_state(pending_id)
    check("pendente vira 'cancelled'", state["status"] == JobStatus.CANCELLED.value, state["status"])
    check("mensagem diz que nem começou", "antes de começar" in state["message"], state["message"])
    check("is_cancelled reconhece", is_cancelled(pending_id))
    with session_scope() as session:
        check("cancelar de novo não faz efeito", not queue.cancel(session, pending_id))

    print("\n[fila: cancelar um job em execução]")
    running_id = make_running_job(book_id)
    check(
        "job reivindicado está 'running'",
        job_state(running_id)["status"] == JobStatus.RUNNING.value,
    )
    with session_scope() as session:
        check("cancelar em execução devolve True", queue.cancel(session, running_id))
    check("is_cancelled reconhece em execução", is_cancelled(running_id))
    check(
        "mensagem fala em interromper",
        "interromp" in job_state(running_id)["message"],
        job_state(running_id)["message"],
    )

    print("\n[fila: nota final não ressuscita o job]")
    with session_scope() as session:
        queue.note_cancelled(session, running_id, "Cancelada — teste.")
    state = job_state(running_id)
    check("continua 'cancelled' após a nota", state["status"] == JobStatus.CANCELLED.value)
    check("nota final gravada", state["message"] == "Cancelada — teste.", state["message"])

    print("\n[fila: job concluído não pode ser cancelado]")
    with session_scope() as session:
        book = session.get(Book, book_id)
        job = queue.enqueue(session, book, target_format="epub", device_profile="eink_generic")
        done_id = job.id
    with session_scope() as session:
        queue.claim_next(session)
        queue.finish(session, done_id, output_book_id=None, output_path=None, message="ok")
    with session_scope() as session:
        check("cancelar concluído devolve False", not queue.cancel(session, done_id))
    check("concluído permanece concluído", job_state(done_id)["status"] == JobStatus.DONE.value)
    return book_id


def check_reporter_signal(book_id: str) -> None:
    from app.database import session_scope
    from app.workers import queue
    from app.workers.progress import JobCancelled, ProgressReporter

    print("\n[reporter: ponto de cancelamento cooperativo]")
    check(
        "JobCancelled escapa de 'except Exception'",
        issubclass(JobCancelled, BaseException) and not issubclass(JobCancelled, Exception),
    )

    job_id = make_running_job(book_id)
    reporter = ProgressReporter(job_id, cancel_interval=0.0, min_interval=0.0)
    reporter(10, "andando")
    check("job em execução não interrompe o reporter", True)

    with session_scope() as session:
        queue.cancel(session, job_id)

    raised = False
    try:
        # A converter that guards its own errors must still stop.
        with contextlib.suppress(Exception):
            reporter(20, "andando")
    except JobCancelled:
        raised = True
    check("o próximo tick levanta JobCancelled", raised)
    check("job cancelado continua 'cancelled'", job_state(job_id)["status"] == "cancelled")


def check_worker_glue(book_id: str) -> None:
    """execute_job must translate a JobCancelled into the cancelled state."""
    import app.workers.conversion as worker
    from app.database import session_scope
    from app.workers import queue
    from app.workers.progress import ProgressReporter

    print("\n[worker: cancela no meio e descarta o resultado]")
    job_id = make_running_job(book_id)
    before = book_count()

    class FastReporter(_FastReporterMixin, ProgressReporter):
        pass

    def fake_run_conversion(**kwargs):
        progress = kwargs["progress"]
        progress(5, "começando")
        with session_scope() as session:
            queue.cancel(session, job_id)
        progress(50, "seguindo")  # must raise here
        raise AssertionError("a conversão não deveria continuar após o cancelamento")

    original_reporter = worker.ProgressReporter
    original_run = worker.run_conversion
    worker.ProgressReporter = FastReporter
    worker.run_conversion = fake_run_conversion
    try:
        asyncio.run(worker.execute_job(job_id))
    finally:
        worker.ProgressReporter = original_reporter
        worker.run_conversion = original_run

    state = job_state(job_id)
    check("job termina cancelado", state["status"] == "cancelled", state["status"])
    check(
        "mensagem final fala de interrupção e descarte",
        "interrompida" in state["message"],
        state["message"],
    )
    check("nenhum livro foi publicado", state["output_book_id"] is None)
    check("a contagem de livros não mudou", book_count() == before, str(book_count()))


def check_real_conversion() -> None:
    """Integration: a real converter is aborted and publishes nothing."""
    import app.workers.conversion as worker
    from app.database import session_scope
    from app.workers import queue
    from app.workers.progress import ProgressReporter

    # Big enough that it cannot finish before the cancel below.
    book_id = new_book(make_cbz("big.cbz", 40, (1600, 2400)), "book-cancel-2")
    print("\n[integração: CBZ -> EPUB real, cancelado durante a conversão]")
    job_id = make_running_job(book_id)
    before = book_count()

    class FastReporter(_FastReporterMixin, ProgressReporter):
        pass

    original = worker.ProgressReporter
    worker.ProgressReporter = FastReporter
    outcome: dict = {}

    def run() -> None:
        try:
            asyncio.run(worker.execute_job(job_id))
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=run)
    try:
        thread.start()
        time.sleep(0.2)
        with session_scope() as session:
            queue.cancel(session, job_id)
        thread.join(timeout=300)
    finally:
        worker.ProgressReporter = original

    check("worker terminou", not thread.is_alive())
    check("worker não levantou exceção", "error" not in outcome, outcome.get("error", ""))

    state = job_state(job_id)
    check("job terminou cancelado", state["status"] == "cancelled", state["status"])
    check(
        "mensagem final fala de interrupção e descarte",
        "interrompida" in state["message"],
        state["message"],
    )
    check("nenhum livro foi publicado", state["output_book_id"] is None)
    check("a contagem de livros não mudou", book_count() == before, str(book_count()))


def check_discard_staged() -> None:
    from app.config import get_settings
    from app.workers.conversion import _discard_staged

    print("\n[arquivo preparado é removido se o job for cancelado]")
    library = get_settings().library_dir
    library.mkdir(parents=True, exist_ok=True)

    inside = library / "staged-teste.epub"
    inside.write_bytes(b"conteudo")
    _discard_staged("staged-teste.epub")
    check("arquivo dentro da biblioteca é apagado", not inside.exists())

    outside = WORKDIR / "fora-da-biblioteca.epub"
    outside.write_bytes(b"conteudo")
    _discard_staged(str(outside))
    check("caminho fora da biblioteca é ignorado", outside.exists())
    outside.unlink()


def main() -> int:
    from app.database import init_db

    init_db()

    book_id = check_queue_semantics()
    check_reporter_signal(book_id)
    check_worker_glue(book_id)
    check_real_conversion()
    check_discard_staged()

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
