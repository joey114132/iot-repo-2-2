/*
 * 출구 차단기 보드 (ESP32) - LCD + APDS-9960
 * - 3.device_client TCP 8080 접속, DEV-GATE-2 로 등록
 * - PING/PONG keepalive, TYPE_DEVICE_LIST, TYPE_DEV_REGISTER
 * - 서버 → TYPE_CMD_DISPLAY 수신 시 LCD 2줄 출력
 * - 차량 감지 시 EXIT 이벤트 전송
 */
 #include <Wire.h>
 #include <SparkFun_APDS9960.h>
 #include <WiFi.h>
 #include <LiquidCrystal.h>
 #include <ESP32Servo.h>
 
 // [설정] 네트워크 환경 (esp32_board1_1 과 동일 구조)
 #if 1
 const char* ssid     = "addinedu_201class_2-2.4G";
 const char* password = "201class2!";
 const char* serverIP = "192.168.0.137";  // 3.device_client PC
 #else
 const char* ssid     = "iptime_WiFiCE6D";
 const char* password = "!Tony6251@";
 const char* serverIP = "192.168.0.137";
 #endif
 const uint16_t serverPort = 8080;
 
 // [안정화 설정]
 const int MAX_RETRY = 5;
 unsigned long lastReconnectAttempt = 0;
 const unsigned long PING_INTERVAL_MS  = 5000;
 const unsigned long PONG_TIMEOUT_MS   = 5000;
 const unsigned long RECONNECT_INTERVAL_MS = 3000;
 
 // 패킷 타입
 #define TYPE_PING          0xFE
 #define TYPE_PONG          0xFD
 #define TYPE_IR_EVENT      0
 #define TYPE_DEVICE_LIST   4
 #define TYPE_DEV_REGISTER  6
 #define TYPE_CMD_OPEN      2
 #define TYPE_CMD_CLOSE     5
 #define TYPE_CMD_DISPLAY   7   // 서버 → 클라이언트: LCD 2줄 출력
 
 #define EV_EXIT            2
 
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
 
 bool isGateOpen = false;
 #define EV_GATE_OPEN       4
 #define EV_GATE_CLOSED     5
 
 // LCD: RS(13), E(23), D4(19), D5(18), D6(17), D7(16)
 LiquidCrystal lcd(13, 23, 19, 18, 17, 16);
 
 // APDS-9960 I2C: board1_1 과 동일하게 32/14 사용 (같은 보드 구성이면 이걸 쓰세요)
 #define I2C_SDA 21
 #define I2C_SCL 22
 // 다른 배선이면 아래로 변경: #define I2C_SDA 21  #define I2C_SCL 22
 
 const uint16_t LIGHT_THRESHOLD = 10;  // board1_1 과 동일: 이 값 이하가 되면 감지(손 가리기)
 bool sensor_ok = false;
 unsigned long lastDebugPrint = 0;
 const unsigned long DEBUG_PRINT_INTERVAL_MS = 500;
 unsigned long lastKeepAliveTime = 0;
 unsigned long lastDetectionTime = 0;
 bool deviceListSent = false;
 
 // devices 테이블과 매칭 (출구 차단기 = id 2, DEV-GATE-2)
 const char* DEVICE_GUID = "DEV-GATE-2";
 const char* DEVICE_NAME = "입출구 차단기 컨트롤러_2";
 
 void updateDisplay(const char* line1, const char* line2 = "") {
     lcd.clear();
     lcd.setCursor(0, 0);
     lcd.print(line1);
     lcd.setCursor(0, 1);
     lcd.print(line2);
 }
 
 void sendDeviceList() {
     if (!client.connected()) return;
 
     memset(&txPkt, 0, sizeof(txPkt));
     txPkt.type = TYPE_DEVICE_LIST;
     txPkt.payload[0] = 0;
     txPkt.payload[1] = 2;
     strncpy((char*)&txPkt.payload[2], "ESP32-S2-EXIT01", 16);
     strncpy((char*)&txPkt.payload[18], "APDS-9960", 14);
     client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
     delay(50);
 
     memset(&txPkt, 0, sizeof(txPkt));
     txPkt.type = TYPE_DEVICE_LIST;
     txPkt.payload[0] = 1;
     txPkt.payload[1] = 2;
     strncpy((char*)&txPkt.payload[2], "ESP32-LCD-01", 16);
     strncpy((char*)&txPkt.payload[18], "LCD1602", 14);
     client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
     delay(20);
     Serial.println("Device list sent to server.");
 }
 
 void registerDevice() {
     if (!client.connected()) return;
     memset(&txPkt, 0, sizeof(txPkt));
     txPkt.type = TYPE_DEV_REGISTER;
     strncpy((char*)&txPkt.payload[0], DEVICE_GUID, 16);
     strncpy((char*)&txPkt.payload[16], DEVICE_NAME, 16);
     client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket));
     Serial.printf("Sent device register: guid=%s name=%s\n", DEVICE_GUID, DEVICE_NAME);
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
 
 void openGate(const char* source) {
     if (isGateOpen) {
         sendEvent(EV_GATE_OPEN, source, "ALREADY_OPEN");
         return;
     }
     myServo.write(90);
     delay(200);
     isGateOpen = true;
     sendEvent(EV_GATE_OPEN, source, "ACK_OK");
     Serial.println("ACTION: GATE_OPEN BY " + String(source));
 }
 
 void closeGate(const char* source) {
     if (!isGateOpen) {
         sendEvent(EV_GATE_CLOSED, source, "ALREADY_CLOSED");
         return;
     }
     myServo.write(0);
     delay(200);
     isGateOpen = false;
     sendEvent(EV_GATE_CLOSED, source, "ACK_OK");
     Serial.println("ACTION: GATE_CLOSED BY " + String(source));
 }
 
 void setup() {
     Serial.begin(115200);
     //delay(3000);
     Serial.println("\n\n## PARKING SYSTEM: EXIT BOARD (esp32_board1_2) ##\n");
     delay(5000);
     Serial.printf(">>> STEP 1: I2C BUS SCAN (SDA:%d, SCL:%d) - board1_1 과 동일\n", I2C_SDA, I2C_SCL);
     Wire.begin(I2C_SDA, I2C_SCL);
     delay(500); // 전원 안정화 대기
 
     // board1_1 과 동일한 초기화 순서: init() → enableLightSensor(false)
     Serial.println("\n>>> STEP 2: INITIALIZING SENSOR (board1_1 동일 방식)");
     for (int i = 0; i < MAX_RETRY; i++) {
         Serial.printf("    Attempt %d/%d...\n", i + 1, MAX_RETRY);
 
         if (apds.init()) {
             Serial.println("    [OK] apds.init()");
         } else {
             Serial.println("    [WARN] apds.init() failed");
         }
 
         if (apds.enableLightSensor(false)) {
             sensor_ok = true;
             Serial.println("    [SUCCESS] Light sensor running (board1_1 동일)");
             break;
         } else {
             Serial.println("    [WARN] enableLightSensor failed");
         }
 
         delay(500);
         if (sensor_ok) 
         {
             Serial.println(F("---Sensor 1 APDS-9960 Light Sensor Test End OK---"));
             break;
         }
 
     }
 
     if (sensor_ok) 
     {   
         Serial.println("sensor1 Init OK");
     }
     else
     {
         Serial.println("   [FATAL] Sensor not found. SDA/SCL=32/14 인지 확인.");
         updateDisplay("I2C ERROR", "CHECK SENSOR");
     }
 
     lcd.begin(16, 2);
     
     myServo.setPeriodHertz(50);
     myServo.attach(25, 500, 2400); // 보드2용 서보 핀 (서보 배선에 따라 다를 수 있음)
     myServo.write(0);
     //updateDisplay("HW CHECKING...", "PLEASE WATCH SER");
 
 
     Serial.printf("Step 3: Connecting to WiFi (%s)...\n", ssid);
     updateDisplay("CONNECTING WIFI", ssid);
     WiFi.begin(ssid, password);
     while (WiFi.status() != WL_CONNECTED) {
         delay(500);
         Serial.print(".");
     }
     Serial.println("\n   [SUCCESS] WiFi Connected!");
     Serial.print("IP Address: ");
     Serial.println(WiFi.localIP());
     updateDisplay("WIFI OK", WiFi.localIP().toString().c_str());
     delay(1000);
 
     Serial.printf("Step 4: Connecting to Server (%s:%d)...\n", serverIP, serverPort);
     if (client.connect(serverIP, serverPort)) {
         registerDevice();
         deviceListSent = true;
         lastKeepAliveTime = millis();
         Serial.println("   [SUCCESS] Server Registered!");
         updateDisplay("SERVER OK", "READY!");
     } else {
         Serial.println("   [FAILED] Server connection.");
         updateDisplay("SERVER FAILED", "RETRYING...");
     }
 }
 
 void loop() {
     if (!client.connected()) {
         if (millis() - lastReconnectAttempt >= RECONNECT_INTERVAL_MS) {
             lastReconnectAttempt = millis();
             deviceListSent = false;
             client.stop();
             Serial.println("Attempting to connect to Server...");
             if (client.connect(serverIP, serverPort)) {
                 lastKeepAliveTime = millis();
                 Serial.println("Reconnected to Server.");
                 registerDevice();
                 deviceListSent = true;
             }
         }
         delay(150);
         return;
     }
 
     if (client.connected() && !deviceListSent) {
         sendDeviceList();
         deviceListSent = true;
     }
 
     // Keep-alive: PING
     if (client.connected() && (millis() - lastKeepAliveTime >= PING_INTERVAL_MS)) {
         while (client.available() >= sizeof(UnifiedPacket)) {
             client.read((uint8_t*)&rxPkt, sizeof(UnifiedPacket));
         }
         memset(&txPkt, 0, sizeof(txPkt));
         txPkt.type = TYPE_PING;
         if (client.write((uint8_t*)&txPkt, sizeof(UnifiedPacket)) != sizeof(UnifiedPacket)) {
             client.stop();
             return;
         }
         client.setTimeout(PONG_TIMEOUT_MS);
         size_t total = 0;
         unsigned long deadline = millis() + PONG_TIMEOUT_MS;
         while (total < sizeof(UnifiedPacket) && millis() < deadline) {
             if (client.available() > 0) {
                 size_t n = client.read((uint8_t*)&rxPkt + total, sizeof(UnifiedPacket) - total);
                 total += n;
             } else {
                 delay(10);
             }
         }
         client.setTimeout(0);
         if (total != sizeof(UnifiedPacket) || rxPkt.type != TYPE_PONG) {
             Serial.println("Keep-alive timeout.");
             client.stop();
             return;
         }
         lastKeepAliveTime = millis();
     }
 
     // 서버 명령 수신 (PONG 은 keepalive 블록에서 처리됨)
     if (client.available() >= sizeof(UnifiedPacket)) {
         client.read((uint8_t*)&rxPkt, sizeof(UnifiedPacket));
         if (rxPkt.type == TYPE_PONG) {
             // keepalive 응답 (이미 위에서 대기하므로 여기 오면 별도 PONG)
         } else if (rxPkt.type == TYPE_CMD_DISPLAY) {
             char line1[17] = {0};
             char line2[17] = {0};
             memcpy(line1, &rxPkt.payload[0], 16);
             memcpy(line2, &rxPkt.payload[16], 16);
             updateDisplay(line1, line2);
         } else if (rxPkt.type == TYPE_CMD_OPEN) {
             openGate("SERVER");
         } else if (rxPkt.type == TYPE_CMD_CLOSE) {
             closeGate("SERVER");
         }
     }
 
     // 조도 디버그: 시리얼에 주기적으로 값 출력 (손 가렸을 때 값이 떨어지는지 확인)
     if (sensor_ok && millis() - lastDebugPrint >= DEBUG_PRINT_INTERVAL_MS) {
         lastDebugPrint = millis();
         uint16_t lightVal = 0;
         if (apds.readAmbientLight(lightVal)) {
             Serial.printf("[APDS] AmbientLight=%u (<=%u 이면 감지)\n", (unsigned)lightVal, (unsigned)LIGHT_THRESHOLD);
         } else {
             Serial.println("[APDS] readAmbientLight failed");
         }
     }
 
     static bool sensor_was_detected = false;
    if (sensor_ok && (millis() - lastDetectionTime > 150)) {
        uint16_t lightVal = 0;
        bool sensor_now_detected = false;
        
        if (apds.readAmbientLight(lightVal)) {
            if (lightVal > 0 && lightVal <= LIGHT_THRESHOLD) {
                sensor_now_detected = true;
            }

            if (sensor_now_detected && !sensor_was_detected) {
                // 막힘 (새로운 감지)
                Serial.printf("[EXIT] DETECTED light=%u\n", (unsigned)lightVal);
                sendEvent(EV_EXIT, "ESP32-S2-EXIT01", "DETECTED");
                updateDisplay("CAR EXITS NOW", "THANK YOU!");
                sensor_was_detected = true;
            } 
            else if (!sensor_now_detected && sensor_was_detected) {
                // 해제 (차량 통과 완료)
                Serial.printf("[EXIT] CLEAR light=%u\n", (unsigned)lightVal);
                sendEvent(EV_EXIT, "ESP32-S2-EXIT01", "CLEAR");
                updateDisplay("SERVER OK", "READY!");
                sensor_was_detected = false;
            }
        }
        lastDetectionTime = millis();
    }
     delay(50);
 }