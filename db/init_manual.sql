-- 1. DB Initialization
CREATE DATABASE IF NOT EXISTS smart_parking
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE smart_parking;

-- 2. Drop Tables (Child -> Parent)
DROP TABLE IF EXISTS event_logs;
DROP TABLE IF EXISTS sensors;
DROP TABLE IF EXISTS rfid_cards;
DROP TABLE IF EXISTS payment_cards;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS guest_visits;
DROP TABLE IF EXISTS residents;
DROP TABLE IF EXISTS parking_slots;
DROP TABLE IF EXISTS device_clients;
DROP TABLE IF EXISTS devices;
DROP TABLE IF EXISTS parking_records;

-- 3. Unified Table Creation

-- Device Table (from Manual: Includes device_guid)
CREATE TABLE devices (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  name            VARCHAR(100) NOT NULL,
  type            VARCHAR(50)  NOT NULL,         
  device_type     ENUM('CLIENT','SERVER') NOT NULL, 
  connection_type VARCHAR(20)  NOT NULL DEFAULT 'ethernet',
  connection_detail VARCHAR(100) NULL,           
  control_method  VARCHAR(50)  NULL,             
  ip_address      VARCHAR(45)  NULL,
  port_info       VARCHAR(50)  NULL,             
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,
  is_connected    TINYINT(1)   NOT NULL DEFAULT 0,
  sensor_guids    VARCHAR(255) NULL,             
  device_guid     VARCHAR(64)  NULL,             
  config          VARCHAR(255) NULL,             
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_devices_id (id),
  KEY idx_devices_guid (device_guid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 디바이스 클라이언트 테이블 (3.device_client PC와 연동되는 장비 묶음)
CREATE TABLE device_clients (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  device_no       VARCHAR(50)  NOT NULL,          -- 3.device_client .env 의 device_no 와 매칭
  name            VARCHAR(100) NOT NULL,          -- 예: "기본 device_client PC"
  devices_ids     VARCHAR(255) NULL,              -- 이 클라이언트가 관리하는 devices.id 리스트(쉼표 구분)
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY idx_device_clients_no (device_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Resident Table (Manual core + Kong's password/balance)
CREATE TABLE residents (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  unit_number  VARCHAR(20)  NOT NULL,
  name         VARCHAR(50)  NOT NULL,
  phone        VARCHAR(20)  NOT NULL,
  password     VARCHAR(255) NOT NULL DEFAULT '1234', -- From Kong
  car_plate    VARCHAR(20)  NOT NULL,
  balance      INT          NOT NULL DEFAULT 0,      -- From Kong
  is_active    TINYINT(1)   NOT NULL DEFAULT 1,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_residents_id (id),
  KEY idx_residents_unit (unit_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- RFID 카드 테이블
CREATE TABLE rfid_cards (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  card_uid     VARCHAR(64)  NOT NULL UNIQUE,
  resident_id  INT          NULL,
  is_active    TINYINT(1)   NOT NULL DEFAULT 1,
  description  VARCHAR(100) NULL,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_rfid_cards_id (id),
  KEY idx_rfid_cards_uid (card_uid),
  CONSTRAINT fk_rfid_resident
    FOREIGN KEY (resident_id) REFERENCES residents(id)
    ON DELETE SET NULL
    ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Sensor Table (Full Manual Logic)
CREATE TABLE sensors (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  guid            VARCHAR(32)  NOT NULL UNIQUE,
  name            VARCHAR(50)  NOT NULL,
  sensor_type     VARCHAR(30)  NOT NULL,            
  sensor_states   TINYINT      NOT NULL DEFAULT 0,  -- 0:Disconnected, 1:Closed, 2:Open, 3:Auto
  gate_auto_state TINYINT      NOT NULL DEFAULT 0,  -- 0:None, 1:Open, 2:Close
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,  
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by      VARCHAR(50)  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Parking Slots (Manual includes sensor_guid mapping)
CREATE TABLE parking_slots (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  name               VARCHAR(50)  NOT NULL,      
  level              VARCHAR(20)  NULL,          
  is_occupied        TINYINT(1)   NOT NULL DEFAULT 0,
  sensor_connected   TINYINT(1)   NOT NULL DEFAULT 0,
  sensor_guid        VARCHAR(32)  NULL,          
  last_vehicle_plate VARCHAR(20)  NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_parking_slots_id (id),
  KEY idx_parking_slots_sensor_guid (sensor_guid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Parking Records (Manual's high-traffic table)
CREATE TABLE parking_records (
    record_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    license_plate  VARCHAR(15)  NOT NULL,
    entry_timestamp DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    exit_timestamp DATETIME(3)  NULL,
    is_registered  TINYINT(1)   NOT NULL DEFAULT 0,
    charge_amount  INT          DEFAULT 0,
    INDEX idx_active_vehicle (license_plate, exit_timestamp),
    INDEX idx_entry_time (entry_timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 초기 데이터(장비 + 슬롯) 삽입

INSERT INTO devices (
  name,
  type,
  device_type,
  connection_type,
  connection_detail,
  control_method,
  ip_address,
  port_info,
  is_active,
  is_connected,
  sensor_guids,
  device_guid,
  config,
  created_at,
  updated_at
)
VALUES
  -- 입출구 차단기 컨트롤러: IR(입구) + RFID + 게이트 서보 센서 포함
  (
    '입출구 차단기 컨트롤러_1',
    'gate_controller',
    'CLIENT',
    'ethernet',
    'tcp',
    'socket',
    '192.168.25.51', -- 입출구 차단기 컨트롤러 IP (esp32_board1_1)
    '8080',
    1,
    0,
    'ESP32-S1-ENTRY01,ESP32-RFID-01,ESP32-GATE-01',
    'DEV-GATE-1',
    '{"socket_port":8080}',
    NOW(),
    NOW()
  ),
  -- 입출구 차단기 컨트롤러: IR(출구) + LED 제어
  (
    '입출구 차단기 컨트롤러_2',
    'gate_controller',
    'CLIENT',
    'ethernet',
    'tcp',
    'socket',
    '192.168.25.52', -- 입출구 차단기 컨트롤러 IP (esp32_board1_2)
    '8080',
    1,
    0,
    'ESP32-S2-EXIT01,ESP32-LED-01',
    'DEV-GATE-2',
    '{"socket_port":8080}',
    NOW(),
    NOW()
  ),

  -- LPR 카메라 서버 (ESP32-CAM + PC 서버 연동 (입구카메라))
  (
    '입구 LPR 카메라',
    'lpr_camera',
    'CLIENT',
    'ethernet',
    'udp,tcp',
    'restapi',
    '192.168.0.34',
    '7080', -- 주 통신 포트 (REST)
    1,
    0,
    'ESP32-CAM-01',
    'DEV-LPR-1',
    '{"rest_port":7080,"udp_port":7070}',
    NOW(),
    NOW()
  ),
  -- LPR 카메라 서버 (ESP32-CAM + PC 서버 연동 (입구카메라))
  (
    '출구 LPR 카메라',
    'lpr_camera',
    'CLIENT',
    'ethernet',
    'udp,tcp',
    'restapi',
    '192.168.0.35',
    '7080', -- 주 통신 포트 (REST)
    1,
    0,
    'ESP32-CAM-02',
    'DEV-LPR-2',
    '{"rest_port":7080,"udp_port":7090}',
    NOW(),
    NOW()
  ),
  -- 노상 주차면 센서 컨트롤러
   (
  '노상 주차면 센서 컨트롤러',
  'street_parking_controller',
  'CLIENT',
  'ethernet',
  'tcp',
  'socket',
  '192.168.0.49',   -- 실제 esp32_board2 고정 IP
  '8080',
  1,
  0,
  'ESP32-IR-PARKINGLOT01,ESP32-IR-PARKINGLOT02,ESP32-IR-PARKINGLOT03,ESP32-IR-PARKINGLOT04',
  'DEV-STREET-1',
  '{"socket_port":8080}',
  NOW(), NOW()
),
  -- 주차타워 컨트롤러: 주차타워 Board No.3 등과 연동 예정
  (
    '주차타워 컨트롤러',
    'tower_controller',
    'CLIENT',
    'serial',
    'serial',
    NULL,
    '',
    'ttyUSB0', -- 예시: USB 직렬 포트명
    1,
    0,
    NULL,
    NULL,
    '{"serial_port":"ttyUSB0"}',
    NOW(),
    NOW()
  )
ON DUPLICATE KEY UPDATE
  type = VALUES(type),
  device_type = VALUES(device_type),
  ip_address = VALUES(ip_address),
  connection_type = VALUES(connection_type),
  connection_detail = VALUES(connection_detail),
  control_method = VALUES(control_method),
  port_info = VALUES(port_info),
  sensor_guids = VALUES(sensor_guids),
  config = VALUES(config),
  is_connected = VALUES(is_connected),
  is_active = VALUES(is_active),
  updated_at = NOW();

-- 디바이스 클라이언트 샘플 데이터 (현재는 3.device_client 한 대가 전체 장비를 관리)
INSERT INTO device_clients (device_no, name, devices_ids, is_active, created_at, updated_at)
VALUES
  ('DC-001', '기본 device_client PC', '1,2,3,4,5', 1, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  devices_ids = VALUES(devices_ids),
  is_active = VALUES(is_active),
  updated_at = NOW();

INSERT INTO parking_slots (name, level, is_occupied, sensor_connected, sensor_guid, last_vehicle_plate, created_at, updated_at)
VALUES
  ('S1', 'street', 0, 0, 'ESP32-IR-PARKINGLOT01', NULL, NOW(), NOW()),
  ('S2', 'street', 0, 0, 'ESP32-IR-PARKINGLOT02', NULL, NOW(), NOW()),
  ('S3', 'street', 0, 0, 'ESP32-IR-PARKINGLOT03', NULL, NOW(), NOW()),
  ('S4', 'street', 0, 0, 'ESP32-IR-PARKINGLOT04', NULL, NOW(), NOW()),
  ('T1', 'tower',  0, 0, NULL, NULL, NOW(), NOW()),
  ('T2', 'tower',  0, 0, NULL, NULL, NOW(), NOW()),
  ('T3', 'tower',  0, 0, NULL, NULL, NOW(), NOW()),
  ('T4', 'tower',  0, 0, NULL, NULL, NOW(), NOW()),
  ('T5', 'tower',  0, 0, NULL, NULL, NOW(), NOW()),
  ('T6', 'tower',  0, 0, NULL, NULL, NOW(), NOW())
ON DUPLICATE KEY UPDATE updated_at = NOW();

-- 입주민 샘플 데이터
INSERT INTO residents (unit_number, name, phone, car_plate, created_at, updated_at)
VALUES
  ('101-101', '홍길동',    '010-1111-1111', '12가1234', NOW(), NOW()),
  ('101-102', '김철수',    '010-2222-2222', '23나2345', NOW(), NOW()),
  ('102-201', '이영희',    '010-3333-3333', '34다3456', NOW(), NOW()),
  ('102-202', '박민수',    '010-4444-4444', '45라4567', NOW(), NOW()),
  ('103-301', '최서연',    '010-5555-5555', '56마5678', NOW(), NOW()),
  ('103-302', '오지훈',    '010-6666-6666', '67바6789', NOW(), NOW()),
  ('104-401', '정하늘',    '010-7777-7777', '78사7890', NOW(), NOW()),
  ('104-402', '한지민',    '010-8888-8888', '89아8901', NOW(), NOW()),
  ('105-501', '조은우',    '010-9999-9999', '90자9012', NOW(), NOW()),
  ('105-502', '신다인',    '010-0000-0000', '01차0123', NOW(), NOW())
ON DUPLICATE KEY UPDATE updated_at = NOW();

-- RFID 카드 샘플 데이터 (입주민과 일부 매핑)
INSERT INTO rfid_cards (card_uid, resident_id, is_active, description, created_at, updated_at)
VALUES
  ('RFID0001', 1, 1, '101-101 차량', NOW(), NOW()),
  ('RFID0002', 2, 1, '101-102 차량', NOW(), NOW()),
  ('RFID0003', 3, 1, '102-201 차량', NOW(), NOW()),
  ('RFID0004', 4, 1, '102-202 차량', NOW(), NOW()),
  ('RFID0005', 5, 1, '103-301 차량', NOW(), NOW()),
  ('RFID0006', 6, 1, '103-302 차량', NOW(), NOW()),
  ('RFID0007', 7, 1, '104-401 차량', NOW(), NOW()),
  ('RFID0008', 8, 1, '104-402 차량', NOW(), NOW()),
  ('RFID0009', 9, 1, '105-501 차량', NOW(), NOW()),
  ('RFID0010', 10, 1, '105-502 차량', NOW(), NOW())
ON DUPLICATE KEY UPDATE updated_at = NOW();

-- 센서 테이블 샘플 데이터 (ESP32 카메라 + IR/RFID/게이트)
INSERT INTO sensors (guid, name, sensor_type, sensor_states, gate_auto_state, is_active, created_at, created_by)
VALUES
  -- ESP32 카메라 모듈
  ('ESP32-CAM-01',    'CamStream_ENTRY',    'CAMERA',      0, 0, 1, NOW(), 'admin'),
  ('ESP32-CAM-02',    'CamStream_EXIT',     'CAMERA',      0, 0, 1, NOW(), 'admin'),
  -- ESP32 보드1: 입구/출구 차량 감지 센서
  ('ESP32-S1-ENTRY01','EntryVehDetect', 'ENTRY_IR',    0, 0, 1, NOW(), 'admin'),
  ('ESP32-S2-EXIT01', 'ExitVehDetect',  'EXIT_IR',     0, 0, 1, NOW(), 'admin'),

  -- ESP32 보드1: RFID 리더기, 게이트 서보모터, LED 제어
  ('ESP32-RFID-01',   'RFIDReader',     'RFID',        0, 0, 1, NOW(), 'admin'),
  ('ESP32-GATE-01',   'GateServo',      'GATE_SERVO',  1, 0, 1, NOW(), 'admin'),
  ('ESP32-LED-01',   'LEDControl',      'LED_CONTROL', 0, 0, 1, NOW(), 'admin'),

  -- ESP32 보드1: 주차면 제어
  ('ESP32-IR-PARKINGLOT01', 'ParkingLotDetect',  'PARKING_IR',     0, 0, 1, NOW(), 'admin'),
  ('ESP32-IR-PARKINGLOT02', 'ParkingLotDetect',  'PARKING_IR',     0, 0, 1, NOW(), 'admin'),
  ('ESP32-IR-PARKINGLOT03', 'ParkingLotDetect',  'PARKING_IR',     0, 0, 1, NOW(), 'admin'),
  ('ESP32-IR-PARKINGLOT04', 'ParkingLotDetect',  'PARKING_IR',     0, 0, 1, NOW(), 'admin')

ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  sensor_type = VALUES(sensor_type),
  sensor_states = VALUES(sensor_states),
  gate_auto_state = VALUES(gate_auto_state),
  is_active = VALUES(is_active);





