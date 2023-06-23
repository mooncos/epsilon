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

SUMS_FFT_SIZE = 20
HOP_SIZE = 20
X_DIM = 200
Y_DIM = SUMS_FFT_SIZE

BIN_NO = 20
BIN_START = 90

h_window = np.hanning(SUMS_FFT_SIZE)

q16_15_to_float = lambda q: q / 2**15

def append_column_matrix(M, column):
    if M.shape[1] < X_DIM:
        return np.c_[M, column]
    else:
        return np.c_[M[:, 1:], column]


def distance_to_bin(distance, samp_freq, ramp_time):
    freq = distance * 2 * 2e9 / (ramp_time * speed_of_light)
    bin = freq / samp_freq * 256
    return bin

class PGCanvas(pg.ImageView):

    def __init__(self, parent=None, background='default', view=None, **kargs):
        super().__init__(parent, background, view, **kargs)
        self.img_mat = np.random.rand(Y_DIM, X_DIM)
        self.ii = self.getImageItem()
        self.vw = self.getView()
        self.setColorMap(pg.colormap.get("jet", source="matplotlib"))
        self.ii.setImage(np.random.normal(size=(Y_DIM, X_DIM)).T)
        self.vw.setAspectLocked(lock=False) 
        self.vw.enableAutoRange('y', True)
        
    def update_plot(self, byteobj: bytearray):
        data_u = np.asarray(unpack("<60i7x", byteobj)) # 60 int32 values and 7 bytes padding
        data_u = q16_15_to_float(data_u)
        Re = data_u[::2]
        Im = data_u[1::2]

        imag_array = np.add(Re, 1j*Im)
        for i in range(30-SUMS_FFT_SIZE+1):
            # fft of first 20 samples shifted by i
            fft_arr = np.fft.fft(imag_array[i:SUMS_FFT_SIZE+i] * h_window, n=SUMS_FFT_SIZE) / SUMS_FFT_SIZE
            autopower = np.abs(fft_arr * np.conj(fft_arr))
            self.img_mat = append_column_matrix(self.img_mat, autopower)
        self.ii.setImage(self.img_mat.T)


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
        self.sc = PGCanvas(view=pg.PlotItem())
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
        self.layout.addWidget(self.sc, stretch=2)
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
