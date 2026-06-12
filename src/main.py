"""
Software Launcher Dashboard - Entry Point
"""

import sys
from PySide6.QtWidgets import QApplication
from lib.main_controller import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Software Launcher")
    app.setOrganizationName("DinasourList")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
