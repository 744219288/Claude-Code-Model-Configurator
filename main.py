from __future__ import annotations

import sys


def main() -> int:
    if "--self-test-gui" in sys.argv[1:]:
        import tkinter as tk

        window = tk.Tk()
        window.withdraw()
        window.update_idletasks()
        window.destroy()
        return 0

    from installer.core import InstallError, verify_release_integrity
    try:
        verify_release_integrity()
    except InstallError as exc:
        import tkinter as tk
        from tkinter import messagebox

        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("安全校验失败", str(exc))
        window.destroy()
        return 2

    if "--launch-claude" in sys.argv[1:]:
        from installer.core import Paths, launch_claude

        try:
            launch_claude(Paths.default())
            return 0
        except Exception as exc:
            import tkinter as tk
            from tkinter import messagebox

            window = tk.Tk()
            window.withdraw()
            messagebox.showerror("无法启动 Claude Code", str(exc))
            window.destroy()
            return 1

    if "--run-claude" in sys.argv[1:]:
        from installer.core import Paths, run_claude_inline

        marker = sys.argv.index("--run-claude")
        try:
            return run_claude_inline(Paths.default(), sys.argv[marker + 1:])
        except Exception as exc:
            print(f"[Claude Code + DeepSeek] {exc}", file=sys.stderr)
            return 1

    from installer.gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
