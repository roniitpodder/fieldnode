/*
 * SU-KRISHI - ESP32 FIELD NODE
 *
 * CONTROL RULES
 * -------------
 *
 * MANUAL:
 *   START -> directly control relay
 *   STOP  -> directly control relay
 *
 * AUTO:
 *   START when:
 *      soil < LOW threshold
 *      sunlight >= minimum threshold
 *      confirmed rain = false
 *
 *   STOP immediately when:
 *      soil >= HIGH threshold
 *      OR sunlight < minimum threshold
 *      OR confirmed rain
 *      OR soil sensor fault
 *      OR maximum runtime reached
 *
 * Hardware:
 *   Relay      = ACTIVE LOW
 *   Soil       = GPIO34
 *   Rain       = GPIO32
 *   LDR        = GPIO35
 *   Buzzer     = GPIO27
 *   RTC SDA    = GPIO21
 *   RTC SCL    = GPIO22
 *   SIM RX     = GPIO16
 *   SIM TX     = GPIO17
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <RTClib.h>
#include <math.h>

// ============================================================================
// NETWORK
// ============================================================================

const char* WIFI_SSID     = "Roniit";
const char* WIFI_PASSWORD = "terabhai";

const char* SERVER_HOST = "172.20.10.4";
const uint16_t SERVER_PORT = 8000;

const char* DEVICE_API_KEY =
    "34bacefc-2dda-4421-a371-d8caf7e80808";

const bool NETWORK_ENABLED = true;

// ============================================================================
// PINS
// ============================================================================

const uint8_t SOIL_PIN   = 34;
const uint8_t RAIN_PIN   = 32;
const uint8_t LDR_PIN    = 35;

const uint8_t RELAY_PIN  = 26;
const uint8_t BUZZER_PIN = 27;

const uint8_t I2C_SDA = 21;
const uint8_t I2C_SCL = 22;

const uint8_t SIM_RX_PIN = 16;
const uint8_t SIM_TX_PIN = 17;

// ACTIVE LOW RELAY
const uint8_t RELAY_ON_LEVEL  = LOW;
const uint8_t RELAY_OFF_LEVEL = HIGH;

// ============================================================================
// SOIL CALIBRATION (OPTIMIZED)
// ============================================================================
//
// Capacitive Soil Moisture Sensor v1.2 / v2.0
//
// Dry in air         ~ 3300 - 3400 ADC (0%)
// Wet a little/damp  ~ 2500 - 2700 ADC (30% - 45%)
// Well-watered soil  ~ 1900 - 2200 ADC (60% - 75%)
// Saturated in water ~ 1300 - 1500 ADC (90% - 100%)
//
// ============================================================================

const int SOIL_ADC_DRY = 3300;
const int SOIL_ADC_WET = 1000;

// Linear response (1.0f) ensures damp soil doesn't jump to 70-100%.
const float SOIL_CURVE = 4.0f;

const int SOIL_FAULT_BELOW_ADC = 30;

const float DEFAULT_SOIL_LOW  = 35.0;
const float DEFAULT_SOIL_HIGH = 70.0;

// ============================================================================
// RAIN CALIBRATION (OPTIMIZED)
// ============================================================================
//
// Rain sensor plate (FC-37 / LM393 on AO pin):
//   Completely DRY plate       ~ 3600 - 4000 ADC (0%)
//   Light droplets / mist      ~ 2600 - 3000 ADC (25% - 45%)
//   Wet / heavy rain           ~ 1500 - 1800 ADC (85% - 100%)
//
// ============================================================================

const int RAIN_ADC_DRY = 3600;
const int RAIN_ADC_WET = 1500;

// Sensor begins registering moisture when ADC drops below this point.
const int RAIN_EFFECTIVE_DRY = 3500;

// Linear response (1.0f) so real water on the plate reaches 85-100%.
const float RAIN_CURVE = 1.0f;

// Rain intensity threshold to confirm rain and stop auto irrigation.
const float RAIN_START_THRESHOLD = 50.0f;

// Hysteresis: clear rain confirmation only when intensity drops below this.
const float RAIN_CLEAR_THRESHOLD = 25.0f;

// Consecutive readings required to confirm rain.
const uint8_t RAIN_CONFIRM_COUNT = 3;

// Consecutive dry readings required to clear rain.
const uint8_t RAIN_CLEAR_COUNT = 5;

const int RAIN_DISCONNECT_ADC = 30;

// ============================================================================
// LDR CALIBRATION (OPTIMIZED)
// ============================================================================
//
// Sensor range on ESP32:
//   Dark / covered           ~ 100 - 300 ADC (0%)
//   Ambient indoor light     ~ 1500 - 2200 ADC (45% - 65%)
//   Bright sunlight / lamp   ~ 3200 - 3300 ADC (100%)
//
// Previous calculation used 4095 as the bright endpoint, which capped
// a real ~3200 ADC reading at roughly 78-80%.
//
// Calibrating the physical range to 100-3200 makes ~3200 ADC = 100%.
//
// NOTE:
//   This assumes your LDR produces a HIGHER ADC reading under brighter light.
//   If your actual hardware behaves opposite to this, set the boolean false.
//
// ============================================================================

const bool LDR_BRIGHT_WHEN_HIGH = true;

const int LDR_ADC_DARK   = 100;
const int LDR_ADC_BRIGHT = 3200;

const float DEFAULT_SUNLIGHT_MIN = 30.0;

// ============================================================================
// PUMP / BUZZER
// ============================================================================

const uint32_t BEEP_PUMP_ON_MS  = 3000;
const uint32_t BEEP_PUMP_OFF_MS = 1000;

const uint32_t MAX_PUMP_RUN_SECONDS = 300;

const uint32_t AUTO_PUMP_CYCLE_SECONDS = 60;

const uint32_t AUTO_RESTART_COOLDOWN_MS = 10000;

// ============================================================================
// TIMING
// ============================================================================

const uint32_t SENSOR_READ_INTERVAL_MS  = 1000;
const uint32_t STATUS_PRINT_INTERVAL_MS = 5000;

const uint32_t READING_POST_INTERVAL_MS = 1000;
const uint32_t COMMAND_POLL_INTERVAL_MS = 500;

const uint32_t WIFI_RETRY_INTERVAL_MS = 5000;

const uint16_t HTTP_TIMEOUT_MS = 3000;

const uint8_t ADC_SAMPLES = 16;

// ============================================================================
// SIM800L
// ============================================================================

const uint32_t SIM_BAUD = 9600;

char SMS_MANAGER_PHONE[20] = "+919876543210";

const char* SMS_TEST_TEXT =
    "SU-KRISHI: test SMS from the field node.";

// ============================================================================
// ENUMS
// ============================================================================

enum LightLevel {
  LIGHT_DARK,
  LIGHT_DIM,
  LIGHT_BRIGHT
};

enum PumpSource {
  SRC_NONE,
  SRC_MANUAL,
  SRC_AUTO
};

// ============================================================================
// SENSOR DATA
// ============================================================================

struct SensorData {

  int soilRaw;
  float soilPct;
  bool soilFault;

  int rainRaw;
  bool rainWet;
  float rainIntensity;

  int ldrRaw;
  LightLevel light;
  float lightPct;
};

SensorData sensors = {
  0,
  0,
  true,

  0,
  false,
  0,

  0,
  LIGHT_DARK,
  0
};

// ============================================================================
// RAIN FILTER STATE
// ============================================================================

uint8_t rainConfirmCounter = 0;
uint8_t rainClearCounter = 0;

// Confirmed rain state.
// This does NOT change because of a single noisy reading.
bool rainConfirmed = false;

// ============================================================================
// RTC
// ============================================================================

RTC_DS3231 rtc;

bool rtcOk = false;
bool rtcLostPower = false;

// ============================================================================
// SIM800L
// ============================================================================

HardwareSerial simSerial(2);

String simStatus = "not tested";

// ============================================================================
// PUMP STATE
// ============================================================================

bool pumpIsOn = false;

PumpSource pumpSource = SRC_NONE;

uint32_t pumpStartMs = 0;

uint32_t pumpRunLimitMs = 0;

uint32_t lastAutoStartMs = 0;

// ============================================================================
// AUTO CONFIG
// ============================================================================

float autoSoilLow = DEFAULT_SOIL_LOW;
float autoSoilHigh = DEFAULT_SOIL_HIGH;

float autoSunlightMin = DEFAULT_SUNLIGHT_MIN;

bool autoModeEnabled = false;

// ============================================================================
// BUZZER
// ============================================================================

bool buzzerOn = false;
uint32_t buzzerOffAtMs = 0;

// ============================================================================
// NETWORK STATE
// ============================================================================

bool wifiWasUp = false;

uint32_t lastWifiTryMs = 0;
uint32_t lastPostMs = 0;
uint32_t lastPollMs = 0;
uint32_t lastSensorMs = 0;
uint32_t lastStatusMs = 0;
uint32_t lastRtcRetryMs = 0;

int lastPostCode = 0;
int lastPollCode = 0;

// ============================================================================
// ACK STATE
// ============================================================================

bool ackPending = false;
uint32_t ackSeconds = 0;

// ============================================================================
// FUNCTION DECLARATIONS
// ============================================================================

String simWaitFor(
    const char* token,
    uint32_t timeoutMs
);

String simCommand(
    const char* cmd,
    uint32_t timeoutMs,
    const char* expect
);

bool simSendSms(
    const char* number,
    const char* text
);

void serviceCritical();

void pumpUpdate();

void buzzerUpdate();

void autoPumpEvaluate();

// ============================================================================
// UTILITY
// ============================================================================

float clampf(
    float value,
    float low,
    float high
) {

  if (value < low)
    return low;

  if (value > high)
    return high;

  return value;
}

bool due(
    uint32_t& last,
    uint32_t interval
) {

  uint32_t now = millis();

  if (now - last >= interval) {

    last = now;

    return true;
  }

  return false;
}

// ============================================================================
// BUZZER
// ============================================================================

void buzzerBeep(
    uint32_t durationMs
) {

  digitalWrite(
      BUZZER_PIN,
      HIGH
  );

  buzzerOn = true;

  buzzerOffAtMs =
      millis() + durationMs;
}

void buzzerUpdate() {

  if (!buzzerOn)
    return;

  if (
      (int32_t)(
        millis() - buzzerOffAtMs
      ) >= 0
  ) {

    digitalWrite(
        BUZZER_PIN,
        LOW
    );

    buzzerOn = false;
  }
}

// ============================================================================
// ADC
// ============================================================================

int readAnalogAvg(
    uint8_t pin
) {

  uint32_t sum = 0;

  for (
      uint8_t i = 0;
      i < ADC_SAMPLES;
      i++
  ) {

    sum += analogRead(pin);

    delayMicroseconds(200);
  }

  return (int)(
      sum / ADC_SAMPLES
  );
}

// ============================================================================
// SENSOR READING
// ============================================================================

void readSensors() {

  // ========================================================================
  // SOIL
  // ========================================================================

  sensors.soilRaw =
      readAnalogAvg(SOIL_PIN);

  sensors.soilFault =
      sensors.soilRaw <
      SOIL_FAULT_BELOW_ADC;

  if (sensors.soilFault) {

    sensors.soilPct = 0;

  } else {

    float span =
        (float)(
          SOIL_ADC_DRY -
          SOIL_ADC_WET
        );

    if (span <= 0.0f)
      span = 1.0f;

    // Convert the sensor reading into a 0..1 wetness value.
    // Higher ADC = drier, lower ADC = wetter.
    float normalized =
        (
          (float)SOIL_ADC_DRY -
          (float)sensors.soilRaw
        ) /
        span;

    normalized =
        clampf(
          normalized,
          0.0f,
          1.0f
        );

    // Gentle curve: small moisture changes remain smaller, while
    // genuinely wet readings still approach 100%.
    float curved =
        powf(
          normalized,
          SOIL_CURVE
        );

    sensors.soilPct =
        clampf(
          curved * 100.0f,
          0.0f,
          100.0f
        );
  }

  // ========================================================================
  // RAIN
  // ========================================================================
  //
  // Tiny droplets are suppressed using:
  //
  //   Effective dry point = 3300
  //   Nonlinear curve      = 3.0
  //
  // Rain is confirmed only after 3 consecutive readings >= 70%.
  // Rain is cleared only after 5 consecutive readings <= 45%.
  //
  // ========================================================================

  sensors.rainRaw =
      readAnalogAvg(RAIN_PIN);

  // ------------------------------------------------------------------------
  // INVALID / DISCONNECTED SENSOR
  // ------------------------------------------------------------------------

  if (
      sensors.rainRaw <
      RAIN_DISCONNECT_ADC
  ) {

    sensors.rainIntensity = 0.0f;

    rainConfirmCounter = 0;

    if (!rainConfirmed) {

      rainClearCounter = 0;

    } else {

      rainClearCounter++;

      if (
          rainClearCounter >=
          RAIN_CLEAR_COUNT
      ) {

        rainConfirmed = false;
        rainClearCounter = 0;

        Serial.println(
            "[RAIN] Rain CLEARED"
        );
      }
    }

    sensors.rainWet =
        rainConfirmed;

  } else {

    // ----------------------------------------------------------------------
    // EFFECTIVE DRY REGION
    // ----------------------------------------------------------------------

    if (
        sensors.rainRaw >=
        RAIN_EFFECTIVE_DRY
    ) {

      sensors.rainIntensity = 0.0f;

    } else {

      float wetSpan =
          (float)(
            RAIN_EFFECTIVE_DRY -
            RAIN_ADC_WET
          );

      if (wetSpan <= 0.0f)
        wetSpan = 1.0f;

      float normalized =
          (
            (float)RAIN_EFFECTIVE_DRY -
            (float)sensors.rainRaw
          ) /
          wetSpan;

      normalized =
          clampf(
            normalized,
            0.0f,
            1.0f
          );

      // Suppress tiny water amounts.
      float curved =
          powf(
            normalized,
            RAIN_CURVE
          );

      sensors.rainIntensity =
          clampf(
            curved * 100.0f,
            0.0f,
            100.0f
          );
    }

    // ----------------------------------------------------------------------
    // RAIN CONFIRMATION / HYSTERESIS
    // ----------------------------------------------------------------------

    if (!rainConfirmed) {

      // --------------------------------------------------------------
      // Currently dry.
      // Need 3 consecutive strong readings.
      // --------------------------------------------------------------

      if (
          sensors.rainIntensity >=
          RAIN_START_THRESHOLD
      ) {

        if (
            rainConfirmCounter <
            RAIN_CONFIRM_COUNT
        ) {

          rainConfirmCounter++;
        }

        rainClearCounter = 0;

        if (
            rainConfirmCounter >=
            RAIN_CONFIRM_COUNT
        ) {

          rainConfirmed = true;

          rainConfirmCounter = 0;

          Serial.printf(
              "[RAIN] Rain CONFIRMED | intensity=%.1f%% raw=%d\n",
              sensors.rainIntensity,
              sensors.rainRaw
          );
        }

      } else {

        rainConfirmCounter = 0;
      }

    } else {

      // --------------------------------------------------------------
      // Currently raining.
      // Need 5 consecutive dry readings to clear.
      // --------------------------------------------------------------

      if (
          sensors.rainIntensity <=
          RAIN_CLEAR_THRESHOLD
      ) {

        rainClearCounter++;

        rainConfirmCounter = 0;

        if (
            rainClearCounter >=
            RAIN_CLEAR_COUNT
        ) {

          rainConfirmed = false;

          rainClearCounter = 0;

          Serial.printf(
              "[RAIN] Rain CLEARED | intensity=%.1f%% raw=%d\n",
              sensors.rainIntensity,
              sensors.rainRaw
          );
        }

      } else {

        rainClearCounter = 0;
      }
    }

    sensors.rainWet =
        rainConfirmed;
  }

// ========================================================================
  // LDR
  // ========================================================================

  sensors.ldrRaw = readAnalogAvg(LDR_PIN);

  float ldrSpan = (float)(LDR_ADC_BRIGHT - LDR_ADC_DARK);
  if (ldrSpan <= 0.0f) ldrSpan = 1.0f;

  float normalizedLight;
  if (LDR_BRIGHT_WHEN_HIGH) {
    normalizedLight = ((float)sensors.ldrRaw - (float)LDR_ADC_DARK) / ldrSpan;
  } else {
    normalizedLight = ((float)LDR_ADC_BRIGHT - (float)sensors.ldrRaw) / ldrSpan;
  }

  sensors.lightPct = clampf(normalizedLight * 100.0f, 0.0f, 100.0f);
  // ========================================================================
  // LIGHT CATEGORY
  // ========================================================================

  if (
      sensors.lightPct <
      15.0f
  ) {

    sensors.light =
        LIGHT_DARK;

  } else if (
      sensors.lightPct <
      40.0f
  ) {

    sensors.light =
        LIGHT_DIM;

  } else {

    sensors.light =
        LIGHT_BRIGHT;
  }

  // ========================================================================
  // DEBUG
  // ========================================================================

  Serial.printf(
      "[SENSORS] "
      "SOIL=%5.1f%% raw=%4d fault=%d | "
      "LDR=%5.1f%% raw=%4d | "
      "RAIN=%5.1f%% raw=%4d wet=%d confirmed=%d\n",

      sensors.soilPct,
      sensors.soilRaw,
      sensors.soilFault,

      sensors.lightPct,
      sensors.ldrRaw,

      sensors.rainIntensity,
      sensors.rainRaw,
      sensors.rainWet,
      rainConfirmed
  );
}

// ============================================================================
// RTC
// ============================================================================

void rtcInit() {

  rtcOk =
      rtc.begin();

  if (!rtcOk) {

    Serial.println(
        "[RTC] DS3231 NOT FOUND"
    );

    return;
  }

  rtcLostPower =
      rtc.lostPower();

  if (rtcLostPower) {

    Serial.println(
        "[RTC] RTC lost power; "
        "setting compile timestamp"
    );

    rtc.adjust(
        DateTime(
          F(__DATE__),
          F(__TIME__)
        )
    );
  }
}

// ============================================================================
// RTC TIMESTAMP
// ============================================================================

String getRtcTimestamp() {

  if (!rtcOk)
    return "";

  DateTime now =
      rtc.now();

  char buffer[25];

  snprintf(
      buffer,
      sizeof(buffer),
      "%04d-%02d-%02dT%02d:%02d:%02d",

      now.year(),
      now.month(),
      now.day(),

      now.hour(),
      now.minute(),
      now.second()
  );

  return String(buffer);
}

// ============================================================================
// PUMP HARDWARE
// ============================================================================

void pumpON() {

  if (pumpIsOn)
    return;

  digitalWrite(
      RELAY_PIN,
      RELAY_ON_LEVEL
  );

  pumpIsOn = true;

  pumpStartMs =
      millis();

  Serial.println(
      "[PUMP] PHYSICAL ON"
  );

  Serial.printf(
      "[PUMP] RTC start=%s\n",
      getRtcTimestamp().c_str()
  );

  buzzerBeep(
      BEEP_PUMP_ON_MS
  );
}

// ============================================================================

void pumpOFF() {

  if (!pumpIsOn)
    return;

  digitalWrite(
      RELAY_PIN,
      RELAY_OFF_LEVEL
  );

  pumpIsOn = false;

  Serial.println(
      "[PUMP] PHYSICAL OFF"
  );

  Serial.printf(
      "[PUMP] RTC stop=%s\n",
      getRtcTimestamp().c_str()
  );

  buzzerBeep(
      BEEP_PUMP_OFF_MS
  );
}

// ============================================================================
// PUMP START
// ============================================================================

bool startPump(
    PumpSource source,
    uint32_t seconds
) {

  if (pumpIsOn)
    return false;

  // ========================================================================
  // AUTO SAFETY
  // ========================================================================

  if (source == SRC_AUTO) {

    // Soil sensor fault
    if (sensors.soilFault) {

      Serial.println(
          "[AUTO] BLOCKED: "
          "soil sensor fault"
      );

      return false;
    }

    // Soil isn't dry enough
    if (
        sensors.soilPct >=
        autoSoilLow
    ) {

      Serial.printf(
          "[AUTO] BLOCKED: "
          "soil %.1f%% >= %.1f%%\n",
          sensors.soilPct,
          autoSoilLow
      );

      return false;
    }

    // Not enough sunlight
    if (
        sensors.lightPct <
        autoSunlightMin
    ) {

      Serial.printf(
          "[AUTO] BLOCKED: "
          "sunlight %.1f%% < %.1f%%\n",
          sensors.lightPct,
          autoSunlightMin
      );

      return false;
    }

    // Confirmed rain
    if (sensors.rainWet) {

      Serial.printf(
          "[AUTO] BLOCKED: "
          "confirmed rain | intensity %.1f%%\n",
          sensors.rainIntensity
      );

      return false;
    }

    lastAutoStartMs =
        millis();
  }

  pumpSource =
      source;

  if (
      seconds == 0 ||
      seconds > MAX_PUMP_RUN_SECONDS
  ) {

    seconds =
        MAX_PUMP_RUN_SECONDS;
  }

  pumpRunLimitMs =
      seconds * 1000UL;

  pumpON();

  Serial.printf(
      "[PUMP] START source=%s duration=%us\n",
      source == SRC_AUTO
        ? "AUTO"
        : "MANUAL",
      (unsigned)seconds
  );

  return true;
}

// ============================================================================
// PUMP STOP
// ============================================================================

void stopPump(
    const char* reason
) {

  if (!pumpIsOn)
    return;

  uint32_t runtime =
      (
        millis() -
        pumpStartMs +
        500
      ) / 1000;

  Serial.printf(
      "[PUMP] STOP reason=%s runtime=%us\n",
      reason,
      (unsigned)runtime
  );

  PumpSource oldSource =
      pumpSource;

  pumpOFF();

  if (
      oldSource == SRC_AUTO ||
      oldSource == SRC_MANUAL
  ) {

    ackPending = true;

    ackSeconds =
        runtime;
  }

  pumpSource =
      SRC_NONE;

  pumpRunLimitMs =
      0;
}

// ============================================================================
// AUTO PUMP EVALUATION
// ============================================================================

void autoPumpEvaluate() {

  if (!autoModeEnabled)
    return;

  if (pumpIsOn)
    return;

  // --------------------------------------------------------------------------
  // Soil sensor fault
  // --------------------------------------------------------------------------

  if (sensors.soilFault) {

    Serial.println(
        "[AUTO] WAITING: soil sensor fault"
    );

    return;
  }

  // --------------------------------------------------------------------------
  // Soil must be below LOW threshold
  // --------------------------------------------------------------------------

  if (
      sensors.soilPct >=
      autoSoilLow
  ) {

    return;
  }

  // --------------------------------------------------------------------------
  // Sunlight must be sufficient
  // --------------------------------------------------------------------------

  if (
      sensors.lightPct <
      autoSunlightMin
  ) {

    Serial.printf(
        "[AUTO] WAITING: sunlight %.1f%% < %.1f%%\n",
        sensors.lightPct,
        autoSunlightMin
    );

    return;
  }

  // --------------------------------------------------------------------------
  // Confirmed rain must be false
  // --------------------------------------------------------------------------

  if (sensors.rainWet) {

    Serial.printf(
        "[AUTO] WAITING: CONFIRMED RAIN | intensity %.1f%%\n",
        sensors.rainIntensity
    );

    return;
  }

  // --------------------------------------------------------------------------
  // Cooldown after previous auto cycle
  // --------------------------------------------------------------------------

  if (
      lastAutoStartMs != 0 &&
      millis() - lastAutoStartMs <
      AUTO_RESTART_COOLDOWN_MS
  ) {

    return;
  }

  // --------------------------------------------------------------------------
  // ALL CONDITIONS SATISFIED
  // --------------------------------------------------------------------------

  Serial.printf(
      "[AUTO] CONDITIONS SATISFIED -> "
      "STARTING PUMP | "
      "soil=%.1f%% "
      "sun=%.1f%% "
      "rain=%.1f%%\n",

      sensors.soilPct,
      sensors.lightPct,
      sensors.rainIntensity
  );

  bool started =
      startPump(
          SRC_AUTO,
          AUTO_PUMP_CYCLE_SECONDS
      );

  if (!started) {

    Serial.println(
        "[AUTO] START FAILED"
    );
  }
}

// ============================================================================
// PUMP SAFETY / AUTO MONITOR
// ============================================================================

void pumpUpdate() {

  // ========================================================================
  // PUMP IS OFF
  // ========================================================================

  if (!pumpIsOn) {

    autoPumpEvaluate();

    return;
  }

  // ========================================================================
  // CONFIRMED RAIN SAFETY
  // ========================================================================

  if (sensors.rainWet) {

    stopPump(
        "confirmed rain detected"
    );

    return;
  }

  // ========================================================================
  // AUTO SAFETY
  // ========================================================================

  if (
      pumpSource ==
      SRC_AUTO
  ) {

    // ----------------------------------------------------------------------
    // SOIL SENSOR FAULT
    // ----------------------------------------------------------------------

    if (sensors.soilFault) {

      stopPump(
          "soil sensor fault detected"
      );

      return;
    }

    // ----------------------------------------------------------------------
    // SOIL REACHED TARGET
    // ----------------------------------------------------------------------

    if (
        sensors.soilPct >=
        autoSoilHigh
    ) {

      stopPump(
          "target moisture reached"
      );

      return;
    }

    // ----------------------------------------------------------------------
    // SUNLIGHT BECAME TOO LOW
    // ----------------------------------------------------------------------

    if (
        sensors.lightPct <
        autoSunlightMin
    ) {

      stopPump(
          "insufficient sunlight"
      );

      return;
    }
  }

  // ========================================================================
  // MAXIMUM RUNTIME
  // ========================================================================

  if (
      pumpRunLimitMs > 0 &&
      millis() -
      pumpStartMs >=
      pumpRunLimitMs
  ) {

    stopPump(
        "maximum runtime reached"
    );

    return;
  }
}

// ============================================================================
// WIFI
// ============================================================================

void wifiUpdate() {

  if (!NETWORK_ENABLED)
    return;

  bool connected =
      WiFi.status() ==
      WL_CONNECTED;

  if (
      connected !=
      wifiWasUp
  ) {

    wifiWasUp =
        connected;

    if (connected) {

      Serial.printf(
          "[WIFI] connected IP=%s\n",
          WiFi.localIP()
              .toString()
              .c_str()
      );

    } else {

      Serial.println(
          "[WIFI] disconnected"
      );
    }
  }

  if (connected)
    return;

  if (
      millis() -
      lastWifiTryMs <
      WIFI_RETRY_INTERVAL_MS
  )
    return;

  lastWifiTryMs =
      millis();

  WiFi.disconnect(
      false,
      false
  );

  delay(50);

  WiFi.begin(
      WIFI_SSID,
      WIFI_PASSWORD
  );
}

// ============================================================================

bool wifiUp() {

  return
      NETWORK_ENABLED &&
      WiFi.status() ==
      WL_CONNECTED;
}

// ============================================================================
// SERVER URL
// ============================================================================

String serverUrl(
    const char* path
) {

  return
      String("http://") +
      SERVER_HOST +
      ":" +
      String(SERVER_PORT) +
      path;
}

// ============================================================================
// SENSOR JSON
// ============================================================================

String buildReadingJson() {

  String json = "{";

  // ========================================================================
  // SOIL
  // ========================================================================

  if (sensors.soilFault) {

    json +=
        "\"soil_moisture\":null";

  } else {

    json +=
        "\"soil_moisture\":" +
        String(
          sensors.soilPct,
          1
        );
  }

  // ========================================================================
  // SUNLIGHT
  // ========================================================================

  json +=
      ",\"light_level\":" +
      String(
        sensors.lightPct,
        1
      );

  // ========================================================================
  // RAIN
  // ========================================================================

  json +=
      ",\"rain_detected\":" +
      String(
        sensors.rainWet
        ? "true"
        : "false"
      );

  json +=
      ",\"rain_intensity\":" +
      String(
        sensors.rainIntensity,
        1
      );

  // ========================================================================
  // SENSOR FAULT
  // ========================================================================

  json +=
      ",\"sensor_fault\":" +
      String(
        sensors.soilFault
        ? "true"
        : "false"
      );

  // ========================================================================
  // PUMP STATE
  // ========================================================================

  json +=
      ",\"pump_is_on\":" +
      String(
        pumpIsOn
        ? "true"
        : "false"
      );

  // ========================================================================
  // PUMP SOURCE
  // ========================================================================

  json +=
      ",\"pump_source\":" +
      String(
        pumpSource == SRC_AUTO
        ? "\"auto\""
        : pumpSource == SRC_MANUAL
        ? "\"manual\""
        : "\"none\""
      );

  // ========================================================================
  // RTC
  // ========================================================================

  json +=
      ",\"rtc_timestamp\":\"" +
      getRtcTimestamp() +
      "\"";

  json += "}";

  return json;
}

// ============================================================================
// SEND SENSOR READING
// ============================================================================

void sendReading() {

  if (!wifiUp())
    return;

  WiFiClient client;
  HTTPClient http;

  http.setConnectTimeout(
      HTTP_TIMEOUT_MS
  );

  http.setTimeout(
      HTTP_TIMEOUT_MS
  );

  String url =
      serverUrl(
          "/api/ingest/reading"
      );

  if (
      !http.begin(
        client,
        url
      )
  )
    return;

  http.addHeader(
      "Content-Type",
      "application/json"
  );

  http.addHeader(
      "X-Device-Key",
      DEVICE_API_KEY
  );

  String body =
      buildReadingJson();

  lastPostCode =
      http.POST(body);

  Serial.printf(
      "[HTTP] reading POST=%d\n",
      lastPostCode
  );

  http.end();
}

// ============================================================================
// ACK
// ============================================================================

void sendAck() {

  if (
      !wifiUp() ||
      !ackPending
  )
    return;

  WiFiClient client;
  HTTPClient http;

  http.setConnectTimeout(
      HTTP_TIMEOUT_MS
  );

  http.setTimeout(
      HTTP_TIMEOUT_MS
  );

  String path =
      "/api/pump/ack?duration_seconds=" +
      String(ackSeconds);

  if (
      !http.begin(
        client,
        serverUrl(
          path.c_str()
        )
      )
  )
    return;

  http.addHeader(
      "X-Device-Key",
      DEVICE_API_KEY
  );

  int code =
      http.POST("");

  Serial.printf(
      "[ACK] response=%d\n",
      code
  );

  if (code == 200)
    ackPending = false;

  http.end();
}

// ============================================================================
// JSON NUMBER
// ============================================================================

float jsonFloat(
    const String& body,
    const char* key,
    float defaultValue
) {

  String target =
      "\"" +
      String(key) +
      "\":";

  int index =
      body.indexOf(target);

  if (index < 0)
    return defaultValue;

  int start =
      index +
      target.length();

  return body.substring(
      start
    ).toFloat();
}

// ============================================================================
// COMMAND POLLING
// ============================================================================

void pollCommand() {

  if (!wifiUp())
    return;

  WiFiClient client;
  HTTPClient http;

  http.setConnectTimeout(
      HTTP_TIMEOUT_MS
  );

  http.setTimeout(
      HTTP_TIMEOUT_MS
  );

  String url =
      serverUrl(
          "/api/pump/command"
      );

  if (
      !http.begin(
        client,
        url
      )
  )
    return;

  http.addHeader(
      "X-Device-Key",
      DEVICE_API_KEY
  );

  lastPollCode =
      http.GET();

  if (
      lastPollCode != 200
  ) {

    Serial.printf(
        "[COMMAND] HTTP=%d\n",
        lastPollCode
    );

    http.end();

    return;
  }

  String body =
      http.getString();

  http.end();

  Serial.printf(
      "[COMMAND] %s\n",
      body.c_str()
  );

  // ========================================================================
  // MODE
  // ========================================================================

  if (
      body.indexOf(
        "\"mode\":\"auto\""
      ) >= 0
  ) {

    if (!autoModeEnabled) {

      Serial.println(
          "[MODE] AUTO ENABLED"
      );
    }

    autoModeEnabled =
        true;
  }

  if (
      body.indexOf(
        "\"mode\":\"manual\""
      ) >= 0
  ) {

    if (autoModeEnabled) {

      Serial.println(
          "[MODE] MANUAL ENABLED"
      );
    }

    autoModeEnabled =
        false;
  }

  // ========================================================================
  // THRESHOLDS
  // ========================================================================

  float serverLow =
      jsonFloat(
        body,
        "moisture_threshold_low",
        autoSoilLow
      );

  float serverHigh =
      jsonFloat(
        body,
        "moisture_threshold_high",
        autoSoilHigh
      );

  float serverSun =
      jsonFloat(
        body,
        "sunlight_threshold",
        autoSunlightMin
      );

  if (
      serverLow >= 0 &&
      serverLow <= 100
  ) {

    autoSoilLow =
        serverLow;
  }

  if (
      serverHigh >= 0 &&
      serverHigh <= 100
  ) {

    autoSoilHigh =
        serverHigh;
  }

  if (
      serverSun >= 0 &&
      serverSun <= 100
  ) {

    autoSunlightMin =
        serverSun;
  }

  // ========================================================================
  // SMS
  // ========================================================================

  int smsIdx =
      body.indexOf(
          "\"sms_alert\":\""
      );

  if (smsIdx >= 0) {

    int startQuote =
        smsIdx + 13;

    int endQuote =
        body.indexOf(
          "\"",
          startQuote
        );

    if (
        endQuote >
        startQuote
    ) {

      String sms =
          body.substring(
            startQuote,
            endQuote
          );

      if (
          sms.length() > 0 &&
          sms != "null"
      ) {

        simSendSms(
            SMS_MANAGER_PHONE,
            sms.c_str()
        );
      }
    }
  }

  // ========================================================================
  // START COMMAND FROM BACKEND
  // ========================================================================

  if (
      body.indexOf(
        "\"command\":\"start\""
      ) >= 0
  ) {

    uint32_t seconds = 30;

    int durationIndex =
        body.indexOf(
          "\"duration_seconds\":"
        );

    if (
        durationIndex >= 0
    ) {

      seconds =
          (
            uint32_t
          )atoi(
            body.c_str() +
            durationIndex +
            19
          );
    }

    if (seconds == 0)
      seconds = 30;

    bool commandIsAuto =
        body.indexOf(
          "\"mode\":\"auto\""
        ) >= 0;

    PumpSource source =
        commandIsAuto
        ? SRC_AUTO
        : SRC_MANUAL;

    Serial.printf(
        "[COMMAND] START requested "
        "mode=%s duration=%us\n",
        commandIsAuto
        ? "AUTO"
        : "MANUAL",
        (unsigned)seconds
    );

    if (!pumpIsOn) {

      bool started =
          startPump(
            source,
            seconds
          );

      if (!started) {

        Serial.println(
            "[COMMAND] START BLOCKED"
        );
      }
    }

    return;
  }

  // ========================================================================
  // STOP COMMAND
  // ========================================================================

  if (
      body.indexOf(
        "\"command\":\"stop\""
      ) >= 0
  ) {

    if (pumpIsOn) {

      stopPump(
          "backend stop"
      );
    }

    return;
  }
}

// ============================================================================
// NETWORK
// ============================================================================

void networkUpdate() {

  wifiUpdate();

  if (!wifiUp())
    return;

  serviceCritical();

  if (ackPending)
    sendAck();

  if (
      due(
        lastPollMs,
        COMMAND_POLL_INTERVAL_MS
      )
  ) {

    pollCommand();

    serviceCritical();
  }

  if (
      due(
        lastPostMs,
        READING_POST_INTERVAL_MS
      )
  ) {

    sendReading();

    serviceCritical();
  }
}

// ============================================================================
// CRITICAL SERVICES
// ============================================================================

void serviceCritical() {

  pumpUpdate();

  buzzerUpdate();
}

// ============================================================================
// SIM800L
// ============================================================================

String simWaitFor(
    const char* token,
    uint32_t timeoutMs
) {

  String output;

  uint32_t start =
      millis();

  while (
      millis() -
      start <
      timeoutMs
  ) {

    while (
        simSerial.available()
    ) {

      output +=
          (char)
          simSerial.read();
    }

    if (
        token &&
        output.indexOf(token) >= 0
    )
      break;

    if (
        output.indexOf(
          "ERROR"
        ) >= 0
    )
      break;

    serviceCritical();

    yield();
  }

  return output;
}

// ============================================================================

String simCommand(
    const char* cmd,
    uint32_t timeoutMs,
    const char* expect
) {

  while (
      simSerial.available()
  )
    simSerial.read();

  simSerial.print(cmd);
  simSerial.print("\r");

  String response =
      simWaitFor(
        expect,
        timeoutMs
      );

  response.trim();

  return response;
}

// ============================================================================

bool simSendSms(
    const char* number,
    const char* text
) {

  if (
      !number ||
      strlen(number) < 8
  )
    return false;

  Serial.printf(
      "[SMS] sending -> %s\n",
      number
  );

  if (
      simCommand(
        "AT",
        1000,
        "OK"
      ).indexOf("OK") < 0
  )
    return false;

  simCommand(
      "AT+CMGF=1",
      2000,
      "OK"
  );

  String command =
      String("AT+CMGS=\"") +
      number +
      "\"";

  if (
      simCommand(
        command.c_str(),
        5000,
        ">"
      ).indexOf(">") < 0
  )
    return false;

  simSerial.print(text);
  simSerial.write(26);

  String response =
      simWaitFor(
        "OK",
        20000
      );

  response.trim();

  bool success =
      response.indexOf(
        "+CMGS"
      ) >= 0;

  Serial.println(
      success
      ? "[SMS] SUCCESS"
      : "[SMS] FAILED"
  );

  return success;
}

// ============================================================================
// LOCAL SERIAL
// ============================================================================

void printSensors() {

  Serial.printf(
      "SOIL raw=%d pct=%.1f fault=%s\n",
      sensors.soilRaw,
      sensors.soilPct,
      sensors.soilFault
      ? "YES"
      : "NO"
  );

  Serial.printf(
      "LDR raw=%d sunlight=%.1f%%\n",
      sensors.ldrRaw,
      sensors.lightPct
  );

  Serial.printf(
      "RAIN raw=%d intensity=%.1f%% wet=%s confirmed=%s\n",
      sensors.rainRaw,
      sensors.rainIntensity,
      sensors.rainWet
      ? "YES"
      : "NO",
      rainConfirmed
      ? "YES"
      : "NO"
  );

  Serial.printf(
      "AUTO=%s | LOW=%.1f | HIGH=%.1f | SUN_MIN=%.1f\n",
      autoModeEnabled
      ? "ON"
      : "OFF",
      autoSoilLow,
      autoSoilHigh,
      autoSunlightMin
  );

  Serial.printf(
      "PUMP=%s source=%d\n",
      pumpIsOn
      ? "ON"
      : "OFF",
      pumpSource
  );
}

// ============================================================================
// LOCAL SERIAL COMMANDS
// ============================================================================

void handleSerial() {

  while (
      Serial.available()
  ) {

    char c =
        Serial.read();

    switch (c) {

      // --------------------------------------------------------------------
      // Sensors
      // --------------------------------------------------------------------

      case 's':

        readSensors();
        printSensors();

        break;

      // --------------------------------------------------------------------
      // SIM test
      // --------------------------------------------------------------------

      case 't':

        simCommand(
            "AT",
            1000,
            "OK"
        );

        break;

      // --------------------------------------------------------------------
      // SMS test
      // --------------------------------------------------------------------

      case 'm':

        simSendSms(
            SMS_MANAGER_PHONE,
            SMS_TEST_TEXT
        );

        break;

      // --------------------------------------------------------------------
      // MANUAL ON
      // --------------------------------------------------------------------

      case 'o':

        autoModeEnabled =
            false;

        Serial.println(
            "[SERIAL] MANUAL PUMP ON"
        );

        startPump(
            SRC_MANUAL,
            MAX_PUMP_RUN_SECONDS
        );

        break;

      // --------------------------------------------------------------------
      // MANUAL OFF
      // --------------------------------------------------------------------

      case 'f':

        Serial.println(
            "[SERIAL] PUMP OFF"
        );

        stopPump(
            "serial stop"
        );

        break;

      // --------------------------------------------------------------------
      // FORCE AUTO MODE
      // --------------------------------------------------------------------

      case 'a':

        autoModeEnabled =
            true;

        Serial.println(
            "[SERIAL] AUTO MODE ENABLED"
        );

        serviceCritical();

        break;

      default:

        break;
    }
  }
}

// ============================================================================
// SETUP
// ============================================================================

void setup() {

  // ========================================================================
  // RELAY
  // ========================================================================

  digitalWrite(
      RELAY_PIN,
      RELAY_OFF_LEVEL
  );

  pinMode(
      RELAY_PIN,
      OUTPUT
  );

  digitalWrite(
      RELAY_PIN,
      RELAY_OFF_LEVEL
  );

  pumpIsOn = false;

  pumpSource =
      SRC_NONE;

  // ========================================================================
  // BUZZER
  // ========================================================================

  pinMode(
      BUZZER_PIN,
      OUTPUT
  );

  digitalWrite(
      BUZZER_PIN,
      LOW
  );

  // ========================================================================
  // SERIAL
  // ========================================================================

  Serial.begin(
      115200
  );

  delay(300);

  Serial.println();
  Serial.println(
      "================================"
  );
  Serial.println(
      "SU-KRISHI FIELD NODE"
  );
  Serial.println(
      "================================"
  );

  // ========================================================================
  // ADC
  // ========================================================================

  analogReadResolution(12);

  analogSetAttenuation(
      ADC_11db
  );

  // ========================================================================
  // RTC
  // ========================================================================

  Wire.begin(
      I2C_SDA,
      I2C_SCL
  );

  rtcInit();

  // ========================================================================
  // SIM800L
  // ========================================================================

  simSerial.begin(
      SIM_BAUD,
      SERIAL_8N1,
      SIM_RX_PIN,
      SIM_TX_PIN
  );

  // ========================================================================
  // INITIAL SENSOR READ
  // ========================================================================

  readSensors();

  // ========================================================================
  // WIFI
  // ========================================================================

  if (NETWORK_ENABLED) {

    WiFi.mode(
        WIFI_STA
    );

    WiFi.setAutoReconnect(
        true
    );

    WiFi.setSleep(
        false
    );

    lastWifiTryMs =
        millis() -
        WIFI_RETRY_INTERVAL_MS;

    lastPostMs =
        millis() -
        READING_POST_INTERVAL_MS;

    lastPollMs =
        millis() -
        COMMAND_POLL_INTERVAL_MS;
  }

  // ========================================================================
  // READY
  // ========================================================================

  Serial.println(
      "[SYSTEM] READY"
  );

  Serial.printf(
      "[CONFIG] Soil dry ADC = %d\n",
      SOIL_ADC_DRY
  );

  Serial.printf(
      "[CONFIG] Soil wet ADC = %d\n",
      SOIL_ADC_WET
  );

  Serial.printf(
      "[CONFIG] Soil curve = %.2f\n",
      SOIL_CURVE
  );

  Serial.printf(
      "[CONFIG] Auto soil LOW = %.1f%%\n",
      autoSoilLow
  );

  Serial.printf(
      "[CONFIG] Auto soil HIGH = %.1f%%\n",
      autoSoilHigh
  );

  Serial.printf(
      "[CONFIG] Minimum sunlight = %.1f%%\n",
      autoSunlightMin
  );

  Serial.printf(
      "[CONFIG] Auto pump cycle = %us\n",
      (unsigned)AUTO_PUMP_CYCLE_SECONDS
  );

  Serial.printf(
      "[CONFIG] Rain dry ADC = %d\n",
      RAIN_ADC_DRY
  );

  Serial.printf(
      "[CONFIG] Rain wet ADC = %d\n",
      RAIN_ADC_WET
  );

  Serial.printf(
      "[CONFIG] Rain effective dry ADC = %d\n",
      RAIN_EFFECTIVE_DRY
  );

  Serial.printf(
      "[CONFIG] Rain curve = %.1f\n",
      RAIN_CURVE
  );

  Serial.printf(
      "[CONFIG] Rain start threshold = %.1f%%\n",
      RAIN_START_THRESHOLD
  );

  Serial.printf(
      "[CONFIG] Rain clear threshold = %.1f%%\n",
      RAIN_CLEAR_THRESHOLD
  );

  Serial.printf(
      "[CONFIG] LDR bright when %s\n",
      LDR_BRIGHT_WHEN_HIGH
      ? "ADC HIGH"
      : "ADC LOW"
  );

  Serial.println(
      "Commands:"
  );

  Serial.println(
      "s = sensors"
  );

  Serial.println(
      "m = SMS test"
  );

  Serial.println(
      "o = manual pump ON"
  );

  Serial.println(
      "f = pump OFF"
  );

  Serial.println(
      "a = force AUTO mode"
  );
}

// ============================================================================
// LOOP
// ============================================================================

void loop() {

  // ========================================================================
  // SAFETY SERVICES ALWAYS RUN
  // ========================================================================

  serviceCritical();

  // ========================================================================
  // LOCAL COMMANDS
  // ========================================================================

  handleSerial();

  // ========================================================================
  // SENSOR UPDATE
  // ========================================================================

  if (
      due(
        lastSensorMs,
        SENSOR_READ_INTERVAL_MS
      )
  ) {

    readSensors();

    // Evaluate immediately using fresh sensor values.
    serviceCritical();
  }

  // ========================================================================
  // RTC RECONNECT
  // ========================================================================

  if (
      !rtcOk &&
      millis() -
      lastRtcRetryMs >
      30000
  ) {

    lastRtcRetryMs =
        millis();

    rtcInit();
  }

  // ========================================================================
  // NETWORK
  // ========================================================================

  networkUpdate();

  // ========================================================================
  // STATUS
  // ========================================================================

  if (
      due(
        lastStatusMs,
        STATUS_PRINT_INTERVAL_MS
      )
  ) {

    Serial.printf(
        "[STATUS] "
        "mode=%s | "
        "pump=%s | "
        "source=%s | "
        "soil=%.1f%% | "
        "sun=%.1f%% | "
        "rain=%.1f%% | "
        "rain_confirmed=%s | "
        "wifi=%s\n",

        autoModeEnabled
        ? "AUTO"
        : "MANUAL",

        pumpIsOn
        ? "ON"
        : "OFF",

        pumpSource == SRC_AUTO
        ? "AUTO"
        : pumpSource == SRC_MANUAL
        ? "MANUAL"
        : "NONE",

        sensors.soilPct,

        sensors.lightPct,

        sensors.rainIntensity,

        rainConfirmed
        ? "YES"
        : "NO",

        wifiUp()
        ? "ONLINE"
        : "OFFLINE"
    );
  }
}