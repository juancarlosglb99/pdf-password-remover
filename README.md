# PDF Password Remover

A local desktop utility for macOS and Windows that saves unencrypted copies of
PDFs you own or are authorized to modify. Files are processed on the computer
and are not uploaded anywhere.

## Install (start here)

Do this once per computer. The app is not signed, so the first time you open it
your computer shows a one-time "unknown developer" warning — that is expected.
The steps below clear it for good.

### On a Mac (Apple Silicon — M1 / M2 / M3 / M4)

1. Unzip **PDF-Password-Remover-macOS.zip** → you get **PDF Password Remover.app**.
   (Or build it yourself: run `./build_macos.sh`; the app lands in the `dist` folder.)
2. Drag **PDF Password Remover.app** into your **Applications** folder (optional).
3. Double-click it. The first time, macOS blocks it and says it can't verify the
   developer — click **Done**.
4. Open the **Apple menu → System Settings → Privacy & Security**.
5. Scroll down to the "PDF Password Remover" message and click **Open Anyway**,
   then confirm.
6. It opens normally every time after that.

### On Windows

1. Get **PDF Password Remover.exe**:
   - In this repo, open the **Actions** tab → the newest **Build Windows app** run →
     under **Artifacts**, download **PDF-Password-Remover-Windows** → unzip it, or
   - build it yourself on a Windows PC with Python 3 by running `build_windows.bat`.
2. Double-click **PDF Password Remover.exe**.
3. If Windows SmartScreen shows "Windows protected your PC," click **More info**,
   then **Run anyway**.
4. If antivirus flags it, that's a known false alarm for this kind of app —
   allow / restore it. It opens normally after that.

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
- Windows: run `build_windows.bat` from a Windows computer with Python 3, or let
  GitHub Actions build it automatically (see the **Actions** tab).

PyInstaller must build separately on each operating system. The output is
written to the `dist` folder.

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest -q
```
