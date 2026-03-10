#include <Wire.h>
#include <SparkFun_APDS9960.h>
#include <WiFi.h>
#include <LiquidCrystal.h>

// [설정] 네트워크 환경
const char* ssid = "addinedu_201class_2-2.4G";
const char* password = "201class2!";
const char* serverIP = "192.168.0.137";
const uint16_t serverPort = 8080;

// [안정화 설정]
const int MAX_RETRY = 5;

// 패킷 타입
#define TYPE_PING        0xFE
#define TYPE_PONG        0xFD
#define TYPE_IR_EVENT    0
#define TYPE_DEVICE_LIST 4
#define TYPE_DEV_REGISTER 6
#define TYPE_CMD_DISPLAY 7 // 서버 -> 클라이언트: LCD 출력 명령

#define EV_EXIT          2

#pragma pack(push, 1)
struct UnifiedPacket {
    uint8_t type;
    uint8_t payload[32];
};
#pragma pack(pop)

WiFiClient client;
UnifiedPacket txPkt;
UnifiedPacket rxPkt;

SparkFun_APDS9960 apds = SparkFun_APDS9960();

// LCD Setup: RS(13), E(23), D4(19), D5(18), D6(17), D7(16)
LiquidCrystal lcd(13, 23, 19, 18, 17, 16);

// I2C Setup: SDA(21), SCL(22)
#define I2C_SDA 21
#define I2C_SCL 22

const uint16_t PROXIMITY_THRESHOLD = 50;
bool sensor_ok = false;
unsigned long lastKeepAliveTime = 0;
unsigned long lastDetectionTime = 0;
const unsigned long DETECTION_DELAY = 1000;
const unsigned long PING_INTERVAL_MS = 5000;

// 장치 정보
const char* DEVICE_GUID = "DEV-GATE-2";
const char* DEVICE_NAME = "Exit (LCD+APDS)";

void updateDisplay(const char* line1, const char* line2 = "") {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print(line1);
    lcd.setCursor(0, 1);
    lcd.print(line2);
}

void sendDeviceList() {
    if (!client.connected()) return;
    
    struct {
        uint8_t idx;
        uint8_t total;
        char guid[16];
        char name[14];
    } __attribute__((packed)) item;

    // 1. APDS-9960
    memset(&txPkt, 0, sizeof(txPkt));
    txPkt.type = TYPE_DEVICE_LIST;
    item.idx = 0; item.total = 2;
    strncpy(item.guid, "ESP32-S2-EXIT01", 16);
    strncpy(item.name, "APDS-9960", 14);
    memcpy(txPkt.payload, &item, sizeof(item));
    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
    delay(50);

    // 2. LCD 1602
    memset(&txPkt, 0, sizeof(txPkt));
    txPkt.type = TYPE_DEVICE_LIST;
    item.idx = 1; item.total = 2;
    strncpy(item.guid, "ESP32-LCD-01", 16);
    strncpy(item.name, "LCD1602 (I2C)", 14);
    memcpy(txPkt.payload, &item, sizeof(item));
    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
}

void registerDevice() {
    if (!client.connected()) return;
    memset(&txPkt, 0, sizeof(txPkt));
    txPkt.type = TYPE_DEV_REGISTER;
    strncpy((char*)&txPkt.payload[0], DEVICE_GUID, 16);
    strncpy((char*)&txPkt.payload[16], DEVICE_NAME, 16);
    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
    Serial.println("Device registered.");
    delay(100);
    sendDeviceList();
}

void sendEvent(uint8_t ev, const char* src, const char* ext) {
    if (!client.connected()) return;
    memset(&txPkt, 0, sizeof(txPkt));
    txPkt.type = TYPE_IR_EVENT;
    txPkt.payload[0] = ev;
    strncpy((char*)&txPkt.payload[1], src, 15);
    if (ext) strncpy((char*)&txPkt.payload[17], ext, 14);
    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
}

