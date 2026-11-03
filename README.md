# TeamAutoflow

Welcome to the code repository for our capstone team project for team autoflow!
This repo will contain all code used on the computer and the data acquisition modules, along with their configuration.

## Hardware

#### Data acquisition:

This project runs on two DAQs: the LabJack T7 Pro, and the NI multifunction I/O USB 2.0 DAQ.
- LabJack DAQ: https://labjack.com/products/labjack-t7-pro?srsltid=AfmBOoqr1vvftwgwHiV0do-kfb_EZijGBZxIicTztYcn1vQxKTV4ERyq
- NI DAQ: https://www.digikey.com/en/products/detail/ni/782602-01/12817647?gclsrc=aw.ds&gad_source=1&gad_campaignid=20560891351&gclid=Cj0KCQjwteTUBhD4ARIsAEYjs3qSkLALeph9ORLpWYowrWSWvuWko_HNv-1DPwD-L17rhGLTlGoupq8aAkMKEALw_wcB

#### Sensors:

We must measure pressure and temperature both inside the cylinder heads and the ambient pressure and temperature.

Inside the cylinder heads:
- RIFE 200 PSI Pressure Transducer 1/8" NPT
    
- Temperature probe:
  - Omega 5SRTC-TT-K-40-36 - Insulated Thermocouple, Ready-Made, K Type, 36" Cable, 0 C to 260 C (32 F to 500 F), ANSI (Std), 5SRTC Series
  - Analog Output K-Type Thermocouple Amplifier

  Ambient temperature and pressure:
  - Adafruit BMP280 I2C or SPI Barometric Pressure & Altitude Sensor

  #### Microcontrollers
  - ESP32 Devkit C
