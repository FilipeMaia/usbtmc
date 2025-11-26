import usbtmc
import time
import numpy as np
import matplotlib.pyplot as plt
import logging

# --- Logging Configuration ---
# This sets up the logger to print messages to the console.
# The format includes a timestamp, the level of the message (e.g., INFO), and the message itself.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Oscilloscope Configuration ---
# Your specific Tektronix Scope IDs
SCOPE_VID = 0x0699 
SCOPE_PID = 0x03b3

WAVEFORM_SOURCE = 'CH2'
NUM_CAPTURES = 100  # How many waveforms to capture before stopping

# --- Main Script ---

# --- Matplotlib Setup for Live Plotting ---
plt.ion() # Turn on interactive mode
fig, ax = plt.subplots()
line, = ax.plot([], []) # Create an empty line object to update

# Configure plot aesthetics
ax.set_title("Live Waveform from Oscilloscope")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Voltage (V)")
ax.grid(True)
# --- End of Matplotlib Setup ---

print("Searching for USBTMC instrument...")
try:
    scope = usbtmc.Instrument(SCOPE_VID, SCOPE_PID)
    scope.timeout = 5
except Exception as e:
    logging.error(f"Error connecting to the scope: {e}")
    logging.error("\nTroubleshooting:\n1. Is the scope connected via USB and powered on?\n2. Are the VID (0x{:04x}) and PID (0x{:04x}) correct?".format(SCOPE_VID, SCOPE_PID))
    exit()

# Configure the oscilloscope for fast acquisition
try:
    instrument_id = scope.ask('*IDN?')
    logging.info(f"Connected to: {instrument_id.strip()}")
    
    scope.write(f'DATA:SOURCE {WAVEFORM_SOURCE}')
    scope.write('DATA:ENCDG RIBinary')
    scope.write('DATA:WIDTH 1')
    scope.write('HEADER OFF')
    scope.write('ACQUIRE:STOPAFTER SEQUENCE')

    logging.info("Scope configured for fast binary acquisition.")

    # --- Query and Log Waveform Scaling Parameters (Done ONCE) ---
    logging.info("Querying waveform scaling parameters...")
    num_points = int(scope.ask('WFMPRE:NR_PT?'))
    x_increment = float(scope.ask('WFMPRE:XINCR?'))
    x_origin = float(scope.ask('WFMPRE:XZERO?'))
    y_multiplier = float(scope.ask('WFMPRE:YMULT?'))
    y_offset = float(scope.ask('WFMPRE:YOFF?'))
    y_zero = float(scope.ask('WFMPRE:YZERO?'))

    # Use a multi-line f-string to format the log message for readability
    parameter_log_message = f"""
    -------------------------------------------
    Waveform Scaling Parameters Acquired:
      - Number of Points (NR_PT): {num_points}
      - Time Increment (XINCR):   {x_increment:.4e} s/point
      - Time Origin (XZERO):      {x_origin:.4e} s
      - Voltage Multiplier (YMULT):{y_multiplier:.4e} V/ADC_level
      - Voltage Offset (YOFF):    {y_offset} ADC_levels
      - Voltage Zero (YZERO):     {y_zero} V
    -------------------------------------------
    """
    logging.info(parameter_log_message)
    # --- End of Parameter Logging ---

except Exception as e:
    logging.error(f"Error during configuration: {e}")
    scope.close()
    exit()

# --- Acquisition Loop ---
print("\nStarting capture loop...")
for i in range(NUM_CAPTURES):
    loop_start_time = time.time()
    
    try:
        start_time = time.perf_counter()
        scope.write('ACQUIRE:STATE ON')
        duration = time.perf_counter() - start_time
        logging.info(f"  scope.write('ACQUIRE:STATE ON') took {duration:.6f} s")
        start_time = time.perf_counter()
        scope.ask('*OPC?') 
        duration = time.perf_counter() - start_time
        logging.info(f"  scope.ask('*OPC?') took {duration:.6f} s")

        # --- Get the Raw Waveform Data ---
        start_time = time.perf_counter()
        scope.write('CURVE?')
        waveform_bytes = scope.read_raw()
        duration = time.perf_counter() - start_time
        logging.info(f"  scope.read_raw() took {duration:.6f} s")
        raw_waveform = np.frombuffer(waveform_bytes, dtype=np.int8)

        # --- Data Processing (using pre-fetched parameters) ---
        start_time = time.perf_counter()
        voltages = (raw_waveform.astype(np.float32) - y_zero + y_offset) * y_multiplier
        times = np.arange(0, len(raw_waveform)) * x_increment + x_origin

        # --- Update Live Plot ---
        line.set_xdata(times)
        line.set_ydata(voltages)
        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw()
        fig.canvas.flush_events()
        
        # --- Save to CSV (Optional) ---
        # You can comment/uncomment this section if you still want to save the data
        filename = f'waveform_{i+1:03d}_{time.strftime("%Y%m%d-%H%M%S")}.csv'
        data_to_save = np.vstack((times, voltages)).T
        np.savetxt(filename, data_to_save, delimiter=',', header='Time(s),Voltage(V)', comments='')
                
        loop_end_time = time.time()
        elapsed_time = loop_end_time - loop_start_time
        actual_rate = 1.0 / elapsed_time if elapsed_time > 0 else 0

        duration = time.perf_counter() - start_time
        logging.info(f"Data processing and plotting took {duration:.6f} s")

        ax.set_title(f"Live Waveform (Capture {i+1}/{NUM_CAPTURES}, Rate: {actual_rate:.2f} Hz)")
        logging.info(f"Live Waveform (Capture {i+1}/{NUM_CAPTURES}, Rate: {actual_rate:.2f} Hz)")
    except Exception as e:
        logging.error(f"An error occurred during capture {i+1}: {e}")
        if "Timeout" in str(e):
            logging.warning("Timeout Error: The scope may not be triggering. Check the trigger source and level.")
            break
        continue

# --- Cleanup ---
logging.info("Acquisition complete.")
scope.close()

# Keep the final plot window open
plt.ioff()
ax.set_title("Acquisition Finished - Final Waveform")
plt.show()