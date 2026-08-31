import threading
from pathlib import Path

import pikepdf
import pytest

from pdf_password_remover import (
    PDFRemoverApp,
    PasswordRecoveryFailed,
    RecoveryOptions,
    RecoveryStopped,
)


OPEN_PASSWORD = "open-secret"
PERMISSIONS_PASSWORD = "edit-secret"


def make_pdf(
    path: Path,
    *,
    open_password: str | None = None,
    permissions_password: str | None = None,
) -> None:
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 200))
        if open_password is None and permissions_password is None:
            pdf.save(path)
            return

        permissions = pikepdf.Permissions(
            accessibility=True,
            extract=False,
            modify_annotation=False,
            modify_assembly=False,
            modify_form=False,
            modify_other=False,
            print_lowres=False,
            print_highres=False,
        )
        pdf.save(
            path,
            encryption=pikepdf.Encryption(
                owner=permissions_password or "",
                user=open_password or "",
                R=6,
                allow=permissions,
            ),
        )


def assert_unlocked(path: Path) -> None:
    with pikepdf.open(path, password="") as pdf:
        assert not pdf.is_encrypted
        assert len(pdf.pages) == 1


def test_copies_unprotected_pdf(tmp_path: Path) -> None:
    source = tmp_path / "plain.pdf"
    destination = tmp_path / "output" / "plain.pdf"
    make_pdf(source)

    result = PDFRemoverApp._unlock_one(source, destination)

    assert result.status == "plain"
    assert_unlocked(destination)


def test_removes_editing_restrictions_without_password(tmp_path: Path) -> None:
    source = tmp_path / "restricted.pdf"
    destination = tmp_path / "output" / "restricted.pdf"
    make_pdf(source, permissions_password=PERMISSIONS_PASSWORD)

    result = PDFRemoverApp._unlock_one(source, destination)

    assert result.status == "restrictions"
    assert_unlocked(destination)


def test_removes_open_password(tmp_path: Path) -> None:
    source = tmp_path / "open-password.pdf"
    destination = tmp_path / "output" / "open-password.pdf"
    make_pdf(
        source,
        open_password=OPEN_PASSWORD,
        permissions_password=PERMISSIONS_PASSWORD,
    )

    result = PDFRemoverApp._unlock_one(
        source,
        destination,
        password=OPEN_PASSWORD,
    )

    assert result.status == "password"
    assert_unlocked(destination)


def test_removes_permissions_password(tmp_path: Path) -> None:
    source = tmp_path / "permissions-password.pdf"
    destination = tmp_path / "output" / "permissions-password.pdf"
    make_pdf(
        source,
        open_password=OPEN_PASSWORD,
        permissions_password=PERMISSIONS_PASSWORD,
    )

    result = PDFRemoverApp._unlock_one(
        source,
        destination,
        password=PERMISSIONS_PASSWORD,
    )

    assert result.status == "password"
    assert_unlocked(destination)


def test_rejects_unknown_password_without_creating_output(tmp_path: Path) -> None:
    source = tmp_path / "protected.pdf"
    destination = tmp_path / "output" / "protected.pdf"
    make_pdf(
        source,
        open_password=OPEN_PASSWORD,
        permissions_password=PERMISSIONS_PASSWORD,
    )

    with pytest.raises(pikepdf.PasswordError):
        PDFRemoverApp._unlock_one(
            source,
            destination,
            password="incorrect",
        )

    assert not destination.exists()


def test_recovers_password_from_wordlist(tmp_path: Path) -> None:
    source = tmp_path / "statement.pdf"
    destination = tmp_path / "output" / "statement.pdf"
    wordlist = tmp_path / "passwords.txt"
    wordlist.write_text("incorrect\nwordlist-secret\n", encoding="utf-8")
    make_pdf(
        source,
        open_password="wordlist-secret",
        permissions_password=PERMISSIONS_PASSWORD,
    )

    result = PDFRemoverApp._unlock_one(
        source,
        destination,
        recovery_options=RecoveryOptions(
            enabled=True,
            wordlist_path=wordlist,
            time_limit_seconds=10,
        ),
    )

    assert result.status == "recovered"
    assert result.attempts > 0
    assert_unlocked(destination)


def test_recovers_four_digit_pin(tmp_path: Path) -> None:
    source = tmp_path / "locked.pdf"
    destination = tmp_path / "output" / "locked.pdf"
    make_pdf(
        source,
        open_password="2468",
        permissions_password=PERMISSIONS_PASSWORD,
    )

    result = PDFRemoverApp._unlock_one(
        source,
        destination,
        recovery_options=RecoveryOptions(
            enabled=True,
            numeric_max_digits=4,
            time_limit_seconds=30,
        ),
    )

    assert result.status == "recovered"
    assert result.attempts >= 2468
    assert_unlocked(destination)


def test_recovery_honors_time_limit(tmp_path: Path) -> None:
    source = tmp_path / "protected.pdf"
    destination = tmp_path / "output" / "protected.pdf"
    make_pdf(
        source,
        open_password=OPEN_PASSWORD,
        permissions_password=PERMISSIONS_PASSWORD,
    )

    with pytest.raises(PasswordRecoveryFailed) as error:
        PDFRemoverApp._unlock_one(
            source,
            destination,
            recovery_options=RecoveryOptions(
                enabled=True,
                numeric_max_digits=6,
                time_limit_seconds=0,
            ),
        )

    assert error.value.timed_out
    assert error.value.attempts == 0
    assert not destination.exists()


def test_recovery_can_be_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "protected.pdf"
    destination = tmp_path / "output" / "protected.pdf"
    cancel_event = threading.Event()
    cancel_event.set()
    make_pdf(
        source,
        open_password=OPEN_PASSWORD,
        permissions_password=PERMISSIONS_PASSWORD,
    )

    with pytest.raises(RecoveryStopped) as error:
        PDFRemoverApp._unlock_one(
            source,
            destination,
            recovery_options=RecoveryOptions(enabled=True, numeric_max_digits=6),
            cancel_event=cancel_event,
        )

    assert error.value.attempts == 0
    assert not destination.exists()


def test_output_folder_detection() -> None:
    output = Path("/documents/pdfs/unlocked")

    assert PDFRemoverApp._is_inside(output / "file.pdf", output)
    assert PDFRemoverApp._is_inside(output, output)
    assert not PDFRemoverApp._is_inside(Path("/documents/pdfs/file.pdf"), output)
