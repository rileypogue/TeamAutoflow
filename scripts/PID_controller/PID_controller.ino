// Flowbench ESP32 firmware: crank RPM measurement + ESC (motor) + throttle-body control + ambient sensor
#include <Arduino.h>
#include <esp_timer.h>
#include <Wire.h>
#include <Adafruit_BMP280.h>

// Pins: ESP32-C6-DevKitC-1 
const int SIGNAL_PIN = 4;                       // crank/tooth signal  - soldered in 
const int ESC_PIN = 5;                         // Predator throttle servo signal  - soldered in 
const int RPWM_PIN = 10, LPWM_PIN = 11;         // BTS7960 PWM inputs: connected to header pins
const int R_EN_PIN = 22, L_EN_PIN = 23;         // BTS7960 enables: connected to header pins
const int TPS1_PIN = 2, TPS2_PIN = 3;           // throttle position sensors (ADC1_CH2, ADC1_CH3) : conected to throttle body 6 pin connector (add screw terminals)
const int I2C_SDA_PIN = 18, I2C_SCL_PIN = 19;   // ambient sensor - wired already 

// Electronic speed control for PWM
const int ESC_FREQ = 50, ESC_BITS = 14; // throttle servo control at 50 Hz
const float ESC_MIN_US = 500.0f, ESC_MAX_US = 2500.0f; // 

// RPM measurement 
const uint32_t posPerRev = 12;       // teeth including any missing ones
const uint8_t  window = 12;
const uint8_t  minInterval = 50;     // us, ignore edges closer than this (noise)
const uint32_t stallTimeout = 500000;

// constants for CKP RPM counter
volatile uint32_t prevDt = 0;
volatile uint32_t sumDt = 0;
volatile uint8_t  nDt = 0;
volatile int64_t  measTime = 0;
volatile bool     lastWasGap = false;
volatile uint32_t gapCount = 0;
volatile bool     newMeasurement = false;
volatile unsigned long lastEdge = 0;
volatile unsigned long avgPeriod = 0;

// settings for throttle drive 
const int   PWM_FREQ  = 15000; // fast for smooth changes
const int   PWM_BITS  = 12;
const int   PWM_MAX   = (1 << PWM_BITS) - 1;
const float MAX_DUTY  = 0.35f;   // hard cap on motor drive (0..1). Raise slowly.
const float CAL_DUTY  = 0.30f;   // drive level while finding the end stops
const int   DIR       = 1;       // set to -1 if positive drive CLOSES the plate

// still need to get these numbers from Austin lol
const bool  HAVE_CAL = false;
const float CAL_CLOSED = 0.0f, CAL_OPEN = 0.0f, CAL_LIMP = 0.0f, CAL_SUM0 = 0.0f;

//  PI servo motor -> throttle controller initial values (need to tune!!) 
const float KP = 1.5f, KI = 0.5f; // planning on no D for this system 
const float KFF_UP = 0.4f, KFF_DOWN = 0.4f; 
const float I_MAX = 0.3f;
const float DEADBAND = 0.01f;

// timing 
const unsigned long CTRL_PERIOD_US = 2000;     // 500 Hz throttle loop
const unsigned long LOG_PERIOD_US  = 40000;    // 25 Hz throttle log
const unsigned long AMB_PERIOD_MS  = 10000;     // 0.1 Hz ambient log, since temp and pressure should barely change

// state-
bool  gCal = HAVE_CAL;
float gClosed = CAL_CLOSED, gOpen = CAL_OPEN, gLimp = CAL_LIMP, gSum0 = CAL_SUM0;
bool  recording = false;
bool  ctrlOn = false;
bool  faulted = false;
float lastAngle = 0;
float target = 0, integ = 0, prevErr = 0, pos = 0, lastCmd = 0, lastTps1 = 0;
unsigned long tPrev = 0, lastCtrlUs = 0, lastLogUs = 0, lastAmbMs = 0;

// i2c setup for ambient pressure sensor
Adafruit_BMP280 bmp(&Wire);
bool haveAmbient = false;
String rx;

