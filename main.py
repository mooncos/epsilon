from ast import Mult
import sys
import matplotlib
import time
from matplotlib.ticker import (
    MultipleLocator,
    AutoMinorLocator,
    AutoLocator,
    FormatStrFormatter,
)
import numpy as np
from scipy.constants import speed_of_light
from dataclasses import dataclass
from functools import cached_property
from struct import unpack
from cmsisdsp import arm_q15_to_float

matplotlib.use("Qt5Agg")

from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice

from PyQt5 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot
import pyqtgraph as pg
import asyncio

from matplotlib.backends.backend_qt5agg import (
    FigureCanvasQTAgg,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure

CHARACTERISTIC_UUID = f"0000{0x00f0:0{4}x}-8e22-4541-9d4c-21edae82ed19"

TIME_DRAW_PERIOD_NS = 50e6
BIN_NO = 20
BIN_START = 0


def distance_to_bin(distance, samp_freq):
    freq = distance * 2 * 2e9 / (525e-6 * speed_of_light)
    bin = freq / samp_freq * 256
    return bin

class PGCanvas(pg.PlotWidget):
    def __init__(self, parent=None, background='default', plotItem=None, **kargs):
        super().__init__(parent, background, plotItem, **kargs)
        self.pi = self.getPlotItem()
        self.pi.showGrid(x=True, y=True)
        self.pi.setLabel('left', 'Amplitude', units='linear')
        self.pi.setLabel('bottom', 'Bins')
        self.pi.setRange(xRange=(0, BIN_NO), yRange=(0, 1))
        self.dataline = self.pi.plot(pen=pg.mkPen('w', width=1))
        
    def update_plot(self, byteobj: bytearray):
        data_u = unpack("<120h1H5x", byteobj)
        data_u = data_u[: (BIN_NO << 1)]
        bins = np.arange(BIN_START, BIN_NO)
        data = np.asarray(arm_q15_to_float(data_u))
        data[0] += 1
        data[1] += 1
        Re = data[::2]
        Im = data[1::2]
        # self.axes.plot(bins, np.abs(Re + 1j*Im), c="b", lw=0.8)
        self.dataline.setData(bins, np.abs(Re - 1j * Im))

class MplCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None, width=5, height=4, dpi=100):
        fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = fig.add_subplot(111)
        self.axes.set_title("Live FFT")
        self.axes.set_xlabel("Bins")
        self.axes.set_ylabel("Amplitude")
        self.axes.yaxis.set_major_locator(AutoLocator())
        self.axes.yaxis.set_minor_locator(AutoMinorLocator())
        self.axes.xaxis.set_major_locator(MultipleLocator(1))
        self.axes.grid(linestyle="--", linewidth=0.5, which="major")
        self.axes.grid(linestyle="-.", linewidth=0.1, which="minor")
        (self.line,) = self.axes.plot(range(BIN_NO), np.zeros(BIN_NO), c="b", lw=0.8)
        self.axes.set_xlim(BIN_START, BIN_NO - 1)
        self.axes.set_ylim([0, 1])
        fig.tight_layout()
        self.next_time = 0
        super(MplCanvas, self).__init__(fig)

    def update_plot(self, byteobj: bytearray):
        now_time = time.time_ns()
        if self.next_time < now_time:
            self.next_time = now_time + TIME_DRAW_PERIOD_NS
            # self.axes.cla()  # Clear the canvas.
            data_u = unpack("<120h1H5x", byteobj)
            data_u = data_u[: (BIN_NO << 1)]
            bins = np.arange(BIN_START, BIN_NO)
            data = np.asarray(arm_q15_to_float(data_u))
            data[0] += 1
            data[1] += 1
            Re = data[::2]
            Im = data[1::2]
            # self.axes.plot(bins, np.abs(Re + 1j*Im), c="b", lw=0.8)
            self.line.set_data(bins, np.abs(Re + 1j * Im))
            # Trigger the canvas to update and redraw.
            self.draw()
            self.flush_events()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)
        self.resize(640, 800)
        self.setStyleSheet(
            """
            QMainWindow { background-color: rgb(0, 0, 0); color: white; }
            QPlainTextEdit { background-color: rgb(0, 0, 0); color: white; }
            QLabel { color: white; }
            """)

        self._client = None

        scan_button = QtWidgets.QPushButton("Scan Devices")
        self.devices_combobox = QtWidgets.QComboBox()
        connect_button = QtWidgets.QPushButton("Connect")
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)
        logo_ssr = QtWidgets.QLabel()
        logo_gmr = QtWidgets.QLabel()
        app_title = QtWidgets.QLabel()
        app_title.setText("Zenith BLE FFT Viewer")
        app_title.setAlignment(QtCore.Qt.AlignCenter)
        title_font = app_title.font()
        title_font.setPointSize(20)
        app_title.setFont(title_font)
        pixmap_ssr = QtGui.QPixmap("assets/logo_ssr.png")
        pixmap_gmr = QtGui.QPixmap("assets/logo_gmr.png")
        logo_ssr.setPixmap(pixmap_ssr.scaled(100, 100, QtCore.Qt.KeepAspectRatio))
        logo_gmr.setPixmap(pixmap_gmr.scaled(100, 100, QtCore.Qt.KeepAspectRatio))

        #self.sc = MplCanvas(self, width=8, height=6, dpi=90)
        self.sc = PGCanvas()
        # Create toolbar, passing canvas as first parament, parent (self, the MainWindow) as second.
        #self.toolbar = NavigationToolbar(self.sc, self)

        self.layout = QtWidgets.QVBoxLayout()
        self.toolbar_bottom = QtWidgets.QHBoxLayout()
        self.window_title = QtWidgets.QHBoxLayout()
        self.window_title.addWidget(logo_ssr, 0, QtCore.Qt.AlignLeft)
        self.window_title.addWidget(logo_gmr, 0, QtCore.Qt.AlignLeft)
        self.window_title.addWidget(app_title, 0, QtCore.Qt.AlignLeft)
        self.window_title.setAlignment(QtCore.Qt.AlignLeft)
        self.layout.addLayout(self.window_title)
        #self.layout.addWidget(self.toolbar)
        self.layout.addWidget(self.sc)
        self.toolbar_bottom.addWidget(scan_button)
        self.toolbar_bottom.addWidget(self.devices_combobox)
        self.toolbar_bottom.addWidget(connect_button)
        self.toolbar_bottom.addWidget(self.disconnect_button)
        self.layout.addLayout(self.toolbar_bottom)
        self.layout.addWidget(self.log_edit)

        self.setWindowTitle("Zenith BLE FFT Viewer")
        self.setWindowIcon(QtGui.QIcon("assets/icon.png"))

        scan_button.clicked.connect(self.handle_scan)
        connect_button.clicked.connect(self.handle_connect)
        self.disconnect_button.clicked.connect(self.handle_disconnect)

        # Create a placeholder widget to hold our toolbar and canvas.
        self.widget = QtWidgets.QWidget()
        self.widget.setLayout(self.layout)
        self.setCentralWidget(self.widget)

    @cached_property
    def devices(self):
        return list()

    @property
    def current_client(self):
        return self._client

    async def build_client(self, device):
        if self._client is not None:
            await self._client.stop()
        self._client = QBleakClient(device)
        self._client.messageChanged.connect(self.handle_message_changed)
        self._client.messageChanged.connect(self.sc.update_plot)
        await self._client.start()

    @asyncSlot()
    async def handle_connect(self):
        self.log_edit.appendPlainText(f"{time.ctime()}: >> Connecting...")
        device = self.devices_combobox.currentData()
        if isinstance(device, BLEDevice):
            await self.build_client(device)
            self.log_edit.appendPlainText(f"{time.ctime()}: << Connected!")
            self.disconnect_button.setEnabled(True)

    @asyncSlot()
    async def handle_disconnect(self):
        self.log_edit.appendPlainText(f"{time.ctime()}: >> Disconnecting...")
        await self._client.stop()
        self.log_edit.appendPlainText(f"{time.ctime()}: << Disconnected!")
        self.disconnect_button.setEnabled(False)

    @asyncSlot()
    async def handle_scan(self):
        self.log_edit.appendPlainText(f"{time.ctime()}: >> Scanning...")
        self.devices.clear()
        devices = await BleakScanner.discover()
        self.devices.extend([device for device in devices if device.name])
        self.devices_combobox.clear()
        for i, device in enumerate(self.devices):
            self.devices_combobox.insertItem(i, device.name, device)
        self.log_edit.appendPlainText(f"{time.ctime()}: << Scan complete")

    def handle_message_changed(self, message):
        self.log_edit.appendPlainText(f"{time.ctime()} : >> Ramp in: {message}")


@dataclass
class QBleakClient(QtCore.QObject):
    device: BLEDevice

    messageChanged = QtCore.pyqtSignal(bytearray)

    def __post_init__(self):
        super().__init__()

    @cached_property
    def client(self) -> BleakClient:
        return BleakClient(self.device, disconnected_callback=self._handle_disconnect)

    async def start(self):
        await self.client.connect()
        await self.client.start_notify(CHARACTERISTIC_UUID, self._handle_read)

    async def stop(self):
        await self.client.disconnect()

    def _handle_disconnect(self, _) -> None:
        print("Device was disconnected, goodbye.")
        # cancelling all tasks effectively ends the program
        for task in asyncio.all_tasks():
            task.cancel()

    def _handle_read(self, _: int, data: bytearray) -> None:
        # print("received:", data)
        self.messageChanged.emit(data)


def main():
    app = QtWidgets.QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    w = MainWindow()
    w.show()
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
