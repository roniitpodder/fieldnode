/*
 * ============================================================
 * SU-KRISHI - ESP32 FIELD NODE
 * ============================================================
 *
 * SENSOR / PUMP LOGIC
 *
 * MANUAL:
 *   START -> directly control relay
 *   STOP  -> directly control relay
 *
 * AUTO:
 *   START when:
 *      soil < LOW threshold
 *      sunlight >= minimum threshold
 *      rain is NOT confirmed
 *
 *   STOP when:
 *      soil >= HIGH threshold
 *      OR sunlight < minimum threshold
 *      OR confirmed rain
 *      OR soil sensor fault
 *      OR maximum runtime reached
 *
 * IMPORTANT SENSOR FIXES:
 *
 *   1. Soil sensor uses:
 *      - ADC averaging
 *      - EMA smoothing
 *      - dry-zone suppression
 *      - nonlinear percentage mapping
 *
 *   2. Rain sensor uses:
 *      - ADC averaging
 *      - EMA smoothing
 *      - strong dry-zone suppression
 *      - nonlinear percentage mapping
 *      - consecutive-reading confirmation
 *      - hysteresis
 *
 * Hardware:
 *
 *   Relay      = ACTIVE LOW
 *   Soil       = GPIO34
 *   Rain       = GPIO32
 *   LDR        = GPIO35
 *   Buzzer     = GPIO27
 *
 *   RTC SDA    = GPIO21
 *   RTC SCL    = GPIO22
 *
 *   SIM RX     = GPIO16
 *   SIM TX     = GPIO17
 *
 * ============================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <RTClib.h>
#include <math.h>

// ============================================================
// NETWORK
// ============================================================

const char* WIFI_SSID     = "Roniit";
const char* WIFI_PASSWORD = "terabhai";

const char* SERVER_HOST = "172.20.10.4";
const uint16_t SERVER_PORT = 8000;

const char* DEVICE_API_KEY =
    "34bacefc-2dda-4421-a371-d8caf7e80808";

const bool NETWORK_ENABLED = true;

// ============================================================
// PINS
// ============================================================

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

// ============================================================
// SOIL SENSOR CALIBRATION
// ============================================================
//
// Higher ADC = drier
// Lower ADC  = wetter
//
// IMPORTANT:
// These are intentionally not mapped directly from 0-4095.
//
// A dry-zone is used so tiny changes around the dry sensor
// don't immediately produce a large moisture percentage.
//

const int SOIL_ADC_DRY = 3400;
const int SOIL_ADC_WET = 1400;

// Anything above this is treated as effectively dry.
const int SOIL_EFFECTIVE_DRY = 3300;

// Nonlinear curve.
// Higher value = less sensitive to small moisture changes.
const float SOIL_CURVE = 1.8f;

const int SOIL_FAULT_BELOW_ADC = 30;

const float DEFAULT_SOIL_LOW  = 35.0;
const float DEFAULT_SOIL_HIGH = 70.0;

// ============================================================
// RAIN SENSOR CALIBRATION
// ============================================================
//
// Lower ADC = more water.
//
// The old code mapped:
//
//   3800 -> 0%
//   1400 -> 100%
//
// That was too sensitive.
//
// Now there is a dry zone:
//
//   >= 3300 -> effectively 0%
//
// And a nonlinear curve:
//
//   small changes -> very small percentage
//   strong wetness -> rises much faster
//
// ============================================================

const int RAIN_ADC_DRY = 3800;
const int RAIN_ADC_WET = 1400;

// Below this point the rain sensor starts contributing
// meaningfully to the displayed percentage.
const int RAIN_EFFECTIVE_DRY = 3300;

// Nonlinear suppression of tiny droplets.
const float RAIN_CURVE = 3.0f;

// Rain must reach this level to be considered actual rain.
const float RAIN_START_THRESHOLD = 70.0;

// Once rain has been confirmed, it must fall below this
// lower threshold before rain is cleared.
//
// This creates hysteresis.
const float RAIN_CLEAR_THRESHOLD = 45.0;

// Number of consecutive readings required to confirm rain.
const uint8_t RAIN_CONFIRM_COUNT = 3;

// Number of consecutive dry readings required to clear rain.
const uint8_t RAIN_CLEAR_COUNT = 5;

const int RAIN_DISCONNECT_ADC = 30;

// ============================================================
// LDR
// ============================================================
//
// Your hardware behaves as:
//
// More light -> LOWER ADC
//
// ============================================================

const bool LDR_BRIGHT_WHEN_HIGH = true;

const float DEFAULT_SUNLIGHT_MIN = 30.0;

// ============================================================
// SENSOR FILTERING
// ============================================================
//
// EMA:
//
// filtered = alpha * new + (1-alpha) * old
//
// Lower alpha = smoother / slower
//
// Soil:
//   0.15 -> approximately several seconds of smoothing
//
// Rain:
//   0.10 -> stronger smoothing because rain sensor is noisy
//
// ============================================================

const float SOIL_FILTER_ALPHA = 0.15f;
const float RAIN_FILTER_ALPHA = 0.10f;

bool soilFilterInitialized = false;
bool rainFilterInitialized = false;

float soilFilteredRaw = 0.0f;
float rainFilteredRaw = 0.0f;

// ============================================================
// PUMP / BUZZER
// ============================================================

const uint32_t BEEP_PUMP_ON_MS  = 3000;
const uint32_t BEEP_PUMP_OFF_MS = 1000;

const uint32_t MAX_PUMP_RUN_SECONDS = 300;

const uint32_t AUTO_PUMP_CYCLE_SECONDS = 60;

const uint32_t AUTO_RESTART_COOLDOWN_MS = 10000;

// ============================================================
// TIMING
// ============================================================

const uint32_t SENSOR_READ_INTERVAL_MS  = 1000;
const uint32_t STATUS_PRINT_INTERVAL_MS = 5000;

const uint32_t READING_POST_INTERVAL_MS = 1000;
const uint32_t COMMAND_POLL_INTERVAL_MS = 500;

const uint32_t WIFI_RETRY_INTERVAL_MS = 5000;

const uint16_t HTTP_TIMEOUT_MS = 3000;

const uint8_t ADC_SAMPLES = 16;

// ============================================================
// SIM800L
// ============================================================

const uint32_t SIM_BAUD = 9600;

char SMS_MANAGER_PHONE[20] = "+919876543210";

const char* SMS_TEST_TEXT =
    "SU-KRISHI: test SMS from the field node.";

// ============================================================
// ENUMS
// ============================================================

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

// ============================================================
// SENSOR DATA
// ============================================================

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

// ============================================================
// RAIN STATE
// ============================================================

uint8_t rainWetCounter = 0;
uint8_t rainDryCounter = 0;

// ============================================================
// RTC
// ============================================================

RTC_DS3231 rtc;

bool rtcOk = false;
bool rtcLostPower = false;

// ============================================================
// SIM800L
// ============================================================

HardwareSerial simSerial(2);

String simStatus = "not tested";

// ============================================================
// PUMP STATE
// ============================================================

bool pumpIsOn = false;

PumpSource pumpSource = SRC_NONE;

uint32_t pumpStartMs = 0;

uint32_t pumpRunLimitMs = 0;

uint32_t lastAutoStartMs = 0;

// ============================================================
// AUTO CONFIG
// ============================================================

float autoSoilLow = DEFAULT_SOIL_LOW;
float autoSoilHigh = DEFAULT_SOIL_HIGH;

float autoSunlightMin = DEFAULT_SUNLIGHT_MIN;

bool autoModeEnabled = false;

// ============================================================
// BUZZER
// ============================================================

bool buzzerOn = false;
uint32_t buzzerOffAtMs = 0;

// ============================================================
// NETWORK STATE
// ============================================================

bool wifiWasUp = false;

uint32_t lastWifiTryMs = 0;
uint32_t lastPostMs = 0;
uint32_t lastPollMs = 0;
uint32_t lastSensorMs = 0;
uint32_t lastStatusMs = 0;
uint32_t lastRtcRetryMs = 0;

int lastPostCode = 0;
int lastPollCode = 0;

// ============================================================
// ACK STATE
// ============================================================

bool ackPending = false;
uint32_t ackSeconds = 0;

// ============================================================
// FUNCTION DECLARATIONS
// ============================================================

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

// ============================================================
// UTILITY
// ============================================================

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

// ============================================================
// NONLINEAR SENSOR CURVE
// ============================================================
//
// Converts normalized 0..1 into a less sensitive curve.
//
// Example:
//
// normalized = 0.3
// curve = 3
//
// 0.3^3 = 0.027
//
// Therefore tiny wetness stays tiny.
//
// ============================================================

float nonlinearCurve(
    float normalized,
    float curve
) {

  normalized =
      clampf(
        normalized,
        0.0f,
        1.0f
      );

  return powf(
      normalized,
      curve
  );
}

// ============================================================
// BUZZER
// ============================================================

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

// ============================================================
// ADC
// ============================================================

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

// ============================================================
// SOIL PERCENTAGE
// ============================================================

float calculateSoilPercentage(
    float raw
) {

  // Completely dry zone.
  if (
      raw >=
      SOIL_EFFECTIVE_DRY
  ) {

    return 0.0f;
  }

  // Completely wet.
  if (
      raw <=
      SOIL_ADC_WET
  ) {

    return 100.0f;
  }

  float span =
      (
        (float)SOIL_EFFECTIVE_DRY -
        (float)SOIL_ADC_WET
      );

  if (span <= 0)
    span = 1.0f;

  // Convert:
  //
  // dry = 0
  // wet = 1
  //
  float normalized =
      (
        (float)SOIL_EFFECTIVE_DRY -
        raw
      ) /
      span;

  normalized =
      clampf(
        normalized,
        0.0f,
        1.0f
      );

  float curved =
      nonlinearCurve(
        normalized,
        SOIL_CURVE
      );

  return
      clampf(
        curved * 100.0f,
        0.0f,
        100.0f
      );
}

// ============================================================
// RAIN PERCENTAGE
// ============================================================

float calculateRainPercentage(
    float raw
) {

  // ----------------------------------------------------------
  // Dry zone
  // ----------------------------------------------------------

  if (
      raw >=
      RAIN_EFFECTIVE_DRY
  ) {

    return 0.0f;
  }

  // ----------------------------------------------------------
  // Very wet
  // ----------------------------------------------------------

  if (
      raw <=
      RAIN_ADC_WET
  ) {

    return 100.0f;
  }

  float span =
      (
        (float)RAIN_EFFECTIVE_DRY -
        (float)RAIN_ADC_WET
      );

  if (span <= 0)
    span = 1.0f;

  // Normalize.
  //
  // 3300 -> 0
  // 1400 -> 1
  //

  float normalized =
      (
        (float)RAIN_EFFECTIVE_DRY -
        raw
      ) /
      span;

  normalized =
      clampf(
        normalized,
        0.0f,
        1.0f
      );

  // Strong nonlinear suppression.
  float curved =
      nonlinearCurve(
        normalized,
        RAIN_CURVE
      );

  return
      clampf(
        curved * 100.0f,
        0.0f,
        100.0f
      );
}

// ============================================================
// RAIN CONFIRMATION
// ============================================================
//
// A single droplet should NOT activate rain mode.
//
// Rain must remain above the threshold for several consecutive
// sensor readings.
//
// ============================================================

void updateRainState() {

  // ----------------------------------------------------------
  // Currently dry
  // ----------------------------------------------------------

  if (!sensors.rainWet) {

    rainDryCounter = 0;

    if (
        sensors.rainIntensity >=
        RAIN_START_THRESHOLD
    ) {

      if (
          rainWetCounter <
          RAIN_CONFIRM_COUNT
      ) {

        rainWetCounter++;
      }

      Serial.printf(
          "[RAIN] wet candidate %u/%u | %.1f%%\n",
          rainWetCounter,
          RAIN_CONFIRM_COUNT,
          sensors.rainIntensity
      );

      if (
          rainWetCounter >=
          RAIN_CONFIRM_COUNT
      ) {

        sensors.rainWet = true;

        rainWetCounter = 0;

        Serial.println(
            "[RAIN] *** RAIN CONFIRMED ***"
        );
      }

    } else {

      rainWetCounter = 0;
    }

    return;
  }

  // ----------------------------------------------------------
  // Currently wet
  // ----------------------------------------------------------

  rainWetCounter = 0;

  if (
      sensors.rainIntensity <=
      RAIN_CLEAR_THRESHOLD
  ) {

    if (
        rainDryCounter <
        RAIN_CLEAR_COUNT
    ) {

      rainDryCounter++;
    }

    Serial.printf(
        "[RAIN] drying %u/%u | %.1f%%\n",
        rainDryCounter,
        RAIN_CLEAR_COUNT,
        sensors.rainIntensity
    );

    if (
        rainDryCounter >=
        RAIN_CLEAR_COUNT
    ) {

      sensors.rainWet = false;

      rainDryCounter = 0;

      Serial.println(
          "[RAIN] rain cleared"
      );
    }

  } else {

    rainDryCounter = 0;
  }
}

// ============================================================
// SENSOR READING
// ============================================================

void readSensors() {

  // ==========================================================
  // SOIL RAW
  // ==========================================================

  int soilRawNow =
      readAnalogAvg(
        SOIL_PIN
      );

  sensors.soilRaw =
      soilRawNow;

  sensors.soilFault =
      soilRawNow <
      SOIL_FAULT_BELOW_ADC;

  // ----------------------------------------------------------
  // Soil EMA filtering
  // ----------------------------------------------------------

  if (!soilFilterInitialized) {

    soilFilteredRaw =
        soilRawNow;

    soilFilterInitialized =
        true;

  } else {

    soilFilteredRaw =
        (
          SOIL_FILTER_ALPHA *
          (float)soilRawNow
        ) +
        (
          (1.0f -
           SOIL_FILTER_ALPHA) *
          soilFilteredRaw
        );
  }

  // ----------------------------------------------------------
  // Soil percentage
  // ----------------------------------------------------------

  if (sensors.soilFault) {

    sensors.soilPct = 0.0f;

  } else {

    sensors.soilPct =
        calculateSoilPercentage(
          soilFilteredRaw
        );
  }

  // ==========================================================
  // RAIN RAW
  // ==========================================================

  int rainRawNow =
      readAnalogAvg(
        RAIN_PIN
      );

  sensors.rainRaw =
      rainRawNow;

  // ----------------------------------------------------------
  // Rain EMA filtering
  // ----------------------------------------------------------

  if (!rainFilterInitialized) {

    rainFilteredRaw =
        rainRawNow;

    rainFilterInitialized =
        true;

  } else {

    rainFilteredRaw =
        (
          RAIN_FILTER_ALPHA *
          (float)rainRawNow
        ) +
        (
          (1.0f -
           RAIN_FILTER_ALPHA) *
          rainFilteredRaw
        );
  }

  // ----------------------------------------------------------
  // Rain percentage
  // ----------------------------------------------------------

  if (
      rainRawNow <
      RAIN_DISCONNECT_ADC
  ) {

    /*
     * IMPORTANT:
     *
     * We don't immediately declare this "100% rain".
     *
     * A very low value may indicate a sensor/wiring problem.
     *
     * Keep the displayed rain value at zero and do not
     * confirm rain.
     */

    sensors.rainIntensity = 0.0f;

    rainWetCounter = 0;

  } else {

    sensors.rainIntensity =
        calculateRainPercentage(
          rainFilteredRaw
        );
  }

  // ----------------------------------------------------------
  // Rain state
  // ----------------------------------------------------------

  if (
      rainRawNow >=
      RAIN_DISCONNECT_ADC
  ) {

    updateRainState();
  }

  // ==========================================================
  // LDR
  // ==========================================================

  sensors.ldrRaw =
      readAnalogAvg(
        LDR_PIN
      );

  float brightnessRaw;

  if (LDR_BRIGHT_WHEN_HIGH) {

    brightnessRaw =
        (float)sensors.ldrRaw;

  } else {

    brightnessRaw =
        4095.0f -
        (float)sensors.ldrRaw;
  }

  sensors.lightPct =
      clampf(
        brightnessRaw *
        100.0f /
        4095.0f,
        0.0f,
        100.0f
      );

  // ==========================================================
  // LIGHT CATEGORY
  // ==========================================================

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

  // ==========================================================
  // DEBUG
  // ==========================================================

  Serial.printf(
      "[SENSORS] "
      "SOIL raw=%d filtered=%.0f moisture=%5.1f%% fault=%d | "
      "LDR raw=%d sun=%5.1f%% | "
      "RAIN raw=%d filtered=%.0f intensity=%5.1f%% wet=%d\n",

      sensors.soilRaw,
      soilFilteredRaw,
      sensors.soilPct,
      sensors.soilFault,

      sensors.ldrRaw,
      sensors.lightPct,

      sensors.rainRaw,
      rainFilteredRaw,
      sensors.rainIntensity,
      sensors.rainWet
  );
}

