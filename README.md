# TeamAutoflow

Welcome to the code repository for our capstone team project for team autoflow!
This repo will contain all code used on the computer and the data acquisition modules, along with their configuration.

## Hardware

#### Data acquisition:
- LabJack DAQ: https://labjack.com/products/labjack-t7-pro?srsltid=AfmBOoqr1vvftwgwHiV0do-kfb_EZijGBZxIicTztYcn1vQxKTV4ERyq
    - Reads pressure transducer data

#### Sensors:

We must measure pressure inside the cylinder heads and the ambient pressure and temperature.

Inside the cylinder heads:
- RIFE 200 PSI Pressure Transducer 1/8" NPT

Ambient temperature and pressure:
- Adafruit BMP280 I2C or SPI Barometric Pressure & Altitude Sensor

#### Microcontroller
- ESP32C6 Devkit C 
    - Used to control throttle body on CBR engine and throttle cable on Predator engine
    - Reads engine speed from crankshaft position sensor on CBR engine 
    - Reads 5V bus to ensure there are no major dips affecting ratiometric pressure transducers
    - Reads ambient temperature and pressure sensor at the beginning of each run