// ISR for CKP sensing for RPM. ESP32 prioritizes interrupts, which occur every time a teeth on the crankshaft passes hall effect sensor
void IRAM_ATTR ISR() {
  unsigned long currTime = micros();
  uint32_t dt = currTime - lastEdge; // time since last edge
  if (dt < minInterval) return; // reject any noise 
  lastEdge = currTime; 

  if (prevDt == 0) {
    prevDt = dt;
    return;
  }

  if (dt * 2 > prevDt * 3 && !lastWasGap) {   // gap if > 1.5x previous interval, still unsure if CBR engine has 12 or 11 teeth (missing tooth often exists for position sensing)
    lastWasGap = true;
    gapCount++;
    return;
  }
  lastWasGap = false; 
  prevDt = dt;

  sumDt += dt;
  if (++nDt >= window) { // windowing to average out any measurement noise, window is a single revolution (could potentially make longer if the readings jump around a lot )
    avgPeriod = sumDt / nDt;
    sumDt = 0; // reset values for next revolution
    nDt = 0;
    measTime = esp_timer_get_time();
    newMeasurement = true;
  }
}


// ----- Electronic Speed Control method 
void escWrite(float angle) {
  angle = constrain(angle, 0.0f, 180.0f); // limit angle input because servo is 180 degree
  float us = ESC_MIN_US + angle / 180.0f * (ESC_MAX_US - ESC_MIN_US); // get pulse width in us from angle out of full-scale range
  uint32_t duty = (uint32_t)(us / 20000.0f * ((1 << ESC_BITS) - 1) + 0.5f); // convert pulse width to timer counts (0-16383 for 14 bit timer)  
  ledcWrite(ESC_PIN, duty); // write PWM with new duty cycle
  lastAngle = angle; // set in lastAngle for logging 
}

// ----- throttle hardware helpers: copied from throttle.ino 
// both sides of the H bridge need to be high for it to turn on, so enable  both pins when we want it on
void enableDriver(bool on) {
  digitalWrite(R_EN_PIN, on ? HIGH : LOW);
  digitalWrite(L_EN_PIN, on ? HIGH : LOW);
}

// drive throttle body: -1 closes it at full force, 1 opens it at full force
void drive(float cmd) {   // -1..1, clamped to MAX_DUTY to protect gears
  cmd = constrain(cmd * DIR, -MAX_DUTY, MAX_DUTY); 
  int duty = (int)(fabsf(cmd) * PWM_MAX); // duty is truncated absolute value of cmd time max counts
  ledcWrite(RPWM_PIN, cmd > 0 ? duty : 0);  // if positive command, right side pin is set (opens throttle body) 
  ledcWrite(LPWM_PIN, cmd < 0 ? duty : 0); // if negative command, left side pin is set, to close throttle body
}

void stopAll() { // sets all pins to 0 to stop movement on throttle body and return it to closed position 
  ledcWrite(RPWM_PIN, 0);
  ledcWrite(LPWM_PIN, 0);
  enableDriver(false);
}

// reads voltavge from throttle body as fraction of ADC full scale range from potentiometer on throttle body  
float readPin(int pin, int n = 8) {
  long total = 0;
  for (int i = 0; i < n; i++) total += analogRead(pin);
  return (float)total / n / 4095.0f; // average reading over 8 reads
}

// safety check for throttle readings. if reading is 1.0 or 0.0 theres probably a short or broken wire or something isn't connected correctly because throttle never sits all the way closed or open
bool plausible(float a, float b, float sum0) {
  if (a < 0.02f || a > 0.98f || b < 0.02f || b > 0.98f) return false;
  return fabsf((a + b) - sum0) < 0.08f;
}



// ---------- Throttle closed loop control ---
// start control 
void startControl() { 
  if (ctrlOn) return; // return immediately if already running
  integ = 0; // else reset values
  prevErr = 0;
  tPrev = micros();
  enableDriver(true);
  ctrlOn = true;
}

// if fault detected emergency stop
void fault(const char *msg) {
  stopAll();
  ctrlOn = false;
  faulted = true;
  Serial.printf("# FAULT %s - throttle motor off - send RESET\n", msg);
}