void setup() {
    Serial.begin(115200);
    delay(3000); // Give user time to open Serial Monitor
    Serial.println("\n\n###########################################");
    Serial.println("##      PARKING SYSTEM: EXIT BOARD       ##");
    Serial.println("###########################################\n");

    lcd.begin(16, 2);
    updateDisplay("HW CHECKING...", "PLEASE WATCH SER");
    
    Serial.println(">>> STEP 1: I2C BUS SCAN (SDA:21, SCL:22)");
    Wire.begin(I2C_SDA, I2C_SCL);
    delay(500);
    
    byte error, address;
    int nDevices = 0;
    for(address = 1; address < 127; address++ ) {
        Wire.beginTransmission(address);
        error = Wire.endTransmission();
        if (error == 0) {
            Serial.printf("    [FOUND] Device at address 0x%02X\n", address);
            nDevices++;
            if (address == 0x39) Serial.println("    [NOTE]  Address 0x39 matches APDS-9960!");
        } else if (error == 4) {
            Serial.printf("    [ERR]   Unknown error at address 0x%02X\n", address);
        }
    }
    
    if (nDevices == 0) {
        Serial.println("    [CRITICAL] NO I2C DEVICES FOUND!");
        Serial.println("    Please check:");
        Serial.println("    1. VCC/GND connected?");
        Serial.println("    2. SDA to D21, SCL to D22?");
        Serial.println("    3. Wires loose?");
    }
    
    Serial.println("\n>>> STEP 2: INITIALIZING SENSOR");
    for (int i = 0; i < MAX_RETRY; i++) {
        Serial.printf("    Attempt %d/%d...\n", i+1, MAX_RETRY);
        
        Wire.beginTransmission(0x39);
        if (Wire.endTransmission() == 0) {
            Serial.println("    [OK] Hardware responded at 0x39.");
            
            // Debug: Read Device ID (Register 0x92)
            Wire.beginTransmission(0x39);
            Wire.write(0x92);
            Wire.endTransmission();
            Wire.requestFrom(0x39, 1);
            if (Wire.available()) {
                byte id = Wire.read();
                Serial.printf("    [DEBUG] Sensor ID Register: 0x%02X (Expected: 0xAB)\n", id);
            }

            if (apds.init()) {
                if (apds.enableLightSensor(false)) {
                    sensor_ok = true;
                    Serial.println("    [SUCCESS] APDS-9960 Ready via Library!");
                    break;
                }
            } else {
                Serial.println("    [WARN] library apds.init() failed. Trying FORCE INIT...");
                
                // Force Power On & Enable Light Sensor (Manual I2C writes)
                // Register 0x80 (ENABLE): PON=1, AEN=1
                Wire.beginTransmission(0x39);
                Wire.write(0x80);
                Wire.write(0x03); 
                Wire.endTransmission();
                
                // Register 0x8F (CONTROL): Set ALS Gain to 16x (0x02) for more sensitivity
                Wire.beginTransmission(0x39);
                Wire.write(0x8F);
                Wire.write(0x02);
                
                if (Wire.endTransmission() == 0) {
                    sensor_ok = true;
                    Serial.println("    [SUCCESS] SENSOR FORCED ON with 16x Gain!");
                    break;
                } else {
                    Serial.println("    [ERROR] Force Init failed.");
                }
            }
        } else {
            Serial.println("    [ERR] No hardware response at 0x39.");
        }
        delay(1500);
    }

    if (!sensor_ok) {
        Serial.println("   [FATAL] Sensor not found. Check wiring (SDA:21, SCL:22)");
        updateDisplay("I2C ERROR", "CHECK SENSOR");
    }

    Serial.printf("Step 3: Connecting to WiFi (%s)...\n", ssid);
    updateDisplay("CONNECTING WIFI", ssid);
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) { 
        delay(500); 
        Serial.print("."); 
    }
    Serial.println("\n   [SUCCESS] WiFi Connected!");
    updateDisplay("WIFI OK", WiFi.localIP().toString().c_str());
    delay(1000);
    
    Serial.printf("Step 4: Connecting to Server (%s)...\n", serverIP);
    if (client.connect(serverIP, serverPort)) {
        registerDevice();
        Serial.println("   [SUCCESS] Server Registered!");
        updateDisplay("SERVER OK", "READY!");
    } else {
        Serial.println("   [FAILED] Server connection.");
        updateDisplay("SERVER FAILED", "RETRYING...");
    }
}

void loop() {
    if (!client.connected()) {
        if (client.connect(serverIP, serverPort)) registerDevice();
        else { delay(5000); return; }
    }

    // Keep-alive
    if (millis() - lastKeepAliveTime >= PING_INTERVAL_MS) {
        memset(&txPkt, 0, sizeof(txPkt));
        txPkt.type = TYPE_PING;
        client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
        lastKeepAliveTime = millis();
    }

    // Server commands
    if (client.available() >= sizeof(UnifiedPacket)) {
        client.read((uint8_t*)&rxPkt, sizeof(UnifiedPacket));
        if (rxPkt.type == TYPE_CMD_DISPLAY) {
            char line1[17] = {0};
            char line2[17] = {0};
            memcpy(line1, &rxPkt.payload[0], 16);
            memcpy(line2, &rxPkt.payload[16], 16);
            updateDisplay(line1, line2);
        }
    }

    // Detection (Check every 200ms)
    static bool isCarDetected = false;

    if (sensor_ok && millis() - lastDetectionTime > 200) {
        uint16_t lightVal = 0;
        if (apds.readAmbientLight(lightVal)) {
            // Car detection: Light level drops when shadowed
            if (lightVal > 0 && lightVal < 15) { 
                if (!isCarDetected) {
                    isCarDetected = true;
                    sendEvent(EV_EXIT, "ESP32-S2-EXIT01", "DETECTED");
                    updateDisplay("CAR EXITS NOW", "THANK YOU!");
                }
            } else {
                if (isCarDetected) {
                    isCarDetected = false;
                    sendEvent(EV_EXIT, "ESP32-S2-EXIT01", "CLEAR");
                    updateDisplay("SERVER OK", "READY!");
                }
            }
        }
        lastDetectionTime = millis();
    }
    delay(50); 
}

