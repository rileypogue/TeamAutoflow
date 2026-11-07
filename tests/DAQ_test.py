# test to determine timing drift/error induced by sampling on two DAQs
# the LabJack T7 will assert a pulse to the NI's programmable function interface (PFI) 0 pin, which will prompt the NI to start recording
# have both DAQs measure a function generator, evaluate sample offset of edge detection.
import nidaqmx
from labjack import ljm
from nidaqmx.constant import AcquisitionType, TerminalConfiguration, Edge
import threading, time # multithreading with for better data logging

# constants
dev = "Dev1" # run test to make sure it shows up as Dev1
voltage_min = -5.0
voltage_max =5.0
fs = 5000 # sample rate in Hz
minutes = 20 # how long we run test
TOTAL_SAMPLES = fs * 60 * 20

start_barrier = threading.Barrier(2) # start the two threads AnalogMultiChannelReader
stop_event = threading.Event() # allow for one thread to stop the other for event
t0 = {} # start time of each device

# logging task
def logging_ni():
    try:
        with nidaqmx.Task() as loggingTask:
            loggingTask.ai_channels.add_ai_voltage(f"{dev}/ai0", terminal_config=TerminalConfiguration.RSE, min_val=voltage_min, max_val=voltage_max) # terminal config set to RSE is referenced to DAQ's ground, can also do differential 
            loggingTask.cfg_sample_clk_timing(fs, sample_mode=AcquisitionType.FINITE, samples_per_chan=TOTAL_SAMPLES) # total num samples, determined by sample rate and how long we run test
            loggingTask.start_trigger.cfg_dig_edge_start_trig(f"{dev}/pfi0", Edge.RISING) # start on digital edge from LJM
        
            reader = AnalogMultiChannelReader(loggingTask.in_stream)  # nidaqmx class multichannelreader
            buf = np.empty((4, fs)) # empty buffer for 4 channels of sample rate sized chunks
        
            with open("ni_log.bin", "wb") as f: # write binary mode file
                start_barrier.wait()
                t0["ni"] = time.time()
                task.start() # go thread!
                for i in range(TOTAL_SAMPLES / fs):
                    if stop_event.is_set(): # end if other thread dies
                        break
                    reader.read_many_sample(buf, fs, timeout=5.0) # read in sample rate number of samples to buffer 
                    buf.T.astype(np.float32).tofile(f) # transpose so rows is scans, columns are analog channels 0 ... 3
    except Exception as e:
        print("NI error", e)
        stop_event.set()
        start_barrier.abort()
            

def logging_labjack():
    try:
        handle = ljm.openS("T7", "ANY", "ANY")

