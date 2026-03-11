-- 1. DB 선택
CREATE DATABASE IF NOT EXISTS smart_parking
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE smart_parking;

-- 2. 기존 테이블 삭제 (FK 순서 고려: 자식 → 부모)
DROP TABLE IF EXISTS event_logs;
DROP TABLE IF EXISTS sensors;
DROP TABLE IF EXISTS rfid_cards;
DROP TABLE IF EXISTS residents;
DROP TABLE IF EXISTS parking_slots;
DROP TABLE IF EXISTS devices;

-- 3. 새 테이블 생성

CREATE TABLE devices (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  name            VARCHAR(100) NOT NULL,
  type            VARCHAR(50)  NOT NULL,         -- esp32, esp32-cam, arduino 등
  device_type     ENUM('CLIENT','SERVER') NOT NULL, -- 'CLIENT' 또는 'SERVER'
  connection_type VARCHAR(20)  NOT NULL DEFAULT 'ethernet', -- ethernet, serial 등
  connection_detail VARCHAR(100) NULL,           -- 예: 'tcp', 'udp,tcp'
  control_method  VARCHAR(50)  NULL,             -- 예: 'socket', 'restapi'
  ip_address      VARCHAR(45)  NULL,
  port_info       VARCHAR(50)  NULL,             -- 이더넷: 포트번호, serial: 포트명
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,
  is_connected    TINYINT(1)   NOT NULL DEFAULT 0,
  sensor_guids    VARCHAR(255) NULL,             -- 이 장비에 연결된 센서 GUID 리스트(쉼표 구분)
  config          VARCHAR(255) NULL,             -- JSON 문자열 등
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_devices_id (id)
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

-- 입주민 테이블
CREATE TABLE residents (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  unit_number  VARCHAR(20)  NOT NULL,  -- 몇 호
  name         VARCHAR(50)  NOT NULL,
  phone        VARCHAR(20)  NOT NULL,
  password     VARCHAR(255) NOT NULL DEFAULT '1234', -- 비밀번호 추가
  car_plate    VARCHAR(20)  NOT NULL,
  balance      INT          NOT NULL DEFAULT 0, -- 예치금 잔액
  is_active    TINYINT(1)   NOT NULL DEFAULT 1, -- 활성화 여부
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

-- 방문 예약 테이블
CREATE TABLE IF NOT EXISTS guest_visits (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  car_plate    VARCHAR(20)  NOT NULL,
  unit_number  VARCHAR(20)  NOT NULL,  -- 어느 세대 방문인지
  status       VARCHAR(20)  NOT NULL DEFAULT '승인대기', -- 승인대기, 승인됨, 거절됨
  arrival_time DATETIME     NULL,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_guest_visits_unit (unit_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 결제 내역 테이블
CREATE TABLE IF NOT EXISTS payments (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  car_plate    VARCHAR(20)  NOT NULL,
  unit_number  VARCHAR(20)  NOT NULL,
  amount       INT          NOT NULL,
  payment_date DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  method       VARCHAR(50)  NULL, -- 카카오페이, 신용카드 등
  status       VARCHAR(20)  NOT NULL DEFAULT '결제완료', -- 결제완료, 미결제
  KEY idx_payments_unit (unit_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 결제 카드 테이블
CREATE TABLE IF NOT EXISTS payment_cards (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  resident_id  INT NOT NULL,
  card_type    VARCHAR(50)  NOT NULL, -- 신한, 국민 등
  card_number  VARCHAR(20)  NOT NULL, -- XXXX-XXXX-XXXX-XXXX
  expiry       VARCHAR(10)  NOT NULL, -- MM/YY
  is_default   TINYINT(1)   NOT NULL DEFAULT 0,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_card_resident FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 센서 테이블 (카메라, IR, RFID, 게이트 서보 등)
CREATE TABLE sensors (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  guid         VARCHAR(32)  NOT NULL,
  name         VARCHAR(50)  NOT NULL,
  sensor_type  VARCHAR(30)  NOT NULL,            -- CAMERA, ENTRY_IR, EXIT_IR, RFID, GATE_SERVO 등
  is_active    TINYINT(1)   NOT NULL DEFAULT 1,  -- 사용 유무
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by   VARCHAR(50)  NOT NULL,            -- 등록한 사람
  UNIQUE KEY idx_sensors_guid (guid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 주차면 테이블 (sensor_connected 포함)
CREATE TABLE parking_slots (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  name               VARCHAR(50)  NOT NULL,      -- S1~S4, T1~T6 등
  level              VARCHAR(20)  NULL,          -- street, tower 등
  is_occupied        TINYINT(1)   NOT NULL DEFAULT 0,
  sensor_connected   TINYINT(1)   NOT NULL DEFAULT 0,
  last_vehicle_plate VARCHAR(20)  NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_parking_slots_id (id),
  KEY idx_parking_slots_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 이벤트 로그 테이블
CREATE TABLE event_logs (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  device_id  INT NULL,
  event_type VARCHAR(50)  NOT NULL,             -- ENTER, EXIT, ERROR, STATUS 등
  message    VARCHAR(255) NULL,
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_event_logs_id (id),
  KEY idx_event_logs_device_id (device_id),
  CONSTRAINT fk_event_logs_device
    FOREIGN KEY (device_id) REFERENCES devices(id)
    ON DELETE SET NULL
    ON UPDATE CASCADE
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
  config,
  created_at,
  updated_at
)
VALUES
  -- 입구 차단기 컨트롤러: IR(입구/출구) + RFID + 게이트 서보 센서 포함
  (
    '입차 차단기 컨트롤러',
    'gate_controller',
    'CLIENT',
    'ethernet',
    'tcp',
    'socket',
    '192.168.25.54',
    '8080',
    1,
    0,
    'ESP32-S1-ENTRY01,ESP32-S2-EXIT01,ESP32-RFID-01,ESP32-GATE-01',
    '{"socket_port":8080}',
    NOW(),
    NOW()
  ),
    -- LPR 카메라 서버 (ESP32-CAM + PC 서버 연동)
  (
    'LPR 카메라 서버',
    'lpr_camera_server',
    'SERVER',
    'ethernet',
    'udp,tcp',
    'restapi',
    '192.168.25.55',
    '80', -- 주 통신 포트 (REST)
    1,
    0,
    'ESP32-CAM-01',
    '{"rest_port":80,"udp_port":7072}',
    NOW(),
    NOW()
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
  ('DC-001', '기본 device_client PC', '1,2,3', 1, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  devices_ids = VALUES(devices_ids),
  is_active = VALUES(is_active),
  updated_at = NOW();

INSERT INTO parking_slots (name, level, is_occupied, sensor_connected, last_vehicle_plate, created_at, updated_at)
VALUES
  ('S1', 'street', 0, 0, NULL, NOW(), NOW()),
  ('S2', 'street', 0, 0, NULL, NOW(), NOW()),
  ('S3', 'street', 0, 0, NULL, NOW(), NOW()),
  ('S4', 'street', 0, 0, NULL, NOW(), NOW()),
  ('T1', 'tower',  0, 0, NULL, NOW(), NOW()),
  ('T2', 'tower',  0, 0, NULL, NOW(), NOW()),
  ('T3', 'tower',  0, 0, NULL, NOW(), NOW()),
  ('T4', 'tower',  0, 0, NULL, NOW(), NOW()),
  ('T5', 'tower',  0, 0, NULL, NOW(), NOW()),
  ('T6', 'tower',  0, 0, NULL, NOW(), NOW())
ON DUPLICATE KEY UPDATE updated_at = NOW();

-- 입주민 샘플 데이터
INSERT INTO residents (unit_number, name, phone, password, car_plate, created_at, updated_at)
VALUES
  ('101-101', '홍길동',    '010-1111-1111', '1234', '12가1234', NOW(), NOW()),
  ('101-102', '김철수',    '010-2222-2222', '1234', '23나2345', NOW(), NOW()),
  ('102-201', '이영희',    '010-3333-3333', '1234', '34다3456', NOW(), NOW()),
  ('102-202', '박민수',    '010-4444-4444', '1234', '45라4567', NOW(), NOW()),
  ('103-301', '최서연',    '010-5555-5555', '1234', '56마5678', NOW(), NOW()),
  ('103-302', '오지훈',    '010-6666-6666', '1234', '67바6789', NOW(), NOW()),
  ('104-401', '정하늘',    '010-7777-7777', '1234', '78사7890', NOW(), NOW()),
  ('104-402', '한지민',    '010-8888-8888', '1234', '89아8901', NOW(), NOW()),
  ('105-501', '조은우',    '010-9999-9999', '1234', '90자9012', NOW(), NOW()),
  ('105-502', '신다인',    '010-0000-0000', '1234', '01차0123', NOW(), NOW())
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

-- 방문 예약 샘플 데이터
INSERT INTO guest_visits (car_plate, unit_number, status, arrival_time)
VALUES
  ('55허5555', '101-101', '승인됨', '2026-03-10 10:00:00'),
  ('11하1111', '101-101', '승인대기', NULL),
  ('99어9999', '101-102', '승인대기', NULL)
ON DUPLICATE KEY UPDATE updated_at = NOW();

-- 결제 샘플 데이터
INSERT INTO payments (car_plate, unit_number, amount, payment_date, method, status)
VALUES
  ('12가1234', '101-101', 5000, '2026-03-09 14:00:00', '카카오페이', '결제완료'),
  ('12가1234', '101-101', 3000, '2026-03-08 09:30:00', '신용카드', '결제완료'),
  ('34다3456', '102-201', 12000, '2026-03-07 18:20:00', '무통장입금', '미결제')
ON DUPLICATE KEY UPDATE updated_at = NOW();

-- 센서 테이블 샘플 데이터 (ESP32 카메라 + IR/RFID/게이트)
INSERT INTO sensors (guid, name, sensor_type, is_active, created_at, created_by)
VALUES
  -- ESP32 카메라 모듈
  ('ESP32-CAM-01',    'CamStream',      'CAMERA',     1, NOW(), 'admin'),

  -- ESP32 보드1: 입구/출구 차량 감지 센서
  ('ESP32-S1-ENTRY01','EntryVehDetect', 'ENTRY_IR',   1, NOW(), 'admin'),
  ('ESP32-S2-EXIT01', 'ExitVehDetect',  'EXIT_IR',    1, NOW(), 'admin'),

  -- ESP32 보드1: RFID 리더기, 게이트 서보모터
  ('ESP32-RFID-01',   'RFIDReader',     'RFID',       1, NOW(), 'admin'),
  ('ESP32-GATE-01',   'GateServo',      'GATE_SERVO', 1, NOW(), 'admin')
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  sensor_type = VALUES(sensor_type),
  is_active = VALUES(is_active);