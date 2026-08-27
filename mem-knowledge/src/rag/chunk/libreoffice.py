"""Synchronous LibreOffice conversion owned by the prefork task process."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ...bootstrap import get_settings


def convert_to_pdf(src_path: str | Path) -> str:
    settings = get_settings()
    source = Path(src_path)
    executable = settings.libreoffice_path
    if not executable.is_file():
        raise RuntimeError("LibreOffice executable is unavailable")
    try:
        subprocess.run(
            [
                str(executable),
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(source.parent),
                str(source),
            ],
            check=True,
            timeout=settings.libreoffice_timeout_seconds,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice conversion timed out") from None
    except subprocess.CalledProcessError:
        raise RuntimeError("LibreOffice conversion failed") from None
    destination = source.with_suffix(".pdf")
    if not destination.is_file():
        raise RuntimeError("LibreOffice did not produce a PDF")
    return str(destination)


__all__ = ["convert_to_pdf"]
