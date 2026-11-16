def _cmdk_start():
    import FreeCAD
    from PySide import QtCore

    def go():
        try:
            import cmdk_palette

            cmdk_palette.install()
        except Exception as exc:
            FreeCAD.Console.PrintError("CmdK failed to start: %s\n" % exc)

    QtCore.QTimer.singleShot(0, go)


_cmdk_start()
