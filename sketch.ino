#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME680.h>
#include <Arduino_RouterBridge.h>
#include <ArduinoJson.h> 
#include <Arduino_LED_Matrix.h>

Adafruit_BME680 bme;
Arduino_LED_Matrix matrix;

// HAPPY FACE (Low VOC)
uint8_t happy_face[104] = {
  0,0,0,0,0,0,0,0,0,0,0,0,0, 
  0,0,0,0,1,0,0,0,1,0,0,0,0, 
  0,0,0,0,1,0,0,0,1,0,0,0,0, 
  0,0,1,0,0,0,1,0,0,0,1,0,0, 
  0,0,1,0,0,0,1,0,0,0,1,0,0, 
  0,0,0,1,1,0,0,0,1,1,0,0,0, 
  0,0,0,0,0,1,1,1,0,0,0,0,0, 
  0,0,0,0,0,0,0,0,0,0,0,0,0
};


// NEUTRAL FACE (Marginal VOC)
uint8_t neutral_face[104] = {
  0,0,0,0,0,0,0,0,0,0,0,0,0, 
  0,0,0,0,1,0,0,0,1,0,0,0,0, 
  0,0,0,0,1,0,0,0,1,0,0,0,0, 
  0,0,0,0,0,0,1,0,0,0,0,0,0, 
  0,0,0,0,0,0,1,0,0,0,0,0,0, 
  0,0,0,0,0,0,0,0,0,0,0,0,0, 
  0,0,1,1,1,1,1,1,1,1,1,0,0, 
  0,0,0,0,0,0,0,0,0,0,0,0,0
};

// DANGER / SKULL (Hazardous VOC)
uint8_t danger_face[104] = {
  0,0,0,0,1,1,1,0,0,0,0,0,0, 
  0,0,0,1,0,0,0,1,0,0,0,0,0, 
  0,0,1,0,1,0,1,0,1,0,0,0,0, 
  0,0,1,0,0,0,0,0,1,0,0,0,0, 
  0,0,1,0,1,1,1,0,1,0,0,0,0, 
  0,0,0,1,0,0,0,1,0,0,0,0,0, 
  0,0,0,0,1,1,1,0,0,0,0,0,0, 
  0,0,0,0,0,1,0,0,0,0,0,0,0
};

// Python sends "0" (Happy), "1" (Neutral), "2" (Danger)
void setMatrixFace(String command) {
  int faceId = command.toInt();
  
  if (faceId == 0) {
    matrix.draw(happy_face);
  } else if (faceId == 1) {
    matrix.draw(neutral_face);
  } else {
    matrix.draw(danger_face);
  }
}

String getAllSensors() {
  StaticJsonDocument<256> doc;
  doc["temp"] = bme.temperature;
  doc["hum"] = bme.humidity;
  doc["press"] = bme.pressure / 100.0;
  doc["gas"] = bme.gas_resistance;
  String output;
  serializeJson(doc, output);
  return output;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  matrix.begin();

  if (!bme.begin()) {
    Serial.println("BME680 not found!");
    while (1);
  }
  
  // Sensor Settings
  bme.setTemperatureOversampling(BME680_OS_8X);
  bme.setHumidityOversampling(BME680_OS_2X);
  bme.setPressureOversampling(BME680_OS_4X);
  bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme.setGasHeater(320, 150);

  Bridge.begin();

  // Expose functions to Python
  Bridge.provide("getAll", getAllSensors);
  Bridge.provide("setFace", setMatrixFace);
  
  // Start with Happy Face
  matrix.draw(happy_face);
}

void loop() {
  if (!bme.performReading()) {
    return;
  }
  delay(2000); 
}
