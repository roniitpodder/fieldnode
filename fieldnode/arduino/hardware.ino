/*
 * SU-KRISHI - ESP32 field node
 *
 * CONTROL RULES
 * -------------
 *
 * MANUAL:
 *   START -> directly control relay
 *   STOP  -> directly control relay
 *
 * AUTO:
 *   START only when:
 *      soil < LOW threshold
 *      sunlight >= minimum threshold
 *      rain <= 50%
 *
 *   STOP immediately when:
 *      soil >= HIGH threshold
 *      OR sunlight < minimum threshold
 *      OR rain > 50%
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
// SOIL CALIBRATION
// ============================================================================

const int SOIL_ADC_DRY = 3400;
const int SOIL_ADC_WET = 1400;

const int SOIL_FAULT_BELOW_ADC = 30;

const float DEFAULT_SOIL_LOW  = 35.0;
const float DEFAULT_SOIL_HIGH = 70.0;

// ============================================================================
// RAIN
// ============================================================================

const int RAIN_FULL_WET_DELTA = 800;

const float RAIN_STOP_THRESHOLD = 50.0;

// ============================================================================
// LDR
// ============================================================================
//
// IMPORTANT:
//
// The ESP32 ADC is 0-4095.
//
// If shining more light onto your LDR makes the ADC value INCREASE,
// keep this true.
//
// If shining more light makes the ADC value DECREASE,
// change this to false.
//
// ============================================================================

const bool LDR_BRIGHT_WHEN_HIGH = true;

// AUTO irrigation is blocked below this sunlight level.
const float DEFAULT_SUNLIGHT_MIN = 30.0;

// ============================================================================
// PUMP / BUZZER
// ============================================================================

const uint32_t BEEP_PUMP_ON_MS  = 3000;
const uint32_t BEEP_PUMP_OFF_MS = 1000;

const uint32_t MAX_PUMP_RUN_SECONDS = 300;

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
// RAIN BASELINE
// ============================================================================

bool rainBaselineSet = false;
int rainBaselineRaw = 0;

const uint32_t RAIN_BASELINE_SETTLE_MS = 200;

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
// RAIN BASELINE
// ============================================================================

void captureRainBaseline() {

  delay(
      RAIN_BASELINE_SETTLE_MS
  );

  rainBaselineRaw =
      readAnalogAvg(RAIN_PIN);

  rainBaselineSet = true;

  Serial.printf(
      "[RAIN] baseline=%d\n",
      rainBaselineRaw
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
        (
          float
        )(
          SOIL_ADC_WET -
          SOIL_ADC_DRY
        );

    if (span == 0)
      span = 1;

    sensors.soilPct =
        clampf(
          (
            sensors.soilRaw -
            SOIL_ADC_DRY
          ) *
          100.0f /
          span,
          0,
          100
        );
  }

  // ========================================================================
  // RAIN
  // ========================================================================

  sensors.rainRaw =
      readAnalogAvg(RAIN_PIN);

  if (!rainBaselineSet) {

    sensors.rainIntensity = 0;
    sensors.rainWet = false;

  } else {

    int delta =
        sensors.rainRaw -
        rainBaselineRaw;

    if (delta < 0)
      delta = -delta;

    float denominator =
        RAIN_FULL_WET_DELTA > 0
        ? (float)RAIN_FULL_WET_DELTA
        : 1.0f;

    sensors.rainIntensity =
        clampf(
          delta *
          100.0f /
          denominator,
          0,
          100
        );

    sensors.rainWet =
        sensors.rainIntensity >
        RAIN_STOP_THRESHOLD;
  }

  // ========================================================================
  // LDR
  // ========================================================================

  sensors.ldrRaw =
      readAnalogAvg(LDR_PIN);

  float brightnessRaw;

  if (LDR_BRIGHT_WHEN_HIGH) {

    brightnessRaw =
        (float)sensors.ldrRaw;

  } else {

    brightnessRaw =
        4095.0f -
        sensors.ldrRaw;
  }

  // Convert ADC 0-4095 to sunlight 0-100%.
  sensors.lightPct =
      clampf(
        brightnessRaw *
        100.0f /
        4095.0f,
        0,
        100
      );

  // Categorize light.
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
      "RAIN=%5.1f%% raw=%4d\n",

      sensors.soilPct,
      sensors.soilRaw,
      sensors.soilFault,

      sensors.lightPct,
      sensors.ldrRaw,

      sensors.rainIntensity,
      sensors.rainRaw
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

    // Rain detected
    if (
        sensors.rainIntensity >
        RAIN_STOP_THRESHOLD
    ) {

      Serial.printf(
          "[AUTO] BLOCKED: "
          "rain %.1f%% > %.1f%%\n",
          sensors.rainIntensity,
          RAIN_STOP_THRESHOLD
      );

      return false;
    }
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
// PUMP SAFETY / AUTO MONITOR
// ============================================================================

void pumpUpdate() {

  if (!pumpIsOn)
    return;

  // ========================================================================
  // RAIN SAFETY
  // ========================================================================

  if (
      sensors.rainIntensity >
      RAIN_STOP_THRESHOLD
  ) {

    stopPump(
        "rain > 50%"
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

    // Soil reached target
    if (
        !sensors.soilFault &&
        sensors.soilPct >=
        autoSoilHigh
    ) {

      stopPump(
          "target moisture reached"
      );

      return;
    }

    // Sunlight became insufficient
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
  //
  // IMPORTANT:
  // Backend expects light_level in 0-100 range.
  //
  // DO NOT multiply by 12.
  //
  // ========================================================================

  float serverLightLevel =
      sensors.lightPct;

  json +=
      ",\"light_level\":" +
      String(
        serverLightLevel,
        1
      );

  // ========================================================================
  // RAIN
  // ========================================================================

  json +=
      ",\"rain_detected\":" +
      String(
        sensors.rainIntensity >
        RAIN_STOP_THRESHOLD
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

    autoModeEnabled =
        true;

    Serial.println(
        "[MODE] AUTO"
    );
  }

  if (
      body.indexOf(
        "\"mode\":\"manual\""
      ) >= 0
  ) {

    autoModeEnabled =
        false;

    Serial.println(
        "[MODE] MANUAL"
    );
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
  // START
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
  // STOP
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
      "RAIN raw=%d intensity=%.1f%% wet=%s\n",
      sensors.rainRaw,
      sensors.rainIntensity,
      sensors.rainWet
      ? "YES"
      : "NO"
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

      case 's':

        readSensors();
        printSensors();

        break;

      case 't':

        simCommand(
            "AT",
            1000,
            "OK"
        );

        break;

      case 'm':

        simSendSms(
            SMS_MANAGER_PHONE,
            SMS_TEST_TEXT
        );

        break;

      // MANUAL ON
      case 'o':

        autoModeEnabled =
            false;

        startPump(
            SRC_MANUAL,
            MAX_PUMP_RUN_SECONDS
        );

        break;

      // MANUAL OFF
      case 'f':

        stopPump(
            "serial stop"
        );

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

  pinMode(
      RELAY_PIN,
      OUTPUT
  );

  // Force physical pump OFF immediately.
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
  // SENSORS
  // ========================================================================

  readSensors();

  captureRainBaseline();

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

    // Evaluate immediately using fresh values.
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
        "pump=%s | "
        "soil=%.1f%% | "
        "sun=%.1f%% | "
        "rain=%.1f%% | "
        "wifi=%s\n",

        pumpIsOn
        ? "ON"
        : "OFF",

        sensors.soilPct,

        sensors.lightPct,

        sensors.rainIntensity,

        wifiUp()
        ? "ONLINE"
        : "OFFLINE"
    );
  }
}