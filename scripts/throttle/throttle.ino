// Bosch throttle body + BTS7960 on ESP32 (Arduino core 3.x)
// Stages: TEST -> CALIBRATE -> RUN. Change MODE and re-upload for each.
// written by Austin & Cole

#include <Arduino.h>

enum Mode { TEST, CALIBRATE, RUN };
const Mode MODE = TEST;

// ---------- Pins (match your wiring) ----------
const int RPWM_PIN = 25, LPWM_PIN = 26;
const int R_EN_PIN = 27, L_EN_PIN = 13;
const int TPS1_PIN = 32, TPS2_PIN = 33;

// ---------- Drive settings ----------
const int   PWM_FREQ  = 15000;
const int   PWM_BITS  = 12;
const int   PWM_MAX   = (1 << PWM_BITS) - 1;
const float MAX_DUTY  = 0.35f;   // hard cap on motor drive (0..1). Raise slowly.
const float CAL_DUTY  = 0.30f;   // drive level while finding the end stops
const int   DIR       = 1;       // set to -1 if positive drive CLOSES the plate

// ---------- Calibration values (fractions 0..1) ----------
// Paste the numbers printed by CALIBRATE, then set HAVE_CAL = true.
const bool  HAVE_CAL = false;
const float CAL_CLOSED = 0.0f, CAL_OPEN = 0.0f, CAL_LIMP = 0.0f, CAL_SUM0 = 0.0f;

// ---------- Controller tuning (starting points) ----------
const float KP = 1.5f, KI = 0.5f, KD = 0.0f;
const float KFF_UP = 0.4f, KFF_DOWN = 0.4f;  // spring feedforward above/below limp-home
const float I_MAX = 0.3f;                    // anti-windup limit
const float DEADBAND = 0.01f;

struct Step { float target; float seconds; };   // target: 0 = closed, 1 = open
const Step SEQUENCE[] = { {0.25f, 2.0f}, {0.60f, 2.0f}, {0.40f, 2.0f}, {0.25f, 2.0f} };
const int  SEQ_LEN = sizeof(SEQUENCE) / sizeof(SEQUENCE[0]);

// ============ Hardware helpers ============
void enableDriver(bool on) {
  digitalWrite(R_EN_PIN, on ? HIGH : LOW);
  digitalWrite(L_EN_PIN, on ? HIGH : LOW);
}

// cmd in -1..1; positive = RPWM side. Clamped to MAX_DUTY.
void drive(float cmd) {
  cmd = constrain(cmd * DIR, -MAX_DUTY, MAX_DUTY);
  int duty = (int)(fabsf(cmd) * PWM_MAX);
  ledcWrite(RPWM_PIN, cmd > 0 ? duty : 0);
  ledcWrite(LPWM_PIN, cmd < 0 ? duty : 0);
}

void stopAll() {
  ledcWrite(RPWM_PIN, 0);
  ledcWrite(LPWM_PIN, 0);
  enableDriver(false);
}

float readPin(int pin, int n = 8) {
  long total = 0;
  for (int i = 0; i < n; i++) total += analogRead(pin);
  return (float)total / n / 4095.0f;
}

bool plausible(float a, float b, float sum0) {
  if (a < 0.02f || a > 0.98f || b < 0.02f || b > 0.98f) return false;  // broken/shorted wire
  return fabsf((a + b) - sum0) < 0.08f;                                // tracks should mirror
}

// ============ Step 1: open-loop test ============
void runTest() {
  Serial.println("Open-loop test: two short pulses, watch the plate.");
  float a = readPin(TPS1_PIN), b = readPin(TPS2_PIN);
  Serial.printf("rest      TPS1 %.3f  TPS2 %.3f  sum %.3f\n", a, b, a + b);

  enableDriver(true);
  const float cmds[2] = {0.25f, -0.25f};
  for (int i = 0; i < 2; i++) {
    drive(cmds[i]);
    delay(250);
    a = readPin(TPS1_PIN);
    b = readPin(TPS2_PIN);
    drive(0);
    Serial.printf("cmd %+.2f  TPS1 %.3f  TPS2 %.3f  sum %.3f\n", cmds[i], a, b, a + b);
    delay(700);
  }
  stopAll();
  Serial.println("Expected: + pulse opens the plate, TPS1 rises, TPS2 falls.");
  Serial.println("Plate opened but TPS1 fell: swap the TPS1/TPS2 pins.");
  Serial.println("Plate closed on the + pulse: set DIR = -1.");
  Serial.println("Nothing moved: raise MAX_DUTY a little (0.45) and retry.");
}

