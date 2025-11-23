#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME680.h>
#include <Arduino_RouterBridge.h>
#include <ArduinoJson.h> 

Adafruit_BME680 bme;

String getAllSensors() {
  // Create a JSON document (256 bytes is sufficient for this data)
  StaticJsonDocument<256> doc;

  // Populate the JSON with the latest readings
  doc["temp"] = bme.temperature;
  doc["hum"] = bme.humidity;
  doc["press"] = bme.pressure / 100.0; // hPa
  doc["gas"] = bme.gas_resistance;     // Ohms (Raw resistance)

  // Serialize JSON to string to send back to Python
  String output;
  serializeJson(doc, output);
  return output;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();

  if (!bme.begin()) {
    Serial.println("BME680 not found!");
    while (1);
  }
  
  // Set up oversampling and filter initialization
  bme.setTemperatureOversampling(BME680_OS_8X);
  bme.setHumidityOversampling(BME680_OS_2X);
  bme.setPressureOversampling(BME680_OS_4X);
  bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
  
  // HEATER PROFILE: 320*C for 150ms
  // This is required for the gas resistance reading to work
  bme.setGasHeater(320, 150);

  Bridge.begin();

  // Bind the C++ function to the key "getAll" so Python can call it
  Bridge.provide("getAll", getAllSensors);
}

void loop() {
  // Trigger a measurement
  // This updates the internal bme.temperature, bme.gas_resistance.
  if (!bme.performReading()) {
    Serial.println("Failed to perform reading :(");
    return;
}
  unsigned long endTime = bme.performReading();
  
  // If you read too fast, the sensor self-heats and throws off the calibration.
  // A 2-second delay aligns with the typical VOC sampling rate.
  delay(2000); 
  
  // The Bridge library handles the RPC calls in the background interrupts
}