// control throttle body (adjust back to requrested position)
void controlStep() {
  if (faulted) return; // if faulted do nothing because we should be emergency stopped

  unsigned long now = micros(); // time since last step for integral control
  float dt = (now - tPrev) / 1e6f;
  tPrev = now;

  // read both sensors
  float a = readPin(TPS1_PIN), b = readPin(TPS2_PIN);
  lastTps1 = a;
  if (!gCal) return; // if not calibrated return because that would make the follwoing steps not work

  if (!plausible(a, b, gSum0)) { // also return if sensor readings are incorrect or possibly faulted
    fault("TPS sensors disagree or out of range");
    return;
  }

  float span = gOpen - gClosed; // span of calibrated values
  float limpN = (gLimp - gClosed) / span; // where the plate sits when nothing is driving it
  pos = (a - gClosed) / span; // converts raw TPS1 fraction readings into how open/closed throttle is from 0 - 1 (0 = closed, 1 = open)

  if (!ctrlOn) return; // if control isn't on stop here (we still can log but won't change the throttle position)

  float err = target - pos; // feed forward error 
  float kff = (pos > limpN) ? KFF_UP : KFF_DOWN; // pos -limp position is the spring force required to push to intended position 
  float ff = kff * (pos - limpN); // if above limp hold open, if - we're more closed so spring needs to hold closed 
  float cmd;

  if (fabsf(err) < DEADBAND) { // if we're close enough (error less than deadband) just hold it there
    cmd = ff;
  } else { // else we need to accumulate error over time (KI controller)
    integ = constrain(integ + err * dt, -I_MAX, I_MAX);
    cmd = KP * err + KI * integ +  ff; // proporitonal error pushes back and small errors add up over time with intgrall controller
  }
  prevErr = err;

  lastCmd = constrain(cmd, -MAX_DUTY, MAX_DUTY); // copy for logging
  drive(cmd); // send command to motor
}

// try and communicate with BMP280 over I2C
bool ambientBegin() {
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN); // uses Arduino Wire library for I2C, set the two wires (data and clock)
  Wire.setClock(50000);
  for (int i= 0; i < 10; i++) { // try a couple times because it doesn't always start immediately for some reason
    if (bmp.begin(0x77) || bmp.begin(0x76)) return true;
    delay(100);
  }
  return false;   // last time answered at 0x77, who knows if thats permanent
}

// read temperature and pressure from BMP280 
void ambientRead(float &tC, float &pKpa) {
  tC = bmp.readTemperature();
  pKpa = bmp.readPressure() / 1000.0f;   // absolute pressure, divide by 1000 to get kPa 
}

// helper for logging 
void emit(int64_t t, const char *type, float value) {
  Serial.printf("%lld,%s,%.4f\n", (long long)t, type, value);
}

// serial commands 
bool isNumberStart(const String &s) {
  return s.length() > 0 && (isDigit(s[0]) || s[0] == '.');
}

// read commands we recieve from computer
void handleCommand(String cmd) {
  cmd.trim(); // for uniformity
  cmd.toUpperCase();
  if (cmd.length() == 0) return;

  if (cmd == "START") { // command to begin logging
    recording = true;
    lastLogUs = 0;
    lastAmbMs = 0;
    int64_t t = esp_timer_get_time();
    Serial.printf("%lld,START,0\n", (long long)t);
    emit(t, "TARGET", lastAngle);
    emit(t, "THR_TARGET", target * 100.0f);
  } else if (cmd == "STOP") { // stop recording
    recording = false;
    Serial.printf("%lld,STOP,0\n", (long long)esp_timer_get_time());
  } else if (cmd.startsWith("SPEED")) { // predator servo angle
    String arg = cmd.substring(5);
    arg.trim();
    if (!isNumberStart(arg)) { Serial.println("# ERR SPEED needs a number"); return; }
    float v = arg.toFloat();
    if (v < 0 || v > 180)    { Serial.println("# ERR SPEED out of range 0-180"); return; }
    escWrite(v);
    emit(esp_timer_get_time(), "TARGET", v);
  } else if (cmd.startsWith("THROTTLE")) { // bosch throttle angle
    String arg = cmd.substring(8);
    arg.trim();
    if (faulted)  { Serial.println("# ERR throttle faulted - send RESET"); return; }
    if (!gCal)    { Serial.println("# ERR throttle not calibrated - send CAL"); return; }
    if (!isNumberStart(arg)) { Serial.println("# ERR THROTTLE needs a number"); return; }
    float v = arg.toFloat();
    if (v < 0 || v > 100) { Serial.println("# ERR THROTTLE out of range 0-100"); return; }
    target = v / 100.0f;
    startControl();
    emit(esp_timer_get_time(), "THR_TARGET", v);
  } else if (cmd.startsWith("MARK")) { // timestep for sweeps
    String arg = cmd.substring(4);
    arg.trim();
    Serial.printf("%lld,MARK,%ld\n", (long long)esp_timer_get_time(), arg.toInt());
  } else if (cmd == "RESET") { // clear latched fault
    faulted = false;
    Serial.println("# fault cleared");
  } else { // else error
    Serial.println("# ERR unknown command");
  }
}


