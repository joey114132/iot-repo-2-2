#include <Wire.h>
#include <SparkFun_APDS9960.h>
#include <ESP32Servo.h>
#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>

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
#define TYPE_RFID        1
#define TYPE_CMD_OPEN    2
#define TYPE_CMD_CLOSE   5
#define TYPE_CMD_WRITE   3
#define TYPE_DEVICE_LIST 4
#define TYPE_DEV_REGISTER 6

#define EV_ENTRY         1
#define EV_GATE_OPEN     4
#define EV_GATE_CLOSED   5

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
Servo myServo;
MFRC522 rfid(5, 32); // SS:5, RST:32
MFRC522::MIFARE_Key key;

// I2C: SDA(21), SCL(22)
#define I2C_SDA 21
#define I2C_SCL 22
#define PIN_SERVO 27
#define RFID_BLOCK 4

const uint16_t PROXIMITY_THRESHOLD = 50;
bool isGateOpen = false;
bool sensor_ok = false;
bool serverWriteSiteID = false;
char serverSiteID[16] = {0};
unsigned long lastKeepAliveTime = 0;
unsigned long lastDetectionTime = 0;
const unsigned long DETECTION_DELAY = 1000;
const unsigned long PING_INTERVAL_MS = 5000;

const char* DEVICE_GUID = "DEV-GATE-1";
const char* DEVICE_NAME = "Entry (APDS+RFID)";

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
    item.idx = 0; item.total = 3;
    strncpy(item.guid, "ESP32-S1-ENTRY01", 16);
    strncpy(item.name, "APDS-9960", 14);
    memcpy(txPkt.payload, &item, sizeof(item));
    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
    delay(50);

    // 2. RFID
    memset(&txPkt, 0, sizeof(txPkt));
    txPkt.type = TYPE_DEVICE_LIST;
    item.idx = 1; item.total = 3;
    strncpy(item.guid, "ESP32-RFID-01", 16);
    strncpy(item.name, "MFRC522", 14);
    memcpy(txPkt.payload, &item, sizeof(item));
    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
    delay(50);

    // 3. Servo
    memset(&txPkt, 0, sizeof(txPkt));
    txPkt.type = TYPE_DEVICE_LIST;
    item.idx = 2; item.total = 3;
    strncpy(item.guid, "ESP32-GATE-01", 16);
    strncpy(item.name, "Servo (SG90)", 14);
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
    Wire.begin(I2C_SDA, I2C_SCL);
    for (int i = 0; i < MAX_RETRY; i++) {
        if (apds.init() && apds.enableProximitySensor(false)) { sensor_ok = true; break; }
        delay(500);
    }
    SPI.begin(18, 19, 23, 5); // Default SPI for ESP2 (can adjust if needed)
    rfid.PCD_Init();
    for (byte i = 0; i < 6; i++) key.keyByte[i] = 0xFF;
    myServo.attach(PIN_SERVO, 500, 2400);
    myServo.write(0);

    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) delay(500);
    if (client.connect(serverIP, serverPort)) registerDevice();
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
        if (rxPkt.type == TYPE_CMD_OPEN) {
            myServo.write(90); isGateOpen = true;
            sendEvent(EV_GATE_OPEN, "ENTRY-GATE", "");
        } else if (rxPkt.type == TYPE_CMD_CLOSE) {
            myServo.write(0); isGateOpen = false;
            sendEvent(EV_GATE_CLOSED, "ENTRY-GATE", "");
        } else if (rxPkt.type == TYPE_CMD_WRITE) {
            memcpy(serverSiteID, &rxPkt.payload[1], 16);
            serverWriteSiteID = true;
        }
    }

    // Detection
    static bool isCarDetected = false;
    
    if (sensor_ok && millis() - lastDetectionTime > 200) {
        uint8_t prox = 0;
        if (apds.readProximity(prox)) {
            if (prox >= PROXIMITY_THRESHOLD) {
                if (!isCarDetected) {
                    isCarDetected = true;
                    sendEvent(EV_ENTRY, "ESP32-S1-ENTRY01", "DETECTED");
                }
            } else if (prox < PROXIMITY_THRESHOLD - 10) {
                if (isCarDetected) {
                    isCarDetected = false;
                    sendEvent(EV_ENTRY, "ESP32-S1-ENTRY01", "CLEAR");
                }
            }
        }
        lastDetectionTime = millis();
    }


    // RFID
    if (rfid.PICC_IsNewCardPresent() && rfid.PICC_ReadCardSerial()) {
        char uidStr[16] = {0};
        for (byte i = 0; i < rfid.uid.size && i < 4; i++)
            snprintf(uidStr + i * 2, sizeof(uidStr) - i * 2, "%02x", rfid.uid.uidByte[i]);
        
        if (rfid.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, RFID_BLOCK, &key, &(rfid.uid)) == MFRC522::STATUS_OK) {
            if (serverWriteSiteID) {
                byte buf[16] = {0}; memcpy(buf, serverSiteID, 15);
                rfid.MIFARE_Write(RFID_BLOCK, buf, 16);
                serverWriteSiteID = false;
            } else {
                byte buf[18]; byte sz = 18;
                if (rfid.MIFARE_Read(RFID_BLOCK, buf, &sz) == MFRC522::STATUS_OK) {
                    char siteStr[16] = {0}; memcpy(siteStr, buf, 15);
                    memset(&txPkt, 0, sizeof(txPkt));
                    txPkt.type = TYPE_RFID;
                    strncpy((char*)&txPkt.payload[1], uidStr, 15);
                    strncpy((char*)&txPkt.payload[17], siteStr, 14);
                    client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
                }
            }
        }
        rfid.PICC_HaltA(); rfid.PCD_StopCrypto1();
    }
    delay(50);
}
