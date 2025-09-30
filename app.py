from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
import sys
from gui import MainWindow

if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = MainWindow()
    w.setWindowIcon(QIcon("AIStudio.ico"))
    w.showMaximized()
    sys.exit(app.exec())