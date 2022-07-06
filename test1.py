import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from tkinter import TOP, BOTH, Tk
from struct import unpack
import sys
import time
import platform
import asyncio
import logging
import sys

from bleak import BleakClient

logger = logging.getLogger(__name__)

ADDRESS = "00:80:e1:21:cc:89"
CHARACTERISTIC_UUID = f"0000{0x00f0:0{4}x}-8e22-4541-9d4c-21edae82ed19" # SiRadarBLE
#CHARACTERISTIC_UUID = f"0000{0xfe81:0{4}x}-8e22-4541-9d4c-21edae82ed19" # BLE DT

async def run_ble_client(address: str, char_uuid: str, queue: asyncio.Queue):
    async def callback_handler(sender, data):
        await queue.put((time.time(), data))

    async with BleakClient(address) as client:
        logger.info(f"Connected: {client.is_connected}")
        await client.start_notify(char_uuid, callback_handler)
        await asyncio.sleep(10)
        await client.stop_notify(char_uuid)
        # Send an "exit command to the consumer"
        await queue.put((time.time(), None))


async def run_queue_consumer(queue: asyncio.Queue):
    while True:
        # Use await asyncio.wait_for(queue.get(), timeout=1.0) if you want a timeout for getting data.
        epoch, data = await queue.get()
        if data is None:
            logger.info("Got message from client about disconnection. Exiting consumer loop...")
            break
        else: 
            #logger.info(f"Received callback data via async queue at {epoch}: {data}")
            print(len(data))
            data_u = unpack("<120h1H5x", data)
            #rx_ramp_no = data_u[120]
            #ramp_list.append(rx_ramp_no)
            #logger.info(f"----->>>>>> RX ramps: {ramp_list}")
            #logger.info(f"----->>>>>> Received ramp number: {len(ramp_list)}")
            logger.info(f"----->>>>>> Received ramp")
            
            fft = np.array(data_u[:40]) * 2 ** -8
            await update_plot(fft)
            #logger.info(f"Translated data: {fft}")
            # fig.clear()
            # plt.subplots(figsize=(6,6))
            # ax.plot(bins, np.random.randn(len(bins)))
            # fig.canvas.draw()
            # fig.canvas.flush_events()
            
# update matplotlib plot with data from queue
async def update_plot(udata):
    line1.set_ydata(udata)
    fig.canvas.draw()
    fig.canvas.flush_events()
    plt.pause(0.01)


async def main(address: str, char_uuid: str):
    queue = asyncio.Queue()
    client_task = run_ble_client(address, char_uuid, queue)
    consumer_task = run_queue_consumer(queue)
    await asyncio.gather(client_task, consumer_task)
    logger.info("Main method done.")


if __name__ == "__main__":
    ramp_list = []

    fig, ax = plt.subplots(figsize=(6,6))
    plt.ion()
    bins = np.arange(40)
    fft = np.zeros_like(bins)
    line1, = ax.plot(bins, fft, linewidth=1)
    plt.draw()

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(ADDRESS, CHARACTERISTIC_UUID))