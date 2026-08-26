"""Keep tkinter discoverable; the normal hook supplies Tcl/Tk explicitly."""


def pre_find_module_path(api):
    # Portable Python can execute tkinter while PyInstaller's TclTkInfo probe
    # still reports unavailable. Do not clear tkinter's normal search path.
    return None