// ============================================================
// RTC
// ============================================================

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

// ============================================================
// RTC TIMESTAMP
// ============================================================

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

// ============================================================
// PUMP HARDWARE
// ============================================================

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

// ============================================================

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

// ============================================================
// PUMP START
// ============================================================

bool startPump(
    PumpSource source,
    uint32_t seconds
) {

  if (pumpIsOn)
    return false;

  // ==========================================================
  // AUTO SAFETY
  // ==========================================================

  if (source == SRC_AUTO) {

    // Soil fault
    if (sensors.soilFault) {

      Serial.println(
          "[AUTO] BLOCKED: soil sensor fault"
      );

      return false;
    }

    // Soil not dry enough
    if (
        sensors.soilPct >=
        autoSoilLow
    ) {

      Serial.printf(
          "[AUTO] BLOCKED: soil %.1f%% >= %.1f%%\n",
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
          "[AUTO] BLOCKED: sunlight %.1f%% < %.1f%%\n",
          sensors.lightPct,
          autoSunlightMin
      );

      return false;
    }

    // CONFIRMED rain only
    if (sensors.rainWet) {

      Serial.printf(
          "[AUTO] BLOCKED: confirmed rain %.1f%%\n",
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
      seconds >
      MAX_PUMP_RUN_SECONDS
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

// ============================================================
// PUMP STOP
// ============================================================

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

// ============================================================
// AUTO PUMP EVALUATION
// ============================================================

void autoPumpEvaluate() {

  if (!autoModeEnabled)
    return;

  if (pumpIsOn)
    return;

  // ==========================================================
  // SOIL FAULT
  // ==========================================================

  if (sensors.soilFault) {

    return;
  }

  // ==========================================================
  // SOIL MUST BE DRY
  // ==========================================================

  if (
      sensors.soilPct >=
      autoSoilLow
  ) {

    return;
  }

  // ==========================================================
  // SUNLIGHT
  // ==========================================================

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

  // ==========================================================
  // CONFIRMED RAIN
  // ==========================================================

  if (sensors.rainWet) {

    Serial.printf(
        "[AUTO] WAITING: CONFIRMED RAIN %.1f%%\n",
        sensors.rainIntensity
    );

    return;
  }

  // ==========================================================
  // COOLDOWN
  // ==========================================================

  if (
      lastAutoStartMs != 0 &&
      millis() -
      lastAutoStartMs <
      AUTO_RESTART_COOLDOWN_MS
  ) {

    return;
  }

  // ==========================================================
  // ALL CONDITIONS SATISFIED
  // ==========================================================

  Serial.printf(
      "[AUTO] CONDITIONS SATISFIED -> "
      "STARTING PUMP | "
      "soil=%.1f%% "
      "sun=%.1f%% "
      "rain=%.1f%% "
      "rainConfirmed=%s\n",

      sensors.soilPct,
      sensors.lightPct,
      sensors.rainIntensity,

      sensors.rainWet
        ? "YES"
        : "NO"
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

// ============================================================
// PUMP SAFETY / MONITOR
// ============================================================

void pumpUpdate() {

  // ==========================================================
  // PUMP OFF
  // ==========================================================

  if (!pumpIsOn) {

    autoPumpEvaluate();

    return;
  }

  // ==========================================================
  // CONFIRMED RAIN SAFETY
  // ==========================================================

  if (sensors.rainWet) {

    stopPump(
        "confirmed rain"
    );

    return;
  }

  // ==========================================================
  // AUTO SAFETY
  // ==========================================================

  if (
      pumpSource ==
      SRC_AUTO
  ) {

    // --------------------------------------------------------
    // SOIL FAULT
    // --------------------------------------------------------

    if (sensors.soilFault) {

      stopPump(
          "soil sensor fault detected"
      );

      return;
    }

    // --------------------------------------------------------
    // TARGET MOISTURE
    // --------------------------------------------------------

    if (
        sensors.soilPct >=
        autoSoilHigh
    ) {

      stopPump(
          "target moisture reached"
      );

      return;
    }

    // --------------------------------------------------------
    // LOW SUNLIGHT
    // --------------------------------------------------------

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

  // ==========================================================
  // MAXIMUM RUNTIME
  // ==========================================================

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

// ============================================================
// WIFI
// ============================================================

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

// ============================================================

bool wifiUp() {

  return
      NETWORK_ENABLED &&
      WiFi.status() ==
      WL_CONNECTED;
}

// ============================================================
// SERVER URL
// ============================================================

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

// ============================================================
// SENSOR JSON
// ============================================================

String buildReadingJson() {

  String json = "{";

  // ----------------------------------------------------------
  // SOIL
  // ----------------------------------------------------------

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

  // ----------------------------------------------------------
  // SUNLIGHT
  // ----------------------------------------------------------

  json +=
      ",\"light_level\":" +
      String(
        sensors.lightPct,
        1
      );

  // ----------------------------------------------------------
  // RAIN
  // ----------------------------------------------------------

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

  // ----------------------------------------------------------
  // SENSOR FAULT
  // ----------------------------------------------------------

  json +=
      ",\"sensor_fault\":" +
      String(
        sensors.soilFault
        ? "true"
        : "false"
      );

  // ----------------------------------------------------------
  // PUMP STATE
  // ----------------------------------------------------------

  json +=
      ",\"pump_is_on\":" +
      String(
        pumpIsOn
        ? "true"
        : "false"
      );

  // ----------------------------------------------------------
  // PUMP SOURCE
  // ----------------------------------------------------------

  json +=
      ",\"pump_source\":" +
      String(
        pumpSource == SRC_AUTO
        ? "\"auto\""
        : pumpSource == SRC_MANUAL
        ? "\"manual\""
        : "\"none\""
      );

  // ----------------------------------------------------------
  // RTC
  // ----------------------------------------------------------

  json +=
      ",\"rtc_timestamp\":\"" +
      getRtcTimestamp() +
      "\"";

  json += "}";

  return json;
}

// ============================================================
// SEND SENSOR READING
// ============================================================

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

// ============================================================
// ACK
// ============================================================

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

// ============================================================
// JSON NUMBER
// ============================================================

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

// ============================================================
// COMMAND POLLING
// ============================================================

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

  // ==========================================================
  // MODE
  // ==========================================================

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

  // ==========================================================
  // THRESHOLDS
  // ==========================================================

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

  // ==========================================================
  // SMS
  // ==========================================================

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

  // ==========================================================
  // START COMMAND
  // ==========================================================

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

  // ==========================================================
  // STOP COMMAND
  // ==========================================================

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

// ============================================================
// NETWORK
// ============================================================

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

    // Immediately evaluate newly received AUTO mode.
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

// ============================================================
// CRITICAL SERVICES
// ============================================================

void serviceCritical() {

  pumpUpdate();

  buzzerUpdate();
}

// ============================================================
// SIM800L
// ============================================================

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

// ============================================================

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

// ============================================================

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

// ============================================================
// LOCAL SERIAL
// ============================================================

void printSensors() {

  Serial.println();
  Serial.println(
      "========== SENSOR STATUS =========="
  );

  Serial.printf(
      "SOIL raw       = %d\n",
      sensors.soilRaw
  );

  Serial.printf(
      "SOIL filtered  = %.0f\n",
      soilFilteredRaw
  );

  Serial.printf(
      "SOIL moisture  = %.1f%%\n",
      sensors.soilPct
  );

  Serial.printf(
      "SOIL fault     = %s\n",
      sensors.soilFault
      ? "YES"
      : "NO"
  );

  Serial.println();

  Serial.printf(
      "LDR raw        = %d\n",
      sensors.ldrRaw
  );

  Serial.printf(
      "SUNLIGHT       = %.1f%%\n",
      sensors.lightPct
  );

  Serial.println();

  Serial.printf(
      "RAIN raw       = %d\n",
      sensors.rainRaw
  );

  Serial.printf(
      "RAIN filtered  = %.0f\n",
      rainFilteredRaw
  );

  Serial.printf(
      "RAIN intensity = %.1f%%\n",
      sensors.rainIntensity
  );

  Serial.printf(
      "RAIN confirmed = %s\n",
      sensors.rainWet
      ? "YES"
      : "NO"
  );

  Serial.println();

  Serial.printf(
      "AUTO           = %s\n",
      autoModeEnabled
      ? "ON"
      : "OFF"
  );

  Serial.printf(
      "LOW            = %.1f%%\n",
      autoSoilLow
  );

  Serial.printf(
      "HIGH           = %.1f%%\n",
      autoSoilHigh
  );

  Serial.printf(
      "SUN MIN        = %.1f%%\n",
      autoSunlightMin
  );

  Serial.printf(
      "PUMP           = %s\n",
      pumpIsOn
      ? "ON"
      : "OFF"
  );

  Serial.println(
      "==================================="
  );
}

// ============================================================
// LOCAL SERIAL COMMANDS
// ============================================================

void handleSerial() {

  while (
      Serial.available()
  ) {

    char c =
        Serial.read();

    switch (c) {

      // ------------------------------------------------------
      // Sensors
      // ------------------------------------------------------

      case 's':

        readSensors();
        printSensors();

        break;

      // ------------------------------------------------------
      // SIM test
      // ------------------------------------------------------

      case 't':

        simCommand(
            "AT",
            1000,
            "OK"
        );

        break;

      // ------------------------------------------------------
      // SMS test
      // ------------------------------------------------------

      case 'm':

        simSendSms(
            SMS_MANAGER_PHONE,
            SMS_TEST_TEXT
        );

        break;

      // ------------------------------------------------------
      // MANUAL ON
      // ------------------------------------------------------

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

      // ------------------------------------------------------
      // MANUAL OFF
      // ------------------------------------------------------

      case 'f':

        Serial.println(
            "[SERIAL] PUMP OFF"
        );

        stopPump(
            "serial stop"
        );

        break;

      // ------------------------------------------------------
      // FORCE AUTO MODE
      // ------------------------------------------------------

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

// ============================================================
// SETUP
// ============================================================

void setup() {

  // ==========================================================
  // RELAY
  // ==========================================================

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

  // ==========================================================
  // BUZZER
  // ==========================================================

  pinMode(
      BUZZER_PIN,
      OUTPUT
  );

  digitalWrite(
      BUZZER_PIN,
      LOW
  );

  // ==========================================================
  // SERIAL
  // ==========================================================

  Serial.begin(
      115200
  );

  delay(300);

  Serial.println();
  Serial.println(
      "========================================"
  );
  Serial.println(
      "        SU-KRISHI FIELD NODE"
  );
  Serial.println(
      "========================================"
  );

  // ==========================================================
  // ADC
  // ==========================================================

  analogReadResolution(12);

  analogSetAttenuation(
      ADC_11db
  );

  // ==========================================================
  // RTC
  // ==========================================================

  Wire.begin(
      I2C_SDA,
      I2C_SCL
  );

  rtcInit();

  // ==========================================================
  // SIM800L
  // ==========================================================

  simSerial.begin(
      SIM_BAUD,
      SERIAL_8N1,
      SIM_RX_PIN,
      SIM_TX_PIN
  );

  // ==========================================================
  // INITIAL SENSOR READ
  // ==========================================================

  readSensors();

  // ==========================================================
  // WIFI
  // ==========================================================

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

  // ==========================================================
  // READY
  // ==========================================================

  Serial.println(
      "[SYSTEM] READY"
  );

  Serial.println();
  Serial.println(
      "========== SENSOR CONFIG =========="
  );

  Serial.printf(
      "Soil dry ADC       = %d\n",
      SOIL_ADC_DRY
  );

  Serial.printf(
      "Soil effective dry = %d\n",
      SOIL_EFFECTIVE_DRY
  );

  Serial.printf(
      "Soil wet ADC       = %d\n",
      SOIL_ADC_WET
  );

  Serial.printf(
      "Soil curve         = %.2f\n",
      SOIL_CURVE
  );

  Serial.println();

  Serial.printf(
      "Rain dry ADC       = %d\n",
      RAIN_ADC_DRY
  );

  Serial.printf(
      "Rain effective dry = %d\n",
      RAIN_EFFECTIVE_DRY
  );

  Serial.printf(
      "Rain wet ADC       = %d\n",
      RAIN_ADC_WET
  );

  Serial.printf(
      "Rain curve         = %.2f\n",
      RAIN_CURVE
  );

  Serial.printf(
      "Rain START         = %.1f%%\n",
      RAIN_START_THRESHOLD
  );

  Serial.printf(
      "Rain CLEAR         = %.1f%%\n",
      RAIN_CLEAR_THRESHOLD
  );

  Serial.printf(
      "Rain confirm count = %u\n",
      RAIN_CONFIRM_COUNT
  );

  Serial.printf(
      "Rain clear count   = %u\n",
      RAIN_CLEAR_COUNT
  );

  Serial.println();

  Serial.printf(
      "Auto soil LOW      = %.1f%%\n",
      autoSoilLow
  );

  Serial.printf(
      "Auto soil HIGH     = %.1f%%\n",
      autoSoilHigh
  );

  Serial.printf(
      "Minimum sunlight   = %.1f%%\n",
      autoSunlightMin
  );

  Serial.printf(
      "Auto pump cycle    = %us\n",
      (unsigned)AUTO_PUMP_CYCLE_SECONDS
  );

  Serial.println(
      "==================================="
  );

  Serial.println();
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

  Serial.println();
}

// ============================================================
// LOOP
// ============================================================

void loop() {

  // ==========================================================
  // SAFETY SERVICES ALWAYS RUN
  // ==========================================================

  serviceCritical();

  // ==========================================================
  // LOCAL COMMANDS
  // ==========================================================

  handleSerial();

  // ==========================================================
  // SENSOR UPDATE
  // ==========================================================

  if (
      due(
        lastSensorMs,
        SENSOR_READ_INTERVAL_MS
      )
  ) {

    readSensors();

    // Immediately evaluate using fresh filtered sensors.
    serviceCritical();
  }

  // ==========================================================
  // RTC RECONNECT
  // ==========================================================

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

  // ==========================================================
  // NETWORK
  // ==========================================================

  networkUpdate();

  // ==========================================================
  // STATUS
  // ==========================================================

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
        "rainConfirmed=%s | "
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

        sensors.rainWet
        ? "YES"
        : "NO",

        wifiUp()
        ? "ONLINE"
        : "OFFLINE"
    );
  }
}