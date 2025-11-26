import usbtmc
import time
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
import logging
import sys

# --- Configuration ---
logging.basicConfig(
    level=logging.INFO, # Set to INFO for cleaner output, DEBUG for timings
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Agilent/Keysight Infiniium Scope IDs ---
# Agilent/Keysight's Vendor ID is 0x0957.
# The Product ID for the 9000 series can vary, 0x0588 is a common one.
# Please verify this in your system's USB devices list.
SCOPE_VID = 0x0957 
SCOPE_PID = 0x900b # <-- VERIFY THIS VALUE!

WAVEFORM_SOURCE = 'CHAN4' # Use 'CHAN1', 'CHAN2', etc.
NUM_CAPTURES = 300

#
# ===== PERFORMANCE TUNING FOR DSO9104A =====
#
# 1. YOU CAN NOW CHANGE THE NUMBER OF POINTS! This is the best speed optimization.
#    Lower values result in much faster acquisitions.
ACQUISITION_POINTS = 1000 # Try 1000, 500, or even 250
#
# 2. Only update the plot every N frames.
PLOT_EVERY_N_FRAMES = 5 
#
# ============================================
#

# --- PyQtGraph Setup ---
app = pg.mkQApp("Live Waveform")
win = pg.GraphicsLayoutWidget(show=True, title="Live Waveform from Agilent DSO9104A")
win.resize(1000, 600)
win.setWindowTitle('Agilent DSO9104A Live Capture')

# Enable antialiasing for prettier plots
pg.setConfigOptions(antialias=True)

p1 = win.addPlot(title="Live Waveform from Agilent DSO9104A")
p1.setLabel('left', "Voltage", units='V')
p1.setLabel('bottom', "Time", units='s')
p1.showGrid(x=True, y=True)
curve = p1.plot(pen='y')

logging.info("Searching for USBTMC instrument...")
try:
    scope = usbtmc.Instrument(SCOPE_VID, SCOPE_PID)
    scope.timeout = 10 # Infiniium scopes can sometimes be slower to respond
    logging.info(f"Connected to: {scope.ask('*IDN?').strip()}")
    
    # --- Configure scope using AGILENT commands ---
    scope.write(':STOP') # Stop acquisition to change settings
    scope.write(f':WAVEFORM:SOURCE {WAVEFORM_SOURCE}')
    scope.write(':WAVEFORM:FORMAT BYTE') # Use 8-bit data for speed
    scope.write(':WAVEFORM:UNSIGNED OFF')
    scope.write(f':ACQUIRE:POINTS {ACQUISITION_POINTS}') # Set the record length!
    scope.write(':ACQUIRE:TYPE NORMAL')
    logging.info(f"Scope configured with record length = {ACQUISITION_POINTS} points.")

    # --- Query scaling parameters using AGILENT commands ---
    logging.info("Querying waveform scaling parameters...")
    # The :WAVEFORM:PREAMBLE? command is a fast way to get all params at once
    preamble = scope.ask(':WAVEFORM:PREAMBLE?').split(',')
    
    # Parse the preamble for Agilent scopes
    # Format: format,type,points,count,xinc,xorg,xref,yinc,yorg,yref
    x_increment = float(preamble[4])
    x_origin = float(preamble[5])
    y_increment = float(preamble[7])
    y_origin = float(preamble[8])
    y_reference = int(float(preamble[9])) # Y-reference is the ADC level for 0 Volts
    logging.info("Parameters acquired.")

except Exception as e:
    logging.error(f"Error during setup: {e}")
    exit()

# --- Acquisition Loop ---
logging.info("Starting capture loop...")
all_rates = []

def update():
    global i, all_rates
    
    # Check if we are done
    if i >= NUM_CAPTURES:
        timer.stop()
        average_rate = np.mean(all_rates) if all_rates else 0
        logging.info(f"Acquisition complete. Average rate: {average_rate:.2f} Hz")
        scope.write(':RUN') # Let the scope run freely again
        scope.close()
        p1.setTitle(f"Finished (Avg Rate: {average_rate:.2f} Hz)")
        return

    loop_start_time = time.perf_counter()
    
    try:
        # --- Use the efficient :DIGITIZE command ---
        # This performs a single-shot acquisition and is faster than run/stop
        scope.write(f':DIGITIZE {WAVEFORM_SOURCE}')
        
        # Ask for the data. The scope will send a binary block header (e.g., #800001000)
        # which usbtmc.read_raw() should handle automatically.
        scope.write(':WAVEFORM:DATA?')
        waveform_bytes = scope.read_raw()
        
        # The first few bytes are the header, we need to find the start of the data
        header_end_index = waveform_bytes.find(b'\n') + 1
        raw_waveform = np.frombuffer(waveform_bytes[header_end_index:], dtype=np.int8)

        voltages = (raw_waveform.astype(np.float32) - y_reference) * y_increment + y_origin
        times = np.arange(0, len(raw_waveform)) * x_increment + x_origin
        
        filename = f'waveform_{i+1:03d}_{time.strftime("%Y%m%d-%H%M%S")}.csv'
        data_to_save = np.vstack((times, voltages)).T
        np.savetxt(filename, data_to_save, delimiter=',', header='Time(s),Voltage(V)', comments='')

        # --- Only plot every Nth frame ---
        if (i + 1) % PLOT_EVERY_N_FRAMES == 0:
            curve.setData(times, voltages)
            app.processEvents()
        
        loop_end_time = time.perf_counter()
        elapsed_time = loop_end_time - loop_start_time
        all_rates.append(1.0 / elapsed_time if elapsed_time > 0 else 0)
        
        p1.setTitle(f"Live Waveform (Plotted frame {i+1}, Avg Rate: {np.mean(all_rates):.2f} Hz)")
        
    except Exception as e:
        logging.error(f"An error occurred during capture {i+1}: {e}")
        timer.stop()
        scope.close()

    i += 1

# Initialize counter
i = 0

# Use a QTimer to run the update loop
timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(0) # Run as fast as possible

if __name__ == '__main__':
    if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
        QtWidgets.QApplication.instance().exec_()