void setup() {
  Serial.begin(115200);

  pinMode(SIGNAL_PIN, INPUT_PULLUP); // crankshaft position input signal, set with input pullup just in case wiring on LM1815 is wonk and pull up resistor isn't enough
  attachInterrupt(digitalPinToInterrupt(SIGNAL_PIN), ISR, FALLING); // using interrupts to count CKP signal, 

  pinMode(R_EN_PIN, OUTPUT);
  pinMode(L_EN_PIN, OUTPUT);
  enableDriver(false);
  
  // using ledc for generic PWM not LED control (timer will work well even ESP32 is busy reading ckp signals etc.)
  ledcAttach(ESC_PIN, ESC_FREQ, ESC_BITS);     // core 3.x API,  
  escWrite(0);
  ledcAttach(RPWM_PIN, PWM_FREQ, PWM_BITS);
  ledcAttach(LPWM_PIN, PWM_FREQ, PWM_BITS);
  ledcWrite(RPWM_PIN, 0);
  ledcWrite(LPWM_PIN, 0);

  analogReadResolution(12); // native resolution of esp32 
  analogSetPinAttenuation(TPS1_PIN, ADC_11db); // we expect to see about 3.0V input so we need to attenuate because the internal ADC can only read 0-1V
  analogSetPinAttenuation(TPS2_PIN, ADC_11db);

  haveAmbient = ambientBegin();
  rx.reserve(64); // recieved string to collect incoming commands from computer. allocates bytes upfront to avoid memory fragmentation
  Serial.printf("# READY throttle_calibrated=%d ambient=%d\n", gCal ? 1 : 0, haveAmbient ? 1 : 0);
}

void loop() {
  static uint32_t RPM = 0;
  static unsigned long lastStallPrint = 0, lastGapPrintMs = 0;

  // serial commands
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleCommand(rx); rx = ""; }
    else if (c != '\r' && rx.length() < 64) rx += c;
  }

  // throttle control loop
  unsigned long nowUs = micros();
  if (nowUs - lastCtrlUs >= CTRL_PERIOD_US) {
    lastCtrlUs = nowUs;
    controlStep();
  }

  // RPM snapshot from the ISR
  noInterrupts(); // need to freeze interrupts so we don't get weird intermediate values
  bool fresh = newMeasurement;
  uint32_t avg = avgPeriod;
  uint32_t gaps = gapCount;
  uint32_t last = lastEdge;
  int64_t mt = measTime;
  newMeasurement = false; // reset and allow for interrrupts again
  interrupts();

  if (!recording) return;

  // logging code
  if (fresh && avg > 0) { // guard against divide by 0 errors
    // calculate RPM: to get from microseconds between teeth we divide 60,000,000 by (avg us * number of teeth per revolution)
    RPM = 60000000UL / (avg * posPerRev); // UL tells us to treat as unsigned long
    Serial.printf("%lld,RPM,%lu\n", (long long)mt, (unsigned long)RPM); // print timestamp and RPM data
    if (millis() - lastGapPrintMs >= 1000) { // gap code - used to figure out whether theres 11 or 12 teeth (we will have a gap every revolution if theres 11)
      lastGapPrintMs = millis();
      Serial.printf("%lld,GAPS,%lu\n", (long long)mt, (unsigned long)gaps);
    }
  } else if (micros() - last > stallTimeout) { // if no teeth have arrived in stallTimeout (500,000 us) then we should reset our data  to not get old data averaged in
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
      Serial.printf("%lld,RPM,0\n", (long long)esp_timer_get_time()); // if stalled print 0 RPM
    }
  }

  // throttle log (25 Hz)
  if (!faulted && (nowUs - lastLogUs >= LOG_PERIOD_US)) { 
    lastLogUs = nowUs;
    int64_t t = esp_timer_get_time();
    emit(t, "tps1", lastTps1);
    if (gCal && ctrlOn) {
      emit(t, "pos", pos);
      emit(t, "target", target);
      emit(t, "cmd", lastCmd);
    }
  }

  // ambient log (1 Hz)
  if (haveAmbient && (millis() - lastAmbMs >= AMB_PERIOD_MS)) {
    lastAmbMs = millis();
    float tC, pK;
    ambientRead(tC, pK);
    int64_t t = esp_timer_get_time();
    emit(t, "amb_T", tC);
    emit(t, "amb_P", pK);
  }
}
