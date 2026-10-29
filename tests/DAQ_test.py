# test to determine timing drift/error induced by sampling on two DAQs
# the LabJack T7 will assert a pulse to the NI's programmable function interface (PFI) 0 pin, which will prompt the NI to start recording
# have both DAQs measure a function generator, evaluate sample offset of edge detection.

import nidaqmx
from labjack import ljm
from nidaqmx.constant import AcquisitionType, TerminalConfiguration, Edgge
import threading # multithreading with for better data logging

# constants
dev = "Dev1" # run test to make sure it shows up as Dev1
voltage_min = 0.0
voltage_max =5.0
fs = 5000 # sample rate in Hz
minutes = 20 # how long we run test
# logging task
with nidaqmx.Task() as loggingTask:
    loggingTask.ai_channels.add_ai_voltage(f"{dev}/ai0", terminal_config=TerminalConfiguration.RSE, min_val=voltage_min, max_val=voltage_max) # terminal config set to RSE is referenced to DAQ's ground, can also do differential 
    loggingTask.cfg_sample_clk_timing(fs, sample_mode=AcquisitionType.FINITE, samples_per_chan=fs*60*minutes)
    loggingTask.start_trigger.cfg_dig_edge_start_trig(f"{dev}/ai0", edge.RISING)

    loggingTask.start()
    
