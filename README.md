# TeamAutoflow

Welcome to the code repository for our capstone team project for team autoflow!
This repo will contain all code used on the computer and the data acquisition modules, along with their configuration.

## Hardware

#### Data acquisition:
This project runs on two DAQs: the LabJack T7 Pro, and the NI multifunction I/O USB 2.0 DAQ. 
-LabJack DAQ: https://labjack.com/products/labjack-t7-pro?srsltid=AfmBOoqr1vvftwgwHiV0do-kfb_EZijGBZxIicTztYcn1vQxKTV4ERyq
-NI DAQ: https://www.digikey.com/en/products/detail/ni/782602-01/12817647?gclsrc=aw.ds&gad_source=1&gad_campaignid=20560891351&gclid=Cj0KCQjwteTUBhD4ARIsAEYjs3qSkLALeph9ORLpWYowrWSWvuWko_HNv-1DPwD-L17rhGLTlGoupq8aAkMKEALw_wcB

#### Sensors:
We must measure pressure and temperature both inside the cylinder heads and the ambient pressure and temperature.

Inside the cylinder heads:
- DATAQ pressure transducer
  - Measures from -14.5 to 15 psi.
  - Accurate to ±0.5%FS.
  - https://dataq.com/products/accessories/pressure-sensor/2000361-hs-n1515.html
- Temperature probe:
  - TBD

  Ambient temperature and pressure:
  - Also tbd but probably digital over SPI 

  #### Microcontrollers
  - Currently we have two Arduino UNOs?
