#!/usr/bin/env python3
"""PDF Password Remover.

Batch-remove password protection / encryption from PDF files, and clearly
report what kind of protection each file had.

For every PDF the app reports one of:
  * "not protected"                  -> just copied through
  * "restrictions removed"           -> the file opened WITHOUT a password but
                                        blocked printing/copying (a permissions
                                        / owner password). Removed automatically,
                                        no password needed.
  * "password removed"               -> the supplied password worked, whether
                                        it was an open or permissions password.
  * "password recovered"             -> optional local recovery found a weak or
                                        guessable password.
  * "needs a password"               -> the file will not open without a
                                        password, and none/you didn't supply the
                                        right one. Skipped — get the password
                                        from whoever sent it.

This tool decrypts PDFs using an empty password or the password you supply. An
optional, time-limited recovery mode can try a local wordlist, filename clues,
common passwords, and numeric PINs. Recovery is best-effort, not guaranteed.
"""

import os
import queue
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import pikepdf
except ImportError:  # pragma: no cover - handled at runtime
    pikepdf = None


APP_TITLE = "PDF Password Remover"

COMMON_PASSWORDS = (
    "1234",
    "0000",
    "1111",
    "12345",
    "123456",
    "000000",
    "password",
    "Password",
    "admin",
    "pdf",
)


@dataclass(frozen=True)
class RecoveryOptions:
    enabled: bool = False
    wordlist_path: Path | None = None
    numeric_max_digits: int = 0
    time_limit_seconds: float = 300


@dataclass(frozen=True)
class UnlockResult:
    status: str
    attempts: int = 0


class PasswordRecoveryFailed(Exception):
    def __init__(self, attempts: int, timed_out: bool) -> None:
        self.attempts = attempts
        self.timed_out = timed_out
        super().__init__("Password recovery did not find a match")


class RecoveryStopped(Exception):
    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__("Password recovery was stopped")


class PDFRemoverApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(760, 700)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.show_pw_var = tk.BooleanVar(value=False)
        self.recovery_var = tk.BooleanVar(value=False)
        self.wordlist_var = tk.StringVar()
        self.numeric_var = tk.StringVar(value="4 digits")
        self.time_limit_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(value="Ready")

        self._msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()

        self._build_ui()
        self.root.after(100, self._drain_queue)

        if pikepdf is None:
            messagebox.showerror(
                APP_TITLE,
                "The 'pikepdf' library is not installed.\n\n"
                "Install it with:\n    python3 -m pip install pikepdf",
            )

    # ---------------------------------------------------------------- UI ----
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="PDF folder:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self._pick_input).grid(row=0, column=2, **pad)

        ttk.Label(frm, text="Save unlocked to:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self._pick_output).grid(row=1, column=2, **pad)

        ttk.Label(frm, text="Password (if required):").grid(
            row=2, column=0, sticky="w", **pad
        )
        self.pw_entry = ttk.Entry(frm, textvariable=self.password_var, show="•")
        self.pw_entry.grid(row=2, column=1, sticky="ew", **pad)
        ttk.Checkbutton(
            frm, text="Show", variable=self.show_pw_var, command=self._toggle_pw
        ).grid(row=2, column=2, **pad)

        ttk.Checkbutton(
            frm, text="Include subfolders", variable=self.recursive_var
        ).grid(row=3, column=1, sticky="w", **pad)

        hint = (
            "Files that open freely but block editing, printing, or copying are "
            "unlocked automatically. Enter a known password when you have one."
        )
        lbl = ttk.Label(frm, text=hint, foreground="#555", wraplength=560, justify="left")
        lbl.grid(row=4, column=1, columnspan=2, sticky="w", padx=10)

        recovery = ttk.LabelFrame(frm, text="Optional password recovery", padding=10)
        recovery.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)
        recovery.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            recovery,
            text="Try local recovery when the password is unknown",
            variable=self.recovery_var,
            command=self._toggle_recovery_controls,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Label(recovery, text="Password list:").grid(row=1, column=0, sticky="w")
        self.wordlist_entry = ttk.Entry(recovery, textvariable=self.wordlist_var)
        self.wordlist_entry.grid(row=1, column=1, sticky="ew", padx=8)
        self.wordlist_btn = ttk.Button(
            recovery, text="Browse…", command=self._pick_wordlist
        )
        self.wordlist_btn.grid(row=1, column=2)

        options_row = ttk.Frame(recovery)
        options_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(options_row, text="Numeric PINs:").pack(side="left")
        self.numeric_combo = ttk.Combobox(
            options_row,
            textvariable=self.numeric_var,
            values=("Off", "4 digits", "4-5 digits", "4-6 digits"),
            state="readonly",
            width=12,
        )
        self.numeric_combo.pack(side="left", padx=(6, 18))
        ttk.Label(options_row, text="Time limit per PDF:").pack(side="left")
        self.time_limit_spin = ttk.Spinbox(
            options_row,
            from_=1,
            to=60,
            textvariable=self.time_limit_var,
            width=5,
        )
        self.time_limit_spin.pack(side="left", padx=(6, 4))
        ttk.Label(options_row, text="minutes").pack(side="left")

        ttk.Label(
            recovery,
            text=(
                "Best for weak passwords and PINs. Strong passwords may not be "
                "recoverable within the selected time."
            ),
            foreground="#555",
            wraplength=650,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(7, 0))

        action_row = ttk.Frame(frm)
        action_row.grid(row=6, column=0, columnspan=3, sticky="ew", **pad)
        action_row.columnconfigure(0, weight=1)
        self.run_btn = ttk.Button(action_row, text="Remove Passwords", command=self._start)
        self.run_btn.grid(row=0, column=0, sticky="ew")
        self.stop_btn = ttk.Button(
            action_row, text="Stop", command=self._stop, state="disabled"
        )
        self.stop_btn.grid(row=0, column=1, padx=(8, 0))

        ttk.Label(frm, textvariable=self.status_var, foreground="#555").grid(
            row=7, column=0, columnspan=3, sticky="w", padx=10
        )

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Label(frm, text="Log:").grid(row=9, column=0, sticky="nw", **pad)
        log_wrap = ttk.Frame(frm)
        log_wrap.grid(row=9, column=1, columnspan=2, sticky="nsew", **pad)
        frm.rowconfigure(9, weight=1)
        log_wrap.columnconfigure(0, weight=1)
        log_wrap.rowconfigure(0, weight=1)

        self.log = tk.Text(log_wrap, height=13, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(log_wrap, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        self._toggle_recovery_controls()

    def _toggle_pw(self) -> None:
        self.pw_entry.configure(show="" if self.show_pw_var.get() else "•")

    def _toggle_recovery_controls(self) -> None:
        state = "normal" if self.recovery_var.get() else "disabled"
        self.wordlist_entry.configure(state=state)
        self.wordlist_btn.configure(state=state)
        self.numeric_combo.configure(state="readonly" if state == "normal" else "disabled")
        self.time_limit_spin.configure(state=state)

    def _pick_input(self) -> None:
        folder = filedialog.askdirectory(title="Choose the folder with your PDFs")
        if folder:
            self.input_var.set(folder)
            if not self.output_var.get():
                self.output_var.set(str(Path(folder) / "unlocked"))

    def _pick_output(self) -> None:
        folder = filedialog.askdirectory(title="Choose where to save unlocked PDFs")
        if folder:
            self.output_var.set(folder)

    def _pick_wordlist(self) -> None:
        filename = filedialog.askopenfilename(
            title="Choose a password list",
            filetypes=(("Password lists", "*.txt *.lst"), ("All files", "*.*")),
        )
        if filename:
            self.wordlist_var.set(filename)

    def _stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_event.set()
            self.stop_btn.configure(state="disabled", text="Stopping…")
            self.status_var.set("Stopping after the current password check…")

    # ------------------------------------------------------------ logging ----
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- worker ----
    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        if pikepdf is None:
            messagebox.showerror(APP_TITLE, "pikepdf is not installed.")
            return

        in_dir = self.input_var.get().strip()
        out_dir = self.output_var.get().strip()
        if not in_dir or not Path(in_dir).is_dir():
            messagebox.showwarning(APP_TITLE, "Please choose a valid PDF folder.")
            return
        if not out_dir:
            out_dir = str(Path(in_dir) / "unlocked")
            self.output_var.set(out_dir)
        if Path(in_dir).resolve() == Path(out_dir).resolve():
            messagebox.showwarning(
                APP_TITLE,
                "Please choose a different output folder so the original PDFs "
                "are never overwritten.",
            )
            return

        password = self.password_var.get()
        recursive = self.recursive_var.get()
        recovery_enabled = self.recovery_var.get()
        wordlist_text = self.wordlist_var.get().strip()
        wordlist_path = Path(wordlist_text) if wordlist_text else None
        if recovery_enabled and wordlist_path is not None and not wordlist_path.is_file():
            messagebox.showwarning(APP_TITLE, "Please choose a valid password-list file.")
            return
        try:
            time_limit_minutes = int(self.time_limit_var.get())
        except ValueError:
            time_limit_minutes = 0
        if recovery_enabled and not 1 <= time_limit_minutes <= 60:
            messagebox.showwarning(APP_TITLE, "Choose a recovery time limit from 1 to 60 minutes.")
            return

        numeric_max_digits = {
            "Off": 0,
            "4 digits": 4,
            "4-5 digits": 5,
            "4-6 digits": 6,
        }.get(self.numeric_var.get(), 4)
        recovery_options = RecoveryOptions(
            enabled=recovery_enabled,
            wordlist_path=wordlist_path,
            numeric_max_digits=numeric_max_digits,
            time_limit_seconds=time_limit_minutes * 60,
        )

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.run_btn.configure(state="disabled", text="Working…")
        self.stop_btn.configure(state="normal", text="Stop")
        self.status_var.set("Scanning PDF files…")
        self._cancel_event.clear()

        self._worker = threading.Thread(
            target=self._process,
            args=(
                Path(in_dir),
                Path(out_dir),
                password,
                recursive,
                recovery_options,
            ),
            daemon=True,
        )
        self._worker.start()

    def _process(
        self,
        in_dir: Path,
        out_dir: Path,
        password: str,
        recursive: bool,
        recovery_options: RecoveryOptions,
    ) -> None:
        q = self._msg_queue
        try:
            pdfs = sorted(in_dir.rglob("*.pdf") if recursive else in_dir.glob("*.pdf"))
            output_root = out_dir.resolve()
            pdfs = [
                p
                for p in pdfs
                if p.is_file() and not self._is_inside(p.resolve(), output_root)
            ]
            total = len(pdfs)
            if total == 0:
                q.put(("log", "No PDF files found in that folder."))
                q.put(("done", None))
                return

            q.put(("log", f"Found {total} PDF file(s). Saving unlocked copies to:\n  {out_dir}\n"))
            q.put(("max", total))

            counts = {
                "plain": 0,
                "restrictions": 0,
                "password": 0,
                "recovered": 0,
                "needs_password": 0,
                "error": 0,
            }
            need_list: list[str] = []
            stopped = False

            for i, pdf_path in enumerate(pdfs, start=1):
                if self._cancel_event.is_set():
                    stopped = True
                    break
                rel = pdf_path.relative_to(in_dir)
                dest = out_dir / rel
                try:
                    q.put(("status", f"Processing {rel}…"))
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    result = self._unlock_one(
                        pdf_path,
                        dest,
                        password,
                        recovery_options=recovery_options,
                        cancel_event=self._cancel_event,
                        progress_callback=lambda attempts, name=str(rel): q.put(
                            ("status", f"Trying passwords for {name}: {attempts:,} tested…")
                        ),
                    )
                    counts[result.status] += 1
                    label = {
                        "plain": "✓ Saved (not protected)",
                        "restrictions": "✓ Editing/printing restrictions removed",
                        "password": "✓ Password protection removed",
                        "recovered": (
                            f"✓ Password recovered locally after {result.attempts:,} attempt(s)"
                        ),
                    }[result.status]
                    q.put(("log", f"[{i}/{total}] {label}: {rel}"))
                except RecoveryStopped as exc:
                    stopped = True
                    q.put(("log", f"[{i}/{total}] Stopped after {exc.attempts:,} recovery attempts: {rel}"))
                    break
                except PasswordRecoveryFailed as exc:
                    counts["needs_password"] += 1
                    need_list.append(str(rel))
                    reason = "time limit reached" if exc.timed_out else "candidates exhausted"
                    q.put(
                        (
                            "log",
                            f"[{i}/{total}] 🔒 Not recovered ({reason}; "
                            f"{exc.attempts:,} tried) — skipped: {rel}",
                        )
                    )
                except pikepdf.PasswordError:
                    counts["needs_password"] += 1
                    need_list.append(str(rel))
                    q.put(("log", f"[{i}/{total}] 🔒 Needs a password you don't have — skipped: {rel}"))
                except Exception as exc:  # noqa: BLE001 - surface any file error
                    counts["error"] += 1
                    q.put(("log", f"[{i}/{total}] ✗ Error: {rel} — {exc}"))
                q.put(("progress", i))

            q.put(("done", (counts, need_list, stopped)))
        except Exception as exc:  # noqa: BLE001
            q.put(("log", f"Unexpected error: {exc}"))
            q.put(("done", None))

    @staticmethod
    def _unlock_one(
        src: Path,
        dest: Path,
        password: str = "",
        recovery_options: RecoveryOptions | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> UnlockResult:
        """Open src and save an unencrypted copy to dest.

        Returns an UnlockResult with status 'plain', 'restrictions',
        'password', or 'recovered'.
        Raises pikepdf.PasswordError if the file needs an open-password that
        was not supplied (or the supplied password is incorrect) and recovery
        is disabled. Raises PasswordRecoveryFailed or RecoveryStopped when a
        recovery run does not complete successfully.
        """
        candidates = [""]
        if password:
            candidates.append(password)

        pdf = None
        used_password = False
        recovered_password = False
        recovery_attempts = 0
        last_password_error = None
        for candidate in candidates:
            try:
                pdf = pikepdf.open(src, password=candidate)
                used_password = bool(candidate)
                break
            except pikepdf.PasswordError as exc:
                last_password_error = exc

        options = recovery_options or RecoveryOptions()
        if pdf is None and options.enabled:
            deadline = time.monotonic() + max(0, options.time_limit_seconds)
            for candidate in PDFRemoverApp._recovery_candidates(src, password, options):
                if cancel_event is not None and cancel_event.is_set():
                    raise RecoveryStopped(recovery_attempts)
                if time.monotonic() >= deadline:
                    raise PasswordRecoveryFailed(recovery_attempts, timed_out=True)

                recovery_attempts += 1
                try:
                    pdf = pikepdf.open(src, password=candidate)
                    used_password = True
                    recovered_password = True
                    break
                except pikepdf.PasswordError:
                    if progress_callback is not None and recovery_attempts % 1000 == 0:
                        progress_callback(recovery_attempts)

            if pdf is None:
                raise PasswordRecoveryFailed(recovery_attempts, timed_out=False)

        if pdf is None:
            if last_password_error is not None:
                raise last_password_error
            raise pikepdf.PasswordError("The PDF requires a valid password")

        temp_path: Path | None = None
        try:
            with pdf:
                was_encrypted = pdf.is_encrypted
                page_count = len(pdf.pages)

                if not was_encrypted:
                    status = "plain"
                elif recovered_password:
                    status = "recovered"
                elif used_password:
                    status = "password"
                else:
                    status = "restrictions"

                dest.parent.mkdir(parents=True, exist_ok=True)
                file_descriptor, temp_name = tempfile.mkstemp(
                    prefix=f".{dest.stem}-", suffix=".pdf", dir=dest.parent
                )
                os.close(file_descriptor)
                temp_path = Path(temp_name)
                temp_path.unlink()

                # Omitting encryption writes a decrypted PDF.
                pdf.save(temp_path)

            # Verify before replacing any existing destination file.
            with pikepdf.open(temp_path, password="") as saved:
                if saved.is_encrypted:
                    raise RuntimeError("The saved PDF is still encrypted")
                if len(saved.pages) != page_count:
                    raise RuntimeError("Page-count verification failed")

            os.replace(temp_path, dest)
            temp_path = None
            return UnlockResult(status=status, attempts=recovery_attempts)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _recovery_candidates(
        src: Path,
        supplied_password: str,
        options: RecoveryOptions,
    ) -> Iterator[str]:
        """Yield unique, local password candidates in a useful order."""
        seen = {"", supplied_password}

        def unique(candidates: Iterator[str] | tuple[str, ...]) -> Iterator[str]:
            for candidate in candidates:
                candidate = candidate.rstrip("\r\n")
                if not candidate or candidate in seen:
                    continue
                if len(candidate.encode("utf-8", errors="ignore")) > 127:
                    continue
                if len(seen) < 100_000:
                    seen.add(candidate)
                yield candidate

        yield from unique(iter(COMMON_PASSWORDS))

        stem = src.stem
        filename_clues = []
        filename_clues.extend(re.findall(r"[A-Za-z0-9]{4,}", stem))
        filename_clues.extend(re.findall(r"\d{4,12}", stem))
        compact_stem = re.sub(r"[^A-Za-z0-9]", "", stem)
        if compact_stem:
            filename_clues.extend((compact_stem, compact_stem.lower(), compact_stem.upper()))
        yield from unique(iter(filename_clues))

        if options.wordlist_path is not None:
            with options.wordlist_path.open("r", encoding="utf-8-sig", errors="ignore") as stream:
                yield from unique(iter(stream))

        if options.numeric_max_digits >= 4:
            for digits in range(4, min(options.numeric_max_digits, 6) + 1):
                for number in range(10**digits):
                    candidate = f"{number:0{digits}d}"
                    if candidate not in seen:
                        yield candidate

    @staticmethod
    def _is_inside(path: Path, directory: Path) -> bool:
        """Return True when path is directory itself or one of its children."""
        try:
            path.relative_to(directory)
            return True
        except ValueError:
            return False

    # -------------------------------------------------------- queue drain ----
    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "max":
                    self.progress.configure(maximum=payload, value=0)
                elif kind == "progress":
                    self.progress.configure(value=payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _finish(self, payload) -> None:
        self.run_btn.configure(state="normal", text="Remove Passwords")
        self.stop_btn.configure(state="disabled", text="Stop")
        if not payload:
            self.status_var.set("Stopped because of an unexpected error")
            return
        counts, need_list, stopped = payload
        unlocked = (
            counts["plain"]
            + counts["restrictions"]
            + counts["password"]
            + counts["recovered"]
        )
        self.status_var.set("Stopped" if stopped else "Finished")
        self._log(
            "\nSummary:"
            f"\n  {unlocked} saved unlocked"
            f"  (restrictions removed: {counts['restrictions']},"
            f" password-protected files unlocked: {counts['password']},"
            f" passwords recovered: {counts['recovered']},"
            f" not protected: {counts['plain']})"
            f"\n  {counts['needs_password']} still need a password you don't have"
            f"\n  {counts['error']} errors"
        )
        if stopped:
            messagebox.showinfo(
                APP_TITLE,
                f"Stopped. {unlocked} file(s) were saved unlocked before stopping.",
            )
        elif need_list:
            self._log("\nPassword recovery did not succeed for:")
            for name in need_list:
                self._log(f"  • {name}")
            messagebox.showinfo(
                APP_TITLE,
                f"{unlocked} file(s) unlocked.\n\n"
                f"{len(need_list)} file(s) remain locked because none of the "
                "attempted passwords matched.",
            )
        elif unlocked:
            messagebox.showinfo(APP_TITLE, f"All done — {unlocked} file(s) unlocked.")


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    PDFRemoverApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
