// BMP280 communication diagnostic for the ESP32-C6-DevKitC-1.
// Expected wiring: sensor VIN -> 3V3, GND -> GND, SDI -> GPIO18, SCK -> GPIO19.
// No libraries needed. Serial monitor at 115200. Repeats every 5 s, so you can
// wiggle or reseat wires between rounds and watch what changes.

#include <Arduino.h>
#include <Wire.h>

const int PIN_SDI = 18;   // sensor SDI (I2C data, SDA)
const int PIN_SCK = 19;   // sensor SCK (I2C clock, SCL)

// ---------- 1. What voltage sits on each line when nothing drives it ----------
void lineCheck(int pin, const char *name) {
  pinMode(pin, INPUT_PULLDOWN); delay(5); int withPulldown = digitalRead(pin);
  pinMode(pin, INPUT_PULLUP);   delay(5); int withPullup   = digitalRead(pin);
  pinMode(pin, INPUT);

  Serial.printf("  GPIO%d (%s): with ESP pull-down reads %s, with ESP pull-up reads %s  -> ",
                pin, name, withPulldown ? "HIGH" : "LOW", withPullup ? "HIGH" : "LOW");
  if (withPulldown && withPullup)
    Serial.println("OK: the sensor board's pull-up resistor is there, so the wire reaches a powered board");
  else if (!withPulldown && withPullup)
    Serial.println("NO pull-up from the sensor: wire not connected, wrong pin, or sensor board unpowered");
  else if (!withPulldown && !withPullup)
    Serial.println("STUCK LOW: line shorted to GND, or the sensor is holding it down");
  else
    Serial.println("odd result (reads high with pull-down, low with pull-up): bad contact?");
}

// ---------- 2. Are the two signal wires shorted together? ----------
void shortCheck() {
  pinMode(PIN_SCK, INPUT_PULLUP);
  pinMode(PIN_SDI, OUTPUT);
  digitalWrite(PIN_SDI, LOW);
  delay(5);
  int sckWhileSdiLow = digitalRead(PIN_SCK);
  pinMode(PIN_SDI, INPUT);
  delay(5);
  int sckAfter = digitalRead(PIN_SCK);
  pinMode(PIN_SCK, INPUT);

  if (!sckWhileSdiLow && sckAfter)
    Serial.println("  SHORT: pulling GPIO18 low also pulls GPIO19 low. SDI and SCK are touching.");
  else
    Serial.println("  No short between GPIO18 and GPIO19.");
}

// ---------- 3. I2C scan with a given pin order and speed ----------
int readChipId(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0xD0);                              // chip ID register
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom(addr, (uint8_t)1) != 1) return -1;
  return Wire.read();
}

const char *errName(int e) {
  switch (e) {
    case 0: return "ACK (device answered)";
    case 2: return "NACK (nobody at this address)";
    case 3: return "NACK on data";
    case 5: return "TIMEOUT (bus stuck, usually a wiring problem)";
    default: return "other error";
  }
}

int scan(int sda, int scl, uint32_t hz) {
  Wire.end();
  Wire.begin(sda, scl, hz);
  Wire.setTimeOut(50);

  Serial.printf("  SDA=GPIO%d SCL=GPIO%d at %lu Hz: ", sda, scl, (unsigned long)hz);
  int found = 0, timeouts = 0;
  for (uint8_t a = 0x08; a <= 0x77; a++) {
    Wire.beginTransmission(a);
    int e = Wire.endTransmission();
    if (e == 0) {
      found++;
      int id = readChipId(a);
      Serial.printf("\n    device at 0x%02X, chip ID 0x%02X", a, id);
      if (id == 0x58)      Serial.print("  = BMP280  <-- this is your sensor");
      else if (id == 0x60) Serial.print("  = BME280  <-- this is your sensor");
      else if (id == 0x55) Serial.print("  = BMP180 (older chip, different library)");
    } else if (e == 5) {
      timeouts++;
    }
  }
  if (found == 0) {
    // show exactly what the two BMP280 addresses returned
    Wire.beginTransmission(0x76); int e76 = Wire.endTransmission();
    Wire.beginTransmission(0x77); int e77 = Wire.endTransmission();
    Serial.printf("nothing found. 0x76: %s, 0x77: %s", errName(e76), errName(e77));
    if (timeouts) Serial.printf(" (%d timeouts)", timeouts);
  }
  Serial.println();
  Wire.end();
  return found;
}

void setup() {
  Serial.begin(115200);
  delay(1500);
}

void loop() {
  Serial.println();
  Serial.println("==================== BMP280 diagnostic ====================");

  Serial.println("1. Line check (is each wire connected to a powered sensor?)");
  lineCheck(PIN_SDI, "SDI/SDA");
  lineCheck(PIN_SCK, "SCK/SCL");

  Serial.println("2. Short check");
  shortCheck();

  Serial.println("3. I2C scans");
  int ok = scan(PIN_SDI, PIN_SCK, 100000);      // normal wiring, normal speed
  if (!ok) ok = scan(PIN_SDI, PIN_SCK, 10000);  // normal wiring, very slow (long/poor wires)
  if (!ok) {
    int swapped = scan(PIN_SCK, PIN_SDI, 100000);  // SDI and SCK swapped
    if (swapped) Serial.println("  ==> Works with the pins SWAPPED: exchange the wires on 18 and 19.");
  }

  Serial.println("4. Verdict");
  if (ok) {
    Serial.println("  The sensor answers on the expected pins. Wiring is good.");
    Serial.println("  If the main firmware still says ambient=0, check the chip ID above matches BMP280/BME280.");
  } else {
    Serial.println("  No answer. Most likely causes, in order:");
    Serial.println("   a) header pins on the sensor not soldered (light flickers when pressed)");
    Serial.println("   b) CS floating low put the chip in SPI mode: wire CS -> 3V3, then unplug USB and replug");
    Serial.println("   c) SDO floating: wire SDO -> GND (address becomes 0x76)");
    Serial.println("   d) sensor GND not connected to ESP32 GND");
  }
  delay(5000);
}
