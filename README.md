# PDF Password Remover

A local desktop utility for macOS and Windows that saves unencrypted copies of
PDFs you own or are authorized to modify. Files are processed on the computer
and are not uploaded anywhere.

## Supported protection

- **Open/user password:** enter the known password in the single password field.
- **Editing/permissions (owner) password:** enter it in the same field.
- **Restrictions with no open password:** leave both fields blank. The app can
  automatically remove editing, printing, and copying restrictions when the
  PDF already opens freely.

Without recovery mode, the app only uses the password entered by the user.

## Optional local password recovery

For PDFs you own or are authorized to access, enable **Try local recovery when
the password is unknown**. Recovery can try:

- a password-list text file, with one candidate per line;
- clues found in the PDF filename;
- a short built-in list of common passwords; and
- numeric PINs from four digits up to the selected maximum.

Set a time limit per PDF and use **Stop** at any time. Recovery is entirely
local and is best-effort: strong or random passwords may remain unrecovered.

## Run from source

```bash
python3 -m pip install -r requirements.txt
python3 pdf_password_remover.py
```

## Build

- macOS: run `./build_macos.sh`.
- Windows: run `build_windows.bat` from a Windows computer with Python 3.

PyInstaller must build separately on each operating system. The output is
written to the `dist` folder.

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest -q
```