// ============ Step 2: calibration ============
float driveUntilStall(float cmd, unsigned long timeoutMs = 3000) {
  enableDriver(true);
  drive(cmd);
  float last = readPin(TPS1_PIN);
  int stable = 0;
  unsigned long t0 = millis();
  while (millis() - t0 < timeoutMs) {
    delay(50);
    float now = readPin(TPS1_PIN);
    stable = (fabsf(now - last) < 0.002f) ? stable + 1 : 0;
    last = now;
    if (stable >= 4) break;
  }
  drive(0);
  return last;
}

bool calibrate(float &closedV, float &openV, float &limp, float &sum0) {
  Serial.println("Calibrating: keep clear of the plate.");
  openV = driveUntilStall(CAL_DUTY);
  closedV = driveUntilStall(-CAL_DUTY);
  stopAll();
  delay(1000);                       // spring returns the plate to limp-home
  float a = readPin(TPS1_PIN), b = readPin(TPS2_PIN);
  limp = a;
  sum0 = a + b;
  if (openV - closedV < 0.2f) {
    Serial.println("Plate barely moved or TPS1 fell on opening. Run TEST and check DIR and TPS pins.");
    return false;
  }
  Serial.printf("CAL_CLOSED = %.4f\n", closedV);
  Serial.printf("CAL_OPEN   = %.4f\n", openV);
  Serial.printf("CAL_LIMP   = %.4f\n", limp);
  Serial.printf("CAL_SUM0   = %.4f\n", sum0);
  return true;
}

// ============ Step 3: closed-loop run ============
void runLoop(float closedV, float openV, float limp, float sum0) {
  float span = openV - closedV;
  float limpN = (limp - closedV) / span;
  float integ = 0, prevErr = 0, pos = 0;
  unsigned long tPrev = micros();

  enableDriver(true);
  for (int s = 0; s < SEQ_LEN; s++) {
    float target = SEQUENCE[s].target;
    Serial.printf("target %.2f\n", target);
    unsigned long tEnd = millis() + (unsigned long)(SEQUENCE[s].seconds * 1000);

    while ((long)(tEnd - millis()) > 0) {
      unsigned long now = micros();
      float dt = (now - tPrev) / 1e6f;
      tPrev = now;

      float a = readPin(TPS1_PIN), b = readPin(TPS2_PIN);
      if (!plausible(a, b, sum0)) {
        stopAll();
        Serial.println("TPS fault: sensors disagree or out of range. Stopped.");
        return;
      }

      pos = (a - closedV) / span;
      float err = target - pos;
      float kff = (pos > limpN) ? KFF_UP : KFF_DOWN;
      float ff = kff * (pos - limpN);
      float cmd;

      if (fabsf(err) < DEADBAND) {
        cmd = ff;
      } else {
        integ = constrain(integ + err * dt, -I_MAX, I_MAX);
        float deriv = (dt > 0) ? (err - prevErr) / dt : 0;
        cmd = KP * err + KI * integ + KD * deriv + ff;
      }
      prevErr = err;

      drive(cmd);
      delayMicroseconds(1500);
    }
    Serial.printf("  reached pos %.2f\n", pos);
  }
  stopAll();
}

// ============ Setup / main ============
void setup() {
  Serial.begin(115200);

  pinMode(R_EN_PIN, OUTPUT);
  pinMode(L_EN_PIN, OUTPUT);
  enableDriver(false);

  ledcAttach(RPWM_PIN, PWM_FREQ, PWM_BITS);   // core 3.x API
  ledcAttach(LPWM_PIN, PWM_FREQ, PWM_BITS);
  ledcWrite(RPWM_PIN, 0);
  ledcWrite(LPWM_PIN, 0);

  analogReadResolution(12);
  analogSetPinAttenuation(TPS1_PIN, ADC_11db);
  analogSetPinAttenuation(TPS2_PIN, ADC_11db);

  delay(1500);   // time to open the Serial Monitor

  if (MODE == TEST) {
    runTest();
  } else if (MODE == CALIBRATE) {
    float c, o, l, s;
    calibrate(c, o, l, s);
  } else {
    float c, o, l, s;
    bool ok = true;
    if (HAVE_CAL) { c = CAL_CLOSED; o = CAL_OPEN; l = CAL_LIMP; s = CAL_SUM0; }
    else          { ok = calibrate(c, o, l, s); }
    if (ok) runLoop(c, o, l, s);
  }
  stopAll();
  Serial.println("Done. Motor off.");
}

void loop() {
  // Nothing here: each stage runs once in setup(). Press reset to run again.
}
