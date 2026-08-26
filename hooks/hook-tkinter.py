"""Reliable Tcl/Tk collection for portable and standard Windows Python."""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


python_root = Path(sys.base_prefix)
tcl_root = python_root / "tcl"
dll_root = python_root / "DLLs"

hiddenimports = collect_submodules("tkinter")
datas = [
    (str(tcl_root / "tcl8.6"), "_tcl_data"),
    (str(tcl_root / "tk8.6"), "_tk_data"),
]
binaries = [
    (str(dll_root / "_tkinter.pyd"), "."),
    (str(dll_root / "tcl86t.dll"), "."),
    (str(dll_root / "tk86t.dll"), "."),
]
