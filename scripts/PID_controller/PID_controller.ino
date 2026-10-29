// PID controller on ESP-32 dev kit c
// Contributers: Riley Pogue
// last edited: 22 October 2026
//
#include <ESP32Servo.h>

Servo servo;


const int signalPin = 4; // GPIO pin connected to CPK signal pin
const int SERVO_PIN = 21; // SERVO pin connected to control of servo motor

const uint32_t posPerRev = 12; // teeth including any missing ones
const uint8_t window = 12;
const uint8_t minInterval = 50; // in us, to ignore if lower than this for noise
const uint32_t stallTimeout = 500000;

volatile uint32_t prevDt = 0;
volatile uint32_t sumDt = 0;
volatile uint8_t nDt = 0;
volatile int64_t measTime = 0;
volatile bool lastWasGap = false;
volatile bool recording = false;
volatile uint32_t gapCount = 0;
volatile bool newMeasurement = false;                         
volatile unsigned long lastEdge = 0;
long lastTarget = 0;

volatile unsigned long avgPeriod;

void IRAM_ATTR ISR() { // ISR, placed in RAM not flash to lower latency
  unsigned long currTime = micros(); // get current time in microseconds
  uint32_t dt = currTime - lastEdge; 
  if (dt < minInterval) return; // ignore noise < 50 us
  lastEdge = currTime;
  
  if (prevDt == 0) { // return, we need previous interval to compare against
    prevDt = dt;
    return;
  } 

  // check to see if this is greater than 1.5x last interval
  if (dt * 2 > prevDt * 3 && !lastWasGap) {
    lastWasGap = true;
    gapCount++;
    return;
  }
  lastWasGap = false;
  prevDt = dt;

  sumDt += dt;
  if (++nDt >= window) {
    avgPeriod = sumDt / nDt;
    sumDt = 0;
    nDt = 0;
    measTime = esp_timer_get_time();
    newMeasurement = true;
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(10);
  pinMode(signalPin, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(signalPin), ISR, FALLING); // perform ISR on every falling edge
  servo.attach(SERVO_PIN, 500, 2500); // min and max values for PWM, may need to calibrate this to be different
  servo.write(0);                                      
  while (!Serial);
}

void loop() {
  static uint32_t RPM = 0;
  static uint32_t lastStallPrint = 0;

  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line == "START") {
      recording = true;
      int64_t t = esp_timer_get_time();
      Serial.printf("%lld,START,0\n",t);
      Serial.printf("%lld,TARGET,%ld\n",t, lastTarget);
    }
    else if (line == "STOP") {
      recording = false;
      Serial.printf("%lld,STOP,0\n",esp_timer_get_time());
    }
    else if (line.startsWith("SPEED ")) {
      long target = line.substring(6).toInt(); // typecast from index 6 to the end as int
      Serial.printf("%lld,TARGET,%ld\n",esp_timer_get_time(), target);
      servo.write(constrain(target, 0, 180));
      lastTarget=target;
    }
  }

  noInterrupts();
  bool fresh = newMeasurement;
  uint32_t avg = avgPeriod;
  uint32_t gaps = gapCount;
  uint32_t last = lastEdge;
  int64_t mt = measTime;
  newMeasurement = false;
  interrupts();

  if (recording) {
    if (fresh && avg > 0) {
      RPM = 60000000UL / (avg * posPerRev);
      Serial.printf("%lld,RPM,%lu\n", mt, (unsigned long)RPM);
      Serial.printf("%lld,GAPS,%lu\n", mt, (unsigned long)gaps);
    }
    else if (micros() - last > stallTimeout) {
      noInterrupts();
      prevDt = 0;
      sumDt = 0;
      nDt = 0;
      lastWasGap = false;
      newMeasurement = false;
      interrupts();
      RPM = 0;

      if (millis() - lastStallPrint >= 100) {
        lastStallPrint = millis();
        Serial.printf("%lld,RPM,0\n", esp_timer_get_time());
      }
    }
  }
}
