import pyvisa
import time
import numpy as np
import matplotlib.pyplot as plt
import logging

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Oscilloscope Configuration ---
SCOPE_VID = '0x0699' 
SCOPE_PID = '0x03B3' # Note: PyVISA usually returns hex in uppercase

WAVEFORM_SOURCE = 'CH2'
NUM_CAPTURES = 100

# --- Main Script ---

# --- Matplotlib Setup ---
plt.ion()
fig, ax = plt.subplots()
line, = ax.plot([], [])

ax.set_title("Live Waveform from Oscilloscope")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Voltage (V)")
ax.grid(True)

print("Searching for Instrument via VISA...")

# --- VISA Connection Setup ---
try:
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()
    
    # Filter the list of resources to find the one matching your VID and PID
    # A typical USB resource looks like: 'USB0::0x0699::0x03B3::C012345::INSTR'
    target_resource = None
    for res in resources:
        if SCOPE_VID in res and SCOPE_PID in res:
            target_resource = res
            break
            
    if target_resource:
        logging.info(f"Found scope at: {target_resource}")
        scope = rm.open_resource(target_resource)
        
        # TIMEOUT CHANGE: PyVISA uses milliseconds (5s = 5000ms)
        scope.timeout = 5000 
        
        # Common settings for binary transfer reliability
        scope.chunk_size = 102400 
        scope.read_termination = '\n'
        scope.write_termination = None 
    else:
        raise ValueError(f"Device with VID {SCOPE_VID} and PID {SCOPE_PID} not found.")

except Exception as e:
    logging.error(f"Error connecting to the scope via VISA: {e}")
    logging.error("\nTroubleshooting:\n1. Have you installed NI-VISA or TekVISA?\n2. Is the scope powered on?")
    exit()

# Configure the oscilloscope
try:
    # SYNTAX CHANGE: .ask() becomes .query()
    instrument_id = scope.query('*IDN?')
    logging.info(f"Connected to: {instrument_id.strip()}")
    
    scope.write(f'DATA:SOURCE {WAVEFORM_SOURCE}')
    scope.write('DATA:ENCDG RIBinary')
    scope.write('DATA:WIDTH 1')
    scope.write('HEADER OFF')
    scope.write('ACQUIRE:STOPAFTER SEQUENCE')

    logging.info("Scope configured for fast binary acquisition.")

    # --- Query Parameters ---
    logging.info("Querying waveform scaling parameters...")
    # .query() returns a string, so we cast to float/int
    num_points = int(scope.query('WFMPRE:NR_PT?'))
    x_increment = float(scope.query('WFMPRE:XINCR?'))
    x_origin = float(scope.query('WFMPRE:XZERO?'))
    y_multiplier = float(scope.query('WFMPRE:YMULT?'))
    y_offset = float(scope.query('WFMPRE:YOFF?'))
    y_zero = float(scope.query('WFMPRE:YZERO?'))

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
        logging.info(f"  ACQUIRE:STATE ON took {duration:.6f} s")
        
        start_time = time.perf_counter()
        scope.query('*OPC?') 
        duration = time.perf_counter() - start_time
        logging.info(f"  *OPC? took {duration:.6f} s")

        # --- Get the Raw Waveform Data ---
        start_time = time.perf_counter()
        scope.write('CURVE?')
        
        # Read raw bytes
        waveform_bytes = scope.read_raw()
        
        # --- TEKTRONIX HEADER STRIPPING ---
        # Tektronix returns an IEEE 488.2 header (e.g. #42500...) before the data.
        # We need to strip this, or the first few data points will be garbage.
        if waveform_bytes[0:1] == b'#':
            # The second byte tells us how many digits follow to indicate length
            len_digits = int(chr(waveform_bytes[1]))
            # The header length is 2 + len_digits (Example: #42500 is 2 + 4 = 6 bytes)
            header_len = 2 + len_digits
            waveform_bytes = waveform_bytes[header_len:-1] # Strip header and trailing newline
        
        duration = time.perf_counter() - start_time
        logging.info(f"  read_raw() took {duration:.6f} s")
        
        raw_waveform = np.frombuffer(waveform_bytes, dtype=np.int8)

        # --- Data Processing ---
        start_time = time.perf_counter()
        
        # Ensure we don't have a mismatch in array length due to parsing
        if len(raw_waveform) != num_points:
             # Sometimes the read buffer might include termination chars, truncate to num_points
             raw_waveform = raw_waveform[:num_points]

        voltages = (raw_waveform.astype(np.float32) - y_zero + y_offset) * y_multiplier
        times = np.arange(0, len(raw_waveform)) * x_increment + x_origin

        # --- Update Live Plot ---
        line.set_xdata(times)
        line.set_ydata(voltages)
        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw()
        fig.canvas.flush_events()
        
        loop_end_time = time.time()
        elapsed_time = loop_end_time - loop_start_time
        actual_rate = 1.0 / elapsed_time if elapsed_time > 0 else 0

        ax.set_title(f"Live Waveform (Capture {i+1}/{NUM_CAPTURES}, Rate: {actual_rate:.2f} Hz)")
        logging.info(f"Capture {i+1}/{NUM_CAPTURES} complete. Rate: {actual_rate:.2f} Hz")

    except Exception as e:
        logging.error(f"An error occurred during capture {i+1}: {e}")
        if "Timeout" in str(e):
            logging.warning("Timeout Error: The scope may not be triggering.")
            break
        continue

# --- Cleanup ---
logging.info("Acquisition complete.")
scope.close()
rm.close() # Clean up the Resource Manager

plt.ioff()
ax.set_title("Acquisition Finished")
plt.show()